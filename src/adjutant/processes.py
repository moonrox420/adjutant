"""Owned subprocess handles, bounded termination, and persisted exit acknowledgements."""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import IO, Any, Literal
from uuid import UUID

from psycopg import Connection
from pydantic import ValidationError

from adjutant.db import Database, Principal, one
from adjutant.errors import DomainError
from adjutant.generation import GenerationResult
from adjutant.models import PlanInput


def child_environment() -> dict[str, str]:
    """Inherit no application credentials; selected provider credentials use the private pipe."""
    allowed = {
        "SYSTEMROOT",
        "WINDIR",
        "PATH",
        "TEMP",
        "TMP",
        "LANG",
        "HOME",
        "USERPROFILE",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["PYTHONUNBUFFERED"] = "1"
    return env


def launch(module: str, output: IO[Any] | int | None) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", module],
        stdin=subprocess.PIPE,
        stdout=output,
        stderr=None,
        env=child_environment(),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def terminate_owned(process: subprocess.Popen) -> int:
    """Only kill a process created by this supervisor, and wait for its OS exit status."""
    if process.poll() is None:
        process.terminate()
        try:
            return process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
    return process.wait(timeout=3)


def lock_active_session(conn: Connection[Any], token_hash: str) -> None:
    if not one(conn, "SELECT lock_session(%s) AS valid", (token_hash,))["valid"]:
        raise DomainError("Unauthorized", "Your session ended. Sign in again.", 401)


def generate_in_process(
    db: Database,
    actor: Principal,
    run: UUID,
    token_hash: str,
    url: str,
    model: str,
    context: dict,
    *,
    provider: Literal["local", "cloud"] = "local",
    api_key: str = "",
) -> GenerationResult:
    deadline = time.monotonic() + 470
    with tempfile.TemporaryFile() as output:
        process = launch("adjutant.generation_worker", output)
        try:
            with db.transaction(actor) as conn:
                conn.execute(
                    "UPDATE agent_run SET worker_pid=%s,worker_heartbeat_at=now() WHERE id=%s",
                    (process.pid, run),
                )
            if process.stdin:
                process.stdin.write(
                    (
                        json.dumps(
                            {
                                "url": url,
                                "model": model,
                                "context": context,
                                "provider": provider,
                                "api_key": api_key,
                            },
                            default=str,
                        )
                        + "\n"
                    ).encode()
                )
                process.stdin.flush()
            while True:
                with db.transaction(actor) as conn:
                    state = one(
                        conn,
                        """SELECT r.cancel_requested_at,
                        EXISTS(SELECT 1 FROM authenticate_session(%s)) AS active,
                        EXISTS(SELECT 1 FROM brand_kill_switch k WHERE k.brand_id=r.brand_id
                               AND k.released_at IS NULL) AS stopped
                        FROM agent_run r WHERE r.id=%s""",
                        (token_hash, run),
                    )
                    conn.execute(
                        "UPDATE agent_run SET worker_heartbeat_at=now() WHERE id=%s",
                        (run,),
                    )
                if state["cancel_requested_at"] or not state["active"] or state["stopped"]:
                    raise DomainError(
                        "GenerationCancelled",
                        "Generation stopped; no draft was saved.",
                        409,
                    )
                if time.monotonic() >= deadline:
                    raise DomainError(
                        "GenerationTimeout", "Generation exceeded its time limit.", 504
                    )
                if os.fstat(output.fileno()).st_size > 1024 * 1024:
                    raise DomainError("GenerationInvalid", "Model output exceeded its limit.", 422)
                if process.poll() is not None:
                    break
                time.sleep(0.15)
            if process.returncode != 0:
                raise DomainError(
                    "GenerationWorkerFailed",
                    "The generation worker exited unexpectedly.",
                    503,
                )
            output.seek(0)
            try:
                body = json.loads(output.read(1024 * 1024))
                if not isinstance(body, dict):
                    raise ValueError("Expected worker response object")
            except (ValueError, UnicodeError) as exc:
                raise DomainError(
                    "GenerationInvalid",
                    "The generation worker returned invalid output.",
                    502,
                ) from exc
            if "error" in body:
                error = body["error"]
                raise DomainError(error["code"], error["message"], error["status"])
            try:
                return GenerationResult(
                    PlanInput.model_validate(body["plan"]),
                    body["input_tokens"],
                    body["output_tokens"],
                    body["attempts"],
                )
            except (KeyError, TypeError, ValidationError) as exc:
                raise DomainError(
                    "GenerationInvalid",
                    "The generation worker returned an invalid draft.",
                    502,
                ) from exc
        finally:
            code = terminate_owned(process)
            if process.stdin:
                process.stdin.close()
            with db.transaction(actor) as conn:
                conn.execute(
                    """UPDATE agent_run SET worker_exit_code=%s,
                             worker_exit_verified_at=now() WHERE id=%s""",
                    (code, run),
                )
