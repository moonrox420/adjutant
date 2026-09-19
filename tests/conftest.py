import secrets
import sys
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from adjutant.api import create_app
from adjutant.config import Settings
from adjutant.security import password_hash

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from bootstrap import provision_runtime, provision_worker  # noqa: E402
from migrate import migrate  # noqa: E402
from test_approval_server import approval_test_server  # noqa: E402


@pytest.fixture(scope="session")
def database_urls():
    url_file = ROOT / ".local/test-admin.url"
    if not url_file.exists():
        pytest.fail("Real PostgreSQL is required. Run scripts/bootstrap.py first.")
    admin = url_file.read_text().strip()
    if admin.rsplit("/", 1)[-1] != "adjutant_test":
        pytest.fail("Tests require the dedicated adjutant_test database")
    migrate(admin)
    password = (ROOT / ".local/app.password").read_text().strip()
    with psycopg.connect(admin) as conn:
        provision_runtime(conn, password)
    host = admin.split("@", 1)[1]
    return admin, f"postgresql://adjutant_app:{quote(password)}@{host}"


@pytest.fixture
def admin(database_urls):
    with psycopg.connect(database_urls[0], autocommit=True, row_factory=dict_row) as conn:
        conn.execute("SET search_path=adjutant,public")
        yield conn


@pytest.fixture
def worker_url(database_urls, admin):
    password = (ROOT / ".local/worker.password").read_text(encoding="utf-8").strip()
    provision_worker(admin, password)
    return f"postgresql://adjutant_worker:{quote(password)}@{database_urls[0].split('@')[1]}"


@pytest.fixture
def identity(admin):
    user, account = uuid4(), uuid4()
    email = f"test-{user}@adjutant.test"
    password = secrets.token_urlsafe(20)
    admin.execute(
        "INSERT INTO app_user(id,email,full_name,email_verified_at) "
        "VALUES(%s,%s,'Test owner',now())",
        (user, email),
    )
    admin.execute(
        "INSERT INTO account(id,account_type,display_name) VALUES(%s,'agency','Test agency')",
        (account,),
    )
    admin.execute("INSERT INTO local_credential VALUES(%s,%s)", (user, password_hash(password)))
    admin.execute(
        """INSERT INTO seat(account_id,user_id,role,accepted_at,
                  approval_daily_usd_cap,approval_total_usd_cap)
                  VALUES(%s,%s,'owner',now(),1000,30000)""",
        (account, user),
    )
    return {"user": user, "account": account, "email": email, "password": password}


@pytest.fixture
def client(database_urls, identity, approval_server):
    config = Settings(
        database_url=database_urls[1],
        worker_database_url=None,
        signing_key_path=ROOT / ".local/approval.key",
        approval_url=approval_server,
    )
    with TestClient(create_app(config)) as client:
        client.headers.update({"x-adjutant-client": "console", "origin": "http://localhost:3000"})
        response = client.post(
            "/api/auth/login", json={"email": identity["email"], "password": identity["password"]}
        )
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture(scope="session")
def approval_server(database_urls):
    with approval_test_server(database_urls[0]) as url:
        yield url


@pytest.fixture
def brand(client, identity):
    response = client.post(
        "/api/brands",
        json={
            "account_id": str(identity["account"]),
            "display_name": "Test Plumbing",
            "website_url": "https://example.com",
            "vertical": "home_services",
            "monthly_ceiling": "5000.00",
            "daily_ceiling": "200.00",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture
def confirmed_brand(client, brand):
    response = client.post(
        f"/api/brands/{brand}/assertions",
        json={
            "field_path": "offerings",
            "value": "Emergency plumbing repairs",
            "provenance_uri": "https://example.com/services",
        },
    )
    assert response.status_code == 201, response.text
    response = client.post(f"/api/brands/{brand}/confirm")
    assert response.status_code == 200, response.text
    return brand


@pytest.fixture
def plan_input():
    return {
        "name": "Emergency plumbing leads",
        "objective": "leads",
        "goal_kind": "target_cpa",
        "goal_value": "50.00",
        "monthly_budget_usd": "3000.00",
        "rationale": "Test urgent repair messaging against homeowners seeking prompt service.",
        "audience": "Local homeowners needing emergency plumbing",
        "hypothesis": "Clear availability information increases qualified repair inquiries.",
        "allocations": [
            {"channel": "meta", "monthly_budget_usd": "3000.00", "daily_budget_usd": "100.00"}
        ],
    }


@pytest.fixture
def plan(client, confirmed_brand, plan_input):
    response = client.post(f"/api/brands/{confirmed_brand}/plans", json=plan_input)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def approval(client, confirmed_brand, plan):
    response = client.post(
        f"/api/brands/{confirmed_brand}/plans/{plan['id']}/submit",
        json={"expected_hash": plan["plan_hash"]},
    )
    assert response.status_code == 200, response.text
    return response.json()
