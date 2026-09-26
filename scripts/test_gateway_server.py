"""Real HTTP spend gateway for isolated browser tests."""

import socket
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import psycopg
import uvicorn
from bootstrap import provision_gateway, provision_gateway_files
from pydantic import SecretStr

from adjutant.gateway_api import GatewaySettings, create_app


@contextmanager
def gateway_test_server(admin_url: str) -> Generator[str, None, None]:
    """Bind an ephemeral loopback listener and use the restricted gateway role."""
    if admin_url.rsplit("/", 1)[-1] != "adjutant_test":
        raise ValueError("Gateway test service requires adjutant_test")
    root = Path(__file__).resolve().parents[1]
    password = provision_gateway_files(root / ".local")
    with psycopg.connect(admin_url) as conn:
        provision_gateway(conn, password)
    settings = GatewaySettings(
        database_url=SecretStr(
            f"postgresql://adjutant_gateway:{quote(password)}@{admin_url.split('@')[1]}"
        ),
        public_keys_path=root / ".local/approval-public-keys.json",
        service_secret_path=root / ".local/gateway-service.secret",
    )
    server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level="warning"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        thread = threading.Thread(
            target=server.run,
            kwargs={"sockets": [listener]},
            name="test-gateway",
            daemon=True,
        )
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("Gateway test service failed to start")
                time.sleep(0.02)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=20)
            if thread.is_alive():
                raise RuntimeError("Gateway test service did not stop")
