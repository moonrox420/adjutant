"""Pause protocol contracts and durable execution; provider fixtures are not live verification."""

import asyncio
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb

from adjutant.adapters.campaign_control import CampaignControl, CampaignTarget
from adjutant.channel_credentials import write_credential
from adjutant.credentials import CredentialStore
from adjutant.remote_stop import enqueue_stop

CHANNELS = [
    "meta",
    "google_ads",
    "youtube",
    "tiktok",
    "linkedin",
    "microsoft",
    "reddit",
    "pinterest",
    "snapchat",
    "amazon_ads",
]


def test_readiness_detects_stopped_remote_worker(client):
    assert client.get("/readyz").status_code == 200
    runner = client.app.state.remote_stops
    runner.close()
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "StopWorkerUnavailable"


def provider_row(channel, paused):
    state = "PAUSED" if paused else "ACTIVE"
    row = {"id": "123", "status": state, "account_id": "456", "ad_account_id": "456"}
    if channel in {"google_ads", "youtube"}:
        return {"results": [{"campaign": row}]}
    if channel == "tiktok":
        return {
            "code": 0,
            "data": {
                "list": [
                    {"campaign_id": "123", "operation_status": "DISABLE" if paused else "ENABLE"}
                ]
            },
        }
    if channel == "microsoft":
        return {"Campaigns": [{"Id": 123, "Status": "Paused" if paused else "Active"}]}
    if channel == "reddit":
        return {"data": {**row, "configured_status": state}}
    if channel == "snapchat":
        return {
            "request_status": "SUCCESS",
            "campaigns": [{"sub_request_status": "SUCCESS", "campaign": row}],
        }
    if channel == "amazon_ads":
        return {"campaigns": [{"campaignId": "123", "state": "PAUSED" if paused else "ENABLED"}]}
    return row


@pytest.mark.parametrize("channel", CHANNELS)
def test_pause_requires_independent_read_back_for_every_channel(channel):
    calls = []
    mutated = False

    def provider(request):
        nonlocal mutated
        read = request.method == "GET" or request.url.path.endswith(
            (":search", "/QueryByIds", "/list")
        )
        calls.append(request)
        if read:
            return httpx.Response(200, json=provider_row(channel, mutated))
        mutated = True
        if channel == "pinterest":
            assert json.loads(request.content) == [{"id": "123", "status": "PAUSED"}]
        if channel == "snapchat":
            assert request.method == "PATCH"
            assert json.loads(request.content) == [
                {"op": "replace", "path": "/status", "value": "PAUSED"}
            ]
        if channel in {"google_ads", "youtube"}:
            assert json.loads(request.content)["operations"][0]["updateMask"] == "status"
        return httpx.Response(200, json={})

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            control = CampaignControl(
                client,
                CampaignTarget(
                    channel,
                    "456",
                    "123",
                    {"customer_id": "789", "ad_product": "SPONSORED_PRODUCTS"},
                ),
                {"developer_token": "test-developer", "client_id": "test-client", "region": "NA"},
                {"access_token": "test-token"},
            )
            result = await control.pause()
            assert result["state"] == "paused"
            assert result["changed"] is True
            assert len(calls) == 3
            calls.clear()
            assert (await control.pause())["changed"] is False
            assert len(calls) == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("channel", CHANNELS)
