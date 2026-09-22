"""S0 acceptance and failure-mode tests using real PostgreSQL and OS processes."""

import io
import json
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from cryptography.exceptions import InvalidTag
from psycopg import sql
from test_process_lifecycle import wait_for

from adjutant.credentials import CredentialStore, provision_master_key
from adjutant.db import Database
from adjutant.processes import child_environment, terminate_owned
from adjutant.storage import ObjectStore
from adjutant.telemetry import JsonFormatter
from adjutant.workflows import WorkflowRunner, advance

ROOT = Path(__file__).resolve().parents[1]


def start(client, brand, delay=0, request_key=None):
    response = client.post(
        f"/api/brands/{brand}/workflows",
        json={"request_key": request_key or str(uuid4()), "delay_seconds": delay},
    )
    assert response.status_code == 202, response.text
    return response.json()


def spawn_worker(database_urls, brand, workflow, tmp_path, stage):
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "tests/workflow_crash_helper.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        env=child_environment(),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    config = {
        "url": database_urls[1],
        "brand_id": brand,
        "workflow_id": workflow["id"],
        "marker": str(tmp_path / f"{stage}.marker"),
        "storage": str(tmp_path / "objects"),
        "stage": stage,
    }
    process.stdin.write((json.dumps(config) + "\n").encode())
    process.stdin.flush()
    return process, Path(config["marker"])


@pytest.mark.parametrize("stage", ["waiting", "before_commit"])
def test_workflow_survives_real_worker_kill(client, brand, database_urls, admin, tmp_path, stage):
    workflow = start(client, brand, delay=1)
    process, marker = spawn_worker(database_urls, brand, workflow, tmp_path, stage)
    try:
        wait_for(marker.exists)
        assert process.poll() is None
        assert (
            admin.execute(
                "SELECT state FROM workflow_run WHERE id=%s", (workflow["id"],)
            ).fetchone()["state"]
            == "waiting"
        )
        process.kill()
        assert process.wait(timeout=5) != 0
    finally:
        terminate_owned(process)
        process.stdin.close()
    replacement, completed = spawn_worker(database_urls, brand, workflow, tmp_path, "complete")
    try:
        assert replacement.wait(timeout=15) == 0
        assert completed.exists()
    finally:
        terminate_owned(replacement)
        replacement.stdin.close()
    result = client.get(f"/api/brands/{brand}/workflows/{workflow['id']}/result")
    assert result.status_code == 200, result.text
    assert result.json() == {
        "brand_id": brand,
        "workflow_id": workflow["id"],
        "status": "completed",
    }
    checkpoints = admin.execute(
        "SELECT step FROM workflow_checkpoint WHERE workflow_id=%s ORDER BY step", (workflow["id"],)
    ).fetchall()
    assert [row["step"] for row in checkpoints] == [1, 2]
    assert len(list((tmp_path / "objects" / brand).iterdir())) == 1


def test_parallel_workers_and_http_retries_commit_once(client, brand, database_urls, tmp_path):
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as pool:
        workflows = list(pool.map(lambda _: start(client, brand, request_key=key), range(4)))
    assert len({item["id"] for item in workflows}) == 1
    workflow = workflows[0]
    assert (
        client.post(
            f"/api/brands/{brand}/workflows", json={"request_key": key, "delay_seconds": 3}
        ).status_code
        == 409
    )
    db = Database(database_urls[1])
    db.open()
    try:
        store = ObjectStore(tmp_path / "objects")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(lambda _: advance(db, store, UUID(brand), UUID(workflow["id"])), range(12))
            )
        assert sum(results) == 2
    finally:
        db.pool.close()
    assert (
        client.get(f"/api/brands/{brand}/workflows/{workflow['id']}").json()["state"] == "completed"
    )


def test_background_runner_picks_up_pending_work(client, brand, database_urls, tmp_path):
    workflow = start(client, brand)
    db = Database(database_urls[1])
    db.open()
    runner = WorkflowRunner(db, ObjectStore(tmp_path / "objects"))
    runner.start()
    try:
        wait_for(
            lambda: (
                client.get(f"/api/brands/{brand}/workflows/{workflow['id']}").json()["state"]
                == "completed"
            )
        )
    finally:
        runner.close()
        db.pool.close()
    assert not runner.alive


def test_credentials_ciphertext_and_log_output(client, brand, admin, tmp_path):
    secret = "plaintext-canary-" + str(uuid4())
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("adjutant")
    logger.addHandler(handler)
    try:
        response = client.put(f"/api/brands/{brand}/credentials/provider", json={"value": secret})
        assert response.status_code == 204, response.text
        invalid = client.put(
            f"/api/brands/{brand}/credentials/provider", json={"value": {"secret": secret}}
        )
        assert invalid.status_code == 422
        assert secret not in invalid.text
        store = CredentialStore(tmp_path / "tenant-master.key")
        assert store.read(admin, UUID(brand), "provider") == secret
        row = admin.execute(
            "SELECT s.ciphertext,k.wrapped_key FROM tenant_secret s JOIN tenant_secret_key k "
            "USING(brand_id) WHERE s.brand_id=%s",
            (brand,),
        ).fetchone()
        assert secret.encode() not in bytes(row["ciphertext"]) + bytes(row["wrapped_key"])
        assert secret not in output.getvalue()
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        assert any(item["trace_id"] == response.headers["x-request-id"] for item in records)
        assert all(len(item["trace_id"]) == 32 for item in records)
        assert secret not in response.text
    finally:
        logger.removeHandler(handler)


