"""Restart local consumers after verified OS exits; never kill a PID read from the database."""

import json
import logging
import subprocess
import threading
import time
from uuid import uuid4

import psycopg

from adjutant.config import Settings
from adjutant.processes import launch, terminate_owned

logger = logging.getLogger(__name__)


class ConsumerSupervisor:
    def __init__(self, config: Settings) -> None:
        self.config = config
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self.run, name="consumer-supervisor", daemon=True
        )
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        if self.config.worker_database_url:
            with psycopg.connect(
                self.config.worker_database_url.get_secret_value(), connect_timeout=5
            ) as conn:
                role = conn.execute("""SELECT rolsuper,rolbypassrls FROM pg_roles
                                       WHERE rolname=current_user""").fetchone()
                if role is None or role[0] or role[1]:
                    raise RuntimeError(
                        "Consumer role must not be superuser or bypass row security"
                    )
            self.thread.start()

    def close(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=15)
        if self.thread.is_alive():
            raise RuntimeError(
                "Consumer supervisor did not shut down within its deadline"
            )

    def run(self) -> None:
        config = self.config
        url = config.worker_database_url.get_secret_value()
        while not self.stop_event.is_set():
            instance = uuid4()
            process = None
            try:
                with psycopg.connect(url, autocommit=True, connect_timeout=5) as conn:
                    conn.execute("SET statement_timeout='5s'")
                    process = launch("adjutant.consumer", subprocess.DEVNULL)
                    self.process = process
                    conn.execute(
                        "INSERT INTO adjutant.consumer_process(instance_id,pid) VALUES(%s,%s)",
                        (instance, process.pid),
                    )
                    payload = {
                        "database_url": url,
                        "instance_id": str(instance),
                        "mail_directory": str(config.mail_directory.resolve()),
                        "mail_transport": config.mail_transport,
                        "mail_from": config.mail_from,
                        "smtp_host": config.smtp_host,
                        "smtp_port": config.smtp_port,
                        "smtp_security": config.smtp_security,
                        "smtp_username": config.smtp_username,
                        "smtp_password": config.smtp_password.get_secret_value(),
                    }
                    process.stdin.write((json.dumps(payload) + "\n").encode())
                    process.stdin.flush()
                    checked = time.monotonic()
                    while process.poll() is None and not self.stop_event.wait(0.25):
                        if time.monotonic() - checked >= 2:
                            healthy = conn.execute(
                                """SELECT heartbeat_at>now()-interval '90 seconds'
                                OR started_at>now()-interval '30 seconds'
                                FROM adjutant.consumer_process WHERE instance_id=%s""",
                                (instance,),
                            ).fetchone()
                            if not healthy or not healthy[0]:
                                logger.error(
                                    "Consumer heartbeat expired: instance=%s", instance
                                )
                                break
                            checked = time.monotonic()
                    code = terminate_owned(process)
                    conn.execute(
                        """UPDATE adjutant.consumer_process SET exited_at=now(),exit_code=%s,
                                    exit_verified_at=now() WHERE instance_id=%s""",
                        (code, instance),
                    )
            except (psycopg.Error, OSError) as exc:
                logger.error("Consumer supervision failed: type=%s", type(exc).__name__)
            finally:
                if process:
                    terminate_owned(process)
                    process.stdin.close()
                self.process = None
            self.stop_event.wait(1)