def test_resume_requires_independent_read_back_for_every_channel(channel):
    calls = []
    mutated = False

    def provider(request):
        nonlocal mutated
        read = request.method == "GET" or request.url.path.endswith(
            (":search", "/QueryByIds", "/list")
        )
        calls.append(request)
        if read:
            return httpx.Response(200, json=provider_row(channel, not mutated))
        mutated = True
        if channel == "pinterest":
            assert json.loads(request.content) == [{"id": "123", "status": "ACTIVE"}]
        if channel == "snapchat":
            assert request.method == "PATCH"
            assert json.loads(request.content) == [
                {"op": "replace", "path": "/status", "value": "ACTIVE"}
            ]
        if channel in {"google_ads", "youtube"}:
            assert json.loads(request.content)["operations"][0]["update"]["status"] == "ENABLED"
        return httpx.Response(200, json={})

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            control = CampaignControl(
                client,
                CampaignTarget(
                    channel,
                    "456",
                    "123",
                    {"customer_id": "789", "ad_product": "SPONSORED_PRODUCTS"},
                ),
                {"developer_token": "test-developer", "client_id": "test-client", "region": "NA"},
                {"access_token": "test-token"},
            )
            result = await control.resume()
            assert result["state"] == "active"
            assert result["changed"] is True
            assert len(calls) == 3
            calls.clear()
            assert (await control.resume())["changed"] is False
            assert len(calls) == 1

    asyncio.run(exercise())



@pytest.mark.parametrize(
    "failure",
    [
        "wrong_identity",
        "wrong_account",
        "unauthorized",
        "partial_failure",
        "unconfirmed",
        "redirect",
    ],
)
def test_provider_failures_never_return_verified_pause(failure):
    calls = []

    def provider(request):
        calls.append(request)
        if failure == "unauthorized":
            return httpx.Response(401, json={"error": "secret-token-never-display"})
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://untrusted.example/token"})
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"partialFailureError": {"message": "secret-token-never-display"}}
                if failure == "partial_failure"
                else {},
            )
        return httpx.Response(
            200,
            json={
                "id": "999" if failure == "wrong_identity" else "123",
                "account_id": "999" if failure == "wrong_account" else "456",
                "status": "ACTIVE",
            },
        )

    async def exercise():
        from adjutant.errors import DomainError

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(provider), follow_redirects=False
        ) as client:
            control = CampaignControl(
                client, CampaignTarget("meta", "456", "123", {}), {}, {"access_token": "test-token"}
            )
            with pytest.raises(DomainError) as raised:
                await control.pause()
            assert "secret-token" not in raised.value.message
            assert all(r.url.host == "graph.facebook.com" for r in calls)
            if failure in {"wrong_identity", "wrong_account"}:
                assert all(r.method == "GET" for r in calls)

    asyncio.run(exercise())


def managed_campaign(admin, brand, channel):
    connection = admin.execute(
        "INSERT INTO "
        "channel_connection(brand_id,channel,external_ad_account_id,selected,verified_at) "
        "VALUES(%s,%s,'456',true,now()) RETURNING id",
        (brand, channel),
    ).fetchone()["id"]
    campaign = admin.execute(
        "INSERT INTO campaign_object(brand_id,connection_id,channel,level,native_id,state) "
        "VALUES(%s,%s,%s,'campaign','123','active') RETURNING id",
        (brand, connection, channel),
    ).fetchone()["id"]
    return connection, campaign


def test_all_missing_credentials_report_failures_without_false_pause(client, admin, brand):
    for channel in CHANNELS:
        managed_campaign(admin, brand, channel)
    started = time.monotonic()
    response = client.post(
        f"/api/brands/{brand}/kill",
        json={"reason": "Verify all-channel stop authorization failures"},
    )
    assert response.status_code == 200, response.text
    assert time.monotonic() - started < 10
    report = response.json()
    assert report["remote_pause_verified"] is False
    assert report["run"]["state"] == "failed"
    assert len(report["items"]) == len(CHANNELS)
    assert all(
        i["error_code"] == "ChannelSetupRequired" and i["verified_at"] is None
        for i in report["items"]
    )
    assert admin.execute(
        "SELECT count(*) AS n FROM campaign_object WHERE brand_id=%s AND state='active'", (brand,)
    ).fetchone()["n"] == len(CHANNELS)
    persisted = client.get(f"/api/brands/{brand}/remote-stop").json()
    assert persisted["run"]["id"] == report["run"]["id"]
    assert client.get(f"/api/brands/{uuid4()}/remote-stop").status_code == 404
    assert admin.execute(
        "SELECT count(*) AS n FROM action WHERE brand_id=%s AND action_type='pause'", (brand,)
    ).fetchone()["n"] == len(CHANNELS)


