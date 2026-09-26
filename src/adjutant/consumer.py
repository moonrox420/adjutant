"""Durable local activity and email consumer with transaction rollback on process death."""

import json
import logging
import os
import smtplib
import ssl
import sys
import threading
import time
from email.message import EmailMessage
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from adjutant.generation_worker import watch_parent

logger = logging.getLogger(__name__)


def deliver_mail(item: dict, config: dict) -> None:
    """Delivery is at least once. Local files are idempotent; SMTP may redeliver after a crash."""
    message = EmailMessage()
    message["From"] = config["mail_from"]
    message["To"] = item["recipient"]
    message["Subject"] = item["subject"]
    message["Message-ID"] = f"<{item['id']}@adjutant.local>"
    message.set_content(item["body"])
    if config["mail_transport"] == "file":
        directory = Path(config["mail_directory"])
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = directory / f"{item['id']}.eml"
        temporary = directory / f"{item['id']}.pending"
        with temporary.open("wb") as handle:
            handle.write(message.as_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, target)
        return
    context = ssl.create_default_context()
    if config["smtp_security"] == "ssl":
        client = smtplib.SMTP_SSL(
            config["smtp_host"], config["smtp_port"], timeout=10, context=context
        )
    else:
        client = smtplib.SMTP(config["smtp_host"], config["smtp_port"], timeout=10)
    with client:
        if config["smtp_security"] == "starttls":
            client.starttls(context=context)
        if config["smtp_username"]:
            client.login(config["smtp_username"], config["smtp_password"])
        client.send_message(message)


def consume_once(conn: psycopg.Connection, config: dict) -> int:
    """Each effect and durable receipt commit together; locked work is reclaimed on disconnect."""
    with conn.transaction():
        conn.execute("SELECT adjutant.reap_abandoned_jobs()")
        count = conn.execute(
            "SELECT adjutant.consume_activity_batch(100) AS n"
        ).fetchone()["n"]
    with conn.transaction():
        item = conn.execute("""SELECT * FROM adjutant.mail_outbox
            WHERE delivered_at IS NULL AND attempts<8 AND next_attempt_at<=now()
            ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()
        if item:
            try:
                deliver_mail(item, config)
            except (OSError, smtplib.SMTPException) as exc:
                # Persist exception classes; SMTP responses can include private addresses.
                logger.warning(
                    "Mail delivery failed: message_id=%s type=%s",
                    item["id"],
                    type(exc).__name__,
                )
                conn.execute(
                    """UPDATE adjutant.mail_outbox SET attempts=attempts+1,last_error=%s,
                    next_attempt_at=now()+make_interval(secs=>LEAST(3600,30*power(2,attempts)::integer))
                    WHERE id=%s""",
                    (type(exc).__name__, item["id"]),
                )
            else:
                conn.execute(
                    """UPDATE adjutant.mail_outbox SET delivered_at=now(),
                    attempts=attempts+1,last_error=NULL,body='[Delivered; secret link removed]'
                    WHERE id=%s""",
                    (item["id"],),
                )
            count += 1
    return count


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = json.loads(sys.stdin.buffer.readline(65536))
    threading.Thread(target=watch_parent, daemon=True).start()
    while True:
        try:
            with psycopg.connect(
                config["database_url"],
                autocommit=True,
                row_factory=dict_row,
                connect_timeout=5,
            ) as conn:
                conn.execute("SET statement_timeout='30s'")
                while True:
                    conn.execute(
                        """UPDATE adjutant.consumer_process SET heartbeat_at=now()
                                  WHERE instance_id=%s""",
                        (config["instance_id"],),
                    )
                    count = consume_once(conn, config)
                    if not count:
                        time.sleep(0.5)
        except psycopg.Error as exc:
            logger.error("Consumer database unavailable: sqlstate=%s", exc.sqlstate)
            time.sleep(2)


if __name__ == "__main__":
    main()
