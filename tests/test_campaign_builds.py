"""Real database, API, journal and worker against an explicitly simulated Meta HTTP server."""

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import psycopg
import pytest
from test_autonomy import selected_account  # noqa: F401

from adjutant.adapters import meta_build
from adjutant.campaign_builds import BuildJournal, CampaignBuildRunner, run_build
from adjutant.channel_credentials import write_credential
from adjutant.credentials import CredentialStore
from adjutant.events import EventRegistry
from adjutant.storage import ObjectStore


@pytest.fixture
def graph_server(monkeypatch):
    state = {
        "objects": {},
        "images": {},
        "posts": [],
        "lose_campaign_response": False,
        "wrong_budget": False,
        "wrong_copy": False,
        "wrong_campaign_state": False,
        "authorization_error": False,
        "reject_creative": False,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return None

        def send(self, body, code=200):
            content = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            if state["authorization_error"]:
                return self.send({"error": {"code": 190, "message": "Expired simulated-secret"}}, 403)
            url = urlsplit(self.path)
            key = url.path.rsplit("/", 1)[-1]
            if key == "act_12345":
                return self.send({"account_id": "12345", "account_status": 1, "currency": "USD"})
            if key == "adimages":
                return self.send({"data": list(state["images"].values())})
            if key in {"campaigns", "adsets", "adcreatives", "ads"}:
                return self.send(
                    {"data": [obj for obj in state["objects"].values() if obj["edge"] == key]}
                )
            row = state["objects"].get(key)
            if row is None:
                return self.send({"error": {"message": "Object missing", "code": 100}}, 400)
            row = json.loads(json.dumps(row))
            if state["wrong_campaign_state"] and row.get("edge") == "campaigns":
                row["status"] = "ACTIVE"
            if state["wrong_budget"] and "daily_budget" in row:
                row["daily_budget"] = "99999"
            if state["wrong_copy"] and "object_story_spec" in row:
                row["object_story_spec"]["link_data"]["message"] = "Unexpected copy"
            return self.send(row)

        def do_POST(self):
            assert self.headers.get("Authorization") == "Bearer simulated-secret"
            edge = self.path.rsplit("/", 1)[-1]
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            state["posts"].append(edge)
            if edge == "adcreatives" and state["reject_creative"]:
                return self.send({"error": {"code": 100, "message": "Fixture creative rejected — revise the offer."}}, 400)
            if edge == "adimages":
                message = BytesParser(policy=policy.default).parsebytes(
                    (
                        f"Content-Type: {self.headers['Content-Type']}\r\nMIME-Version: 1.0\r\n\r\n"
                    ).encode()
                    + raw
                )
                part = next(message.iter_parts())
                assert part.get_payload(decode=True).startswith(b"\x89PNG")
                image = {
                    "hash": "imagehash",
                    "name": part.get_filename(),
                    "width": 1080,
                    "height": 1080,
                }
                state["images"]["imagehash"] = image
                return self.send({"images": {part.get_filename(): image}})
            values = {key: value[0] for key, value in parse_qs(raw.decode()).items()}
            for key in ("targeting", "creative", "object_story_spec"):
                if key in values:
                    values[key] = json.loads(values[key])
            if "creative" in values:
                values["creative"] = {"id": values["creative"]["creative_id"]}
            identity = str(1000 + len(state["objects"]))
            state["objects"][identity] = {
                "id": identity,
                "account_id": "12345",
                "edge": edge,
                **values,
            }
            if edge == "campaigns" and state["lose_campaign_response"]:
                state["lose_campaign_response"] = False
                self.close_connection = True
                return None
            return self.send({"id": identity})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(meta_build, "GRAPH", f"http://127.0.0.1:{server.server_port}")
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def deployment(client, admin, brand, plan, request):
    connection_id = request.getfixturevalue("selected_account")
    admin.execute(
        "UPDATE channel_connection SET external_ad_account_id='12345' WHERE id=%s",
        (connection_id,),
    )
    config = client.app.state.config
    with client.app.state.db.transaction(extra_brand=UUID(brand)) as conn:
        store = CredentialStore(config.credential_master_key_path)
        write_credential(
            conn,
            store,
            UUID(brand),
            "meta",
            "app",
            {"client_id": "fixture", "client_secret": "fixture"},
        )
        write_credential(
            conn, store, UUID(brand), "meta", "token", {"access_token": "simulated-secret"}
        )
    yield {
        "request_key": str(uuid4()),
        "connection_id": str(connection_id),
        "expected_plan_hash": plan["plan_hash"],
        "expected_guardrail_version": 1,
        "settings": {
            "page_id": "5678",
            "destination_url": "https://example.com",
            "countries": ["US"],
            "pixel_id": "9000",
            "end_time": (datetime.now(UTC) + timedelta(days=14)).isoformat(),
        },
    }
    admin.execute(
        "UPDATE campaign_build SET state='cancelled',cancellation_requested=true "
        "WHERE brand_id=%s AND state IN ('queued','running')",
        (brand,),
    )


def tick(client):
    config = client.app.state.config
    CampaignBuildRunner(
        client.app.state.db,
        config,
        ObjectStore(config.object_store_path),
        EventRegistry(config.registry_path),
    ).tick()


def queue(client, brand, plan, deployment):
    response = client.post(f"/api/brands/{brand}/plans/{plan['id']}/deployments", json=deployment)
    assert response.status_code == 202, response.text
    return response.json()


def report(client, brand, plan):
    response = client.get(f"/api/brands/{brand}/plans/{plan['id']}/deployments")
    assert response.status_code == 200, response.text
    return response.json()[0]


def test_paused_build_has_verified_ancestry_and_no_duplicate_writes(
    client, brand, plan, deployment, graph_server, admin
):
    first = queue(client, brand, plan, deployment)
    assert first["state"] == "queued"
    tick(client)
    result = report(client, brand, plan)
    assert result["state"] == "paused", result
    assert len(result["objects"]) == 3
    assert all(item["state"] == "paused" and item["last_verified_at"] for item in result["objects"])
    assert all(item["verified_at"] and item["native_id"] for item in result["steps"])
    assert graph_server["posts"] == ["campaigns", "adsets", "adimages", "adcreatives", "ads"]
    again = queue(client, brand, plan, deployment)
    tick(client)
    assert again["id"] == first["id"]
    assert len(graph_server["posts"]) == 5
    roots = [item for item in result["objects"] if item["level"] == "campaign"]
    group = next(item for item in result["objects"] if item["level"] == "ad_group")
    ad = next(item for item in result["objects"] if item["level"] == "ad")
    assert group["parent_id"] == roots[0]["id"] and ad["parent_id"] == group["id"]
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM action WHERE target_id=%s AND actor_kind='system'",
            (first["id"],),
        ).fetchone()["n"]
        == 1
    )


