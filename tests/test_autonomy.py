"""Real PostgreSQL and real HTTP between the application, signer, and gateway."""

import io
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import uvicorn
from bootstrap import provision_gateway, provision_gateway_files
from PIL import Image
from psycopg.types.json import Jsonb

from adjutant.approval_client import approval_request
from adjutant.autonomy import consume_launch_authorization
from adjutant.campaign_api import scene_graph
from adjutant.db import Database
from adjutant.errors import DomainError
from adjutant.gateway_api import GatewaySettings, create_app
from adjutant.security import ApprovalSigner, digest
from adjutant.storage import ObjectStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def launch_gateway(database_urls, client):
    password = provision_gateway_files(ROOT / ".local")
    with psycopg.connect(database_urls[0]) as conn:
        provision_gateway(conn, password)
    url = f"postgresql://adjutant_gateway:{quote(password)}@{database_urls[0].split('@', 1)[1]}"
    server = uvicorn.Server(
        uvicorn.Config(create_app(GatewaySettings(database_url=url)), log_level="warning")
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(32)
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("Gateway did not start")
                time.sleep(0.02)
            client.app.state.config.gateway_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            yield url
        finally:
            server.should_exit = True
            thread.join(timeout=20)
            assert not thread.is_alive(), "Gateway did not stop"


@pytest.fixture
def selected_account(admin, brand, plan, client, identity):
    document = {
        "brand_name": "Test Plumbing",
        "destination_url": "https://example.com",
        "meta": {
            "headline": "Plumbing repairs",
            "primary_text": "Local residential plumbing.",
            "description": "Book a service visit.",
            "cta": "Contact us",
            "image_prompt": "Copper pipes",
        },
        "google": {
            "headlines": ["Plumbing repairs", "Local service", "Request a visit"],
            "descriptions": ["Get help with your home's plumbing.", "Contact our team."],
            "destination_path": "repairs",
        },
        "tiktok": {
            "hook": "Need plumbing help?",
            "visual_script": "Show a tap being repaired.",
            "cta": "Contact us",
        },
    }
    buffer = io.BytesIO()
    Image.new("RGB", (128, 128), "#456789").save(buffer, format="PNG")
    store = ObjectStore(client.app.state.config.object_store_path)
    key = store.put(UUID(brand), buffer.getvalue())
    context = admin.execute(
        "INSERT INTO brand_context(brand_id,version,input_hash,source_kind,document) "
        "VALUES(%s,1,%s,'prompt','{}') RETURNING id",
        (brand, digest(document)),
    ).fetchone()["id"]
    draft = admin.execute(
        "INSERT INTO studio_draft(brand_id,context_id,actor_user_id,state,document,scene_graph,"
        "image_key,image_mime,image_model) VALUES(%s,%s,%s,'completed',%s,%s,%s,'image/png',"
        "'local-test-fixture') RETURNING id",
        (brand, context, identity["user"], Jsonb(document), Jsonb(scene_graph(document, key)), key),
    ).fetchone()["id"]
    attached = client.post(
        f"/api/brands/{brand}/studio/{draft}/attach",
        json={"expected_revision": 1, "plan_id": plan["id"]},
    )
    assert attached.status_code == 201, attached.text
    return admin.execute(
        "INSERT INTO channel_connection(brand_id,channel,external_ad_account_id,"
        "external_account_name,selected,health,verified_at) "
        "VALUES(%s,'meta',%s,'Isolated authorization fixture',true,'healthy',now()) RETURNING id",
        (brand, str(uuid4())),
    ).fetchone()["id"]


def review(client, brand, plan):
    scope = client.get(f"/api/brands/{brand}/plans/{plan['id']}/launch-scope")
    assert scope.status_code == 200, scope.text
    return {
        "expected_hash": plan["plan_hash"],
        "expected_review_hash": scope.json()["review_hash"],
        "expected_guardrail_version": scope.json()["guardrails"]["version"],
        "request_key": str(uuid4()),
    }


def test_once_per_account_authority_survives_plan_edits_and_retries(
    client,
    admin,
    brand,
    plan,
    plan_input,
    selected_account,
    launch_gateway,
):
    admin.execute("UPDATE brand SET brand_graph_confirmed_at=NULL WHERE id=%s", (brand,))
    data = review(client, brand, plan)
    path = f"/api/brands/{brand}/plans/{plan['id']}/authorize-launch"
    first = client.post(path, json=data)
    assert first.status_code == 200, first.text
    assert first.json()["channels"][0]["authorized_at"]
    assert client.post(path, json=data).status_code == 200
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM channel_launch_grant WHERE brand_id=%s", (brand,)
        ).fetchone()["n"]
        == 1
    )
    changed = client.put(
        f"/api/brands/{brand}/plans/{plan['id']}",
        json={
            **plan_input,
            "expected_hash": plan["plan_hash"],
            "name": "Later autonomous revision",
        },
    )
    assert changed.status_code == 200, changed.text
    updated = changed.json()
    repeat = client.post(path, json=review(client, brand, updated))
    assert repeat.status_code == 200, repeat.text
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM launch_authorization WHERE brand_id=%s", (brand,)
        ).fetchone()["n"]
        == 1
    )