def test_stop_includes_unselected_managed_accounts_and_exposes_orphans(client, admin, brand):
    connection, campaign = managed_campaign(admin, brand, "meta")
    admin.execute("UPDATE channel_connection SET selected=false WHERE id=%s", (connection,))
    child = admin.execute(
        "INSERT INTO "
        "campaign_object(brand_id,connection_id,channel,level,native_id,state,parent_id) "
        "VALUES(%s,%s,'meta','ad_group','234','active',%s) RETURNING id",
        (brand, connection, campaign),
    ).fetchone()["id"]
    orphan = admin.execute(
        "INSERT INTO campaign_object(brand_id,connection_id,channel,level,native_id,state) "
        "VALUES(%s,%s,'meta','ad_group','345','active') RETURNING id",
        (brand, connection),
    ).fetchone()["id"]
    response = client.post(
        f"/api/brands/{brand}/kill", json={"reason": "Stop all formerly selected managed campaigns"}
    )
    assert response.status_code == 200, response.text
    items = {i["campaign_object_id"]: i for i in response.json()["items"]}
    assert set(items[str(campaign)]["covered_object_ids"]) == {str(campaign), str(child)}
    assert items[str(orphan)]["error_code"] == "CampaignParentMissing"
    assert response.json()["remote_pause_verified"] is False


def test_remote_stop_inventory_cannot_cross_account_scope(admin, brand, identity):
    connection, campaign = managed_campaign(admin, brand, "meta")
    run = enqueue_stop(admin, UUID(brand), identity["user"])
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "UPDATE remote_stop_item SET account_id='other-account' WHERE run_id=%s", (run,)
        )


def test_durable_runner_executes_saved_work_and_persists_observed_state(
    client, admin, brand, identity, monkeypatch
):
    """Recover running work through real TCP HTTP; this emulator is not a live ad account."""
    connection, campaign = managed_campaign(admin, brand, "meta")
    config = client.app.state.config
    store = CredentialStore(config.credential_master_key_path)
    write_credential(
        admin,
        store,
        UUID(brand),
        "meta",
        "app",
        {"client_id": "fixture", "client_secret": "fixture"},
    )
    write_credential(admin, store, UUID(brand), "meta", "token", {"access_token": "fixture"})
    admin.execute(
        "INSERT INTO channel_authorization(brand_id,channel,accounts) VALUES(%s,'meta',%s)",
        (brand, Jsonb([{"id": "456"}])),
    )
    mutated = []

    class ProviderHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            logging.getLogger(__name__).debug("Provider emulator: %s", format % args)

        def send_json(self, value):
            encoded = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            self.send_json(provider_row("meta", bool(mutated)))

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            assert body == b"status=PAUSED"
            mutated.append(body)
            self.send_json({"success": True})

    server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def route_fixture(request):
        request.url = request.url.copy_with(
            scheme="http", host="127.0.0.1", port=server.server_port
        )

    original = httpx.AsyncClient

    def emulator(**kwargs):
        return original(**kwargs, event_hooks={"request": [route_fixture]})

    monkeypatch.setattr("adjutant.remote_stop.httpx.AsyncClient", emulator)
    try:
        with admin.transaction():
            run = enqueue_stop(admin, UUID(brand), identity["user"])
            admin.execute("UPDATE remote_stop_run SET state='running' WHERE id=%s", (run,))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            report = client.get(f"/api/brands/{brand}/remote-stop").json()
            if report["run"]["state"] not in {"queued", "running"}:
                break
            time.sleep(0.05)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert report["remote_pause_verified"] is True, report
    assert report["run"]["id"] == str(run)
    assert len(mutated) == 1
    row = admin.execute(
        "SELECT state,last_verified_at FROM campaign_object WHERE id=%s", (campaign,)
    ).fetchone()
    assert row["state"] == "paused" and row["last_verified_at"]
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM action WHERE brand_id=%s AND action_type='pause'", (brand,)
        ).fetchone()["n"]
        == 1
    )
