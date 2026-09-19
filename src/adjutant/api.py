import logging
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from adjutant.auth import auth_router, cancellation_report
from adjutant.config import Settings
from adjutant.consumer_supervisor import ConsumerSupervisor
from adjutant.db import Database, Principal, one, require_role
from adjutant.deployment import deployment_preflight
from adjutant.errors import DomainError
from adjutant.events import EventRegistry
from adjutant.generation import OllamaPlanner
from adjutant.models import (
    ApprovalInput,
    AssertionInput,
    BrandInput,
    CeilingInput,
    Decision,
    GenerateInput,
    KillInput,
    Login,
    PlanEdit,
    PlanInput,
)
from adjutant.processes import generate_in_process, lock_active_session
from adjutant.research import fetch_website
from adjutant.security import (
    ApprovalSigner,
    digest,
    password_hash,
    password_matches,
    session_digest,
)
from adjutant.service import (
    EDIT_ROLES,
    RESTRICTED,
    audit,
    decide,
    generation_gate,
    locked_brand,
    persist_plan,
    request_approval,
)

logger = logging.getLogger("adjutant.api")


def principal(request: Request) -> Principal:
    token = request.cookies.get("adjutant_session", "")
    if not token or len(token) > 128:
        raise DomainError("Unauthorized", "Sign in to continue.", 401)
    return request.app.state.db.authenticate(session_digest(token))