def test_unconnected_account_cannot_be_authorized(client, brand, plan, launch_gateway):
    response = client.post(
        f"/api/brands/{brand}/plans/{plan['id']}/authorize-launch", json=review(client, brand, plan)
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AccountAuthorizationRequired"


def test_reconnected_account_requires_new_authority_without_erasing_history(
    client, admin, brand, plan, selected_account, launch_gateway
):
    path = f"/api/brands/{brand}/plans/{plan['id']}/authorize-launch"
    first_review = review(client, brand, plan)
    assert client.post(path, json=first_review).status_code == 200
    disconnected = client.delete(f"/api/brands/{brand}/channels/meta/authorization")
    assert disconnected.status_code == 200, disconnected.text
    admin.execute(
        "UPDATE channel_connection SET health='healthy',selected=true,verified_at=now() "
        "WHERE id=%s",
        (selected_account,),
    )
    scope = client.get(f"/api/brands/{brand}/plans/{plan['id']}/launch-scope").json()
    assert scope["channels"][0]["authorized_at"] is None
    assert scope["channels"][0]["authorization_generation"] == 2
    assert scope["review_hash"] != first_review["expected_review_hash"]
    repeated = client.post(path, json=first_review)
    assert (
        repeated.status_code == 409 and repeated.json()["error"]["code"] == "AuthorizationRevoked"
    )
    fresh = client.post(path, json=review(client, brand, plan))
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["channels"][0]["authorized_at"] is not None
    grants = admin.execute(
        "SELECT authorization_generation FROM channel_launch_grant WHERE connection_id=%s "
        "ORDER BY authorization_generation",
        (selected_account,),
    ).fetchall()
    assert [row["authorization_generation"] for row in grants] == [1, 2]
    admin.execute(
        "UPDATE channel_connection SET authorization_generation=1 WHERE id=%s", (selected_account,)
    )
    assert (
        admin.execute(
            "SELECT authorization_generation FROM channel_connection WHERE id=%s",
            (selected_account,),
        ).fetchone()["authorization_generation"]
        == 2
    )


def test_pending_launch_is_permanently_void_after_connection_revocation(
    client, admin, brand, plan, selected_account, launch_gateway
):
    authorization_id = signed_review(client, brand, plan)
    assert client.delete(f"/api/brands/{brand}/channels/meta/authorization").status_code == 200
    admin.execute(
        "UPDATE channel_connection SET health='healthy',selected=true,verified_at=now() "
        "WHERE id=%s",
        (selected_account,),
    )
    assert (
        admin.execute(
            "SELECT reason FROM launch_authorization_void WHERE authorization_id=%s",
            (authorization_id,),
        ).fetchone()["reason"]
        == "connection_authorization_revoked"
    )
    signer = ApprovalSigner(ROOT / ".local/approval.key")
    db = Database(launch_gateway)
    db.open()
    try:
        with (
            pytest.raises(psycopg.errors.CheckViolation),
            db.transaction(extra_brand=UUID(brand)) as conn,
        ):
            consume_launch_authorization(
                conn, {signer.key_id: signer.public_bytes}, UUID(brand), authorization_id
            )
    finally:
        db.pool.close()


def test_account_change_after_review_requires_a_fresh_review(
    client,
    admin,
    brand,
    plan,
    selected_account,
    launch_gateway,
):
    data = review(client, brand, plan)
    admin.execute("UPDATE channel_connection SET selected=false WHERE id=%s", (selected_account,))
    admin.execute(
        "INSERT INTO channel_connection(brand_id,channel,external_ad_account_id,"
        "selected,health,verified_at) VALUES(%s,'meta',%s,true,'healthy',now())",
        (brand, str(uuid4())),
    )
    response = client.post(f"/api/brands/{brand}/plans/{plan['id']}/authorize-launch", json=data)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "ReviewChanged"
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM launch_authorization WHERE brand_id=%s", (brand,)
        ).fetchone()["n"]
        == 0
    )


