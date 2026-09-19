import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import psycopg
import pytest
from psycopg.rows import dict_row
from test_process_lifecycle import wait_for

from adjutant.config import Settings
from adjutant.consumer import consume_once
from adjutant.consumer_supervisor import ConsumerSupervisor
from adjutant.processes import child_environment, terminate_owned

ROOT = Path(__file__).resolve().parents[1]


def crash_helper(config):
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "tests/consumer_crash_helper.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        env=child_environment(),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    process.stdin.write((json.dumps(config) + "\n").encode())
    process.stdin.flush()
    return process


@pytest.mark.parametrize("stage", ["before_commit", "after_commit"])
def test_activity_process_kill_restart_and_duplicate_receipt(
    worker_url, admin, brand, tmp_path, stage
):
    event = admin.execute(
        "SELECT event_id FROM event_outbox WHERE brand_id=%s LIMIT 1", (brand,)
    ).fetchone()["event_id"]
    marker = tmp_path / "activity-ready"
    process = crash_helper(
        {"url": worker_url, "kind": "activity", "stage": stage, "marker": str(marker)}
    )
    try:
        wait_for(marker.exists)
        assert process.poll() is None
        before = admin.execute(
            "SELECT count(*) AS n FROM consumer_receipt WHERE event_id=%s", (event,)
        ).fetchone()["n"]
        assert before == (0 if stage == "before_commit" else 1)
        process.kill()
        assert process.wait(timeout=3) != 0
    finally:
        terminate_owned(process)
        process.stdin.close()
    with psycopg.connect(worker_url, autocommit=True, row_factory=dict_row) as conn:
        while conn.execute("SELECT adjutant.consume_activity_batch(100) AS n").fetchone()["n"]:
            continue
        assert conn.execute("SELECT adjutant.consume_activity_batch(100) AS n").fetchone()["n"] == 0
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM consumer_receipt WHERE event_id=%s", (event,)
        ).fetchone()["n"]
        == 1
    )
    assert (
        admin.execute(
            "SELECT published_at FROM event_outbox WHERE event_id=%s", (event,)
        ).fetchone()["published_at"]
        is None
    )


def test_mail_delivery_kill_before_commit_redelivers_one_local_file(worker_url, admin, tmp_path):
    item = admin.execute("""INSERT INTO mail_outbox(recipient,subject,body)
        VALUES('consumer@example.com','Crash recovery','A real test email')
        RETURNING id""").fetchone()["id"]
    directory = tmp_path / "mail"
    config = {
        "url": worker_url,
        "kind": "mail",
        "stage": "before_commit",
        "marker": str(tmp_path / "mail-ready"),
        "mail_id": str(item),
        "mail_transport": "file",
        "mail_from": "accounts@example.com",
        "mail_directory": str(directory),
    }
    process = crash_helper(config)
    try:
        wait_for(Path(config["marker"]).exists)
        assert (directory / f"{item}.eml").exists()
        assert (
            admin.execute("SELECT delivered_at FROM mail_outbox WHERE id=%s", (item,)).fetchone()[
                "delivered_at"
            ]
            is None
        )
        process.kill()
        assert process.wait(timeout=3) != 0
    finally:
        terminate_owned(process)
        process.stdin.close()
    with psycopg.connect(worker_url, autocommit=True, row_factory=dict_row) as conn:
        while not admin.execute(
            "SELECT delivered_at FROM mail_outbox WHERE id=%s", (item,)
        ).fetchone()["delivered_at"]:
            assert consume_once(conn, config) > 0
    assert len(list(directory.glob(f"{item}.eml"))) == 1
    assert "A real test email" in (directory / f"{item}.eml").read_text()


def test_supervisor_verifies_killed_consumer_and_starts_replacement(
    worker_url, database_urls, admin, tmp_path
):
    supervisor = ConsumerSupervisor(
        Settings(
            database_url=database_urls[1],
            worker_database_url=worker_url,
            mail_directory=tmp_path / "mail",
        )
    )
    supervisor.start()
    try:
        first = wait_for(lambda: supervisor.process)
        wait_for(
            lambda: admin.execute(
                "SELECT 1 FROM consumer_process WHERE pid=%s", (first.pid,)
            ).fetchone()
        )
        first.kill()
        first.wait(timeout=3)
        replacement = wait_for(
            lambda: (
                supervisor.process
                if supervisor.process is not None and supervisor.process.pid != first.pid
                else None
            )
        )
        assert replacement.poll() is None
        record = wait_for(
            lambda: admin.execute(
                "SELECT * FROM consumer_process WHERE pid=%s AND exit_verified_at IS NOT NULL",
                (first.pid,),
            ).fetchone()
        )
        assert record["exit_code"] == first.returncode
        wait_for(
            lambda: admin.execute(
                "SELECT 1 FROM consumer_process WHERE pid=%s AND heartbeat_at>started_at",
                (replacement.pid,),
            ).fetchone()
        )
    finally:
        supervisor.close()
    assert replacement.poll() is not None