def test_ciphertext_cannot_move_between_brands_or_names(client, brand, admin, tmp_path, identity):
    other = admin.execute(
        "INSERT INTO brand(account_id,display_name) VALUES(%s,'Other brand') RETURNING id",
        (identity["account"],),
    ).fetchone()["id"]
    store = CredentialStore(tmp_path / "tenant-master.key")
    store.write(admin, UUID(brand), "provider", "first-secret")
    store.write(admin, other, "provider", "second-secret")
    keys = admin.execute(
        "SELECT wrapped_key FROM tenant_secret_key WHERE brand_id=ANY(%s)",
        ([UUID(brand), other],),
    ).fetchall()
    assert len(keys) == 2 and keys[0] != keys[1]
    admin.execute(
        "UPDATE tenant_secret SET ciphertext=(SELECT ciphertext FROM tenant_secret "
        "WHERE brand_id=%s AND name='provider') WHERE brand_id=%s",
        (brand, other),
    )
    with pytest.raises(InvalidTag):
        store.read(admin, other, "provider")
    admin.execute("UPDATE tenant_secret SET name='renamed' WHERE brand_id=%s", (brand,))
    with pytest.raises(InvalidTag):
        store.read(admin, UUID(brand), "renamed")


@pytest.mark.parametrize(
    "role,write_status,start_status",
    [
        ("owner", 204, 202),
        ("admin", 204, 202),
        ("buyer", 403, 202),
        ("creative", 403, 403),
        ("reviewer", 403, 403),
        ("client_approver", 403, 403),
        ("client_viewer", 403, 403),
    ],
)
def test_foundation_api_roles(client, brand, admin, identity, role, write_status, start_status):
    admin.execute(
        "UPDATE seat SET role=%s,brand_id=%s WHERE user_id=%s",
        (role, brand, identity["user"]),
    )
    assert (
        client.put(
            f"/api/brands/{brand}/credentials/provider", json={"value": "credential"}
        ).status_code
        == write_status
    )
    response = client.post(
        f"/api/brands/{brand}/workflows", json={"request_key": str(uuid4()), "delay_seconds": 0}
    )
    assert response.status_code == start_status


def test_foundation_rls_missing_and_other_tenant_context(client, brand, database_urls, admin):
    start(client, brand)
    assert (
        client.put(
            f"/api/brands/{brand}/credentials/provider", json={"value": "credential"}
        ).status_code
        == 204
    )
    with psycopg.connect(database_urls[1]) as conn:
        assert conn.execute(
            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ).fetchone() == (False, False)
        for context in ("", str(uuid4())):
            conn.execute("SELECT set_config('app.current_brand_ids',%s,true)", (context,))
            for table in (
                "tenant_secret_key",
                "tenant_secret",
                "workflow_run",
                "workflow_checkpoint",
            ):
                assert (
                    conn.execute(
                        sql.SQL("SELECT * FROM adjutant.{}").format(sql.Identifier(table))
                    ).fetchall()
                    == []
                )


def test_trace_on_errors_excludes_sensitive_url(client):
    canary = "query-secret-" + str(uuid4())
    response = client.get(f"/does-not-exist?secret={canary}")
    assert response.status_code == 404 and len(response.headers["x-request-id"]) == 32
    rejected = client.post("/api/auth/logout", headers={"origin": "https://invalid.example"})
    assert rejected.status_code == 403 and len(rejected.headers["x-request-id"]) == 32


def test_object_storage_rejects_traversal_and_corruption(tmp_path):
    store = ObjectStore(tmp_path)
    brand = uuid4()
    key = store.put(brand, b"real persisted bytes")
    assert ObjectStore(tmp_path).read(brand, key) == b"real persisted bytes"
    assert store.put(brand, b"real persisted bytes") == key
    with pytest.raises(ValueError):
        store.read(brand, "../outside")
    with pytest.raises(FileNotFoundError):
        store.read(uuid4(), key)
    (tmp_path / str(brand) / key).write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="hash"):
        store.read(brand, key)


def test_master_key_is_preserved_and_invalid_key_rejected(tmp_path):
    path = tmp_path / "key"
    provision_master_key(path)
    first = path.read_bytes()
    provision_master_key(path)
    assert path.read_bytes() == first and len(first) == 32
    path.write_bytes(b"bad key")
    with pytest.raises(ValueError):
        provision_master_key(path)
