"""Durable paused campaign construction and provider-write reconciliation."""

import asyncio
import logging
import threading
from datetime import UTC, datetime
from functools import partial
from typing import Annotated, Any
from uuid import UUID, uuid4

import httpx
import psycopg
from fastapi import APIRouter, Depends
from psycopg.types.json import Jsonb
from pydantic import Field, ValidationError

from adjutant.adapters.builds import build_configuration, builder_for
from adjutant.adapters.errors import ProviderRejection
from adjutant.channel_credentials import authorization_for, invalidate_connection
from adjutant.config import Settings
from adjutant.db import Database, Principal, one, require_role
from adjutant.errors import DomainError
from adjutant.events import EventRegistry
from adjutant.gateway import SpendIntent
from adjutant.models import Input
from adjutant.service import EDIT_ROLES, audit, generation_gate, locked_brand
from adjutant.storage import ObjectStore

logger = logging.getLogger(__name__)


class BuildInterrupted(Exception):
    """A worker shutdown leaves committed work eligible for recovery."""


class CampaignBuildInput(Input):
    request_key: UUID
    connection_id: UUID
    expected_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_guardrail_version: int = Field(ge=1)
    settings: dict


def build_report(conn: psycopg.Connection[Any], brand_id: UUID, build_id: UUID) -> dict:
    row = one(
        conn,
        "SELECT * FROM campaign_build WHERE id=%s AND brand_id=%s",
        (build_id, brand_id),
    )
    row["steps"] = conn.execute(
        "SELECT step_key,native_id,sent_at,verified_at FROM campaign_build_step "
        "WHERE build_id=%s ORDER BY sent_at,step_key",
        (build_id,),
    ).fetchall()
    row["objects"] = conn.execute(
        "SELECT id,level,native_id,parent_id,state,last_verified_at FROM "
        "campaign_object WHERE build_id=%s ORDER BY created_at,id",
        (build_id,),
    ).fetchall()
    row["provider_errors"] = conn.execute(
        "SELECT raw_code,raw_message,occurred_at FROM channel_rejection "
        "WHERE build_id=%s AND brand_id=%s ORDER BY occurred_at,id",
        (build_id, brand_id),
    ).fetchall()
    return row


