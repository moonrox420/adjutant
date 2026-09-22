"""A real worker process that exposes deterministic crash windows to the parent test."""

import json
import sys
import threading
import time
from pathlib import Path
from uuid import UUID

from adjutant.db import Database
from adjutant.storage import ObjectStore
from adjutant.workflows import advance


def main() -> None:
    config = json.loads(sys.stdin.readline())
    db = Database(config["url"])
    db.open()
    brand_id, workflow_id = UUID(config["brand_id"]), UUID(config["workflow_id"])
    marker = Path(config["marker"])

    class PausingStore(ObjectStore):
        def put(self, brand_id: UUID, content: bytes) -> str:
            key = super().put(brand_id, content)
            if config["stage"] == "before_commit":
                marker.write_text("object written; database transaction still open")
                if not threading.Event().wait(30):
                    raise TimeoutError("Crash test parent did not terminate worker")
            return key

    store = PausingStore(Path(config["storage"]))
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            advance(db, store, brand_id, workflow_id)
            with db.transaction(extra_brand=brand_id) as conn:
                row = conn.execute(
                    "SELECT state FROM workflow_run WHERE id=%s", (workflow_id,)
                ).fetchone()
            if config["stage"] == "waiting" and row["state"] == "waiting":
                marker.write_text("first checkpoint committed")
                if not threading.Event().wait(30):
                    raise TimeoutError("Crash test parent did not terminate worker")
            if row["state"] == "completed":
                marker.write_text("completed")
                return
            time.sleep(0.05)
        raise TimeoutError("Workflow did not complete")
    finally:
        db.pool.close()


if __name__ == "__main__":
    main()
