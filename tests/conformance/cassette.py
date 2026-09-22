"""HTTP Cassette recording and replay engine for offline CI conformance verification."""

import json
from pathlib import Path
from typing import Any
import httpx


class CassetteError(RuntimeError):
    """Raised when an unrecorded HTTP request is received during replay."""


class Cassette:
    """Manages recorded HTTP request/response interactions."""

    def __init__(self, path: Path | str | None = None, interactions: list[dict[str, Any]] | None = None) -> None:
        self.path = Path(path) if path else None
        self.interactions: list[dict[str, Any]] = []
        if interactions:
            self.interactions = list(interactions)
        elif self.path and self.path.exists():
            content = self.path.read_text(encoding="utf-8")
            self.interactions = json.loads(content) if content.strip() else []

    def record(self, method: str, url: str, status_code: int, response_data: Any, request_data: Any = None) -> None:
        interaction = {
            "request": {
                "method": method.upper(),
                "url": url,
                "data": request_data,
            },
            "response": {
                "status_code": status_code,
                "body": response_data,
            },
        }
        self.interactions.append(interaction)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.interactions, indent=2), encoding="utf-8")

    def match(self, method: str, url: str) -> dict[str, Any] | None:
        norm_method = method.upper()
        req_path = url.split("?")[0].rstrip("/")
        for item in self.interactions:
            req = item.get("request", {})
            if req.get("method") == norm_method:
                item_url = req.get("url", "")
                item_path = item_url.split("?")[0].rstrip("/")
                if (
                    item_path == req_path
                    or req_path.endswith("/" + item_path.lstrip("/"))
                    or (item_path.startswith("/") and req_path.endswith(item_path))
                ):
                    return item["response"]
        return None


class CassetteTransport(httpx.AsyncBaseTransport):
    """httpx transport that replays responses from a Cassette without live network calls."""

    def __init__(self, cassette: Cassette) -> None:
        self.cassette = cassette

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        resp_data = self.cassette.match(request.method, url_str)
        if resp_data is None:
            raise CassetteError(
                f"No matching cassette interaction for {request.method} {url_str}. "
                f"CI conformance tests must not touch live network or unrecorded paths."
            )
        status = resp_data.get("status_code", 200)
        body = resp_data.get("body", {})
        content = json.dumps(body).encode("utf-8") if not isinstance(body, bytes) else body
        return httpx.Response(
            status_code=status,
            content=content,
            headers={"Content-Type": "application/json"},
            request=request,
        )