def test_lost_create_response_is_reconciled_without_repeating_post(
    client, brand, plan, deployment, graph_server
):
    graph_server["lose_campaign_response"] = True
    queue(client, brand, plan, deployment)
    tick(client)
    failed = report(client, brand, plan)
    assert failed["state"] == "failed" and failed["error_code"] == "ProviderStateUncertain"
    retry = client.post(f"/api/brands/{brand}/deployments/{failed['id']}/retry")
    assert retry.status_code == 200, retry.text
    tick(client)
    assert report(client, brand, plan)["state"] == "paused"
    assert graph_server["posts"].count("campaigns") == 1


@pytest.mark.parametrize("fault", ["wrong_budget", "wrong_copy"])
def test_readback_mismatch_never_reports_success(
    client, brand, plan, deployment, graph_server, fault
):
    graph_server[fault] = True
    queue(client, brand, plan, deployment)
    tick(client)
    result = report(client, brand, plan)
    assert result["state"] == "failed"
    assert result["error_code"] == "RemoteVerificationFailed"
    assert result["verified_at"] is None
    assert "ads" not in graph_server["posts"]


def test_cancel_before_worker_prevents_all_remote_writes(
    client, brand, plan, deployment, graph_server
):
    job = queue(client, brand, plan, deployment)
    result = client.post(f"/api/brands/{brand}/deployments/{job['id']}/cancel")
    assert result.status_code == 200
    tick(client)
    assert report(client, brand, plan)["state"] == "cancelled"
    assert graph_server["posts"] == []


