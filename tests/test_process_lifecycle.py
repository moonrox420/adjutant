import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from test_accounts import mail_token

from adjutant.processes import launch


@pytest.fixture
def inference_server(plan_input):
    entered, release = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply(
                {
                    "models": [
                        {"name": "controlled-model", "capabilities": ["completion"]}
                    ]
                }
            )

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            entered.set()
            if not release.wait(20):
                self.send_error(504)
                return
            self.reply(
                {
                    "message": {"content": json.dumps(plan_input)},
                    "eval_count": 100,
                    "prompt_eval_count": 200,
                }
            )

        def reply(self, body):
            try:
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # Cancellation intentionally closes this fixture's HTTP connection.
                self.close_connection = True

        def log_message(self, *_):
            """Fixture traffic has no diagnostic value; assertions inspect the real HTTP result."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", entered, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for a real process lifecycle transition")


@pytest.mark.parametrize(
    "action",
    ["logout", "logout-all", "cancel", "brand-stop", "reset", "expire", "worker-kill"],
)
def test_real_generation_process_exit_and_no_late_plan(
    client, confirmed_brand, admin, identity, inference_server, action
):
    url, entered, release = inference_server
    client.app.state.config.ollama_url = url
    processes = []

    def capture(*args, **kwargs):
        process = launch(*args, **kwargs)
        processes.append(process)
        return process

    body = {"model": "controlled-model", "brief": "Create a local repair campaign"}
    with (
        patch("adjutant.processes.launch", side_effect=capture),
        ThreadPoolExecutor(1) as executor,
    ):
        future = executor.submit(
            client.post, f"/api/brands/{confirmed_brand}/generate-plan", json=body
        )
        assert entered.wait(10)
        run = admin.execute(
            "SELECT * FROM agent_run WHERE brand_id=%s", (confirmed_brand,)
        ).fetchone()
        assert processes[0].poll() is None and run["worker_pid"] == processes[0].pid
        if action in ("logout", "logout-all"):
            result = client.post(f"/api/auth/{action}")
            assert result.status_code == 200, result.text
            assert result.json()["cancellation_verified"]
        elif action == "cancel":
            result = client.post(f"/api/jobs/{run['id']}/cancel")
            assert result.status_code == 200, result.text
            assert result.json()["cancellation_verified"]
        elif action == "brand-stop":
            assert (
                client.post(
                    f"/api/brands/{confirmed_brand}/kill",
                    json={"reason": "Stop this controlled generation"},
                ).status_code
                == 200
            )
        elif action == "reset":
            # Provisioned .test identities cannot pass EmailStr validation; their database email
            # is replaced with a syntactically valid public-domain address for this recovery test.
            email = f"reset-{identity['user']}@example.com"
            admin.execute(
                "UPDATE app_user SET email=%s WHERE id=%s", (email, identity["user"])
            )
            assert (
                client.post(
                    "/api/auth/request-link?purpose=reset", json={"email": email}
                ).status_code
                == 202
            )
            token = mail_token(admin, email, "reset")
            assert (
                client.post(
                    "/api/auth/reset-password",
                    json={
                        "token": token,
                        "password": "new recovery passphrase",
                        "confirm_password": "new recovery passphrase",
                    },
                ).status_code
                == 200
            )
        elif action == "expire":
            admin.execute(
                "UPDATE auth_session SET expires_at=now() WHERE user_id=%s",
                (identity["user"],),
            )
        else:
            processes[0].kill()
            processes[0].wait(timeout=3)
        response = future.result(timeout=12)
        assert response.status_code in (401, 409, 503), response.text
        release.set()
    recorded = admin.execute(
        "SELECT * FROM agent_run WHERE id=%s", (run["id"],)
    ).fetchone()
    assert recorded["finished_at"] and recorded["worker_exit_verified_at"]
    assert recorded["worker_exit_code"] == processes[0].returncode
    assert processes[0].poll() is not None
    assert not recorded["schema_valid"]
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM plan WHERE brand_id=%s", (confirmed_brand,)
        ).fetchone()["n"]
        == 0
    )


def test_real_worker_success_releases_process_and_records_usage(
    client, confirmed_brand, admin, inference_server
):
    url, _, release = inference_server
    client.app.state.config.ollama_url = url
    release.set()
    result = client.post(
        f"/api/brands/{confirmed_brand}/generate-plan",
        json={"model": "controlled-model", "brief": "Create a local repair campaign"},
    )
    assert result.status_code == 201, result.text
    run = admin.execute(
        "SELECT * FROM agent_run WHERE brand_id=%s", (confirmed_brand,)
    ).fetchone()
    assert run["worker_exit_code"] == 0 and run["worker_exit_verified_at"] is not None
    assert run["schema_valid"] and run["usage_complete"]
    assert run["input_tokens"] == 200 and run["output_tokens"] == 100


def test_inference_worker_exits_when_parent_pipe_is_lost(inference_server):
    import subprocess

    from adjutant.processes import terminate_owned

    url, entered, release = inference_server
    process = launch("adjutant.generation_worker", subprocess.DEVNULL)
    try:
        process.stdin.write(
            (
                json.dumps({"url": url, "model": "controlled-model", "context": {}})
                + "\n"
            ).encode()
        )
        process.stdin.flush()
        assert entered.wait(10)
        process.stdin.close()
        assert process.wait(timeout=5) == 70
    finally:
        terminate_owned(process)
        release.set()