def test_guardrail_update_is_versioned_and_updates_legacy_caps(client, brand, admin):
    limits = client.get(f"/api/brands/{brand}/guardrails").json()
    data = {k: v for k, v in limits.items() if k not in {"brand_id", "version", "updated_at"}}
    data.update(
        expected_version=limits["version"],
        monthly_spend_cap_usd="4000.00",
        blocked_claims=["Guaranteed results"],
    )
    saved = client.put(f"/api/brands/{brand}/guardrails", json=data)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == limits["version"] + 1
    assert client.put(f"/api/brands/{brand}/guardrails", json=data).status_code == 409
    assert (
        str(
            admin.execute(
                "SELECT monthly_usd_max FROM budget_ceiling "
                "WHERE brand_id=%s AND scope_kind='brand'",
                (brand,),
            ).fetchone()["monthly_usd_max"]
        )
        == "4000.00"
    )
    legacy = client.put(
        f"/api/brands/{brand}/ceiling",
        json={"monthly_ceiling": "4500.00", "daily_ceiling": "150.00"},
    )
    assert legacy.status_code == 200, legacy.text
    assert client.get(f"/api/brands/{brand}/guardrails").json()["version"] == limits["version"] + 2


def signed_review(client, brand, plan):
    data = review(client, brand, plan)
    response = approval_request(
        client.app.state.config,
        f"/internal/brands/{brand}/plans/{plan['id']}/authorize-launch",
        {"session": client.cookies.get("adjutant_session"), "approval": data},
    )
    return UUID(response["authorization_id"])


@pytest.mark.parametrize(
    "changed", ["plan", "guardrail", "kill", "account", "approver", "creative"]
)
def test_database_rejects_stale_unconsumed_scope(
    changed,
    client,
    brand,
    plan,
    selected_account,
    launch_gateway,
    admin,
):
    authorization_id = signed_review(client, brand, plan)
    if changed == "plan":
        admin.execute("UPDATE plan SET plan_hash=%s WHERE id=%s", ("a" * 64, plan["id"]))
    elif changed == "guardrail":
        admin.execute("UPDATE guardrail SET version=version+1 WHERE brand_id=%s", (brand,))
    elif changed == "kill":
        assert (
            client.post(
                f"/api/brands/{brand}/kill", json={"reason": "Stop before first launch"}
            ).status_code
            == 200
        )
    elif changed == "approver":
        admin.execute(
            "UPDATE seat SET revoked_at=now() WHERE user_id=("
            "SELECT approver_id FROM launch_authorization WHERE id=%s)",
            (authorization_id,),
        )
    elif changed == "creative":
        admin.execute(
            "UPDATE creative SET copy_fields=copy_fields || %s WHERE brand_id=%s",
            (Jsonb({"cta": "Changed call to action"}), brand),
        )
    else:
        admin.execute(
            "UPDATE channel_connection SET selected=false WHERE id=%s", (selected_account,)
        )
    signer = ApprovalSigner(ROOT / ".local/approval.key")
    db = Database(launch_gateway)
    db.open()
    try:
        with (
            pytest.raises((psycopg.errors.CheckViolation, DomainError)),
            db.transaction(extra_brand=UUID(brand)) as conn,
        ):
            consume_launch_authorization(
                conn, {signer.key_id: signer.public_bytes}, UUID(brand), authorization_id
            )
    finally:
        db.pool.close()
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM channel_launch_grant WHERE brand_id=%s", (brand,)
        ).fetchone()["n"]
        == 0
    )