def queue_build(
    conn: psycopg.Connection[Any],
    actor: Principal,
    brand_id: UUID,
    plan_id: UUID,
    data: CampaignBuildInput,
) -> dict:
    """Freeze a complete source snapshot before the worker can perform provider writes."""
    brand = locked_brand(conn, brand_id)
    require_role(conn, brand_id, EDIT_ROLES)
    generation_gate(conn, brand)
    previous = conn.execute(
        "SELECT * FROM campaign_build WHERE id=%s AND brand_id=%s",
        (data.request_key, brand_id),
    ).fetchone()
    if previous:
        if (
            previous["plan_id"] != plan_id
            or previous["connection_id"] != data.connection_id
            or previous["plan_hash"] != data.expected_plan_hash
            or previous["guardrail_version"] != data.expected_guardrail_version
            or previous["document"]["input_settings"] != data.settings
        ):
            raise DomainError(
                "IdempotencyConflict",
                "This deployment key belongs to a different request.",
                409,
            )
        return build_report(conn, brand_id, previous["id"])
    plan = one(conn, "SELECT * FROM plan WHERE id=%s AND brand_id=%s", (plan_id, brand_id))
    if plan["state"] not in {"approved", "deploying", "live"}:
        raise DomainError(
            "ApprovalRequired",
            "This exact plan revision must be approved before deployment.",
            403,
        )
    limits = one(conn, "SELECT * FROM guardrail WHERE brand_id=%s", (brand_id,))
    account = one(
        conn,
        "SELECT * FROM channel_connection WHERE id=%s AND brand_id=%s",
        (data.connection_id, brand_id),
    )
    _, schema, validate = builder_for(account["channel"])
    if (
        plan["plan_hash"] != data.expected_plan_hash
        or limits["version"] != data.expected_guardrail_version
    ):
        raise DomainError(
            "ReviewChanged",
            "Plan or guardrails changed. Reload before creating the campaign.",
            409,
        )
    try:
        settings = schema.model_validate(data.settings)
    except ValidationError as exc:
        raise DomainError(
            "CampaignSettingsInvalid",
            "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()),
            422,
        ) from exc
    if settings.end_time <= datetime.now(UTC):
        raise DomainError("CampaignEndTime", "Campaign end time must be in the future.", 422)
    allocation = one(
        conn,
        "SELECT * FROM plan_allocation WHERE plan_id=%s AND channel=%s",
        (plan_id, account["channel"]),
    )
    creatives = conn.execute(
        "SELECT DISTINCT ON(c.id) "
        "c.id,c.copy_fields,a.storage_uri,a.content_hash,r.id AS rendition_id "
        "FROM creative c JOIN creative_concept cc ON cc.id=c.concept_id "
        "JOIN rendition r ON r.creative_id=c.id JOIN placement_spec s ON s.id=r.placement_spec_id "
        "JOIN asset a ON a.id=r.asset_id WHERE cc.plan_id=%s AND c.brand_id=%s "
        "AND r.brand_id=c.brand_id AND a.brand_id=c.brand_id AND r.channel=%s "
        "AND r.spec_validation_passed AND s.aspect_ratio='1:1' AND s.retired_at IS NULL "
        "AND c.state IN ('rendered','approved','live') ORDER BY c.id,r.rendered_at DESC,r.id",
        (plan_id, brand_id, account["channel"]),
    ).fetchall()
    document = {
        "name": plan["name"],
        "objective": plan["objective"],
        "daily_budget_usd": str(allocation["daily_budget_usd"]),
        "monthly_budget_usd": str(allocation["monthly_budget_usd"]),
        "settings": settings.model_dump(mode="json"),
        "input_settings": data.settings,
        "creatives": [
            {
                "id": str(c["id"]),
                "copy": c["copy_fields"],
                "storage_key": c["storage_uri"],
                "content_hash": c["content_hash"],
                "rendition_id": str(c["rendition_id"]),
            }
            for c in creatives
        ],
    }
    violations = validate(document)
    if violations:
        raise DomainError("CampaignPreflightFailed", " ".join(violations), 422)
    conn.execute(
        "INSERT INTO "
        "campaign_build(id,brand_id,plan_id,connection_id,channel,plan_hash,guardrail_version,"
        "authorization_generation,requested_by,document,daily_budget_usd,monthly_budget_usd,state) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,\'authorizing\')",
        (
            data.request_key,
            brand_id,
            plan_id,
            account["id"],
            account["channel"],
            plan["plan_hash"],
            limits["version"],
            account["authorization_generation"],
            actor.user_id,
            Jsonb(document),
            allocation["daily_budget_usd"],
            allocation["monthly_budget_usd"],
        ),
    )
    conn.execute("SELECT validate_campaign_build(%s)", (data.request_key,))
    return build_report(conn, brand_id, data.request_key)


