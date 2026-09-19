import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from bootstrap import provision_gateway, provision_gateway_files
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from adjutant.db import Database
from adjutant.errors import DomainError
from adjutant.gateway import SpendAuthority, SpendIntent
from adjutant.gateway_api import GatewaySettings, create_app
from adjutant.security import ApprovalSigner

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def gateway_url(database_urls):
    password = provision_gateway_files(ROOT / ".local")
    with psycopg.connect(database_urls[0]) as conn:
        provision_gateway(conn, password)
    return f"postgresql://adjutant_gateway:{quote(password)}@{database_urls[0].split('@', 1)[1]}"


@pytest.fixture
def intent(client, confirmed_brand, approval, plan):
    result = client.post(
        f"/api/brands/{confirmed_brand}/approvals/{approval['id']}/decide",
        json={"decision": "approved", "expected_hash": approval["subject_hash"]},
    )
    assert result.status_code == 200, result.text
    return SpendIntent(
        brand_id=confirmed_brand,
        token_id=result.json()["token_id"],
        subject_hash=approval["subject_hash"],
        channel="meta",
        operation="create",
        daily_usd="100.00",
        total_usd="3000.00",
        payload=plan["plan_document"],
    )


def authority():
    signer = ApprovalSigner(ROOT / ".local/approval.key")
    return SpendAuthority({signer.key_id: signer.public_bytes})


def test_gateway_reserves_once_without_signing_or_mutation_privileges(gateway_url, intent):
    config = GatewaySettings(database_url=gateway_url)
    secret = config.service_secret_path.read_text(encoding="utf-8").strip()
    with TestClient(create_app(config)) as client:
        assert (
            client.post("/internal/spend/reserve", json=intent.model_dump(mode="json")).status_code
            == 401
        )
        client.headers["Authorization"] = f"Bearer {secret}"
        assert client.get("/readyz").status_code == 200
        assert (
            client.post("/internal/spend/validate", json=intent.model_dump(mode="json")).status_code
            == 200
        )
        result = client.post("/internal/spend/reserve", json=intent.model_dump(mode="json"))
        assert result.status_code == 200, result.text
        assert result.json()["reservation_id"]
        assert (
            client.post("/internal/spend/reserve", json=intent.model_dump(mode="json")).status_code
            == 409
        )
    with psycopg.connect(gateway_url, autocommit=True) as conn:
        for table in ("local_credential", "auth_session", "mail_outbox"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    psycopg.sql.SQL("SELECT * FROM adjutant.{}").format(
                        psycopg.sql.Identifier(table)
                    )
                )
        for statement in (
            "UPDATE adjutant.approval_token SET voided_at=now()",
            "INSERT INTO adjutant.approval_token DEFAULT VALUES",
            "DELETE FROM adjutant.approval_token_consumption",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


@pytest.mark.parametrize(
    "change,code",
    [
        ({"subject_hash": "0" * 64}, "SubjectChanged"),
        ({"channel": "google_ads"}, "ScopeDenied"),
        ({"operation": "resume"}, "ScopeDenied"),
        ({"daily_usd": "100.01"}, "AllocationExceeded"),
        ({"total_usd": "3000.01"}, "AllocationExceeded"),
        ({"payload": {"name": "Unapproved replacement"}}, "PayloadChanged"),
    ],
)
def test_gateway_negative_authority(gateway_url, intent, change, code):
    altered = SpendIntent.model_validate({**intent.model_dump(), **change})
    db = Database(gateway_url)
    db.open()
    try:
        with (
            pytest.raises(DomainError) as error,
            db.transaction(extra_brand=intent.brand_id) as conn,
        ):
            authority().reserve(conn, altered)
        assert error.value.code == code
    finally:
        db.pool.close()


def test_gateway_cross_brand_and_unknown_key(gateway_url, intent):
    db = Database(gateway_url)
    db.open()
    try:
        other_brand = uuid4()
        with pytest.raises(DomainError) as error, db.transaction(extra_brand=other_brand) as conn:
            authority().validate(conn, intent.model_copy(update={"brand_id": other_brand}))
        assert error.value.code == "NotFound"
    finally:
        db.pool.close()
    db = Database(gateway_url)
    db.open()
    try:
        with (
            pytest.raises(DomainError) as error,
            db.transaction(extra_brand=intent.brand_id) as conn,
        ):
            SpendAuthority({"untrusted": bytes(32)}).validate(conn, intent)
        assert error.value.code == "UnknownSigningKey"
    finally:
        db.pool.close()


def test_concurrent_gateway_reservation_has_one_winner(gateway_url, intent):
    db = Database(gateway_url)
    db.open()

    def reserve():
        try:
            with db.transaction(extra_brand=intent.brand_id) as conn:
                authority().reserve(conn, intent)
            return "reserved"
        except DomainError as exc:
            return exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: reserve(), range(2)))
        assert outcomes.count("reserved") == 1
        assert any(outcome in {"CapExceeded", "ReplayDenied"} for outcome in outcomes)
    finally:
        db.pool.close()