def test_concurrent_consumption_rejects_replay_without_duplicate_grants(
    client,
    brand,
    plan,
    selected_account,
    launch_gateway,
):
    authorization_id = signed_review(client, brand, plan)
    signer = ApprovalSigner(ROOT / ".local/approval.key")
    db = Database(launch_gateway)
    db.open()

    def consume():
        try:
            with db.transaction(extra_brand=UUID(brand)) as conn:
                consume_launch_authorization(
                    conn, {signer.key_id: signer.public_bytes}, UUID(brand), authorization_id
                )
            return "consumed"
        except DomainError as exc:
            return exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: consume(), range(2)))
        assert sorted(results) == ["LaunchTokenReplay", "consumed"]
        with (
            pytest.raises(psycopg.errors.InsufficientPrivilege),
            db.transaction(extra_brand=uuid4()) as conn,
        ):
            consume_launch_authorization(
                conn, {signer.key_id: signer.public_bytes}, UUID(brand), authorization_id
            )
    finally:
        db.pool.close()


def test_application_cannot_issue_or_consume_launch_authority(client, database_urls, brand):
    with psycopg.connect(database_urls[1], autocommit=True) as conn:
        for statement in (
            "INSERT INTO adjutant.launch_authorization DEFAULT VALUES",
            "INSERT INTO adjutant.channel_launch_grant DEFAULT VALUES",
            "DELETE FROM adjutant.channel_launch_grant",
            "SELECT adjutant.lock_runner_brand(%s)",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement, (brand,) if "%s" in statement else ())


def test_replayed_token_raises_persisted_security_alert(
    client,
    admin,
    brand,
    plan,
    selected_account,
    launch_gateway,
):
    result = client.post(
        f"/api/brands/{brand}/plans/{plan['id']}/authorize-launch", json=review(client, brand, plan)
    )
    assert result.status_code == 200, result.text
    authorization_id = result.json()["channels"][0]["authorization_id"]
    config = client.app.state.config
    response = httpx.post(
        config.gateway_url
        + f"/internal/brands/{brand}/launch-authorizations/{authorization_id}/consume",
        headers={
            "Authorization": "Bearer " + config.gateway_service_secret_path.read_text().strip()
        },
        timeout=10,
        trust_env=False,
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "LaunchTokenReplay"
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM action WHERE brand_id=%s "
            "AND diff->>'security_alert'='LaunchTokenReplay'",
            (brand,),
        ).fetchone()["n"]
        == 1
    )


def test_plan_change_persistently_voids_unconsumed_review(
    client,
    admin,
    brand,
    plan,
    selected_account,
    launch_gateway,
    plan_input,
):
    authorization_id = signed_review(client, brand, plan)
    edited = client.put(
        f"/api/brands/{brand}/plans/{plan['id']}",
        json={
            **plan_input,
            "expected_hash": plan["plan_hash"],
            "name": "Changed review content",
        },
    )
    assert edited.status_code == 200, edited.text
    assert (
        admin.execute(
            "SELECT reason FROM launch_authorization_void WHERE authorization_id=%s",
            (authorization_id,),
        ).fetchone()["reason"]
        == "plan_changed"
    )


