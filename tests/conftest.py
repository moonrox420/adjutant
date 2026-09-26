import io
import secrets
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

import psycopg
import pytest
import uvicorn
from fastapi.testclient import TestClient
from PIL import Image
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from adjutant.api import create_app
from adjutant.campaign_api import scene_graph
from adjutant.config import Settings
from adjutant.credentials import provision_master_key
from adjutant.gateway_api import GatewaySettings
from adjutant.gateway_api import create_app as create_gateway_app
from adjutant.security import digest, password_hash
from adjutant.storage import ObjectStore

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from bootstrap import (  # noqa: E402
    ensure_cluster_roles,
    provision_approval,
    provision_gateway,
    provision_gateway_files,
    provision_runtime,
    provision_worker,
)
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
    app_pwd = (ROOT / ".local/app.password").read_text().strip()
    worker_pwd = (ROOT / ".local/worker.password").read_text().strip()
    gw_pwd = (
        (ROOT / ".local/gateway.password").read_text().strip()
        if (ROOT / ".local/gateway.password").exists()
        else "test-gw-password"
    )
    appr_pwd = (
        (ROOT / ".local/approval.password").read_text().strip()
        if (ROOT / ".local/approval.password").exists()
        else "test-approval-password"
    )
    with psycopg.connect(admin, autocommit=True) as conn:
        ensure_cluster_roles(
            conn,
            {
                "adjutant_app": app_pwd,
                "adjutant_worker": worker_pwd,
                "adjutant_gateway": gw_pwd,
                "adjutant_approval": appr_pwd,
            },
        )
    migrate(admin)
    with psycopg.connect(admin) as conn:
        provision_runtime(conn, app_pwd)
        provision_worker(conn, worker_pwd)
        provision_gateway(conn, gw_pwd)
        provision_approval(conn, appr_pwd)
    host = admin.split("@", 1)[1]
    return admin, f"postgresql://adjutant_app:{quote(app_pwd)}@{host}"


@pytest.fixture
def admin(database_urls):
    with psycopg.connect(
        database_urls[0], autocommit=True, row_factory=dict_row
    ) as conn:
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
    admin.execute(
        "INSERT INTO local_credential VALUES(%s,%s)", (user, password_hash(password))
    )
    admin.execute(
        """INSERT INTO seat(account_id,user_id,role,accepted_at,
                  approval_daily_usd_cap,approval_total_usd_cap)
                  VALUES(%s,%s,'owner',now(),1000,30000)""",
        (account, user),
    )
    return {"user": user, "account": account, "email": email, "password": password}


@pytest.fixture
def client(database_urls, identity, approval_server, tmp_path):
    provision_master_key(tmp_path / "tenant-master.key")
    config = Settings(
        database_url=database_urls[1],
        worker_database_url=None,
        workflow_enabled=False,
        credential_master_key_path=tmp_path / "tenant-master.key",
        object_store_path=tmp_path / "objects",
        signing_key_path=ROOT / ".local/approval.key",
        approval_url=approval_server,
    )
    with TestClient(create_app(config)) as client:
        client.headers.update(
            {"x-adjutant-client": "console", "origin": "http://localhost:3000"}
        )
        response = client.post(
            "/api/auth/login",
            json={"email": identity["email"], "password": identity["password"]},
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
            {
                "channel": "meta",
                "monthly_budget_usd": "3000.00",
                "daily_budget_usd": "100.00",
            }
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


@pytest.fixture
def launch_gateway(database_urls, client):
    password = provision_gateway_files(ROOT / ".local")
    with psycopg.connect(database_urls[0]) as conn:
        provision_gateway(conn, password)
    url = f"postgresql://adjutant_gateway:{quote(password)}@{database_urls[0].split('@', 1)[1]}"
    server = uvicorn.Server(
        uvicorn.Config(
            create_gateway_app(GatewaySettings(database_url=url)), log_level="warning"
        )
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(32)
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("Gateway did not start")
                time.sleep(0.02)
            client.app.state.config.gateway_url = (
                f"http://127.0.0.1:{listener.getsockname()[1]}"
            )
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
            "descriptions": [
                "Get help with your home's plumbing.",
                "Contact our team.",
            ],
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
        (
            brand,
            context,
            identity["user"],
            Jsonb(document),
            Jsonb(scene_graph(document, key)),
            key,
        ),
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
