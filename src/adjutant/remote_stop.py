"""Durable, bounded parallel pause operations with independent provider read-back."""

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID

import httpx
from psycopg import Connection
from psycopg.types.json import Jsonb

from adjutant.adapters.campaign_control import CampaignControl, CampaignTarget
from adjutant.channel_credentials import authorization_for, invalidate_connection
from adjutant.config import Settings
from adjutant.db import Database, Principal, one
from adjutant.errors import DomainError
from adjutant.events import EventRegistry
from adjutant.service import locked_brand

logger = logging.getLogger(__name__)


def enqueue_stop(conn: Connection[Any], brand_id: UUID, actor_id: UUID) -> UUID:
    """Snapshot managed roots and descendants atomically with the brand's kill switch."""
    run_id = one(
        conn,
        "INSERT INTO remote_stop_run(brand_id,requested_by) VALUES(%s,%s) RETURNING id",
        (brand_id, actor_id),
    )["id"]
    objects: Any = conn.execute(
        "SELECT o.*,c.external_ad_account_id FROM campaign_object o JOIN channel_connection c "
        "ON c.id=o.connection_id AND c.brand_id=o.brand_id WHERE o.brand_id=%s "
        "AND o.state NOT IN ('deleted','archived')",
        (brand_id,),
    ).fetchall()
    by_id = {row["id"]: row for row in objects}
    roots: dict[UUID, list[UUID]] = {}
    for obj in objects:
        root = obj
        visited = {obj["id"]}
        while root["level"] != "campaign" and root["parent_id"] in by_id:
            parent = by_id[root["parent_id"]]
            if (
                parent["id"] in visited
                or parent["connection_id"] != obj["connection_id"]
            ):
                break
            visited.add(parent["id"])
            root = parent
        roots.setdefault(root["id"], []).append(obj["id"])
    for root_id, covered in roots.items():
        row = by_id[root_id]
        missing_parent = row["level"] != "campaign"
        conn.execute(
            "INSERT INTO remote_stop_item(brand_id,run_id,campaign_object_id,connection_id,"
            "channel,native_id,account_id,covered_object_ids,state,error_code,error_message) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                brand_id,
                run_id,
                root_id,
                row["connection_id"],
                row["channel"],
                row["native_id"],
                row["external_ad_account_id"],
                covered,
                "failed" if missing_parent else "pending",
                "CampaignParentMissing" if missing_parent else None,
                (
                    "The managed object has no valid campaign ancestor. Pause it in the platform "
                    "console."
                    if missing_parent
                    else None
                ),
            ),
        )
    if not roots:
        conn.execute(
            "UPDATE remote_stop_run SET state='completed',finished_at=now() WHERE id=%s",
            (run_id,),
        )
    return run_id


def stop_report(db: Database, actor: Principal, brand_id: UUID, run_id: UUID) -> dict:
    with db.transaction(actor) as conn:
        run = one(
            conn,
            "SELECT * FROM remote_stop_run WHERE brand_id=%s AND id=%s",
            (brand_id, run_id),
        )
        items = conn.execute(
            "SELECT * FROM remote_stop_item WHERE brand_id=%s AND run_id=%s ORDER BY "
            "channel,native_id",
            (brand_id, run_id),
        ).fetchall()
    return {
        "run": run,
        "items": items,
        "scope": "managed_campaigns",
        "remote_pause_verified": bool(items)
        and all(i["state"] == "verified" for i in items),
    }


def wait_for_stop(
    db: Database, actor: Principal, brand_id: UUID, run_id: UUID, timeout: float = 40
) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        report = stop_report(db, actor, brand_id, run_id)
        if (
            report["run"]["state"] not in {"queued", "running"}
            or time.monotonic() >= deadline
        ):
            return report
        time.sleep(0.1)


