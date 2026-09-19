from pathlib import Path
from urllib.parse import quote

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from adjutant.api import create_app
from adjutant.approval_api import ApprovalSettings
from adjutant.approval_api import create_app as create_approval_app
from adjutant.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_core_starts_without_private_key(database_urls, tmp_path):
    config = Settings(
        database_url=database_urls[1],
        worker_database_url=None,
        signing_key_path=tmp_path / "no-private-key",
    )
    with TestClient(create_app(config)) as api:
        assert api.get("/healthz").status_code == 200


def test_core_cannot_issue_tokens(database_urls):
    with psycopg.connect(database_urls[1], autocommit=True) as conn:
        assert conn.execute(
            "SELECT has_table_privilege(current_user,'adjutant.approval_token','INSERT')"
        ).fetchone() == (False,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO adjutant.approval_token DEFAULT VALUES")


def test_approval_role_cannot_read_secrets_or_change_campaign_content(
    database_urls, approval_server
):
    password = (ROOT / ".local/approval.password").read_text(encoding="utf-8").strip()
    url = f"postgresql://adjutant_approval:{quote(password)}@{database_urls[0].split('@')[1]}"
    with psycopg.connect(url, autocommit=True) as conn:
        for table in ("local_credential", "auth_session", "mail_outbox", "channel_connection"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    psycopg.sql.SQL("SELECT * FROM adjutant.{}").format(
                        psycopg.sql.Identifier(table)
                    )
                )
        for query in (
            "UPDATE adjutant.plan SET plan_document='{}'",
            "UPDATE adjutant.brand SET campaigns_enabled=true",
            "DELETE FROM adjutant.approval_token",
            "UPDATE adjutant.approval_token SET signed_claims='{}'",
            "INSERT INTO adjutant.approval_request DEFAULT VALUES",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(query)


def test_approval_service_authenticates_service_and_live_session(
    client,
    approval_server,
    confirmed_brand,
    approval,
    admin,
):
    path = f"{approval_server}/internal/brands/{confirmed_brand}/approvals/{approval['id']}/decide"
    session = client.cookies.get("adjutant_session")
    body = {
        "session": session,
        "decision": {
            "decision": "approved",
            "expected_hash": approval["subject_hash"],
        },
    }
    secret = (ROOT / ".local/approval-service.secret").read_text(encoding="utf-8").strip()
    headers = {"Authorization": f"Bearer {secret}"}
    with httpx.Client(trust_env=False) as transport:
        assert transport.post(path, json=body).status_code == 401
        invalid = transport.post(path, headers=headers, json={**body, "session": "x" * 48})
        assert invalid.status_code == 401
        malformed = transport.post(path, headers=headers, json={**body, "actor_id": "forged"})
        assert malformed.status_code == 422
        assert session not in malformed.text and secret not in malformed.text
        assert client.post("/api/auth/logout").status_code == 200
        assert transport.post(path, headers=headers, json=body).status_code == 401
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM approval_token WHERE approval_request_id=%s",
            (approval["id"],),
        ).fetchone()["n"]
        == 0
    )


def test_approval_service_rejects_application_database_role(database_urls):
    with (
        pytest.raises(RuntimeError, match="adjutant_approval role"),
        TestClient(create_approval_app(ApprovalSettings(database_url=database_urls[1]))),
    ):
        raise AssertionError("Privileged service started under the wrong identity")


def test_missing_approval_service_fails_closed(client, confirmed_brand, approval, tmp_path, admin):
    client.app.state.config.approval_service_secret_path = tmp_path / "missing-service-secret"
    response = client.post(
        f"/api/brands/{confirmed_brand}/approvals/{approval['id']}/decide",
        json={"decision": "approved", "expected_hash": approval["subject_hash"]},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ApprovalUnavailable"
    row = admin.execute(
        "SELECT state FROM approval_request WHERE id=%s", (approval["id"],)
    ).fetchone()
    assert row["state"] == "pending_internal"


@pytest.mark.parametrize(
    "url", ["http://example.com", "https://user:secret@example.com", "https://example.com/path"]
)
def test_approval_transport_rejects_insecure_or_ambiguous_origins(url):
    with pytest.raises(ValueError, match="APPROVAL_URL"):
        Settings(_env_file=None, database_url="postgresql://unused", approval_url=url)
