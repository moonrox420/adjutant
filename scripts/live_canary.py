"""Run real local inference through the API using only the dedicated test database."""

import json
import secrets
import sys
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_approval_server import approval_test_server

from adjutant.api import create_app
from adjutant.config import Settings
from adjutant.credentials import provision_master_key
from adjutant.security import password_hash


def seed_identity(admin_url: str) -> dict[str, str]:
    if admin_url.rsplit("/", 1)[-1] != "adjutant_test":
        raise RuntimeError("Canary must use the dedicated test database")
    email, password = f"canary-{uuid4()}@adjutant.test", secrets.token_urlsafe(24)
    with psycopg.connect(admin_url) as conn:
        conn.execute("SET search_path=adjutant,public")
        user = conn.execute(
            "INSERT INTO app_user(email,full_name,email_verified_at) "
            "VALUES(%s,'Test operator',now()) RETURNING id",
            (email,),
        ).fetchone()[0]
        account = conn.execute("""INSERT INTO account(account_type,display_name)
                                  VALUES('agency','Canary workspace') RETURNING id""").fetchone()[0]
        conn.execute("INSERT INTO local_credential VALUES(%s,%s)", (user, password_hash(password)))
        conn.execute(
            """INSERT INTO seat(account_id,user_id,role,accepted_at,
                     approval_daily_usd_cap,approval_total_usd_cap)
                     VALUES(%s,%s,'owner',now(),1000,30000)""",
            (account, user),
        )
    return {"email": email, "password": password, "account_id": str(account)}


def test_settings(origin: str = "http://localhost:3000") -> Settings:
    admin_url = Path(".local/test-admin.url").read_text().strip()
    password = Path(".local/app.password").read_text().strip()
    key_path = Path(".local/browser-tenant-master.key")
    provision_master_key(key_path)
    return Settings(
        database_url=f"postgresql://adjutant_app:{quote(password)}@{admin_url.split('@')[1]}",
        public_origin=origin,
        worker_database_url=None,
        credential_master_key_path=key_path,
    )


def run(model: str) -> None:
    identity = seed_identity(Path(".local/test-admin.url").read_text().strip())
    with approval_test_server(Path(".local/test-admin.url").read_text().strip()) as approval_url:
        config = test_settings()
        config.approval_url = approval_url
        run_workflow(identity, config, model)


def run_workflow(identity: dict[str, str], config: Settings, model: str) -> None:
    """Exercise the application with the dedicated approval service available."""
    with TestClient(create_app(config)) as client:
        client.headers.update({"x-adjutant-client": "console"})
        response = client.post(
            "/api/auth/login", json={"email": identity["email"], "password": identity["password"]}
        )
        response.raise_for_status()
        response = client.post(
            "/api/brands",
            json={
                "account_id": identity["account_id"],
                "display_name": "Canary Plumbing",
                "website_url": "https://example.com",
                "vertical": "home_services",
                "monthly_ceiling": "3000.00",
                "daily_ceiling": "100.00",
            },
        )
        response.raise_for_status()
        brand = response.json()["id"]
        response = client.post(
            f"/api/brands/{brand}/assertions",
            json={
                "field_path": "offerings",
                "value": "Residential plumbing repairs. Audience: local homeowners. "
                "No pricing or response-time promises are substantiated.",
                "provenance_uri": "https://example.com/services",
            },
        )
        response.raise_for_status()
        client.post(f"/api/brands/{brand}/confirm").raise_for_status()
        response = client.post(
            f"/api/brands/{brand}/generate-plan",
            json={
                "model": model,
                "brief": "Create a Meta-only leads campaign for local plumbing repairs. "
                "Use a 3000.00 monthly allocation and 100.00 daily budget. Target CPA 50.00. "
                "Do not invent claims or prices. State an honest testable hypothesis.",
            },
        )
        if response.status_code != 201:
            raise RuntimeError(f"Live generation failed: {response.status_code} {response.text}")
        plan = response.json()
        submission = client.post(
            f"/api/brands/{brand}/plans/{plan['id']}/submit",
            json={"expected_hash": plan["plan_hash"]},
        )
        submission.raise_for_status()
        result = {
            "model": model,
            "brand_id": brand,
            "plan_id": plan["id"],
            "plan_hash": plan["plan_hash"],
            "plan_name": plan["name"],
            "state": "pending_approval",
            "monthly_budget_usd": plan["monthly_budget_usd"],
            "live_campaigns_created": 0,
        }
        Path(".local/live-canary.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    run(args.model)