def reserve_build_authority(
    db: Database,
    config: Settings,
    actor: Principal,
    brand_id: UUID,
    build_id: UUID,
) -> dict:
    """Consume exact signed create authority before a build becomes runnable."""
    with db.transaction(actor) as conn:
        build = one(
            conn,
            "SELECT * FROM campaign_build WHERE id=%s AND brand_id=%s FOR UPDATE",
            (build_id, brand_id),
        )
        if build["state"] != "authorizing":
            return build_report(conn, brand_id, build_id)
        plan = one(
            conn,
            "SELECT * FROM plan WHERE id=%s AND brand_id=%s",
            (build["plan_id"], brand_id),
        )
        if plan["state"] not in {"approved", "deploying", "live"}:
            raise DomainError(
                "ApprovalRequired",
                "This exact plan revision must be approved before deployment.",
                403,
            )
        token = conn.execute(
            """SELECT t.id FROM approval_token t
            JOIN approval_request a ON a.id=t.approval_request_id
            WHERE t.brand_id=%s AND t.subject_type='plan' AND t.subject_id=%s
            AND t.subject_hash=%s AND t.voided_at IS NULL AND t.expires_at>now()
            AND a.state='approved' AND a.subject_hash=t.subject_hash
            AND %s=ANY(t.scopes) AND 'op:create'=ANY(t.scopes)
            ORDER BY t.issued_at DESC LIMIT 1""",
            (
                brand_id,
                build["plan_id"],
                build["plan_hash"],
                f"channel:{build['channel']}",
            ),
        ).fetchone()
        if not token:
            raise DomainError(
                "ApprovalRequired",
                "No live signed approval authorizes this channel deployment.",
                403,
            )
        intent = SpendIntent(
            brand_id=brand_id,
            token_id=token["id"],
            subject_hash=build["plan_hash"],
            channel=build["channel"],
            operation="create",
            daily_usd=build["daily_budget_usd"],
            total_usd=build["monthly_budget_usd"],
            payload=plan["plan_document"],
        )

    try:
        secret = config.gateway_service_secret_path.read_text(encoding="utf-8").strip()
        if len(secret) < 32:
            raise ValueError("Gateway service secret is invalid")
        response = httpx.post(
            config.gateway_url.rstrip("/") + "/internal/spend/reserve",
            headers={"Authorization": f"Bearer {secret}"},
            json=intent.model_dump(mode="json"),
            timeout=10,
            trust_env=False,
            follow_redirects=False,
        )
        body = response.json()
    except (OSError, httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise DomainError(
            "SpendAuthorityUnavailable",
            "The spend-authority gateway could not reserve this deployment.",
            503,
        ) from exc

    reservation_id = body.get("reservation_id") if response.status_code == 200 else None
    if not reservation_id:
        error = body.get("error", {}) if isinstance(body, dict) else {}
        if error.get("code") == "ReplayDenied":
            with db.transaction(actor) as conn:
                prior = conn.execute(
                    """SELECT c.id FROM approval_token_consumption c
                    WHERE c.token_id=%s AND c.channel=%s AND c.operation='create'
                    AND c.idem_key=%s""",
                    (intent.token_id, intent.channel, intent.idempotency_key),
                ).fetchone()
                reservation_id = str(prior["id"]) if prior else None
        if not reservation_id:
            raise DomainError(
                error.get("code", "SpendAuthorityDenied"),
                error.get("message", "The signed approval does not authorize this deployment."),
                response.status_code if 400 <= response.status_code < 500 else 503,
            )

    with db.transaction(actor) as conn:
        locked_brand(conn, brand_id)
        current = one(
            conn,
            "SELECT * FROM campaign_build WHERE id=%s AND brand_id=%s FOR UPDATE",
            (build_id, brand_id),
        )
        if current["state"] != "authorizing":
            return build_report(conn, brand_id, build_id)
        conn.execute(
            """UPDATE campaign_build
            SET approval_token_id=%s,approval_reservation_id=%s,
                authority_reserved_at=now(),state='queued'
            WHERE id=%s""",
            (intent.token_id, UUID(reservation_id), build_id),
        )
        conn.execute("SELECT validate_campaign_build(%s)", (build_id,))
        return build_report(conn, brand_id, build_id)


class BuildJournal:
    """Commit intent before network I/O, then preserve returned IDs before read-back."""

    def __init__(
        self,
        db: Database,
        brand_id: UUID,
        build_id: UUID,
        stop: threading.Event | None = None,
    ) -> None:
        self.db, self.brand_id, self.build_id = db, brand_id, build_id
        self.stop = stop

    def checkpoint(self) -> None:
        if self.stop and self.stop.is_set():
            raise BuildInterrupted("Worker is shutting down")
        with self.db.transaction(extra_brand=self.brand_id) as conn:
            conn.execute("SELECT validate_campaign_build(%s)", (self.build_id,))

    def begin(self, key: str, payload: dict) -> dict:
        with self.db.transaction(extra_brand=self.brand_id) as conn:
            conn.execute("SELECT validate_campaign_build(%s)", (self.build_id,))
            previous = conn.execute(
                "SELECT * FROM campaign_build_step WHERE build_id=%s AND step_key=%s",
                (self.build_id, key),
            ).fetchone()
            if previous:
                if previous["request"] != payload:
                    raise DomainError(
                        "ProviderRequestChanged",
                        "A resumed provider request differs from its immutable journal.",
                        409,
                    )
                return {**previous, "fresh": False}
            conn.execute(
                "INSERT INTO "
                "campaign_build_step(build_id,brand_id,step_key,request) "
                "VALUES(%s,%s,%s,%s)",
                (self.build_id, self.brand_id, key, Jsonb(payload)),
            )
            return {"fresh": True, "native_id": None}

    def finish(self, key: str, native_id: str, response: dict) -> None:
        with self.db.transaction(extra_brand=self.brand_id) as conn:
            verified = "create_response" not in response
            conn.execute(
                "UPDATE campaign_build_step SET native_id=%s,response=%s,verified_at="
                "CASE WHEN %s THEN now() ELSE verified_at END WHERE build_id=%s AND step_key=%s",
                (native_id, Jsonb(response), verified, self.build_id, key),
            )
            if key == "campaign":
                build = one(conn, "SELECT * FROM campaign_build WHERE id=%s", (self.build_id,))
                intent = one(
                    conn,
                    "SELECT request FROM campaign_build_step WHERE build_id=%s AND step_key=%s",
                    (self.build_id, key),
                )["request"]
                conn.execute(
                    "INSERT INTO "
                    "campaign_object(id,brand_id,connection_id,channel,level,native_id,plan_id,"
                    "name,state,intended_state,idem_key,native_payload,last_verified_at,build_id) "
                    "VALUES(%s,%s,%s,%s,'campaign',%s,%s,%s,%s,'paused',%s,%s,"
                    "CASE WHEN %s THEN now() ELSE NULL END,%s) "
                    "ON CONFLICT(connection_id,idem_key) DO UPDATE SET "
                    "native_payload=excluded.native_payload,state=excluded.state,"
                    "last_verified_at=excluded.last_verified_at",
                    (
                        uuid4(),
                        self.brand_id,
                        build["connection_id"],
                        build["channel"],
                        native_id,
                        build["plan_id"],
                        intent["name"],
                        "paused" if verified else "unknown",
                        f"{self.build_id}:campaign",
                        Jsonb(response),
                        verified,
                        self.build_id,
                    ),
                )


async def run_build(
    db: Database,
    config: Settings,
    storage: ObjectStore,
    events: EventRegistry,
    brand_id: UUID,
    build_id: UUID,
    stop: threading.Event | None = None,
) -> None:
    journal = BuildJournal(db, brand_id, build_id, stop)
    with db.transaction(extra_brand=brand_id) as conn:
        build = one(conn, "SELECT * FROM campaign_build WHERE id=%s FOR UPDATE", (build_id,))
        if build["state"] not in {"queued", "running"}:
            return
        conn.execute(
            "UPDATE campaign_build SET "
            "state='running',attempt_count=attempt_count+1,error_code=NULL,error_message=NULL "
            "WHERE id=%s",
            (build_id,),
        )
    try:
        with db.transaction(extra_brand=brand_id) as conn:
            locked_brand(conn, brand_id)
            app, token = authorization_for(
                conn, config, brand_id, build["channel"], refresh_timeout=10
            )
            account = one(
                conn,
                "SELECT * FROM channel_connection WHERE id=%s",
                (build["connection_id"],),
            )
        constructor, _, _ = builder_for(build["channel"])
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20, connect=5, write=10, pool=5),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            builder = constructor(
                client,
                account["external_ad_account_id"],
                token,
                journal.begin,
                journal.finish,
                partial(storage.read, brand_id),
                journal.checkpoint,
            )
            objects = await builder.build(build["document"], str(build_id))
        with db.transaction(extra_brand=brand_id) as conn:
            conn.execute("SELECT validate_campaign_build(%s)", (build_id,))
            identities = {}
            for item in objects:
                remote = item["remote"]
                result = one(
                    conn,
                    "INSERT INTO "
                    "campaign_object(brand_id,connection_id,channel,level,native_id,parent_id,plan_id,"
                    "creative_id,name,state,intended_state,idem_key,native_payload,"
                    "last_verified_at,build_id) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'paused','paused',%s,%s,now(),%s) "
                    "ON CONFLICT(connection_id,idem_key) DO UPDATE SET "
                    "native_payload=excluded.native_payload,"
                    "last_verified_at=now() RETURNING id",
                    (
                        brand_id,
                        build["connection_id"],
                        build["channel"],
                        item["level"],
                        remote["id"],
                        identities.get(item["parent_key"]),
                        build["plan_id"],
                        item["creative_id"],
                        remote["name"],
                        f"{build_id}:{item['key']}",
                        Jsonb(remote),
                        build_id,
                    ),
                )
                identities[item["key"]] = result["id"]
            conn.execute(
                "UPDATE campaign_build SET state='paused',verified_at=now() WHERE id=%s",
                (build_id,),
            )
            audit(
                conn,
                events,
                brand_id,
                "campaign_build",
                "campaign_build",
                build_id,
                {
                    "state": "paused",
                    "objects": [str(value) for value in identities.values()],
                },
                "Created provider campaign in paused state and verified every object independently",
                actor_kind="system",
            )
    except BuildInterrupted:
        with db.transaction(extra_brand=brand_id) as conn:
            conn.execute(
                "UPDATE campaign_build SET state=CASE WHEN cancellation_requested "
                "THEN 'cancelled' ELSE 'queued' END WHERE id=%s",
                (build_id,),
            )
    except Exception as exc:
        if isinstance(exc, DomainError):
            code, message = exc.code, exc.message
        elif isinstance(exc, psycopg.IntegrityError):
            code, message = (
                "CampaignGuardrailDenied",
                exc.diag.message_primary or "Campaign guardrails denied this operation.",
            )
        else:
            logger.error(
                "campaign_build.failed",
                extra={"build_id": str(build_id), "error_type": type(exc).__name__},
            )
            code, message = (
                "CampaignBuildFailed",
                "Campaign execution failed. Reconcile the recorded operations before retrying.",
            )
        with db.transaction(extra_brand=brand_id) as conn:
            if code in {"PlatformAuthorization", "ReauthorizationRequired"}:
                invalidate_connection(conn, brand_id, build["connection_id"], message)
            if isinstance(exc, ProviderRejection):
                conn.execute(
                    "INSERT INTO channel_rejection(brand_id,channel,raw_code,raw_message,"
                    "classified_as,build_id) VALUES(%s,%s,%s,%s,'provider_rejection',%s)",
                    (
                        brand_id,
                        build["channel"],
                        exc.provider_code,
                        exc.raw_message,
                        build_id,
                    ),
                )
            row = one(
                conn,
                "UPDATE campaign_build SET state=CASE WHEN cancellation_requested "
                "THEN 'cancelled' ELSE 'failed' END,"
                "error_code=%s,error_message=%s WHERE id=%s RETURNING *",
                (code, message, build_id),
            )
            audit(
                conn,
                events,
                brand_id,
                "campaign_build",
                "campaign_build",
                build_id,
                {"state": row["state"], "error_code": code, "error_message": message},
                "Campaign construction stopped before verification",
                actor_kind="system",
            )


