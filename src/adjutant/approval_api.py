"""Separately authenticated approval decisions and spend-token signing."""

import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Header
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from adjutant.audit_export import AuditWindow, signed_export
from adjutant.db import Database
from adjutant.errors import DomainError
from adjutant.events import EventRegistry
from adjutant.models import Decision
from adjutant.processes import lock_active_session
from adjutant.security import ApprovalSigner, session_digest
from adjutant.service import decide

logger = logging.getLogger(__name__)


class ApprovalSettings(BaseSettings):
    """Only the approval service loads the signing key and issuance database identity."""

    model_config = SettingsConfigDict(
        env_prefix="ADJUTANT_APPROVAL_", env_file=".env", extra="ignore"
    )
    database_url: SecretStr
    signing_key_path: Path = Path(".local/approval.key")
    service_secret_path: Path = Path(".local/approval-service.secret")
    registry_path: Path = Path(__file__).with_name("event_registry.json")


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session: SecretStr = Field(min_length=16, max_length=128)
    decision: Decision


class AuditExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session: SecretStr = Field(min_length=16, max_length=128)
    window: AuditWindow


def create_app(settings: ApprovalSettings | None = None) -> FastAPI:
    """Reauthenticate every decision and lock its session until the transaction commits."""
    config = settings or ApprovalSettings()
    secret = config.service_secret_path.read_text(encoding="utf-8").strip()
    if len(secret) < 32:
        raise ValueError("Approval service secret must have at least 32 characters")
    signer = ApprovalSigner(config.signing_key_path)
    events = EventRegistry(config.registry_path)
    db = Database(config.database_url.get_secret_value())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.open()
        try:
            with db.transaction() as conn:
                role = conn.execute("SELECT current_user AS name").fetchone()["name"]
                if role != "adjutant_approval":
                    raise RuntimeError("Approval service requires the adjutant_approval role")
            yield
        finally:
            db.pool.close()

    app = FastAPI(title="Adjutant approvals", lifespan=lifespan, docs_url=None, redoc_url=None)

    def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        if not authorization or not hmac.compare_digest(authorization, f"Bearer {secret}"):
            raise DomainError("Unauthorized", "An approval service credential is required.", 401)

    @app.exception_handler(DomainError)
    async def domain_error(request: Any, exc: DomainError) -> JSONResponse:
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Any, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": "InvalidDecision", "message": "Approval decision is invalid."}},
            422,
        )

    @app.exception_handler(psycopg.Error)
    async def database_error(request: Any, exc: psycopg.Error) -> JSONResponse:
        logger.error("Approval transaction failed: sqlstate=%s", exc.sqlstate)
        return JSONResponse(
            {"error": {"code": "ApprovalUnavailable", "message": "Approval was not recorded."}},
            503,
        )

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"service": "adjutant-approval", "status": "alive"}

    @app.get("/readyz", dependencies=[Depends(authenticate)])
    def ready() -> dict[str, str]:
        with db.transaction() as conn:
            conn.execute("SELECT id FROM approval_request LIMIT 1")
        return {"status": "ready"}

    @app.post(
        "/internal/brands/{brand_id}/approvals/{approval_id}/decide",
        dependencies=[Depends(authenticate)],
    )
    def record_decision(
        brand_id: UUID, approval_id: UUID, body: ApprovalDecision
    ) -> dict[str, Any]:
        token_hash = session_digest(body.session.get_secret_value())
        actor = db.authenticate(token_hash)
        with db.transaction(actor) as conn:
            lock_active_session(conn, token_hash)
            return decide(conn, events, signer, brand_id, approval_id, actor.user_id, body.decision)

    @app.post("/internal/brands/{brand_id}/audit-export", dependencies=[Depends(authenticate)])
    def export(brand_id: UUID, body: AuditExportRequest) -> dict[str, Any]:
        token_hash = session_digest(body.session.get_secret_value())
        actor = db.authenticate(token_hash)
        with db.transaction(actor) as conn:
            lock_active_session(conn, token_hash)
            return signed_export(conn, signer, brand_id, body.window)

    return app