def test_releasing_stop_does_not_restore_unconsumed_launch_token(
    client, admin, brand, plan, selected_account, launch_gateway
):
    authorization_id = signed_review(client, brand, plan)
    assert (
        client.post(
            f"/api/brands/{brand}/kill",
            json={"reason": "Stop before consuming first-launch authority"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/brands/{brand}/resume",
            json={"reason": "Release local stop after reviewing results"},
        ).status_code
        == 200
    )
    assert (
        admin.execute(
            "SELECT reason FROM launch_authorization_void WHERE authorization_id=%s",
            (authorization_id,),
        ).fetchone()["reason"]
        == "kill_switch"
    )
    signer = ApprovalSigner(ROOT / ".local/approval.key")
    db = Database(launch_gateway)
    db.open()
    try:
        with (
            pytest.raises(psycopg.errors.CheckViolation),
            db.transaction(extra_brand=UUID(brand)) as conn,
        ):
            consume_launch_authorization(
                conn, {signer.key_id: signer.public_bytes}, UUID(brand), authorization_id
            )
    finally:
        db.pool.close()


def test_blocked_claim_cannot_bypass_api_by_direct_database_write(admin, brand, selected_account):
    admin.execute(
        "UPDATE guardrail SET blocked_claims=ARRAY['guaranteed results'] WHERE brand_id=%s",
        (brand,),
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "UPDATE studio_draft SET document=jsonb_set(document,'{meta,headline}',"
            "to_jsonb('GUARANTEED   RESULTS'::text)) WHERE brand_id=%s",
            (brand,),
        )


def test_new_blocked_claim_prevents_approval_of_previously_rendered_ad(
    client,
    admin,
    brand,
    plan,
    selected_account,
    launch_gateway,
):
    admin.execute(
        "UPDATE guardrail SET blocked_claims=ARRAY['Plumbing repairs'] WHERE brand_id=%s", (brand,)
    )
    response = client.post(
        f"/api/brands/{brand}/plans/{plan['id']}/authorize-launch", json=review(client, brand, plan)
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "BlockedClaim"


def test_review_asset_preview_is_tenant_scoped_and_checks_content_integrity(
    client,
    admin,
    brand,
    selected_account,
):
    rendition = admin.execute(
        "SELECT png_key,width,height FROM studio_rendition WHERE brand_id=%s LIMIT 1", (brand,)
    ).fetchone()
    store = ObjectStore(client.app.state.config.object_store_path)
    png = store.read(UUID(brand), rendition["png_key"])
    asset = admin.execute(
        "INSERT INTO asset(brand_id,role,storage_uri,content_hash,mime_type,"
        "bytes,width_px,height_px) "
        "VALUES(%s,'rendition',%s,%s,'image/png',%s,%s,%s) "
        "ON CONFLICT(brand_id,content_hash,role) DO UPDATE SET "
        "content_hash=excluded.content_hash RETURNING id",
        (
            brand,
            rendition["png_key"],
            rendition["png_key"],
            len(png),
            rendition["width"],
            rendition["height"],
        ),
    ).fetchone()["id"]
    assert client.get(f"/api/brands/{brand}/assets/{asset}/preview").content == png
    assert client.get(f"/api/brands/{uuid4()}/assets/{asset}/preview").status_code == 404
    admin.execute("UPDATE asset SET storage_uri=%s WHERE id=%s", ("0" * 64, asset))
    response = client.get(f"/api/brands/{brand}/assets/{asset}/preview")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AssetUnavailable"


@pytest.mark.parametrize("changed", ["asset", "placement"])
def test_canonical_asset_and_placement_changes_invalidate_signed_review(
    changed, client, admin, brand, plan, selected_account, launch_gateway
):
    creative = admin.execute(
        "SELECT creative_id FROM studio_plan_creative WHERE plan_id=%s", (plan["id"],)
    ).fetchone()["creative_id"]
    asset = admin.execute(
        "INSERT INTO asset(brand_id,role,storage_uri,content_hash,mime_type) "
        "VALUES(%s,'rendition',%s,%s,'image/png') RETURNING id",
        (brand, "a" * 64, "a" * 64),
    ).fetchone()["id"]
    spec = admin.execute(
        "INSERT INTO placement_spec(channel,placement_key,registry_version,format,"
        "aspect_ratio,min_width_px,min_height_px,retired_at) "
        "VALUES('meta',%s,'test','static_image','1:1',1080,1080,now()) RETURNING id",
        (str(uuid4()),),
    ).fetchone()["id"]
    admin.execute(
        "INSERT INTO rendition(brand_id,creative_id,channel,placement_spec_id,asset_id,"
        "render_cache_key) VALUES(%s,%s,'meta',%s,%s,%s)",
        (brand, creative, spec, asset, "a" * 64),
    )
    authorization_id = signed_review(client, brand, plan)
    if changed == "asset":
        admin.execute("UPDATE asset SET content_hash=%s WHERE id=%s", ("b" * 64, asset))
    else:
        admin.execute("UPDATE placement_spec SET min_width_px=1200 WHERE id=%s", (spec,))
    signer = ApprovalSigner(ROOT / ".local/approval.key")
    db = Database(launch_gateway)
    db.open()
    try:
        with (
            pytest.raises(psycopg.errors.CheckViolation),
            db.transaction(extra_brand=UUID(brand)) as conn,
        ):
            consume_launch_authorization(
                conn, {signer.key_id: signer.public_bytes}, UUID(brand), authorization_id
            )
    finally:
        db.pool.close()


def test_negative_suite_spend_cap_overflow_fails_closed(
    client, admin, brand, plan, selected_account, launch_gateway
):
    """S5.4 & S5.7: Token cannot authorize spend above its cap; cap overflow fails closed."""
    authorization_id = signed_review(client, brand, plan)
    row = admin.execute(
        "SELECT * FROM launch_authorization WHERE id=%s", (authorization_id,)
    ).fetchone()
    signer = ApprovalSigner(ROOT / ".local/approval.key")
    overflow_id = uuid4()
    req_key = uuid4()
    claims = dict(row["claims"])
    claims["id"] = str(overflow_id)
    claims["request_key"] = str(req_key)
    # Token claims daily cap is 1.00 USD, less than plan's daily budget
    claims["usd_daily_cap"] = "1.00"
    overflow_sig = signer.sign(claims)
    admin.execute(
        """INSERT INTO launch_authorization(id,brand_id,plan_id,plan_hash,request_key,
        guardrail_version,approver_id,claims,signature,signing_key_id,issued_at,expires_at)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            overflow_id,
            row["brand_id"],
            row["plan_id"],
            row["plan_hash"],
            req_key,
            row["guardrail_version"],
            row["approver_id"],
            Jsonb(claims),
            overflow_sig,
            signer.key_id,
            row["issued_at"],
            row["expires_at"],
        ),
    )
    db = Database(launch_gateway)
    db.open()
    try:
        with (
            pytest.raises(DomainError) as exc_info,
            db.transaction(extra_brand=UUID(brand)) as conn,
        ):
            consume_launch_authorization(
                conn, {signer.key_id: signer.public_bytes}, UUID(brand), overflow_id
            )
        assert exc_info.value.code == "CapMismatch"
    finally:
        db.pool.close()
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM channel_launch_grant WHERE authorization_id=%s",
            (overflow_id,),
        ).fetchone()["n"]
        == 0
    )


def test_negative_suite_scope_escalation_fails_closed(
    client, admin, brand, plan, selected_account, launch_gateway
):
    """S5.4 & S5.7: Token cannot authorize operations outside its list; escalation fails closed."""
    authorization_id = signed_review(client, brand, plan)
    row = admin.execute(
        "SELECT * FROM launch_authorization WHERE id=%s", (authorization_id,)
    ).fetchone()
    signer = ApprovalSigner(ROOT / ".local/approval.key")
    escalated_id = uuid4()
    req_key = uuid4()
    claims = dict(row["claims"])
    claims["id"] = str(escalated_id)
    claims["request_key"] = str(req_key)
    claims["operations"] = ["activate", "escalate_admin"]
    escalated_sig = signer.sign(claims)
    admin.execute(
        """INSERT INTO launch_authorization(id,brand_id,plan_id,plan_hash,request_key,
        guardrail_version,approver_id,claims,signature,signing_key_id,issued_at,expires_at)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            escalated_id,
            row["brand_id"],
            row["plan_id"],
            row["plan_hash"],
            req_key,
            row["guardrail_version"],
            row["approver_id"],
            Jsonb(claims),
            escalated_sig,
            signer.key_id,
            row["issued_at"],
            row["expires_at"],
        ),
    )
    db = Database(launch_gateway)
    db.open()
    try:
        with (
            pytest.raises(DomainError) as exc_info,
            db.transaction(extra_brand=UUID(brand)) as conn,
        ):
            consume_launch_authorization(
                conn, {signer.key_id: signer.public_bytes}, UUID(brand), escalated_id
            )
        assert exc_info.value.code == "InvalidScope"
    finally:
        db.pool.close()
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM channel_launch_grant WHERE authorization_id=%s",
            (escalated_id,),
        ).fetchone()["n"]
        == 0
    )


