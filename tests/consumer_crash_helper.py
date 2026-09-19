"""Integration harness: stop at real transaction boundaries for OS-kill tests."""

import json
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from adjutant.consumer import deliver_mail


def main():
    config = json.loads(sys.stdin.buffer.readline())
    if config["url"].rsplit("/", 1)[-1] != "adjutant_test":
        raise RuntimeError("Crash tests require the dedicated test database")
    with psycopg.connect(config["url"], autocommit=True, row_factory=dict_row) as conn:
        with conn.transaction():
            if config["kind"] == "activity":
                while conn.execute("SELECT adjutant.consume_activity_batch(100) AS n").fetchone()[
                    "n"
                ]:
                    continue
            else:
                item = conn.execute(
                    "SELECT * FROM adjutant.mail_outbox WHERE id=%s FOR UPDATE",
                    (config["mail_id"],),
                ).fetchone()
                deliver_mail(item, config)
                conn.execute(
                    "UPDATE adjutant.mail_outbox SET delivered_at=now() WHERE id=%s",
                    (config["mail_id"],),
                )
            if config["stage"] == "before_commit":
                Path(config["marker"]).write_text("uncommitted")
                sys.stdin.buffer.readline()
        Path(config["marker"]).write_text("committed")
        sys.stdin.buffer.readline()


if __name__ == "__main__":
    main()
