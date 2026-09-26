"""Durable Studio jobs with database ownership, resumable checkpoints, and cancellation."""

import asyncio
import logging
from collections.abc import Callable
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from adjutant.campaign_api import draft_response, studio_generator
from adjutant.config import Settings
from adjutant.creative_concepts import CONCEPT_DIRECTIONS
from adjutant.db import Database, Principal, one, require_role
from adjutant.errors import DomainError
from adjutant.events import EventRegistry
from adjutant.processes import lock_active_session
from adjutant.security import session_digest
from adjutant.service import EDIT_ROLES, audit, generation_gate, locked_brand
from adjutant.storage import ObjectStore
from adjutant.studio_models import QuickGenerateRequest
from adjutant.studio_operations import visual_generator

logger = logging.getLogger(__name__)


class StudioJobInput(QuickGenerateRequest):
    request_key: UUID
    concept_count: Literal[1, 5] = 5


def public_job(row: dict) -> dict:
    return {
        key: row[key]
        for key in (
            "id",
            "brand_id",
            "state",
            "cancel_requested_at",
            "error_code",
            "error_message",
            "created_at",
            "updated_at",
            "finished_at",
            "attempts",
            "concept_count",
        )
    }


def studio_jobs_router(
    db: Database,
    config: Settings,
    storage: ObjectStore,
    authenticate: Callable[[Request], Principal],
) -> APIRouter:
    router = APIRouter(tags=["studio-jobs"])
    actor_type = Annotated[Principal, Depends(authenticate)]
    events = EventRegistry(config.registry_path)

    @router.post("/api/studio/jobs", status_code=202)
    def enqueue(data: StudioJobInput, request: Request, actor: actor_type) -> dict:
        if not config.workflow_enabled:
            raise DomainError(
                "StudioWorkerDisabled",
                "Enable the workflow worker before queuing Studio jobs.",
                503,
            )
        token_hash = session_digest(request.cookies.get("adjutant_session", ""))
        with db.transaction(actor) as conn:
            lock_active_session(conn, token_hash)
            brand = locked_brand(conn, data.brand_id)
            require_role(conn, data.brand_id, EDIT_ROLES)
            generation_gate(conn, brand)
            existing = conn.execute(
                "SELECT * FROM studio_job WHERE brand_id=%s AND request_key=%s",
                (data.brand_id, data.request_key),
            ).fetchone()
            if existing:
                if (
                    existing["url_or_prompt"] != data.url_or_prompt
                    or existing["concept_count"] != data.concept_count
                ):
                    raise DomainError(
                        "IdempotencyConflict",
                        "This request key belongs to a different brief.",
                        409,
                    )
                return public_job(existing)
            provider = visual_generator(conn, data.brand_id, config)
            provider.require_configuration()
            if not config.ollama_model:
                raise DomainError(
                    "CopyModelNotConfigured",
                    "Configure the installed local copy model.",
                    503,
                )
            active = conn.execute(
                "SELECT 1 FROM studio_draft WHERE brand_id=%s AND state='generating' "
                "UNION ALL SELECT 1 FROM studio_job WHERE brand_id=%s "
                "AND state IN ('queued','running')",
                (data.brand_id, data.brand_id),
            ).fetchone()
            count = one(
                conn,
                "SELECT count(*) AS n FROM studio_draft WHERE brand_id=%s AND "
                "created_at>now()-interval '1 day'",
                (data.brand_id,),
            )["n"]
            if active or count + data.concept_count > 30:
                raise DomainError(
                    "GenerationLimited",
                    "One generation at a time and 30 daily runs are allowed per brand.",
                    429,
                )
            draft = one(
                conn,
                "INSERT INTO studio_draft(brand_id,actor_user_id,image_model) "
                "VALUES(%s,%s,%s) RETURNING id",
                (data.brand_id, actor.user_id, provider.model),
            )
            job = one(
                conn,
                "INSERT INTO "
                "studio_job(id,brand_id,actor_id,session_hash,request_key,url_or_prompt,concept_count)"
                " "
                "VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    draft["id"],
                    data.brand_id,
                    actor.user_id,
                    token_hash,
                    data.request_key,
                    data.url_or_prompt,
                    data.concept_count,
                ),
            )
            conn.execute(
                "UPDATE studio_draft SET job_id=%s,concept_index=0 WHERE id=%s",
                (job["id"], job["id"]),
            )
            audit(
                conn,
                events,
                data.brand_id,
                "studio_generate",
                "studio_job",
                job["id"],
                {"state": "queued"},
                "Queued durable Studio generation",
            )
            return public_job(job)

    @router.get("/api/brands/{brand_id}/studio/jobs/latest")
    def latest(brand_id: UUID, actor: actor_type) -> dict | None:
        with db.transaction(actor) as conn:
            one(conn, "SELECT id FROM brand WHERE id=%s", (brand_id,))
            row = conn.execute(
                "SELECT * FROM studio_job WHERE brand_id=%s ORDER BY created_at DESC LIMIT 1",
                (brand_id,),
            ).fetchone()
            return public_job(row) if row else None

    @router.get("/api/brands/{brand_id}/studio/jobs/{job_id}")
    def status(brand_id: UUID, job_id: UUID, actor: actor_type) -> dict:
        with db.transaction(actor) as conn:
            row = one(
                conn,
                "SELECT * FROM studio_job WHERE brand_id=%s AND id=%s",
                (brand_id, job_id),
            )
            result = public_job(row)
            if row["state"] == "completed":
                result["result"] = draft_response(
                    one(conn, "SELECT * FROM studio_draft WHERE id=%s", (job_id,)),
                    storage,
                    conn,
                )
            return result

    @router.delete("/api/brands/{brand_id}/studio/jobs/{job_id}", status_code=202)
    def cancel(brand_id: UUID, job_id: UUID, actor: actor_type) -> dict:
        with db.transaction(actor) as conn:
            require_role(conn, brand_id, EDIT_ROLES)
            row = one(
                conn,
                "SELECT * FROM studio_job WHERE brand_id=%s AND id=%s FOR UPDATE",
                (brand_id, job_id),
            )
            if row["state"] in {"queued", "running"}:
                row = one(
                    conn,
                    "UPDATE studio_job SET cancel_requested_at=now(),updated_at=now() WHERE"
                    " id=%s RETURNING *",
                    (job_id,),
                )
            return public_job(row)

    @router.post("/api/brands/{brand_id}/studio/jobs/{job_id}/retry", status_code=202)
    def retry(
        brand_id: UUID, job_id: UUID, request: Request, actor: actor_type
    ) -> dict:
        token_hash = session_digest(request.cookies.get("adjutant_session", ""))
        if not config.workflow_enabled:
            raise DomainError(
                "StudioWorkerDisabled", "Enable the Studio worker before retrying.", 503
            )
        with db.transaction(actor) as conn:
            lock_active_session(conn, token_hash)
            generation_gate(conn, locked_brand(conn, brand_id))
            require_role(conn, brand_id, EDIT_ROLES)
            row = one(
                conn,
                "SELECT * FROM studio_job WHERE brand_id=%s AND id=%s FOR UPDATE",
                (brand_id, job_id),
            )
            if row["state"] != "failed":
                raise DomainError(
                    "StudioRetryUnavailable",
                    "Only a failed generation can be retried.",
                    409,
                )
            if (
                conn.execute(
                    "SELECT 1 FROM studio_job WHERE brand_id=%s AND state IN ('queued','running')",
                    (brand_id,),
                ).fetchone()
                or conn.execute(
                    "SELECT 1 FROM studio_draft WHERE brand_id=%s AND state='generating'",
                    (brand_id,),
                ).fetchone()
            ):
                raise DomainError(
                    "GenerationLimited",
                    "Wait for the current generation before retrying.",
                    409,
                )
            provider = visual_generator(conn, brand_id, config)
            provider.require_configuration()
            original = one(
                conn, "SELECT image_model FROM studio_draft WHERE id=%s", (job_id,)
            )
            if original["image_model"] != provider.model:
                raise DomainError(
                    "ImageModelChanged",
                    "The image model changed. Start a new generation.",
                    409,
                )
            row = one(
                conn,
                "UPDATE studio_job SET state='queued',session_hash=%s,actor_id=%s,"
                "cancel_requested_at=NULL,error_code=NULL,error_message=NULL,finished_at=NULL,"
                "updated_at=now() WHERE id=%s RETURNING *",
                (token_hash, actor.user_id, job_id),
            )
            audit(
                conn,
                events,
                brand_id,
                "studio_generate",
                "studio_job",
                job_id,
                {"state": "queued", "retry": True},
                "Retrying unfinished Studio concepts",
            )
            return public_job(row)

    return router


