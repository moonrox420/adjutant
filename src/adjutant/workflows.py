"""PostgreSQL checkpoint runner hosted by the HTTP process, with crash-safe steps."""

import logging
import threading
from uuid import UUID

from adjutant.db import Database
from adjutant.security import canonical_bytes
from adjutant.storage import ObjectStore

logger = logging.getLogger(__name__)


def advance(db: Database, storage: ObjectStore, brand_id: UUID, workflow_id: UUID) -> bool:
    """Commit a single step under a row lock; competing workers skip claimed work."""
    with db.transaction(extra_brand=brand_id) as conn:
        row = conn.execute(
            "SELECT * FROM workflow_run WHERE id=%s AND state<>'completed' "
            "AND ready_at<=now() FOR UPDATE SKIP LOCKED",
            (workflow_id,),
        ).fetchone()
        if row is None:
            return False
        if row["state"] == "queued":
            step = 1
            conn.execute(
                "UPDATE workflow_run SET state='waiting', "
                "ready_at=now()+delay_seconds*interval '1 second' WHERE id=%s",
                (workflow_id,),
            )
        else:
            step = 2
            result = canonical_bytes(
                {"workflow_id": str(workflow_id), "brand_id": str(brand_id), "status": "completed"}
            )
            # A crash after the file write replays the same content-addressed put.
            key = storage.put(brand_id, result)
            conn.execute(
                "UPDATE workflow_run SET state='completed',result_key=%s,completed_at=now() "
                "WHERE id=%s",
                (key, workflow_id),
            )
        conn.execute(
            "INSERT INTO workflow_checkpoint(brand_id,workflow_id,step) VALUES(%s,%s,%s)",
            (brand_id, workflow_id, step),
        )
    logger.info(
        "workflow.checkpoint_committed",
        extra={"trace_id": row["trace_id"], "workflow_id": str(workflow_id)},
    )
    return True


class WorkflowRunner:
    """Resume committed work on startup; uncommitted steps replay after connection death."""

    def __init__(self, db: Database, storage: ObjectStore) -> None:
        self.db = db
        self.storage = storage
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="adjutant-workflows", daemon=True)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=15)
        if self._thread.is_alive():
            raise RuntimeError("Workflow runner did not stop before shutdown deadline")

    def tick(self) -> None:
        with self.db.transaction() as conn:
            candidates = conn.execute("SELECT * FROM runnable_workflows()").fetchall()
        for item in candidates:
            if self._stop.is_set():
                return
            try:
                advance(self.db, self.storage, item["brand_id"], item["id"])
            except Exception as exc:
                logger.error(
                    "workflow.step_failed",
                    extra={"workflow_id": str(item["id"]), "error_type": type(exc).__name__},
                )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                logger.error("workflow.poll_failed", extra={"error_type": type(exc).__name__})
            self._stop.wait(0.5)