class CampaignBuildRunner:
    """Resume queued work after process death using a session-scoped advisory lock."""

    def __init__(
        self,
        db: Database,
        config: Settings,
        storage: ObjectStore,
        events: EventRegistry,
    ) -> None:
        self.db, self.config, self.storage, self.events = db, config, storage, events
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name="campaign-builds", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=40)
        if self.thread.is_alive():
            raise RuntimeError("Campaign worker did not stop before its shutdown deadline")

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                logger.error(
                    "campaign_build.poll_failed",
                    extra={"error_type": type(exc).__name__},
                )
            self.stop.wait(1)

    def tick(self) -> None:
        with self.db.transaction() as conn:
            jobs = conn.execute("SELECT * FROM runnable_campaign_builds()").fetchall()
        for job in jobs:
            if self.stop.is_set():
                return
            with self.db.pool.connection() as lock:
                res: Any = lock.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,33)) AS acquired",
                    (str(job["id"]),),
                ).fetchone()
                if not res:
                    continue
                acquired = res["acquired"] if isinstance(res, dict) else res[0]
                if not acquired:
                    continue
                try:
                    asyncio.run(
                        run_build(
                            self.db,
                            self.config,
                            self.storage,
                            self.events,
                            job["brand_id"],
                            job["id"],
                            self.stop,
                        )
                    )
                finally:
                    lock.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s,33))",
                        (str(job["id"]),),
                    )