def record_result(
    db: Database,
    events: EventRegistry,
    brand_id: UUID,
    item: dict,
    result: dict | None,
    error: DomainError | None,
) -> None:
    """Commit observed state, audit, and activity event together; never infer child status."""
    with db.transaction(extra_brand=brand_id) as conn:
        current = one(
            conn,
            "SELECT state FROM remote_stop_item WHERE id=%s FOR UPDATE",
            (item["id"],),
        )
        if current["state"] != "pending":
            return
        conn.execute(
            "UPDATE remote_stop_item SET state=%s,observed_state=%s,provider_status=%s,"
            "verified_at=CASE WHEN %s THEN now() ELSE NULL END,error_code=%s,error_message=%s "
            "WHERE id=%s",
            (
                "failed" if error else "verified",
                result["state"] if result else None,
                result["provider_status"] if result else None,
                error is None,
                error.code if error else None,
                error.message if error else None,
                item["id"],
            ),
        )
        if result:
            conn.execute(
                "UPDATE campaign_object SET state=%s,last_verified_at=now() WHERE id=%s AND "
                "brand_id=%s",
                (result["state"], item["campaign_object_id"], brand_id),
            )
        if error and error.code in {"PlatformAuthorization", "ReauthorizationRequired"}:
            invalidate_connection(conn, brand_id, item["connection_id"], error.message)
        detail = {
            "run_id": str(item["run_id"]),
            "verified": error is None,
            "state": result,
            "error_code": error.code if error else None,
        }
        action_id = one(
            conn,
            "INSERT INTO action(brand_id,actor_kind,action_type,target_kind,target_id,channel,"
            "target_native_id,diff,rationale,revert_path) "
            "VALUES(%s,'system','pause','campaign_object',%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                brand_id,
                item["campaign_object_id"],
                item["channel"],
                item["native_id"],
                Jsonb(detail),
                (
                    error.message
                    if error
                    else "Provider campaign state independently verified after brand stop"
                ),
                Jsonb(
                    {
                        "operation": "resume",
                        "connection_id": str(item["connection_id"]),
                        "native_id": item["native_id"],
                        "requires_current_guardrails": True,
                    }
                ),
            ),
        )["id"]
        events.append(
            conn,
            "action.recorded",
            brand_id,
            {
                "brand_id": str(brand_id),
                "action_id": str(action_id),
                "action_type": "pause",
                "actor_kind": "system",
                "target_kind": "campaign_object",
            },
        )


async def pause_items(
    db: Database,
    config: Settings,
    events: EventRegistry,
    brand_id: UUID,
    items: list[dict],
    credentials: dict,
    deadline: float,
) -> None:
    semaphore = asyncio.Semaphore(10)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(8, connect=3), follow_redirects=False, trust_env=False
    ) as client:

        async def pause_one(item: dict) -> None:
            result = None
            error = None
            try:
                async with (
                    asyncio.timeout(max(0.01, min(35, deadline - time.monotonic()))),
                    semaphore,
                ):
                    app, token, metadata = credentials[item["connection_id"]]
                    control = CampaignControl(
                        client,
                        CampaignTarget(
                            item["channel"],
                            item["account_id"],
                            item["native_id"],
                            metadata | (item["native_payload"] or {}),
                        ),
                        app,
                        token,
                    )
                    result = await control.pause()
            except TimeoutError:
                error = DomainError(
                    "PauseDeadlineExceeded",
                    "The platform did not confirm pause within 35 seconds. Retry pause and "
                    "check its console.",
                    504,
                )
            except DomainError as exc:
                error = exc
            except Exception as exc:
                logger.error(
                    "remote_stop.provider_failed",
                    extra={
                        "error_type": type(exc).__name__,
                        "item_id": str(item["id"]),
                    },
                )
                error = DomainError(
                    "PauseExecutionFailed",
                    "Pause failed before remote verification. Inspect service logs and retry.",
                    502,
                )
            await asyncio.to_thread(
                record_result, db, events, brand_id, item, result, error
            )

        await asyncio.gather(*(pause_one(item) for item in items))