def test_supervisor_connection_loss_terminates_child_without_fabricated_exit_record(
    worker_url,
    database_urls,
    admin,
    tmp_path,
    caplog,
):
    unavailable = threading.Event()
    connected = threading.Event()
    backend_pids = []
    connect = psycopg.connect

    def controlled_connection(*args, **kwargs):
        if unavailable.is_set():
            raise psycopg.OperationalError("Test supervisor connection is unavailable")
        conn = connect(*args, **kwargs)
        if kwargs.get("autocommit"):
            backend_pids.append(conn.info.backend_pid)
            connected.set()
        return conn

    supervisor = ConsumerSupervisor(
        Settings(
            database_url=database_urls[1],
            worker_database_url=worker_url,
            mail_directory=tmp_path / "mail",
        )
    )
    with patch("adjutant.consumer_supervisor.psycopg.connect", side_effect=controlled_connection):
        supervisor.start()
        try:
            assert connected.wait(5)
            first = wait_for(lambda: supervisor.process)
            record = wait_for(
                lambda: admin.execute(
                    "SELECT * FROM consumer_process WHERE pid=%s AND heartbeat_at>started_at",
                    (first.pid,),
                ).fetchone()
            )
            assert first.poll() is None
            unavailable.set()
            assert admin.execute(
                "SELECT pg_terminate_backend(%s) AS stopped", (backend_pids[-1],)
            ).fetchone()["stopped"]
            wait_for(lambda: first.poll() is not None)
            wait_for(lambda: supervisor.process is None)
            assert first.returncode is not None
            missing = admin.execute(
                "SELECT * FROM consumer_process WHERE instance_id=%s", (record["instance_id"],)
            ).fetchone()
            assert missing["exit_code"] is None
            assert missing["exited_at"] is None
            assert missing["exit_verified_at"] is None
            assert "Consumer supervision failed" in caplog.text
            assert worker_url not in caplog.text
            unavailable.clear()
            replacement = wait_for(lambda: supervisor.process)
            assert replacement.pid != first.pid and replacement.poll() is None
            wait_for(
                lambda: admin.execute(
                    "SELECT 1 FROM consumer_process WHERE pid=%s AND heartbeat_at>started_at",
                    (replacement.pid,),
                ).fetchone()
            )
            missing = admin.execute(
                "SELECT exit_verified_at FROM consumer_process WHERE instance_id=%s",
                (record["instance_id"],),
            ).fetchone()
            assert missing["exit_verified_at"] is None
        finally:
            unavailable.clear()
            supervisor.close()
    assert replacement.poll() is not None
    verified = admin.execute(
        "SELECT exit_code,exit_verified_at FROM consumer_process WHERE pid=%s",
        (replacement.pid,),
    ).fetchone()
    assert verified["exit_code"] == replacement.returncode
    assert verified["exit_verified_at"] is not None


def test_worker_cannot_read_accounts_or_sessions(worker_url):
    with psycopg.connect(worker_url, autocommit=True) as conn:
        for table in ("local_credential", "auth_session", "app_user", "brand"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f"SELECT * FROM adjutant.{table}")


def test_consumer_rejects_privileged_database_role(database_urls):
    supervisor = ConsumerSupervisor(
        Settings(database_url=database_urls[1], worker_database_url=database_urls[0])
    )
    with pytest.raises(RuntimeError, match="must not be superuser"):
        supervisor.start()
    assert supervisor.process is None


def test_stale_jobs_are_recoverable_without_fabricated_exit_proof(
    worker_url, admin, brand, identity
):
    run = admin.execute(
        """INSERT INTO agent_run(brand_id,agent_name,model_id,model_tier,
        actor_user_id,session_hash,started_at,worker_heartbeat_at)
        VALUES(%s,'Strategist','controlled-model','local',%s,'abandoned-test-session',
               now()-interval '1 minute',now()-interval '1 minute') RETURNING id""",
        (brand, identity["user"]),
    ).fetchone()["id"]
    with psycopg.connect(worker_url, autocommit=True) as conn:
        assert conn.execute("SELECT adjutant.reap_abandoned_jobs()").fetchone()[0] >= 1
    recorded = admin.execute("SELECT * FROM agent_run WHERE id=%s", (run,)).fetchone()
    assert recorded["finished_at"] and recorded["cancel_requested_at"]
    assert recorded["error_code"] == "WorkerHeartbeatLost"
    assert recorded["worker_exit_verified_at"] is None
    assert recorded["worker_exit_code"] is None


def test_mail_failure_is_retried_with_bounded_backoff_and_no_secret_logging(
    worker_url, admin, tmp_path
):
    from unittest.mock import patch

    config = {
        "mail_transport": "file",
        "mail_directory": str(tmp_path),
        "mail_from": "accounts@example.com",
    }
    with psycopg.connect(worker_url, autocommit=True, row_factory=dict_row) as conn:
        while consume_once(conn, config):
            continue
        item = admin.execute("""INSERT INTO mail_outbox(recipient,subject,body)
            VALUES('retry@example.com','Retry delivery','Private link') RETURNING id""").fetchone()[
            "id"
        ]
        smtp = {
            **config,
            "mail_transport": "smtp",
            "smtp_host": "localhost",
            "smtp_port": 587,
            "smtp_security": "starttls",
            "smtp_username": "",
            "smtp_password": "",
        }
        with patch(
            "adjutant.consumer.smtplib.SMTP", side_effect=OSError("private-server-response")
        ):
            assert consume_once(conn, smtp) == 1
        row = admin.execute("SELECT * FROM mail_outbox WHERE id=%s", (item,)).fetchone()
        assert row["attempts"] == 1 and row["delivered_at"] is None
        assert row["last_error"] == "OSError"
        assert row["next_attempt_at"] > row["created_at"]
        assert consume_once(conn, config) == 0
        admin.execute("UPDATE mail_outbox SET next_attempt_at=now() WHERE id=%s", (item,))
        assert consume_once(conn, config) == 1
        row = admin.execute("SELECT * FROM mail_outbox WHERE id=%s", (item,)).fetchone()
        assert row["delivered_at"] and row["attempts"] == 2 and row["last_error"] is None
        assert "Private link" not in row["body"]