class StudioJobRunner:
    """Hold a PostgreSQL session advisory lock throughout each job, including remote waits."""

    def __init__(self, db: Database, config: Settings, storage: ObjectStore) -> None:
        self.db = db
        self.generate = studio_generator(db, config, storage)
        self.events = EventRegistry(config.registry_path)
        self._tasks: dict[UUID, asyncio.Task] = {}
        self._supervisor: asyncio.Task | None = None

    def start(self) -> None:
        self._supervisor = asyncio.create_task(self._run())

    @property
    def alive(self) -> bool:
        return self._supervisor is not None and not self._supervisor.done()

    async def close(self) -> None:
        if self._supervisor:
            self._supervisor.cancel()
            await asyncio.gather(self._supervisor, return_exceptions=True)
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _run(self) -> None:
        while True:
            try:
                for job_id, task in self._tasks.items():
                    if (
                        task.done()
                        and not task.cancelled()
                        and task.exception() is not None
                    ):
                        logger.error(
                            "studio.supervised_job_failed",
                            extra={
                                "workflow_id": str(job_id),
                                "error_type": type(task.exception()).__name__,
                            },
                        )
                self._tasks = {
                    key: value for key, value in self._tasks.items() if not value.done()
                }
                if len(self._tasks) < 2:
                    with self.db.transaction() as conn:
                        rows = conn.execute(
                            "SELECT * FROM runnable_studio_jobs()"
                        ).fetchall()
                    for row in rows:
                        if row["id"] not in self._tasks and len(self._tasks) < 2:
                            self._tasks[row["id"]] = asyncio.create_task(
                                self.run_job(row["brand_id"], row["id"])
                            )
            except Exception as exc:
                logger.error(
                    "studio.poll_failed", extra={"error_type": type(exc).__name__}
                )
            await asyncio.sleep(0.5)

    def finish(
        self,
        brand_id: UUID,
        job_id: UUID,
        state: str,
        code: str | None = None,
        message: str | None = None,
    ) -> None:
        with self.db.transaction(extra_brand=brand_id) as conn:
            row = conn.execute(
                "UPDATE studio_job SET state=%s,error_code=%s,error_message=%s,"
                "updated_at=now(),finished_at=CASE WHEN %s='queued' THEN NULL ELSE now() END "
                "WHERE id=%s AND state IN ('queued','running') RETURNING actor_id",
                (state, code, message, state, job_id),
            ).fetchone()
            if row:
                if state in {"failed", "cancelled"}:
                    conn.execute(
                        "UPDATE studio_draft SET state='failed',error_code=%s WHERE job_id=%s AND "
                        "state='generating'",
                        (code, job_id),
                    )
                conn.execute(
                    "SELECT set_config('app.current_actor_id',%s,true)",
                    (str(row["actor_id"]),),
                )
                audit(
                    conn,
                    self.events,
                    brand_id,
                    "studio_generate",
                    "studio_job",
                    job_id,
                    {"state": state, "error_code": code},
                    message or f"Studio job {state}",
                )

    async def run_job(self, brand_id: UUID, job_id: UUID) -> None:
        work: asyncio.Task | None = None
        with self.db.pool.connection() as ownership:
            held_res: Any = ownership.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS held",
                (str(job_id),),
            ).fetchone()
            if not held_res:
                return
            held = held_res["held"] if isinstance(held_res, dict) else held_res[0]
            if not held:
                return
            try:
                with self.db.transaction(extra_brand=brand_id) as conn:
                    row = one(conn, "SELECT * FROM studio_job WHERE id=%s", (job_id,))
                    if row["state"] not in {"queued", "running"}:
                        return
                    if row["cancel_requested_at"]:
                        raise DomainError(
                            "GenerationCancelled", "Studio generation cancelled.", 409
                        )
                    conn.execute(
                        "UPDATE studio_job SET "
                        "state='running',attempts=attempts+1,updated_at=now() WHERE id=%s",
                        (job_id,),
                    )
                actor = self.db.authenticate(row["session_hash"])
                data = QuickGenerateRequest(
                    brand_id=brand_id, url_or_prompt=row["url_or_prompt"]
                )
                work = asyncio.create_task(self.generate_concepts(data, actor, row))
                while not work.done():
                    await asyncio.sleep(0.15)
                    with self.db.transaction(actor) as conn:
                        current = one(
                            conn,
                            "SELECT cancel_requested_at FROM studio_job WHERE id=%s",
                            (job_id,),
                        )
                        lock_active_session(conn, row["session_hash"])
                        require_role(conn, brand_id, EDIT_ROLES)
                        generation_gate(
                            conn,
                            one(conn, "SELECT * FROM brand WHERE id=%s", (brand_id,)),
                        )
                    if current["cancel_requested_at"]:
                        raise DomainError(
                            "GenerationCancelled", "Studio generation cancelled.", 409
                        )
                await work
                self.finish(brand_id, job_id, "completed")
            except asyncio.CancelledError:
                if work:
                    work.cancel()
                    await asyncio.gather(work, return_exceptions=True)
                self.finish(brand_id, job_id, "queued")
                raise
            except Exception as exc:
                if work:
                    work.cancel()
                    await asyncio.gather(work, return_exceptions=True)
                cancelled = isinstance(exc, DomainError) and exc.code in {
                    "GenerationCancelled",
                    "Unauthorized",
                    "KillSwitchActive",
                    "Forbidden",
                }
                self.finish(
                    brand_id,
                    job_id,
                    "cancelled" if cancelled else "failed",
                    exc.code if isinstance(exc, DomainError) else "GenerationFailed",
                    (
                        exc.message
                        if isinstance(exc, DomainError)
                        else "Studio generation failed. Check the server request logs."
                    ),
                )
                logger.error(
                    "studio.job_failed",
                    extra={
                        "workflow_id": str(job_id),
                        "error_type": type(exc).__name__,
                    },
                )
            finally:
                ownership.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s,0))", (str(job_id),)
                )

    async def generate_concepts(
        self, data: QuickGenerateRequest, actor: Principal, job: dict
    ) -> None:
        """Resume each concept independently so completed images never regenerate on recovery."""
        previous: list[dict] = []
        source_checkpoint = None
        for index in range(job["concept_count"]):
            with self.db.transaction(actor) as conn:
                lock_active_session(conn, job["session_hash"])
                generation_gate(conn, locked_brand(conn, data.brand_id))
                require_role(conn, data.brand_id, EDIT_ROLES)
                current = one(
                    conn, "SELECT * FROM studio_job WHERE id=%s", (job["id"],)
                )
                if current["cancel_requested_at"]:
                    raise DomainError(
                        "GenerationCancelled", "Studio generation cancelled.", 409
                    )
                draft = conn.execute(
                    "SELECT * FROM studio_draft WHERE job_id=%s AND concept_index=%s",
                    (job["id"], index),
                ).fetchone()
                if draft is None:
                    draft = one(
                        conn,
                        "INSERT INTO studio_draft(brand_id,actor_user_id,image_model,job_id,"
                        "concept_index) SELECT brand_id,actor_user_id,image_model,%s,%s FROM "
                        "studio_draft WHERE id=%s RETURNING *",
                        (job["id"], index, job["id"]),
                    )
            await self.generate(
                data,
                actor,
                job["session_hash"],
                resume_id=draft["id"],
                job_id=job["id"],
                concept=CONCEPT_DIRECTIONS[index] if job["concept_count"] > 1 else None,
                previous=previous,
                complete_job=index == job["concept_count"] - 1,
                source_checkpoint=source_checkpoint,
            )
            with self.db.transaction(actor) as conn:
                saved = one(
                    conn, "SELECT * FROM studio_draft WHERE id=%s", (draft["id"],)
                )
                previous.append(saved["document"])
                source_checkpoint = saved["work_checkpoint"]