def execute_stop(
    db: Database, config: Settings, events: EventRegistry, brand_id: UUID, run_id: UUID
) -> None:
    """A session advisory lock prevents duplicate workers and releases on process death."""
    deadline = time.monotonic() + 45
    with db.pool.connection() as lock:
        lock_row: Any = lock.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s,28)) AS acquired",
            (str(run_id),),
        ).fetchone()
        acquired = lock_row["acquired"] if lock_row else False
        if not acquired:
            return
        try:
            with db.transaction(extra_brand=brand_id) as conn:
                run = one(
                    conn, "SELECT state FROM remote_stop_run WHERE id=%s", (run_id,)
                )
                if run["state"] not in {"queued", "running"}:
                    return
                conn.execute(
                    "UPDATE remote_stop_run SET state='running' WHERE id=%s", (run_id,)
                )
                items: Any = conn.execute(
                    "SELECT i.*,o.native_payload FROM remote_stop_item i JOIN campaign_object "
                    "o ON o.id=i.campaign_object_id WHERE i.run_id=%s AND i.state='pending'",
                    (run_id,),
                ).fetchall()
            credentials = {}
            errors = {}
            for item in items:
                connection_id = item["connection_id"]
                if connection_id in credentials or connection_id in errors:
                    continue
                try:
                    if time.monotonic() >= deadline:
                        raise DomainError(
                            "PauseDeadlineExceeded",
                            "Account authorization exceeded the stop deadline. Retry pause.",
                            504,
                        )
                    with db.transaction(extra_brand=brand_id) as conn:
                        locked_brand(conn, brand_id)
                        app, token = authorization_for(
                            conn, config, brand_id, item["channel"], refresh_timeout=3
                        )
                        auth = one(
                            conn,
                            "SELECT accounts FROM channel_authorization WHERE brand_id=%s AND "
                            "channel=%s",
                            (brand_id, item["channel"]),
                        )
                        metadata = next(
                            (
                                a
                                for a in auth["accounts"]
                                if a["id"] == item["account_id"]
                            ),
                            {},
                        )
                        credentials[connection_id] = (app, token, metadata)
                except DomainError as exc:
                    errors[connection_id] = exc
            pending = []
            for item in items:
                if item["connection_id"] in errors:
                    record_result(
                        db, events, brand_id, item, None, errors[item["connection_id"]]
                    )
                else:
                    pending.append(item)
            asyncio.run(
                pause_items(
                    db, config, events, brand_id, pending, credentials, deadline
                )
            )
            with db.transaction(extra_brand=brand_id) as conn:
                conn.execute(
                    "UPDATE remote_stop_run SET state=CASE WHEN EXISTS(SELECT 1 FROM "
                    "remote_stop_item WHERE run_id=%s AND state<>'verified') THEN 'failed' "
                    "ELSE 'completed' END,finished_at=now() WHERE id=%s",
                    (run_id, run_id),
                )
        except Exception as exc:
            logger.error(
                "remote_stop.run_failed",
                extra={"run_id": str(run_id), "error_type": type(exc).__name__},
            )
            with db.transaction(extra_brand=brand_id) as conn:
                conn.execute(
                    "UPDATE remote_stop_run SET "
                    "state='failed',error_code='StopExecutionFailed',finished_at=now() WHERE "
                    "id=%s",
                    (run_id,),
                )
                conn.execute(
                    "UPDATE remote_stop_item SET "
                    "state='failed',error_code='StopExecutionFailed',error_message='Remote "
                    "state is unverified. Retry pause and inspect service logs.' WHERE "
                    "run_id=%s AND state='pending'",
                    (run_id,),
                )
        finally:
            lock.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s,28))", (str(run_id),)
            )


class RemoteStopRunner:
    """Resume durable stop requests on startup; all channel calls have bounded deadlines."""

    def __init__(self, db: Database, config: Settings, events: EventRegistry):
        self.db, self.config, self.events = db, config, events
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=self.run, name="remote-stop-runner", daemon=True
        )

    def start(self) -> None:
        self.thread.start()

    @property
    def alive(self) -> bool:
        return self.thread.is_alive()

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=55)
        if self.thread.is_alive():
            raise RuntimeError(
                "Remote stop worker has not finished its bounded shutdown"
            )

    def run(self) -> None:
        with ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="remote-stop"
        ) as pool:
            active = {}
            while not self.stop.is_set():
                for key, future in list(active.items()):
                    if future.done():
                        try:
                            future.result()
                        except Exception as exc:
                            logger.error(
                                "remote_stop.worker_failed",
                                extra={
                                    "run_id": str(key),
                                    "error_type": type(exc).__name__,
                                },
                            )
                        del active[key]
                try:
                    with self.db.transaction() as conn:
                        rows = conn.execute(
                            "SELECT * FROM runnable_remote_stops()"
                        ).fetchall()
                    for row in rows:
                        if len(active) >= 2:
                            break
                        if row["id"] not in active:
                            active[row["id"]] = pool.submit(
                                execute_stop,
                                self.db,
                                self.config,
                                self.events,
                                row["brand_id"],
                                row["id"],
                            )
                except Exception as exc:
                    logger.error(
                        "remote_stop.poll_failed",
                        extra={"error_type": type(exc).__name__},
                    )
                self.stop.wait(0.2)
