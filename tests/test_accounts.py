import re
from uuid import uuid4

import psycopg
import pytest


def account_data():
    return {
        "email": f"account-{uuid4()}@example.com",
        "password": " a very long exact passphrase ",
        "confirm_password": " a very long exact passphrase ",
        "full_name": "New owner",
        "workspace_name": "New consumer workspace",
        "account_type": "business",
    }


def mail_token(admin, email, purpose):
    row = admin.execute(
        "SELECT body FROM mail_outbox WHERE recipient=%s ORDER BY created_at DESC LIMIT 1",
        (email,),
    ).fetchone()
    return re.search(rf"#{purpose}=([A-Za-z0-9_-]+)", row["body"])[1]


@pytest.mark.parametrize("account_type", ["business", "agency"])
def test_signup_account_type_is_persisted_and_duplicate_does_not_change_it(
    client,
    admin,
    account_type,
):
    data = {**account_data(), "account_type": account_type}
    assert client.post("/api/auth/register", json=data).status_code == 202
    assert (
        client.post(
            "/api/auth/register",
            json={
                **data,
                "account_type": "agency" if account_type == "business" else "business",
            },
        ).status_code
        == 202
    )
    token = mail_token(admin, data["email"], "verify")
    assert client.post("/api/auth/verify", json={"token": token}).status_code == 200
    assert (
        client.post(
            "/api/auth/login",
            json={
                "email": data["email"],
                "password": data["password"],
            },
        ).status_code
        == 200
    )
    account = client.get("/api/me").json()["accounts"][0]
    assert account["account_type"] == account_type
    new_brand = {
        "account_id": account["id"],
        "display_name": "First test brand",
        "website_url": "https://example.com",
        "vertical": "home_services",
        "monthly_ceiling": "3000",
        "daily_ceiling": "100",
    }
    assert client.post("/api/brands", json=new_brand).status_code == 201
    second = client.post(
        "/api/brands", json={**new_brand, "display_name": "Second test brand"}
    )
    assert second.status_code == (201 if account_type == "agency" else 409)


def test_signup_rejects_unrecognized_account_type(client):
    assert (
        client.post(
            "/api/auth/register",
            json={
                **account_data(),
                "account_type": "administrator",
            },
        ).status_code
        == 422
    )


def test_signup_requires_explicit_account_type(client):
    data = account_data()
    del data["account_type"]
    response = client.post("/api/auth/register", json=data)
    assert response.status_code == 422
    assert "account type: Field required" in response.json()["error"]["message"]


@pytest.fixture(autouse=True)
def reset_signup_rate(admin):
    # Every test uses the same TestClient source address in the isolated test database.
    admin.execute("DELETE FROM login_attempt")


def test_account_lifecycle_exact_password_single_use_and_session_recovery(
    client, admin
):
    data = account_data()
    response = client.post("/api/auth/register", json=data)
    assert response.status_code == 202, response.text
    assert response.json()["delivery"] == "file"
    login = {"email": data["email"], "password": data["password"]}
    assert client.post("/api/auth/login", json=login).status_code == 401
    token = mail_token(admin, data["email"], "verify")
    assert client.post("/api/auth/verify", json={"token": token}).status_code == 200
    assert client.post("/api/auth/verify", json={"token": token}).status_code == 400
    assert (
        client.post(
            "/api/auth/login", json={**login, "password": data["password"].strip()}
        ).status_code
        == 401
    )
    assert client.post("/api/auth/login", json=login).status_code == 200
    first_cookie = client.cookies.get("adjutant_session")
    me = client.get("/api/me").json()
    assert me["accounts"][0]["display_name"] == data["workspace_name"]
    assert len(me["accounts"]) == 1
    assert all(seat["role"] == "owner" for seat in me["seats"])
    assert client.get("/api/brands").json() == []
    assert client.post("/api/auth/login", json=login).status_code == 200
    second_cookie = client.cookies.get("adjutant_session")
    assert first_cookie != second_cookie
    assert (
        client.post(
            "/api/auth/request-link?purpose=reset", json={"email": data["email"]}
        ).status_code
        == 202
    )
    reset = mail_token(admin, data["email"], "reset")
    new_password = "new long recovery passphrase"
    body = {"token": reset, "password": new_password, "confirm_password": new_password}
    assert client.post("/api/auth/reset-password", json=body).status_code == 200
    for cookie in (first_cookie, second_cookie):
        assert (
            client.get(
                "/api/me", headers={"cookie": f"adjutant_session={cookie}"}
            ).status_code
            == 401
        )
    assert client.post("/api/auth/reset-password", json=body).status_code == 400
    assert client.post("/api/auth/login", json=login).status_code == 401
    assert (
        client.post(
            "/api/auth/login", json={**login, "password": new_password}
        ).status_code
        == 200
    )
    signed_in = client.cookies.get("adjutant_session")
    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200 and logout.json()["cancellation_verified"]
    assert (
        "httponly"
        in client.post("/api/auth/login", json={**login, "password": new_password})
        .headers["set-cookie"]
        .lower()
    )
    assert (
        client.get(
            "/api/me", headers={"cookie": f"adjutant_session={signed_in}"}
        ).status_code
        == 401
    )