def test_sql_cumulative_cap_is_enforced_across_channels(
    client,
    confirmed_brand,
    plan_input,
    database_urls,
):
    plan_input["allocations"] = [
        {"channel": "meta", "monthly_budget_usd": "1500.00", "daily_budget_usd": "50.00"},
        {"channel": "google_ads", "monthly_budget_usd": "1500.00", "daily_budget_usd": "50.00"},
    ]
    plan = client.post(f"/api/brands/{confirmed_brand}/plans", json=plan_input).json()
    approval = client.post(
        f"/api/brands/{confirmed_brand}/plans/{plan['id']}/submit",
        json={"expected_hash": plan["plan_hash"]},
    ).json()
    result = client.post(
        f"/api/brands/{confirmed_brand}/approvals/{approval['id']}/decide",
        json={"decision": "approved", "expected_hash": plan["plan_hash"]},
    )
    assert result.status_code == 200, result.text

    def consume(channel):
        try:
            with psycopg.connect(database_urls[0], row_factory=dict_row) as conn:
                conn.execute("SET LOCAL search_path=adjutant,public")
                conn.execute(
                    """INSERT INTO approval_token_consumption
                    (token_id,channel,operation,idem_key,usd_committed,usd_daily_committed)
                    VALUES(%s,%s,'create',%s,2000,60)""",
                    (UUID(result.json()["token_id"]), channel, str(uuid4())),
                )
            return "reserved"
        except psycopg.errors.CheckViolation:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(consume, ["meta", "google_ads"])) == ["denied", "reserved"]


def test_preflight_verifies_gateway_without_reserving_or_faking_launch_readiness(
    client,
    gateway_url,
    intent,
    admin,
):
    plan_id = admin.execute(
        "SELECT subject_id FROM approval_token WHERE id=%s", (intent.token_id,)
    ).fetchone()["subject_id"]
    with TestClient(create_app(GatewaySettings(database_url=gateway_url))) as gateway:

        def send(request):
            result = gateway.post(
                request.url.path,
                json=json.loads(request.content),
                headers={"Authorization": request.headers["Authorization"]},
            )
            return httpx.Response(result.status_code, json=result.json())

        transport = httpx.Client(transport=httpx.MockTransport(send))
        with patch("adjutant.deployment.httpx.Client", return_value=transport):
            response = client.post(f"/api/brands/{intent.brand_id}/plans/{plan_id}/preflight")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is False and body["authority_reserved"] is False
    assert any(check["key"] == "spend_authority" and check["passed"] for check in body["checks"])
    assert any(check["key"] == "channel_access" and not check["passed"] for check in body["checks"])
    assert any(
        check["key"] == "creative_approval" and not check["passed"] for check in body["checks"]
    )
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM approval_token_consumption WHERE token_id=%s",
            (intent.token_id,),
        ).fetchone()["n"]
        == 0
    )


def test_preflight_gateway_outage_fails_closed(client, intent, admin):
    plan_id = admin.execute(
        "SELECT subject_id FROM approval_token WHERE id=%s", (intent.token_id,)
    ).fetchone()["subject_id"]
    transport = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, text="upstream failure"))
    )
    with patch("adjutant.deployment.httpx.Client", return_value=transport):
        response = client.post(f"/api/brands/{intent.brand_id}/plans/{plan_id}/preflight")
    assert response.status_code == 200, response.text
    assert response.json()["ready"] is False
    assert any(
        check["key"] == "spend_authority" and not check["passed"]
        for check in response.json()["checks"]
    )


def test_brand_ceiling_combines_concurrent_reservations_from_different_plans(
    client,
    intent,
    plan_input,
    gateway_url,
):
    plan_input["name"] = "Second independently approved campaign"
    plan_response = client.post(f"/api/brands/{intent.brand_id}/plans", json=plan_input)
    assert plan_response.status_code == 201, plan_response.text
    plan = plan_response.json()
    approval = client.post(
        f"/api/brands/{intent.brand_id}/plans/{plan['id']}/submit",
        json={"expected_hash": plan["plan_hash"]},
    ).json()
    decision = client.post(
        f"/api/brands/{intent.brand_id}/approvals/{approval['id']}/decide",
        json={"decision": "approved", "expected_hash": plan["plan_hash"]},
    )
    assert decision.status_code == 200, decision.text
    second = intent.model_copy(
        update={
            "token_id": UUID(decision.json()["token_id"]),
            "subject_hash": plan["plan_hash"],
            "payload": plan["plan_document"],
        }
    )
    db = Database(gateway_url)
    db.open()

    def reserve(candidate):
        try:
            with db.transaction(extra_brand=candidate.brand_id) as conn:
                authority().reserve(conn, candidate)
            return "reserved"
        except DomainError as exc:
            return exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(reserve, [intent, second])) == [
                "BudgetCeilingExceeded",
                "reserved",
            ]
    finally:
        db.pool.close()