def campaign_build_router(db: Database, config: Settings, principal) -> APIRouter:
    router = APIRouter(prefix="/api/brands/{brand_id}")

    @router.get("/plans/{plan_id}/deployment-options")
    def options(brand_id: UUID, plan_id: UUID, actor: Principal = Depends(principal)):
        with db.transaction(actor) as conn:
            plan = one(
                conn,
                "SELECT plan_hash FROM plan WHERE id=%s AND brand_id=%s",
                (plan_id, brand_id),
            )
            limits = one(conn, "SELECT version FROM guardrail WHERE brand_id=%s", (brand_id,))
            rows = conn.execute(
                "SELECT a.channel,c.id AS connection_id,c.external_account_name,c.health,"
                "c.verified_at,c.token_expires_at FROM plan_allocation a "
                "LEFT JOIN channel_connection c "
                "ON c.brand_id=a.brand_id AND c.channel=a.channel AND c.selected "
                "WHERE a.plan_id=%s AND a.brand_id=%s ORDER BY a.channel",
                (plan_id, brand_id),
            ).fetchall()
            return {
                "plan_hash": plan["plan_hash"],
                "guardrail_version": limits["version"],
                "channels": [
                    {**row, "configuration": build_configuration(row["channel"])} for row in rows
                ],
            }

    @router.post("/plans/{plan_id}/deployments", status_code=202)
    def create(
        brand_id: UUID,
        plan_id: UUID,
        data: CampaignBuildInput,
        actor: Principal = Depends(principal),
    ):
        try:
            with db.transaction(actor) as conn:
                build = queue_build(conn, actor, brand_id, plan_id, data)
            return reserve_build_authority(db, config, actor, brand_id, build["id"])
        except psycopg.IntegrityError as exc:
            raise DomainError(
                "CampaignGuardrailDenied",
                exc.diag.message_primary or "Campaign creation violates an execution guardrail.",
                409,
            ) from exc

    @router.get("/plans/{plan_id}/deployments")
    def listing(brand_id: UUID, plan_id: UUID, actor: Principal = Depends(principal)):
        with db.transaction(actor) as conn:
            one(
                conn,
                "SELECT id FROM plan WHERE id=%s AND brand_id=%s",
                (plan_id, brand_id),
            )
            rows = conn.execute(
                "SELECT id FROM campaign_build WHERE plan_id=%s AND brand_id=%s "
                "ORDER BY created_at DESC",
                (plan_id, brand_id),
            ).fetchall()
            return [build_report(conn, brand_id, row["id"]) for row in rows]

    @router.post("/deployments/{build_id}/{operation}")
    def operate(
        brand_id: UUID,
        build_id: UUID,
        operation: str,
        actor: Principal = Depends(principal),
    ):
        if operation not in {"cancel", "retry"}:
            raise DomainError("InvalidOperation", "Choose cancel or retry.", 422)
        with db.transaction(actor) as conn:
            locked_brand(conn, brand_id)
            require_role(conn, brand_id, EDIT_ROLES)
            row = one(
                conn,
                "SELECT * FROM campaign_build WHERE id=%s AND brand_id=%s FOR UPDATE",
                (build_id, brand_id),
            )
            if operation == "cancel":
                if row["state"] == "paused":
                    raise DomainError(
                        "CampaignAlreadyBuilt",
                        "The provider campaign is already paused. Use campaign "
                        "controls to manage it.",
                        409,
                    )
                conn.execute(
                    "UPDATE campaign_build SET "
                    "cancellation_requested=true,state=CASE WHEN state='running' "
                    "THEN state ELSE 'cancelled' END WHERE id=%s",
                    (build_id,),
                )
            else:
                if row["state"] != "failed":
                    raise DomainError(
                        "CampaignNotFailed",
                        "Only failed campaign builds can be reconciled and retried.",
                        409,
                    )
                conn.execute(
                    "UPDATE campaign_build SET "
                    "state='queued',error_code=NULL,error_message=NULL WHERE id=%s",
                    (build_id,),
                )
            return build_report(conn, brand_id, build_id)

    return router
