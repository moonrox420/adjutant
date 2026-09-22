"""Dedicated browser-test server; never points at the working Adjutant database."""

import json
import os
from pathlib import Path
from urllib.parse import quote

import psycopg
import uvicorn
from bootstrap import provision_runtime, provision_worker
from browser_inference import MODEL, browser_inference
from browser_studio_fixture import seed_concepts
from live_canary import create_app, seed_identity, test_settings
from migrate import migrate
from pydantic import SecretStr
from test_approval_server import approval_test_server

from adjutant.storage import ObjectStore

root = Path(__file__).resolve().parents[1]
os.chdir(root)
admin_url = Path(".local/test-admin.url").read_text().strip()
if admin_url.rsplit("/", 1)[-1] != "adjutant_test":
    raise RuntimeError("Browser tests require the dedicated test database")
migrate(admin_url)
worker_password = Path(".local/worker.password").read_text().strip()
with psycopg.connect(admin_url) as conn:
    provision_runtime(conn, Path(".local/app.password").read_text().strip())
    provision_worker(conn, worker_password)
identity = seed_identity(Path(".local/test-admin.url").read_text().strip())
Path(".local/browser-user.json").write_text(json.dumps(identity))
generation_identity = seed_identity(admin_url)
Path(".local/browser-studio-user.json").write_text(
    json.dumps(seed_identity(admin_url)), encoding="utf-8"
)
Path(".local/browser-generation-user.json").write_text(
    json.dumps(generation_identity), encoding="utf-8"
)
stop_identity = seed_identity(admin_url)
with psycopg.connect(admin_url) as conn:
    conn.execute("SET search_path=adjutant,public")
    stop_brand = conn.execute(
        "INSERT INTO brand(account_id,display_name,website_url,vertical) "
        "VALUES(%s,'Remote pause test inventory','https://example.com','home_services') "
        "RETURNING id",
        (stop_identity["account_id"],),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO guardrail(brand_id,monthly_spend_cap_usd,daily_spend_cap_usd) "
        "VALUES(%s,3000,100)",
        (stop_brand,),
    )
    for (channel,) in conn.execute("SELECT unnest(enum_range(NULL::channel))::text").fetchall():
        connection = conn.execute(
            "INSERT INTO channel_connection(brand_id,channel,external_ad_account_id,selected) "
            "VALUES(%s,%s,'browser-test-account',true) RETURNING id",
            (stop_brand, channel),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO campaign_object(brand_id,connection_id,channel,level,native_id,state) "
            "VALUES(%s,%s,%s,'campaign','browser-test-campaign','active')",
            (stop_brand, connection, channel),
        )
Path(".local/browser-stop-user.json").write_text(json.dumps(stop_identity), encoding="utf-8")
config = test_settings("http://127.0.0.1:3001")
config.ollama_provider = "local"
config.workflow_enabled = False
config.ollama_cloud_api_key = SecretStr("")
config.ollama_model = MODEL
config.worker_database_url = SecretStr(
    f"postgresql://adjutant_worker:{quote(worker_password)}@{admin_url.split('@')[1]}"
)
config.mail_directory = root / ".local/browser-mail"
concept_identity = seed_identity(admin_url)
concept_identity["brand_id"] = seed_concepts(
    admin_url, concept_identity, ObjectStore(config.object_store_path)
)
Path(".local/browser-concepts-user.json").write_text(json.dumps(concept_identity), encoding="utf-8")
with (
    approval_test_server(admin_url) as approval_url,
    browser_inference(root / ".local/browser-inference.json") as inference_url,
):
    config.approval_url = approval_url
    config.ollama_url = inference_url
    uvicorn.run(create_app(config), host="127.0.0.1", port=8001)
