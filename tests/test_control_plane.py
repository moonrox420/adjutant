from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql

from adjutant.db import Database
from adjutant.errors import DomainError
from adjutant.security import ApprovalSigner, verify_claims


def approve(client, brand, approval):
    return client.post(
        f"/api/brands/{brand}/approvals/{approval['id']}/decide",
        json={"decision": "approved", "expected_hash": approval["subject_hash"]},
    )


def test_authentication_and_csrf(client):
    assert client.get("/api/me").status_code == 200
    assert (
        client.post("/api/auth/logout", headers={"origin": "https://evil.example"}).status_code
        == 403
    )
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/brands").status_code == 401


def test_no_generation_before_confirmation(client, brand, plan_input):
    response = client.post(f"/api/brands/{brand}/plans", json=plan_input)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GraphUnconfirmed"


def test_plan_approval_is_persisted_signed_and_audited(client, confirmed_brand, approval, admin):
    result = approve(client, confirmed_brand, approval)
    assert result.status_code == 200, result.text
    token = admin.execute(
        "SELECT * FROM approval_token WHERE id=%s", (result.json()["token_id"],)
    ).fetchone()
    from pathlib import Path

    signer = ApprovalSigner(Path(".local/approval.key"))
    verify_claims(signer.public_bytes, token["signed_claims"], bytes(token["signature"]))
    claims = dict(token["signed_claims"], usd_daily_cap="999999.00")
    with pytest.raises(DomainError, match="signature"):
        verify_claims(signer.public_bytes, claims, bytes(token["signature"]))
    assert token["signed_claims"]["brand_id"] == confirmed_brand
    events = admin.execute(
        "SELECT envelope FROM event_outbox WHERE brand_id=%s", (confirmed_brand,)
    ).fetchall()
    assert len(events) >= 9
    assert any(e["envelope"]["event_type"] == "approval.token.issued" for e in events)
    assert all("signature" not in str(e) and "nonce" not in str(e) for e in events)
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM action WHERE brand_id=%s", (confirmed_brand,)
        ).fetchone()["n"]
        == 6
    )
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM deployment WHERE brand_id=%s", (confirmed_brand,)
        ).fetchone()["n"]
        == 0
    )


def test_changed_plan_voids_approval(client, confirmed_brand, plan, approval, plan_input, admin):
    assert approve(client, confirmed_brand, approval).status_code == 200
    response = client.put(
        f"/api/brands/{confirmed_brand}/plans/{plan['id']}",
        json={**plan_input, "name": "Revised plan", "expected_hash": plan["plan_hash"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["plan_hash"] != plan["plan_hash"]
    row = admin.execute(
        "SELECT voided_at FROM approval_token WHERE approval_request_id=%s", (approval["id"],)
    ).fetchone()
    assert row["voided_at"] is not None
    assert approve(client, confirmed_brand, approval).status_code == 409


def test_concurrent_approval_only_issues_one_token(client, confirmed_brand, approval, admin):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: approve(client, confirmed_brand, approval), range(2)))
    assert sorted(r.status_code for r in results) == [200, 409]
    count = admin.execute(
        "SELECT count(*) AS n FROM approval_token WHERE approval_request_id=%s", (approval["id"],)
    ).fetchone()["n"]
    assert count == 1


def test_ceiling_rechecked_at_approval(client, confirmed_brand, approval):
    response = client.put(
        f"/api/brands/{confirmed_brand}/ceiling",
        json={"monthly_ceiling": "2000.00", "daily_ceiling": "50.00"},
    )
    assert response.status_code == 200, response.text
    result = approve(client, confirmed_brand, approval)
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "BudgetCeilingExceeded"


def test_seat_caps_fail_closed(client, confirmed_brand, approval, admin, identity):
    admin.execute("UPDATE seat SET approval_daily_usd_cap=1 WHERE user_id=%s", (identity["user"],))
    response = approve(client, confirmed_brand, approval)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SpendAuthorityExceeded"


@pytest.mark.parametrize("role", ["creative", "reviewer", "client_viewer", "client_approver"])
def test_non_spend_roles_cannot_approve(client, confirmed_brand, approval, admin, identity, role):
    admin.execute(
        "UPDATE seat SET brand_id=%s,role=%s WHERE user_id=%s",
        (confirmed_brand, role, identity["user"]),
    )
    assert approve(client, confirmed_brand, approval).status_code == 403


def test_expired_requests_fail_closed(client, confirmed_brand, approval, admin):
    admin.execute(
        "UPDATE approval_request SET expires_at=now()-interval '1 second' WHERE id=%s",
        (approval["id"],),
    )
    assert approve(client, confirmed_brand, approval).json()["error"]["code"] == "ApprovalExpired"


def test_kill_switch_blocks_approval(client, confirmed_brand, approval):
    result = client.post(
        f"/api/brands/{confirmed_brand}/kill", json={"reason": "Unexpected campaign activity"}
    )
    assert result.status_code == 200
    assert result.json()["remote_pause_verified"] is False
    assert approve(client, confirmed_brand, approval).json()["error"]["code"] == "KillSwitchActive"


def test_audit_immutable_even_for_owner(admin, brand):
    for operation in (
        "UPDATE action SET rationale='tampered' WHERE brand_id=%s",
        "DELETE FROM action WHERE brand_id=%s",
    ):
        with pytest.raises(psycopg.Error, match="append-only"):
            admin.execute(operation, (brand,))


def test_cross_tenant_api_and_views(client, brand, admin, database_urls, identity):
    other = uuid4()
    account = uuid4()
    admin.execute(
        "INSERT INTO account(id,account_type,display_name) VALUES(%s,'agency','Other')", (account,)
    )
    admin.execute(
        "INSERT INTO brand(id,account_id,display_name) VALUES(%s,%s,'Other secret brand')",
        (other, account),
    )
    assert client.get(f"/api/brands/{other}/workspace").status_code == 404
    assert {b["id"] for b in client.get("/api/brands").json()} == {brand}
    with psycopg.connect(database_urls[1]) as conn:
        conn.execute("SET LOCAL search_path=adjutant,public")
        conn.execute("SELECT set_config('app.current_brand_ids',%s,true)", (brand,))
        for view in ["brand", "v_approval_queue", "v_agency_portfolio", "v_spend_authority_trail"]:
            rows = conn.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(view))).fetchall()
            assert str(other) not in str(rows)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO brand_graph_assertion(brand_id,field_path,value) VALUES(%s,'x','{}')",
                (other,),
            )