def test_expiry_resend_duplicate_registration_and_enumeration(client, admin):
    data = account_data()
    created = client.post("/api/auth/register", json=data)
    first = mail_token(admin, data["email"], "verify")
    assert client.post("/api/auth/register", json=data).json() == created.json()
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM app_user WHERE email=%s", (data["email"],)
        ).fetchone()["n"]
        == 1
    )
    assert (
        client.post(
            "/api/auth/request-link?purpose=verify", json={"email": data["email"]}
        ).status_code
        == 202
    )
    second = mail_token(admin, data["email"], "verify")
    assert second != first
    assert client.post("/api/auth/verify", json={"token": first}).status_code == 400
    admin.execute(
        "UPDATE account_token SET expires_at=now()-interval '1 second' "
        "WHERE user_id=(SELECT id FROM app_user WHERE email=%s)",
        (data["email"],),
    )
    assert client.post("/api/auth/verify", json={"token": second}).status_code == 400
    unknown = client.post(
        "/api/auth/request-link?purpose=reset",
        json={"email": f"unknown-{uuid4()}@example.com"},
    )
    known = client.post(
        "/api/auth/request-link?purpose=reset", json={"email": data["email"]}
    )
    assert unknown.json() == known.json()


@pytest.mark.parametrize(
    "changes",
    [
        {"email": "invalid"},
        {"workspace_name": "   "},
        {"full_name": ""},
        {"password": "short", "confirm_password": "short"},
        {"confirm_password": "does not match this password"},
        {"role": "admin"},
        {"email_verified_at": "2026-01-01"},
    ],
)
def test_registration_validation_rejects_untrusted_fields(client, changes):
    assert (
        client.post(
            "/api/auth/register", json={**account_data(), **changes}
        ).status_code
        == 422
    )


def test_registration_rate_limit_is_persistent(client):
    data = account_data()
    for _ in range(10):
        assert (
            client.post(
                "/api/auth/request-link?purpose=verify", json={"email": data["email"]}
            ).status_code
            == 202
        )
    assert (
        client.post(
            "/api/auth/request-link?purpose=verify", json={"email": data["email"]}
        ).status_code
        == 429
    )


def test_private_auth_and_worker_tables_are_not_readable(database_urls):
    with psycopg.connect(database_urls[1], autocommit=True) as conn:
        for table in (
            "local_credential",
            "auth_session",
            "account_token",
            "mail_outbox",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f"SELECT * FROM adjutant.{table}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT adjutant.consume_activity_batch(1)")


def test_logout_all_revokes_every_cookie(client, identity):
    first = client.cookies.get("adjutant_session")
    assert (
        client.post(
            "/api/auth/login",
            json={"email": identity["email"], "password": identity["password"]},
        ).status_code
        == 200
    )
    second = client.cookies.get("adjutant_session")
    assert client.post("/api/auth/logout-all").status_code == 200
    for cookie in (first, second):
        assert (
            client.get(
                "/api/me", headers={"cookie": f"adjutant_session={cookie}"}
            ).status_code
            == 401
        )
