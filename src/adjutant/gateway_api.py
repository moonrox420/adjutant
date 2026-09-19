"""Internal spend-authority service with a dedicated database role and public keys only."""

import base64
import hashlib
import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import psycopg
from fastapi import Depends, FastAPI, Header
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from adjutant.db import Database
from adjutant.errors import DomainError
from adjutant.gateway import SpendAuthority, SpendIntent

logger = logging.getLogger(__name__)


class GatewaySettings(BaseSettings):
    """The gateway accepts neither the application database URL nor a private signing key."""

    model_config = SettingsConfigDict(
        env_prefix="ADJUTANT_GATEWAY_", env_file=".env", extra="ignore"
    )
    database_url: SecretStr
    public_keys_path: Path = Path(".local/approval-public-keys.json")
    service_secret_path: Path = Path(".local/gateway-service.secret")


def create_app(settings: GatewaySettings | None = None) -> FastAPI:
    """Create a fail-closed internal API; callers must possess a separate service credential."""
    config = settings or GatewaySettings()
    secret = config.service_secret_path.read_text(encoding="utf-8").strip()
    if len(secret) < 32:
        raise ValueError("Gateway service secret must have at least 32 characters")
    encoded_keys = json.loads(config.public_keys_path.read_text(encoding="utf-8"))
    keys = {
        key_id: base64.b64decode(value, validate=True) for key_id, value in encoded_keys.items()
    }
    if any(hashlib.sha256(value).hexdigest()[:24] != key_id for key_id, value in keys.items()):
        raise ValueError("Public key identifier does not match its key material")
    authority = SpendAuthority(keys)
    db = Database(config.database_url.get_secret_value())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.open()
        try:
            with db.transaction() as conn:
                if (
                    conn.execute("SELECT current_user AS name").fetchone()["name"]
                    != "adjutant_gateway"
                ):
                    raise RuntimeError("The gateway requires the adjutant_gateway database role")
            yield
        finally:
            db.pool.close()

    app = FastAPI(
        title="Adjutant channel gateway", lifespan=lifespan, docs_url=None, redoc_url=None
    )

    def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        if not authorization or not hmac.compare_digest(authorization, f"Bearer {secret}"):
            raise DomainError(
                "Unauthorized", "A valid gateway service credential is required.", 401
            )

    @app.exception_handler(DomainError)
    async def domain_error(request: Any, exc: DomainError) -> JSONResponse:
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Any, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": "InvalidSpendIntent", "message": "Spend intent is invalid."}}, 422
        )

    @app.exception_handler(psycopg.Error)
    async def database_error(request: Any, exc: psycopg.Error) -> JSONResponse:
        logger.error("Gateway database failure: sqlstate=%s", exc.sqlstate)
        return JSONResponse(
            {
                "error": {
                    "code": "SpendDenied",
                    "message": "Spend authorization could not complete.",
                }
            },
            409 if isinstance(exc, psycopg.IntegrityError) else 503,
        )

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"service": "adjutant-gateway", "status": "alive"}

    @app.get("/readyz", dependencies=[Depends(authenticate)])
    def ready() -> dict[str, str]:
        with db.transaction() as conn:
            conn.execute("SELECT id FROM approval_token_consumption LIMIT 1")
        return {"status": "ready"}

    @app.post("/internal/spend/validate", dependencies=[Depends(authenticate)])
    def validate(intent: SpendIntent) -> dict[str, Any]:
        with db.transaction(extra_brand=intent.brand_id) as conn:
            return authority.validate(conn, intent)

    @app.post("/internal/spend/reserve", dependencies=[Depends(authenticate)])
    def reserve(intent: SpendIntent) -> dict[str, Any]:
        with db.transaction(extra_brand=intent.brand_id) as conn:
            return authority.reserve(conn, intent)

    return app
