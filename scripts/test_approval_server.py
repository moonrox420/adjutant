"""Real HTTP approval service for isolated browser, canary, and backend test databases."""

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import psycopg
import uvicorn
from bootstrap import provision_approval, provision_approval_files

from adjutant.approval_api import ApprovalSettings, create_app


@contextmanager
def approval_test_server(admin_url: str) -> Iterator[str]:
    """Bind an ephemeral loopback listener and use the real restricted approval role."""
    if admin_url.rsplit("/", 1)[-1] != "adjutant_test":
        raise ValueError("Approval test service requires adjutant_test")
    root = Path(__file__).resolve().parents[1]
    password = provision_approval_files(root / ".local")
    with psycopg.connect(admin_url) as conn:
        provision_approval(conn, password)
    settings = ApprovalSettings(
        database_url=f"postgresql://adjutant_approval:{quote(password)}@{admin_url.split('@')[1]}",
        signing_key_path=root / ".local/approval.key",
        service_secret_path=root / ".local/approval-service.secret",
    )
    server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level="warning"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, name="test-approval", daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("Approval test service failed to start")
                time.sleep(0.02)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=20)
            if thread.is_alive():
                raise RuntimeError("Approval test service did not stop")