def test_database_refuses_changed_scope_and_step_identity(client, brand, plan, deployment, admin):
    job = queue(client, brand, plan, deployment)
    journal = BuildJournal(client.app.state.db, UUID(brand), UUID(job["id"]))
    assert journal.begin("campaign", {"name": "test"})["fresh"]
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute("UPDATE campaign_build_step SET request='{}' WHERE build_id=%s", (job["id"],))
    admin.execute(
        "UPDATE guardrail SET max_new_ads_per_day=max_new_ads_per_day+1 WHERE brand_id=%s", (brand,)
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        journal.begin("ad:test", {"name": "test"})


def test_database_enforces_rolling_creation_limit(client, brand, plan, deployment, admin):
    admin.execute("UPDATE guardrail SET max_new_ads_per_day=1 WHERE brand_id=%s", (brand,))
    deployment["expected_guardrail_version"] = 2
    job = queue(client, brand, plan, deployment)
    journal = BuildJournal(client.app.state.db, UUID(brand), UUID(job["id"]))
    journal.begin("ad:first", {"name": "first"})
    with pytest.raises(psycopg.errors.CheckViolation, match="Rolling ad creation limit"):
        journal.begin("ad:second", {"name": "second"})


def test_worker_shutdown_leaves_job_resumable(client, brand, plan, deployment, graph_server):
    job = queue(client, brand, plan, deployment)
    stop = threading.Event()
    stop.set()
    config = client.app.state.config
    asyncio.run(
        run_build(
            client.app.state.db,
            config,
            ObjectStore(config.object_store_path),
            EventRegistry(config.registry_path),
            UUID(brand),
            UUID(job["id"]),
            stop,
        )
    )
    assert report(client, brand, plan)["state"] == "queued"
    assert graph_server["posts"] == []
    tick(client)
    assert report(client, brand, plan)["state"] == "paused"


def test_tampered_creative_snapshot_is_denied_before_remote_create(
    client, brand, plan, deployment, admin, graph_server
):
    job = queue(client, brand, plan, deployment)
    creative = job["document"]["creatives"][0]["id"]
    admin.execute(
        "UPDATE creative SET copy_fields=jsonb_set(copy_fields,'{meta,headline}',"
        "'\"Changed headline\"') WHERE id=%s",
        (creative,),
    )
    tick(client)
    assert report(client, brand, plan)["state"] == "failed"
    assert graph_server["posts"] == []


def test_false_completion_and_cross_plan_object_insertion_fail_at_database(
    client, brand, plan, deployment, admin
):
    job = queue(client, brand, plan, deployment)
    with pytest.raises(psycopg.errors.CheckViolation, match="fully verified"):
        admin.execute(
            "UPDATE campaign_build SET state='paused',verified_at=now() WHERE id=%s", (job["id"],)
        )
    with pytest.raises(psycopg.errors.CheckViolation, match="execution receipt"):
        admin.execute(
            "INSERT INTO campaign_object(brand_id,connection_id,channel,level,native_id,"
            "plan_id,build_id,idem_key,state) "
            "VALUES(%s,%s,'meta','campaign','fake',%s,%s,%s,'paused')",
            (brand, deployment["connection_id"], plan["id"], job["id"], f"{job['id']}:campaign"),
        )


def test_failed_campaign_readback_keeps_native_identity_for_stop(
    client, brand, plan, deployment, graph_server
):
    graph_server["wrong_campaign_state"] = True
    queue(client, brand, plan, deployment)
    tick(client)
    result = report(client, brand, plan)
    assert result["state"] == "failed"
    assert result["error_code"] == "RemoteVerificationFailed"
    assert len(result["objects"]) == 1
    assert result["objects"][0]["native_id"] == "1000"
    assert result["objects"][0]["state"] == "unknown"
    assert result["objects"][0]["last_verified_at"] is None


def test_provider_authorization_failure_revokes_runtime_access(client, brand, plan, deployment, graph_server, admin):
    job = queue(client, brand, plan, deployment)
    original = admin.execute("SELECT authorization_generation FROM channel_connection WHERE id=%s", (deployment["connection_id"],)).fetchone()["authorization_generation"]
    graph_server["authorization_error"] = True
    tick(client)
    result = report(client, brand, plan)
    assert result["error_code"] == "PlatformAuthorization"
    assert "simulated-secret" not in json.dumps(result)
    assert result["provider_errors"][0]["raw_message"] == "Expired [redacted]"
    connection = admin.execute("SELECT * FROM channel_connection WHERE id=%s", (deployment["connection_id"],)).fetchone()
    assert connection["health"] == "revoked" and not connection["selected"]
    assert connection["verified_at"] is None
    assert connection["authorization_generation"] == original + 1
    assert result["id"] == job["id"]


def test_provider_rejection_text_is_preserved_and_visible_after_retry(client, brand, plan, deployment, graph_server):
    graph_server["reject_creative"] = True
    job = queue(client, brand, plan, deployment)
    tick(client)
    result = report(client, brand, plan)
    assert result["provider_errors"][0]["raw_message"] == "Fixture creative rejected — revise the offer."
    assert result["provider_errors"][0]["raw_code"] == "100"
    assert "ads" not in graph_server["posts"]
    assert client.post(f"/api/brands/{brand}/deployments/{job['id']}/retry").status_code == 200
    tick(client)
    retried = report(client, brand, plan)
    assert retried["provider_errors"] == result["provider_errors"]
    assert retried["error_code"] == "ProviderStateUncertain"
    assert graph_server["posts"].count("adcreatives") == 1
