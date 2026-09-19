"""Dedicated browser-test server; never points at the working Adjutant database."""

import json
import os
from pathlib import Path
from urllib.parse import quote

import psycopg
import uvicorn
from bootstrap import provision_runtime, provision_worker
from browser_inference import MODEL, browser_inference
from live_canary import create_app, seed_identity, test_settings
from migrate import migrate
from pydantic import SecretStr
from test_approval_server import approval_test_server

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
Path(".local/browser-generation-user.json").write_text(
    json.dumps(generation_identity), encoding="utf-8"
)
config = test_settings("http://127.0.0.1:3001")
config.ollama_provider = "local"
config.ollama_cloud_api_key = SecretStr("")
config.ollama_model = MODEL
config.worker_database_url = SecretStr(
    f"postgresql://adjutant_worker:{quote(worker_password)}@{admin_url.split('@')[1]}"
)
config.mail_directory = root / ".local/browser-mail"
with (
    approval_test_server(admin_url) as approval_url,
    browser_inference(root / ".local/browser-inference.json") as inference_url,
):
    config.approval_url = approval_url
    config.ollama_url = inference_url
    uvicorn.run(create_app(config), host="127.0.0.1", port=8001)
