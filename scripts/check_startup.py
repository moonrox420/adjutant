import http.cookiejar
import json
import secrets
import time
import urllib.request
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from adjutant.credentials import CredentialStore


def verify(state: Path, port: int) -> None:
    origin = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )

    def request(method: str, path: str, payload: dict | None = None) -> Any:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            origin + path,
            data=data,
            method=method,
            headers={
                "Content-Type": "application/json",
                "X-Adjutant-Client": "console",
                "Origin": origin,
            },
        )
        with opener.open(req, timeout=5) as response:
            assert len(response.headers["X-Request-ID"]) == 32
            content = response.read()
            return json.loads(content) if content else None

    request(
        "POST",
        "/api/auth/login",
        {
            "email": "owner@adjutant.local",
            "password": (state / "owner.password").read_text(encoding="utf-8"),
        },
    )
    me = request("GET", "/api/me")
    assert me and "accounts" in me
    account = me["accounts"][0]["id"]
    brands = request("GET", "/api/brands")
    if brands:
        brand = brands[0]["id"]
    else:
        brand_created = request(
            "POST",
            "/api/brands",
            {
                "account_id": account,
                "display_name": "S0 acceptance test",
                "website_url": "https://example.com",
                "vertical": "home_services",
                "monthly_ceiling": "2800.00",
                "daily_ceiling": "100.00",
            },
        )
        assert brand_created
        brand = brand_created["id"]
    canary = secrets.token_urlsafe(32)
    request("PUT", f"/api/brands/{brand}/credentials/startup_canary", {"value": canary})
    configuration = json.loads((state / "runtime.json").read_text(encoding="utf-8"))
    with psycopg.connect(configuration["database_url"], row_factory=cast(Any, dict_row)) as conn:
        conn.execute("SET LOCAL search_path=adjutant,public")
        conn.execute("SELECT set_config('app.current_brand_ids',%s,true)", (brand,))
        assert (
            CredentialStore(state / "tenant-master.key").read(conn, UUID(brand), "startup_canary")
            == canary
        )
        secret_row: Any = conn.execute(
            "SELECT ciphertext FROM tenant_secret WHERE brand_id=%s", (brand,)
        ).fetchone()
        assert secret_row is not None
        ciphertext = secret_row["ciphertext"] if isinstance(secret_row, dict) else secret_row[0]
        assert canary.encode() not in bytes(ciphertext)
    payload = {"request_key": str(uuid4()), "delay_seconds": 1}
    workflow = request("POST", f"/api/brands/{brand}/workflows", payload)
    assert workflow is not None
    retry = request("POST", f"/api/brands/{brand}/workflows", payload)
    assert retry is not None
    assert retry["id"] == workflow["id"]
    path = f"/api/brands/{brand}/workflows/{workflow['id']}"
    deadline = time.monotonic() + 15
    while True:
        status_res = request("GET", path)
        if status_res and status_res.get("state") == "completed":
            break
        if time.monotonic() >= deadline:
            raise AssertionError("The HTTP-hosted worker did not finish its durable workflow")
        time.sleep(0.1)
    assert request("GET", path + "/result") == {
        "workflow_id": workflow["id"],
        "brand_id": brand,
        "status": "completed",
    }
    print(
        "S0 startup acceptance passed: HTTP, restricted database, encrypted secret, "
        "durable result.",
        flush=True,
    )