def test_negative_suite_expired_token_fails_closed(
    client, admin, brand, plan, selected_account, launch_gateway
):
    """S5.7: Expired token fails closed and is rejected."""
    authorization_id = signed_review(client, brand, plan)
    row = admin.execute(
        "SELECT * FROM launch_authorization WHERE id=%s", (authorization_id,)
    ).fetchone()
    signer = ApprovalSigner(ROOT / ".local/approval.key")
    expired_id = uuid4()
    req_key = uuid4()
    now = datetime.now(UTC).replace(microsecond=0)
    issued_at = now - timedelta(hours=5)
    expires_at = now - timedelta(hours=1)
    claims = dict(row["claims"])
    claims["id"] = str(expired_id)
    claims["request_key"] = str(req_key)
    claims["iat"] = int(issued_at.timestamp())
    claims["exp"] = int(expires_at.timestamp())
    expired_sig = signer.sign(claims)
    admin.execute(
        """INSERT INTO launch_authorization(id,brand_id,plan_id,plan_hash,request_key,
        guardrail_version,approver_id,claims,signature,signing_key_id,issued_at,expires_at)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            expired_id,
            row["brand_id"],
            row["plan_id"],
            row["plan_hash"],
            req_key,
            row["guardrail_version"],
            row["approver_id"],
            Jsonb(claims),
            expired_sig,
            signer.key_id,
            issued_at,
            expires_at,
        ),
    )
    db = Database(launch_gateway)
    db.open()
    try:
        with (
            pytest.raises((DomainError, psycopg.errors.CheckViolation)),
            db.transaction(extra_brand=UUID(brand)) as conn,
        ):
            consume_launch_authorization(
                conn, {signer.key_id: signer.public_bytes}, UUID(brand), expired_id
            )
    finally:
        db.pool.close()
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM channel_launch_grant WHERE authorization_id=%s",
            (expired_id,),
        ).fetchone()["n"]
        == 0
    )


def test_negative_suite_cross_brand_token_fails_closed(
    client, admin, brand, plan, selected_account, launch_gateway, identity
):
    """S5.7: Token issued for brand A cannot be consumed by brand B (cross-tenant fail closed)."""
    authorization_id = signed_review(client, brand, plan)
    res_b = client.post(
        "/api/brands",
        json={
            "account_id": str(identity["account"]),
            "display_name": "Second Tenant Plumbing",
            "website_url": "https://brandb.example.com",
            "vertical": "home_services",
            "monthly_ceiling": "5000.00",
            "daily_ceiling": "200.00",
        },
    )
    assert res_b.status_code == 201, res_b.text
    other_brand = UUID(res_b.json()["id"])
    signer = ApprovalSigner(ROOT / ".local/approval.key")
    db = Database(launch_gateway)
    db.open()
    try:
        with (
            pytest.raises((DomainError, psycopg.errors.InsufficientPrivilege)),
            db.transaction(extra_brand=other_brand) as conn,
        ):
            consume_launch_authorization(
                conn, {signer.key_id: signer.public_bytes}, other_brand, authorization_id
            )
    finally:
        db.pool.close()
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM channel_launch_grant WHERE authorization_id=%s",
            (authorization_id,),
        ).fetchone()["n"]
        == 0
    )


def test_negative_suite_token_store_outage_fails_closed(
    client, brand, plan, selected_account
):
    """S5.6 & S5.7: When the token store/gateway is unreachable, preflight/launch denies rather than permits."""
    # Point gateway_url to an unreachable address
    client.app.state.config.gateway_url = "http://127.0.0.1:59999"
    preflight_res = client.post(f"/api/brands/{brand}/plans/{plan['id']}/preflight")
    # Gateway outage must fail closed
    assert preflight_res.status_code == 200
    data = preflight_res.json()
    assert data["ready"] is False
    # Also verify direct launch authorization call fails closed
    auth_res = client.post(
        f"/api/brands/{brand}/plans/{plan['id']}/authorize-launch",
        json=review(client, brand, plan),
    )
    assert auth_res.status_code == 503
    assert auth_res.json()["error"]["code"] == "GatewayUnavailable"