def test_missing_context_and_pool_reuse(database_urls, brand):
    db = Database(database_urls[1])
    db.open()
    try:
        for _ in range(3):
            with db.transaction(extra_brand=UUID(brand)) as conn:
                assert len(conn.execute("SELECT * FROM brand").fetchall()) == 1
            with db.transaction() as conn:
                assert conn.execute("SELECT count(*) AS n FROM brand").fetchone()["n"] == 0
                assert (
                    conn.execute("SELECT count(*) AS n FROM v_agency_portfolio").fetchone()["n"]
                    == 0
                )
    finally:
        db.pool.close()


def test_every_brand_table_and_partition_has_rls(admin):
    unprotected = admin.execute("""SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attname='brand_id'
        WHERE n.nspname='adjutant' AND c.relkind IN ('r','p')
        AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)""").fetchall()
    assert unprotected == []


def test_budget_allocation_exactness(client, confirmed_brand, plan_input):
    plan_input["monthly_budget_usd"] = "3000.01"
    assert client.post(f"/api/brands/{confirmed_brand}/plans", json=plan_input).status_code == 422


def test_token_expiry_and_scope_and_replay_database(client, confirmed_brand, approval, admin):
    result = approve(client, confirmed_brand, approval)
    assert result.status_code == 200, result.text
    token = result.json()["token_id"]
    for channel, op in [("tiktok", "create"), ("meta", "budget_set")]:
        with pytest.raises(psycopg.errors.CheckViolation):
            admin.execute(
                """INSERT INTO approval_token_consumption(token_id,channel,operation,idem_key)
                          VALUES(%s,%s,%s,'invalid')""",
                (token, channel, op),
            )
    admin.execute(
        """INSERT INTO approval_token_consumption(token_id,channel,operation,idem_key)
                     VALUES(%s,'meta','create','first-key')""",
        (token,),
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        admin.execute(
            """INSERT INTO approval_token_consumption(token_id,channel,operation,idem_key)
                         VALUES(%s,'meta','create','different-key')""",
            (token,),
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "UPDATE approval_token SET expires_at=now()+interval '4 days' WHERE id=%s", (token,)
        )


def test_confirmed_assertion_requires_source(admin, brand):
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            """INSERT INTO brand_graph_assertion(brand_id,field_path,value,human_confirmed_at)
                         VALUES(%s,'offer','{}',now())""",
            (brand,),
        )


def test_approval_signatures_expire(client, confirmed_brand, approval, admin):
    from pathlib import Path

    result = approve(client, confirmed_brand, approval)
    token = admin.execute(
        "SELECT * FROM approval_token WHERE id=%s", (result.json()["token_id"],)
    ).fetchone()
    signer = ApprovalSigner(Path(".local/approval.key"))
    with pytest.raises(DomainError, match="validity"):
        verify_claims(
            signer.public_bytes,
            token["signed_claims"],
            bytes(token["signature"]),
            now=datetime.now(UTC) + timedelta(days=4),
        )
