"""Start the S0 service and its own PostgreSQL cluster without interactive setup."""

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]


def run(arguments: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, check=True, cwd=ROOT, **kwargs)


def postgres_binary(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    candidates = [
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "PostgreSQL/18/bin",
        Path("/usr/lib/postgresql/18/bin"),
    ]
    for directory in candidates:
        binary = directory / (f"{name}.exe" if os.name == "nt" else name)
        if binary.is_file():
            return str(binary)
    raise RuntimeError("PostgreSQL 18 with pgvector is required; add its bin directory to PATH")


def secret_file(path: Path) -> str:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text(encoding="utf-8").strip()
    value = secrets.token_urlsafe(32)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value)
    return value


def ensure_environment() -> None:
    if sys.prefix != sys.base_prefix:
        return
    python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        run([sys.executable, "-m", "venv", str(ROOT / ".venv")])
        run([str(python), "-m", "pip", "install", "-r", "requirements.lock"])
    if Path(sys.executable).absolute() != python.absolute():
        raise SystemExit(subprocess.call([str(python), str(Path(__file__)), *sys.argv[1:]]))


def start(state: Path, port: int, db_port: int, *, check: bool = False) -> None:
    import psycopg
    from bootstrap import provision_runtime
    from migrate import migrate

    from adjutant.credentials import provision_master_key
    from adjutant.security import password_hash

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    state.mkdir(parents=True, exist_ok=True)
    database_password = secret_file(state / "postgres.password")
    app_password = secret_file(state / "app.password")
    owner_password = secret_file(state / "owner.password")
    data = state / "postgres"
    initdb = postgres_binary("initdb")
    pg_ctl = postgres_binary("pg_ctl")
    if not (data / "PG_VERSION").exists():
        if data.exists() and any(data.iterdir()):
            raise RuntimeError(
                "PostgreSQL directory is nonempty but uninitialized; refusing to replace it"
            )
        run(
            [
                initdb,
                "-D",
                str(data),
                "-U",
                "adjutant_admin",
                "-A",
                "scram-sha-256",
                "--encoding=UTF8",
                f"--pwfile={state / 'postgres.password'}",
            ]
        )
    status = subprocess.run([pg_ctl, "-D", str(data), "status"], capture_output=True)
    if status.returncode != 0:
        run(
            [
                pg_ctl,
                "-D",
                str(data),
                "-l",
                str(state / "postgres.log"),
                "-o",
                f"-h 127.0.0.1 -p {db_port}",
                "-w",
                "start",
            ]
        )
    server = f"postgresql://adjutant_admin:{quote(database_password)}@127.0.0.1:{db_port}"
    with psycopg.connect(server + "/postgres", autocommit=True) as conn:
        if (
            conn.execute("SHOW data_directory")
            .fetchone()[0]
            .replace("\\", "/")
            .rstrip("/")
            .casefold()
            != str(data).replace("\\", "/").casefold()
        ):
            raise RuntimeError("Database port belongs to a different cluster")
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='adjutant'").fetchone():
            conn.execute("CREATE DATABASE adjutant")
    admin_url = server + "/adjutant"
    migrate(admin_url)
    provision_master_key(state / "tenant-master.key")
    with psycopg.connect(admin_url) as conn:
        provision_runtime(conn, app_password)
        owner = conn.execute(
            "SELECT id FROM app_user WHERE email='owner@adjutant.local'"
        ).fetchone()
        if owner is None:
            owner = conn.execute(
                "INSERT INTO app_user(email,full_name,email_verified_at) "
                "VALUES('owner@adjutant.local','Workspace owner',now()) RETURNING id"
            ).fetchone()
            account = conn.execute(
                "INSERT INTO account(account_type,display_name) "
                "VALUES('business','My business') RETURNING id"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO local_credential VALUES(%s,%s)",
                (owner[0], password_hash(owner_password)),
            )
            conn.execute(
                "INSERT INTO seat(account_id,user_id,role,accepted_at) VALUES(%s,%s,'owner',now())",
                (account, owner[0]),
            )
    config_path = state / "runtime.json"
    config = {
        "database_url": f"postgresql://adjutant_app:{quote(app_password)}@127.0.0.1:{db_port}/adjutant",
        "worker_database_url": None,
        "workflow_enabled": True,
        "credential_master_key_path": str(state / "tenant-master.key"),
        "object_store_path": str(state / "objects"),
        "mail_directory": str(state / "mail"),
        "public_origin": f"http://127.0.0.1:{port}",
    }
    descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(config, handle)
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    env["ADJUTANT_RUNNER_CONFIG"] = str(config_path)
    # Configuration and credentials stay out of process arguments and HTTP access logs.
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts/serve.py"), "--port", str(port)],
        cwd=ROOT,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        deadline = time.monotonic() + 30
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while True:
            if process.poll() is not None:
                raise RuntimeError(f"HTTP service exited with code {process.returncode}")
            try:
                with opener.open(f"http://127.0.0.1:{port}/readyz", timeout=1) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("HTTP service failed its readiness deadline") from None
                time.sleep(0.2)
        print(f"Adjutant S0 ready at http://127.0.0.1:{port}/docs", flush=True)
        print(f"Owner: owner@adjutant.local; password file: {state / 'owner.password'}", flush=True)
        if check:
            from check_startup import verify

            verify(state, port)
            return
        process.wait()
        if process.returncode:
            raise RuntimeError(f"HTTP service exited with code {process.returncode}")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-directory", type=Path, default=ROOT / ".local/runner")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--db-port", type=int, default=55440)
    parser.add_argument("--check", action="store_true", help="Run the S0 HTTP smoke test and stop")
    args = parser.parse_args()
    ensure_environment()
    sys.path.insert(0, str(ROOT / "src"))
    try:
        start(args.state_directory.resolve(), args.port, args.db_port, check=args.check)
    except KeyboardInterrupt:
        print("HTTP service stopped; PostgreSQL and durable state are preserved.")
    finally:
        data = args.state_directory.resolve() / "postgres"
        if args.check and (data / "postmaster.pid").exists():
            run([postgres_binary("pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"])


if __name__ == "__main__":
    main()
