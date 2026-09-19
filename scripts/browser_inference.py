"""Controlled HTTP provider for browser tests; never used by the application launcher."""

import json
import select
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL = "browser-lifecycle-model"


@contextmanager
def browser_inference(marker: Path) -> Iterator[str]:
    """Hold a real worker HTTP request until disconnect, shutdown, or a bounded timeout."""
    stopped = threading.Event()
    marker.write_text("{}", encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/api/tags":
                self.send_error(404)
                return
            data = json.dumps(
                {"models": [{"name": MODEL, "capabilities": ["completion"]}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            if self.path != "/api/chat":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024 * 1024:
                self.send_error(413)
                return
            payload = json.loads(self.rfile.read(length))
            context = json.loads(payload["messages"][-1]["content"])
            receipt = {"brief": context["brief"], "model": payload["model"], "disconnected": False}

            def record() -> None:
                temporary = marker.with_suffix(".tmp")
                temporary.write_text(json.dumps(receipt), encoding="utf-8")
                temporary.replace(marker)

            record()
            deadline = time.monotonic() + 45
            while not stopped.wait(0.05) and time.monotonic() < deadline:
                try:
                    readable, _, _ = select.select([self.connection], [], [], 0)
                    if readable and not self.connection.recv(1, socket.MSG_PEEK):
                        receipt["disconnected"] = True
                        record()
                        return
                except (ConnectionResetError, ConnectionAbortedError):
                    receipt["disconnected"] = True
                    record()
                    return
            if not stopped.is_set():
                self.send_error(504, "Controlled inference deadline reached")

        def log_message(self, *_: object) -> None:
            """The marker records the fixture's lifecycle without logging prompt contents."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name="browser-inference", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("Browser inference fixture did not stop")