Actor = Annotated[Principal, Depends(principal)]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an API with explicit dependencies and fail-closed database readiness."""
    config = settings or Settings()
    db = Database(config.database_url.get_secret_value())
    events = EventRegistry(config.registry_path)
    signer = ApprovalSigner(config.signing_key_path)
    planner = OllamaPlanner(config.ollama_url)
    cloud_planner = OllamaPlanner(
        "https://ollama.com",
        provider="cloud",
        api_key=config.ollama_cloud_api_key.get_secret_value(),
    )
    consumer = ConsumerSupervisor(config)
    dummy_password = password_hash(secrets.token_urlsafe(32))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.open()
        app.state.db = db
        app.state.config = config
        app.state.consumer = consumer
        consumer.start()
        try:
            yield
        finally:
            consumer.close()
            db.pool.close()

    app = FastAPI(title="Adjutant", version="0.1.0", lifespan=lifespan)

    app.include_router(auth_router(db, config))

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        request_id = str(uuid4())
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if (
                request.headers.get("x-adjutant-client") != "console"
                or request.headers.get("origin", config.public_origin) != config.public_origin
            ):
                return JSONResponse(
                    {
                        "error": {
                            "code": "InvalidOrigin",
                            "message": "Request origin is not permitted.",
                        }
                    },
                    403,
                )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        messages = []
        for issue in exc.errors():
            field = ".".join(map(str, issue["loc"][1:])).replace("_", " ")
            message = issue["msg"].removeprefix("Value error, ")
            messages.append(f"{field}: {message}" if field else message)
        return JSONResponse(
            {"error": {"code": "ValidationFailed", "message": "; ".join(messages)}}, 422
        )

    @app.exception_handler(psycopg.Error)
    async def database_error(request: Request, exc: psycopg.Error) -> JSONResponse:
        logger.error(
            "Database operation failed: sqlstate=%s path=%s", exc.sqlstate, request.url.path
        )
        if isinstance(exc, psycopg.IntegrityError):
            return JSONResponse(
                {
                    "error": {
                        "code": "Conflict",
                        "message": "This change conflicts with an existing record or safety rule.",
                    }
                },
                409,
            )
        return JSONResponse(
            {
                "error": {
                    "code": "DatabaseUnavailable",
                    "message": "The database could not complete this request. Retry shortly.",
                }
            },
            503,
        )

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "alive", "service": "adjutant-core"}

    @app.get("/readyz")
    def ready() -> dict[str, str]:
        with db.transaction() as conn:
            conn.execute("SELECT 1 FROM plan LIMIT 1")
        return {"status": "ready"}

    @app.post("/api/auth/login")
    def login(data: Login, response: Response) -> dict[str, str]:
        with db.transaction() as conn:
            permitted = one(
                conn,
                "SELECT check_login_rate(%s) AS allowed",
                (session_digest(data.email.casefold()),),
            )["allowed"]
        if not permitted:
            raise DomainError(
                "RateLimited", "Too many sign-in attempts. Try again in 15 minutes.", 429
            )
        with db.transaction() as conn:
            identity = conn.execute(
                "SELECT * FROM login_identity(%s)", (data.email.strip().casefold(),)
            ).fetchone()
            encoded = identity["password_hash"] if identity else dummy_password
            valid = password_matches(data.password, encoded)
            if not identity or not valid:
                raise DomainError("InvalidCredentials", "Email or password is incorrect.", 401)
            token = secrets.token_urlsafe(48)
            conn.execute(
                "SELECT create_session(%s,%s)", (identity["user_id"], session_digest(token))
            )
        response.set_cookie(
            "adjutant_session",
            token,
            max_age=43200,
            httponly=True,
            secure=config.secure_cookies,
            samesite="strict",
            path="/",
        )
        return {"status": "signed_in"}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response) -> dict[str, Any]:
        with db.transaction() as conn:
            conn.execute(
                "SELECT revoke_session(%s)",
                (session_digest(request.cookies.get("adjutant_session", "")),),
            )
        response.delete_cookie("adjutant_session", path="/")
        return {
            "status": "signed_out",
            **cancellation_report(db, session_digest(request.cookies.get("adjutant_session", ""))),
        }

    @app.post("/api/auth/logout-all")
    def logout_all(request: Request, response: Response, actor: Actor) -> dict[str, Any]:
        with db.transaction(actor) as conn:
            hashes = one(
                conn,
                "SELECT revoke_all_sessions(%s) AS hashes",
                (session_digest(request.cookies.get("adjutant_session", "")),),
            )["hashes"]
        response.delete_cookie("adjutant_session", path="/")
        return {"status": "signed_out_everywhere", **cancellation_report(db, hashes)}

    @app.get("/api/jobs")
    def jobs(actor: Actor) -> list[dict[str, Any]]:
        with db.transaction(actor) as conn:
            return conn.execute(
                """SELECT id,brand_id,started_at,finished_at,model_id,error_code,
                cancel_requested_at,worker_pid,worker_exit_code,worker_exit_verified_at
                FROM agent_run WHERE actor_user_id=%s ORDER BY started_at DESC LIMIT 30""",
                (actor.user_id,),
            ).fetchall()

    @app.post("/api/jobs/{run_id}/cancel")
    def cancel_job(run_id: UUID, actor: Actor) -> dict[str, Any]:
        with db.transaction(actor) as conn:
            row = one(
                conn,
                """UPDATE agent_run SET cancel_requested_at=COALESCE(cancel_requested_at,now())
                WHERE id=%s AND actor_user_id=%s RETURNING id,session_hash""",
                (run_id, actor.user_id),
            )
        return cancellation_report(db, row["session_hash"], [run_id])

    @app.get("/api/consumer-activity")
    def consumer_activity(actor: Actor) -> dict[str, Any]:
        with db.transaction(actor) as conn:
            receipts = conn.execute("""SELECT event_id,event_type,processed_at FROM consumer_receipt
                ORDER BY processed_at DESC LIMIT 20""").fetchall()
            processes = conn.execute("""SELECT instance_id,started_at,heartbeat_at,exit_code,
                exit_verified_at,(heartbeat_at>now()-interval '30 seconds'
                AND exited_at IS NULL) AS healthy
                FROM consumer_process ORDER BY started_at DESC LIMIT 5""").fetchall()
        return {"receipts": receipts, "processes": processes, "transport": "local_postgresql"}

    @app.get("/api/me")
    def me(actor: Actor) -> dict[str, Any]:
        with db.transaction(actor) as conn:
            accounts = conn.execute(
                "SELECT id,display_name,account_type FROM account ORDER BY created_at"
            ).fetchall()
            seats = conn.execute("""SELECT account_id,brand_id,role,approval_daily_usd_cap,
                                 approval_total_usd_cap FROM seat WHERE revoked_at IS NULL
                                 AND accepted_at IS NOT NULL""").fetchall()
        return {
            "id": actor.user_id,
            "email": actor.email,
            "full_name": actor.full_name,
            "accounts": accounts,
            "seats": seats,
        }

    @app.get("/api/channels")
    def channels(actor: Actor) -> list[dict[str, Any]]:
        with db.transaction(actor) as conn:
            rows = conn.execute("""SELECT DISTINCT ON(channel) channel,registry_version,objectives,
                                supports,prerequisites FROM channel_capability
                                ORDER BY channel,registry_version DESC""").fetchall()
        return [
            {**row, "connection_status": "not_connected", "live_adapter_available": False}
            for row in rows
        ]

    @app.post("/api/brands/{brand_id}/plans/{plan_id}/preflight")
    def preflight(brand_id: UUID, plan_id: UUID, actor: Actor) -> dict[str, Any]:
        return deployment_preflight(db, config, actor, brand_id, plan_id)

    @app.get("/api/brands")
    def brands(actor: Actor) -> list[dict[str, Any]]:
        with db.transaction(actor) as conn:
            return conn.execute("""SELECT b.*,v.monthly_ceiling,v.spend_last_30d,
                v.pending_approvals,
                v.active_objects,EXISTS(SELECT 1 FROM brand_kill_switch k
                WHERE k.brand_id=b.id AND k.released_at IS NULL) AS stopped
                FROM brand b JOIN v_agency_portfolio v ON v.brand_id=b.id
                ORDER BY b.created_at DESC""").fetchall()

    @app.post("/api/brands", status_code=201)
    def create_brand(data: BrandInput, actor: Actor) -> dict[str, Any]:
        brand_id = uuid4()
        with db.transaction(actor, extra_brand=brand_id) as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(data.account_id),)
            )
            account = one(conn, "SELECT * FROM account WHERE id=%s", (data.account_id,))
            owner = conn.execute(
                """SELECT 1 FROM seat WHERE account_id=%s AND user_id=%s
                AND brand_id IS NULL AND role IN ('owner','admin') AND revoked_at IS NULL
                AND accepted_at IS NOT NULL""",
                (data.account_id, actor.user_id),
            ).fetchone()
            if not owner:
                raise DomainError(
                    "Forbidden", "Only workspace owners and admins can add brands.", 403
                )
            if (
                account["account_type"] == "business"
                and conn.execute(
                    "SELECT 1 FROM brand WHERE account_id=%s", (data.account_id,)
                ).fetchone()
            ):
                raise DomainError("SingleBrandAccount", "A business workspace supports one brand.")
            restricted = [data.vertical] if data.vertical in RESTRICTED else []
            brand = one(
                conn,
                """INSERT INTO brand(id,account_id,display_name,website_url,vertical,
                restricted_flags,campaigns_enabled) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    brand_id,
                    data.account_id,
                    data.display_name,
                    str(data.website_url),
                    data.vertical,
                    restricted,
                    not restricted,
                ),
            )
            conn.execute(
                """INSERT INTO budget_ceiling(brand_id,scope_kind,monthly_usd_max,
                daily_usd_max,set_by) VALUES(%s,'brand',%s,%s,%s)""",
                (brand_id, data.monthly_ceiling, data.daily_ceiling, actor.user_id),
            )
            conn.execute("INSERT INTO brand_kit(brand_id) VALUES(%s)", (brand_id,))
            audit(
                conn,
                events,
                brand_id,
                "brand_create",
                "brand",
                brand_id,
                {"display_name": data.display_name, "vertical": data.vertical},
            )
            events.append(
                conn,
                "brand.created",
                brand_id,
                {
                    "brand_id": str(brand_id),
                    "account_id": str(data.account_id),
                    "account_type": account["account_type"],
                    "display_name": data.display_name,
                },
            )
        return brand

    @app.get("/api/brands/{brand_id}/workspace")
    def workspace(brand_id: UUID, actor: Actor) -> dict[str, Any]:
        with db.transaction(actor) as conn:
            brand = one(conn, "SELECT * FROM brand WHERE id=%s", (brand_id,))
            assertions = conn.execute(
                """SELECT * FROM brand_graph_assertion WHERE brand_id=%s
                                         AND superseded_by IS NULL ORDER BY field_path""",
                (brand_id,),
            ).fetchall()
            plans = conn.execute(
                "SELECT * FROM plan WHERE brand_id=%s ORDER BY created_at DESC LIMIT 100",
                (brand_id,),
            ).fetchall()
            approvals = conn.execute(
                """SELECT a.*,p.name AS plan_name,p.plan_document,
                (a.expires_at<=now()) AS is_expired FROM approval_request a
                JOIN plan p ON p.id=a.subject_id
                WHERE a.brand_id=%s ORDER BY a.created_at DESC LIMIT 100""",
                (brand_id,),
            ).fetchall()
            history = conn.execute(
                """SELECT id,action_type,target_kind,target_id,rationale,diff,
                actor_kind,actor_user_id,executed_at FROM action WHERE brand_id=%s
                ORDER BY executed_at DESC,id DESC LIMIT 100""",
                (brand_id,),
            ).fetchall()
            ceilings = conn.execute(
                "SELECT * FROM budget_ceiling WHERE brand_id=%s", (brand_id,)
            ).fetchall()
            stop = conn.execute(
                "SELECT * FROM brand_kill_switch WHERE brand_id=%s AND released_at IS NULL",
                (brand_id,),
            ).fetchone()
        return {
            "brand": brand,
            "assertions": assertions,
            "plans": plans,
            "approvals": approvals,
            "audit": history,
            "ceilings": ceilings,
            "stop": stop,
        }

    @app.post("/api/brands/{brand_id}/assertions", status_code=201)
    def add_assertion(brand_id: UUID, data: AssertionInput, actor: Actor) -> dict[str, Any]:
        with db.transaction(actor) as conn:
            locked_brand(conn, brand_id)
            require_role(conn, brand_id, EDIT_ROLES | {"creative"})
            old = conn.execute(
                """SELECT id FROM brand_graph_assertion WHERE brand_id=%s
                                  AND field_path=%s AND superseded_by IS NULL""",
                (brand_id, data.field_path),
            ).fetchall()
            row = one(
                conn,
                """INSERT INTO brand_graph_assertion(brand_id,field_path,value,provenance_uri,
                provenance_kind,is_claim) VALUES(%s,%s,%s,%s,'user',%s) RETURNING *""",
                (
                    brand_id,
                    data.field_path,
                    Jsonb(data.value),
                    str(data.provenance_uri),
                    data.is_claim,
                ),
            )
            for prior in old:
                conn.execute(
                    "UPDATE brand_graph_assertion SET superseded_by=%s WHERE id=%s",
                    (row["id"], prior["id"]),
                )
            conn.execute("UPDATE brand SET brand_graph_confirmed_at=NULL WHERE id=%s", (brand_id,))
            conn.execute(
                """UPDATE approval_token SET voided_at=now(),voided_reason='brand_graph_changed'
                            WHERE brand_id=%s AND voided_at IS NULL""",
                (brand_id,),
            )
            conn.execute(
                """UPDATE approval_request SET state='voided' WHERE brand_id=%s
                            AND state IN ('pending_internal','pending_client','approved')""",
                (brand_id,),
            )
            conn.execute(
                """UPDATE plan SET state='draft' WHERE brand_id=%s
                            AND state IN ('pending_approval','approved')""",
                (brand_id,),
            )
            audit(
                conn,
                events,
                brand_id,
                "brand_graph_edit",
                "brand_graph_assertion",
                row["id"],
                {
                    "field_path": data.field_path,
                    "value": data.value,
                    "provenance_uri": str(data.provenance_uri),
                },
            )
        return row

    @app.post("/api/brands/{brand_id}/confirm")
    def confirm(brand_id: UUID, actor: Actor) -> dict[str, str]:
        with db.transaction(actor) as conn:
            locked_brand(conn, brand_id)
            require_role(conn, brand_id, EDIT_ROLES)
            rows = conn.execute(
                """SELECT * FROM brand_graph_assertion WHERE brand_id=%s
                                    AND superseded_by IS NULL""",
                (brand_id,),
            ).fetchall()
            if not rows or any(not row["provenance_uri"] for row in rows):
                raise DomainError("ProvenanceMissing", "Add sourced brand facts before confirming.")
            conn.execute(
                """UPDATE brand_graph_assertion SET human_confirmed_at=now(),confirmed_by=%s
                            WHERE brand_id=%s AND superseded_by IS NULL""",
                (actor.user_id, brand_id),
            )
            conn.execute("UPDATE brand SET brand_graph_confirmed_at=now() WHERE id=%s", (brand_id,))
            paths = [row["field_path"] for row in rows]
            audit(
                conn, events, brand_id, "brand_graph_confirm", "brand", brand_id, {"paths": paths}
            )
            events.append(
                conn,
                "brand.graph.confirmed",
                brand_id,
                {
                    "brand_id": str(brand_id),
                    "graph_version": len(rows),
                    "confirmed_by": str(actor.user_id),
                    "confirmed_paths": paths,
                },
            )
        return {"status": "confirmed"}

    @app.post("/api/brands/{brand_id}/plans", status_code=201)
    def create_plan(brand_id: UUID, data: PlanInput, actor: Actor) -> dict[str, Any]:
        with db.transaction(actor) as conn:
            brand = locked_brand(conn, brand_id)
            require_role(conn, brand_id, EDIT_ROLES)
            generation_gate(conn, brand)
            return persist_plan(conn, events, brand_id, data)

    @app.put("/api/brands/{brand_id}/plans/{plan_id}")
    def edit_plan(brand_id: UUID, plan_id: UUID, data: PlanEdit, actor: Actor) -> dict[str, Any]:
        with db.transaction(actor) as conn:
            locked_brand(conn, brand_id)
            require_role(conn, brand_id, EDIT_ROLES)
            old = one(
                conn,
                "SELECT * FROM plan WHERE id=%s AND brand_id=%s FOR UPDATE",
                (plan_id, brand_id),
            )
            if old["plan_hash"] != data.expected_hash:
                raise DomainError("SubjectChanged", "The plan changed. Reload before editing.")
            if old["state"] in {"live", "deploying", "paused", "archived"}:
                raise DomainError(
                    "InvalidTransition", "Create a new plan to change an operational campaign."
                )
            return persist_plan(
                conn,
                events,
                brand_id,
                PlanInput.model_validate(data.model_dump(exclude={"expected_hash"})),
                old,
            )

    @app.post("/api/brands/{brand_id}/plans/{plan_id}/submit")
    def submit(brand_id: UUID, plan_id: UUID, data: ApprovalInput, actor: Actor) -> dict[str, Any]:
        with db.transaction(actor) as conn:
            return request_approval(
                conn, events, brand_id, plan_id, data.expected_hash, data.requires_client_approval
            )

    @app.post("/api/brands/{brand_id}/approvals/{approval_id}/decide")
    def decision(brand_id: UUID, approval_id: UUID, data: Decision, actor: Actor) -> dict[str, Any]:
        with db.transaction(actor) as conn:
            return decide(conn, events, signer, brand_id, approval_id, actor.user_id, data)

    @app.put("/api/brands/{brand_id}/ceiling")
    def ceiling(brand_id: UUID, data: CeilingInput, actor: Actor) -> dict[str, str]:
        with db.transaction(actor) as conn:
            locked_brand(conn, brand_id)
            require_role(conn, brand_id, {"owner", "admin"})
            conn.execute(
                """UPDATE budget_ceiling SET monthly_usd_max=%s,daily_usd_max=%s,set_by=%s
                            WHERE brand_id=%s AND scope_kind='brand'""",
                (data.monthly_ceiling, data.daily_ceiling, actor.user_id, brand_id),
            )
            audit(
                conn,
                events,
                brand_id,
                "ceiling_change",
                "brand",
                brand_id,
                data.model_dump(mode="json"),
            )
        return {"status": "updated"}

    @app.post("/api/brands/{brand_id}/kill")
    def kill(brand_id: UUID, data: KillInput, actor: Actor) -> dict[str, Any]:
        with db.transaction(actor) as conn:
            locked_brand(conn, brand_id)
            require_role(conn, brand_id, EDIT_ROLES)
            conn.execute(
                """INSERT INTO brand_kill_switch(brand_id,engaged_by,reason)
                VALUES(%s,%s,%s) ON CONFLICT(brand_id) DO UPDATE SET engaged_at=now(),
                engaged_by=EXCLUDED.engaged_by,reason=EXCLUDED.reason,released_at=NULL,
                released_by=NULL,verification='{}'::jsonb""",
                (brand_id, actor.user_id, data.reason),
            )
            conn.execute(
                """UPDATE approval_token SET voided_at=now(),voided_reason='kill_switch'
                            WHERE brand_id=%s AND voided_at IS NULL""",
                (brand_id,),
            )
            conn.execute(
                """UPDATE agent_run SET cancel_requested_at=now()
                            WHERE brand_id=%s AND finished_at IS NULL""",
                (brand_id,),
            )
            stopped_jobs = conn.execute(
                """SELECT id,session_hash FROM agent_run WHERE brand_id=%s
                   AND cancel_requested_at IS NOT NULL AND session_hash IS NOT NULL""",
                (brand_id,),
            ).fetchall()
            audit(conn, events, brand_id, "kill_switch", "brand", brand_id, {}, data.reason)
            events.append(
                conn,
                "brand.kill_switch.engaged",
                brand_id,
                {
                    "brand_id": str(brand_id),
                    "engaged_by": str(actor.user_id),
                    "scope": "brand",
                    "reason": data.reason,
                },
            )
        return {
            "status": "local_operations_stopped",
            **cancellation_report(
                db,
                list({row["session_hash"] for row in stopped_jobs}),
                [row["id"] for row in stopped_jobs],
            ),
            "remote_pause_verified": False,
            "message": "Local approvals are voided. No platform pause has been verified.",
        }

    @app.get("/api/status")
    def status(actor: Actor) -> dict[str, Any]:
        with db.transaction(actor) as conn:
            pending = one(
                conn, "SELECT count(*) AS count FROM event_outbox WHERE published_at IS NULL"
            )["count"]
        return {
            "database": "connected",
            "approval_signing": "ready",
            "live_channel_writes": False,
            "model_configured": bool(config.ollama_model),
            "outbox_pending": pending,
            "checked_at": datetime.now(UTC),
        }

    @app.get("/api/models")
    def models(actor: Actor, provider: Literal["local", "cloud"] = "local") -> dict[str, Any]:
        selected = cloud_planner if provider == "cloud" else planner
        return {
            "models": selected.models(),
            "provider": provider,
            "configured_model": config.ollama_cloud_model
            if provider == "cloud"
            else config.ollama_model,
        }

    @app.get("/api/model-providers")
    def model_providers(actor: Actor) -> dict[str, Any]:
        return {
            "default_provider": config.ollama_provider,
            "providers": [
                {
                    "id": "local",
                    "label": "Local Ollama",
                    "configured": True,
                    "configured_model": config.ollama_model,
                },
                {
                    "id": "cloud",
                    "label": "Ollama Cloud",
                    "configured": bool(config.ollama_cloud_api_key.get_secret_value()),
                    "configured_model": config.ollama_cloud_model,
                },
            ],
        }

    @app.post("/api/brands/{brand_id}/research")
    def research(brand_id: UUID, actor: Actor) -> dict[str, Any]:
        with db.transaction(actor) as conn:
            brand = one(conn, "SELECT * FROM brand WHERE id=%s", (brand_id,))
            require_role(conn, brand_id, EDIT_ROLES)
            website_url = brand["website_url"]
        evidence = fetch_website(website_url)
        with db.transaction(actor) as conn:
            locked_brand(conn, brand_id)
            require_role(conn, brand_id, EDIT_ROLES)
            row = conn.execute(
                """INSERT INTO website_evidence(brand_id,source_url,content_hash,title,text_content)
                   VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING *""",
                (
                    brand_id,
                    evidence.source_url,
                    evidence.content_hash,
                    evidence.title,
                    evidence.text,
                ),
            ).fetchone()
            if not row:
                return one(
                    conn,
                    """SELECT * FROM website_evidence WHERE brand_id=%s
                                  AND source_url=%s AND content_hash=%s""",
                    (brand_id, evidence.source_url, evidence.content_hash),
                )
            audit(
                conn,
                events,
                brand_id,
                "brand_graph_edit",
                "website_evidence",
                row["id"],
                {"source_url": evidence.source_url, "content_hash": evidence.content_hash},
                "Imported website text for human review; no assertions were auto-confirmed.",
            )
            return row

    @app.get("/api/brands/{brand_id}/evidence")
    def evidence(brand_id: UUID, actor: Actor) -> list[dict[str, Any]]:
        with db.transaction(actor) as conn:
            one(conn, "SELECT id FROM brand WHERE id=%s", (brand_id,))
            return conn.execute(
                """SELECT * FROM website_evidence WHERE brand_id=%s
                                   ORDER BY fetched_at DESC LIMIT 20""",
                (brand_id,),
            ).fetchall()

    @app.post("/api/brands/{brand_id}/generate-plan", status_code=201)
    def generate_plan(
        brand_id: UUID, data: GenerateInput, actor: Actor, request: Request
    ) -> dict[str, Any]:
        token_hash = session_digest(request.cookies.get("adjutant_session", ""))
        if data.provider == "cloud" and not config.ollama_cloud_api_key.get_secret_value():
            raise DomainError(
                "CloudNotConfigured", "Configure the Ollama Cloud API key first.", 503
            )
        with db.transaction(actor) as conn:
            lock_active_session(conn, token_hash)
            brand = locked_brand(conn, brand_id)
            require_role(conn, brand_id, EDIT_ROLES)
            generation_gate(conn, brand)
            active = conn.execute(
                """SELECT 1 FROM agent_run WHERE brand_id=%s
                AND finished_at IS NULL AND started_at>now()-interval '15 minutes'""",
                (brand_id,),
            ).fetchone()
            count = one(
                conn,
                """SELECT count(*) AS n FROM agent_run WHERE brand_id=%s
                               AND started_at>now()-interval '1 day'""",
                (brand_id,),
            )["n"]
            if active or count >= 30:
                raise DomainError(
                    "GenerationLimited",
                    "One generation at a time and 30 daily runs are allowed per brand.",
                    429,
                )
            facts = conn.execute(
                """SELECT field_path,value,provenance_uri FROM brand_graph_assertion
                WHERE brand_id=%s AND superseded_by IS NULL AND human_confirmed_at IS NOT NULL
                ORDER BY field_path""",
                (brand_id,),
            ).fetchall()
            ceiling = one(
                conn,
                """SELECT monthly_usd_max,daily_usd_max FROM budget_ceiling
                                    WHERE brand_id=%s AND scope_kind='brand'""",
                (brand_id,),
            )
            capabilities = conn.execute("""SELECT channel,objectives FROM channel_capability
                                           ORDER BY channel""").fetchall()
            snapshot = digest(facts)
            run = one(
                conn,
                """INSERT INTO agent_run(brand_id,agent_name,model_id,model_tier,
                              schema_valid,actor_user_id,session_hash)
                              VALUES(%s,'Strategist',%s,%s,false,%s,%s)
                              RETURNING id""",
                (brand_id, data.model, data.provider, actor.user_id, token_hash),
            )["id"]
        start = time.monotonic()
        result = None
        failure = None
        try:
            result = generate_in_process(
                db,
                actor,
                run,
                token_hash,
                "https://ollama.com" if data.provider == "cloud" else config.ollama_url,
                data.model,
                {
                    "brand": brand["display_name"],
                    "brief": data.brief,
                    "confirmed_facts": facts,
                    "monthly_ceiling_usd": str(ceiling["monthly_usd_max"]),
                    "daily_ceiling_usd": str(ceiling["daily_usd_max"]),
                    "channels": capabilities,
                },
                provider=data.provider,
                api_key=(
                    config.ollama_cloud_api_key.get_secret_value()
                    if data.provider == "cloud"
                    else ""
                ),
            )
            with db.transaction(actor) as conn:
                lock_active_session(conn, token_hash)
                brand = locked_brand(conn, brand_id)
                state = one(
                    conn, "SELECT cancel_requested_at FROM agent_run WHERE id=%s FOR UPDATE", (run,)
                )
                if state["cancel_requested_at"]:
                    raise DomainError(
                        "GenerationCancelled", "Generation stopped; no draft was saved.", 409
                    )
                require_role(conn, brand_id, EDIT_ROLES)
                generation_gate(conn, brand)
                current = conn.execute(
                    """SELECT field_path,value,provenance_uri
                    FROM brand_graph_assertion WHERE brand_id=%s AND superseded_by IS NULL
                    AND human_confirmed_at IS NOT NULL ORDER BY field_path""",
                    (brand_id,),
                ).fetchall()
                if digest(current) != snapshot:
                    raise DomainError(
                        "BrandChanged", "The brand changed during generation. Try again."
                    )
                plan = persist_plan(conn, events, brand_id, result.plan)
                conn.execute("UPDATE plan SET created_by_actor='agent' WHERE id=%s", (plan["id"],))
            return plan
        except DomainError as exc:
            failure = exc.code
            raise
        except Exception:
            failure = "UnexpectedGenerationFailure"
            raise
        finally:
            with db.transaction(actor) as conn:
                conn.execute(
                    """UPDATE agent_run SET finished_at=now(),latency_ms=%s,
                    schema_valid=%s,usage_complete=%s,retry_count=%s,input_tokens=%s,output_tokens=%s,
                    error_code=%s,escalated_to_human=%s WHERE id=%s""",
                    (
                        int((time.monotonic() - start) * 1000),
                        result is not None and failure is None,
                        result is not None,
                        result.attempts - 1 if result else 0,
                        result.input_tokens if result else 0,
                        result.output_tokens if result else 0,
                        failure,
                        failure is not None,
                        run,
                    ),
                )

    return app
