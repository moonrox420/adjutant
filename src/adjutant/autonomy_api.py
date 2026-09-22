"""Human review and editable limits for autonomous brand operation."""

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Request, Response
from psycopg import sql

from adjutant.approval_client import approval_request
from adjutant.autonomy import GuardrailInput, LaunchApprovalInput, launch_scope
from adjutant.config import Settings
from adjutant.db import Database, Principal, one, require_role
from adjutant.errors import DomainError
from adjutant.events import EventRegistry
from adjutant.service import audit, locked_brand
from adjutant.storage import ObjectStore


def consume_remotely(config: Settings, brand_id: UUID, authorization_id: str) -> dict[str, Any]:
    """Consume once; the caller reconciles the review receipt before retrying."""
    try:
        secret = config.gateway_service_secret_path.read_text(encoding="utf-8").strip()
        if len(secret) < 32:
            raise ValueError("Invalid service credential")
        with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
            response = client.post(
                config.gateway_url.rstrip("/")
                + f"/internal/brands/{brand_id}/launch-authorizations/{authorization_id}/consume",
                headers={"Authorization": f"Bearer {secret}"},
            )
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Invalid gateway response")
        if response.status_code != 200:
            error = body.get("error", {})
            if not isinstance(error, dict) or not all(
                isinstance(error.get(k), str) for k in ("code", "message")
            ):
                raise ValueError("Invalid gateway error")
            raise DomainError(error["code"], error["message"], response.status_code)
        if body.get("consumed") is not True:
            raise ValueError("Gateway did not consume authorization")
        return body
    except (OSError, ValueError, httpx.HTTPError) as exc:
        raise DomainError(
            "GatewayUnavailable",
            "Launch authorization could not be verified. "
            "Retry this same review after the gateway recovers.",
            503,
        ) from exc


def autonomy_router(
    db: Database,
    config: Settings,
    events: EventRegistry,
    authenticate: Callable[[Request], Principal],
) -> APIRouter:
    router = APIRouter(prefix="/api/brands/{brand_id}", tags=["autonomy"])
    actor_type = Annotated[Principal, Depends(authenticate)]
    storage = ObjectStore(config.object_store_path)

    @router.get("/assets/{asset_id}/preview")
    def asset_preview(brand_id: UUID, asset_id: UUID, actor: actor_type) -> Response:
        with db.transaction(actor) as conn:
            asset = one(
                conn,
                "SELECT storage_uri,mime_type FROM asset WHERE brand_id=%s AND id=%s",
                (brand_id, asset_id),
            )
        if asset["mime_type"] not in {"image/png", "image/jpeg", "image/webp"}:
            raise DomainError(
                "UnsupportedPreview", "This asset cannot be displayed as an ad preview.", 422
            )
        try:
            content = storage.read(brand_id, asset["storage_uri"])
        except (OSError, ValueError) as exc:
            raise DomainError(
                "AssetUnavailable", "This ad asset is missing or failed its integrity check.", 409
            ) from exc
        return Response(
            content, media_type=asset["mime_type"], headers={"Cache-Control": "private, no-store"}
        )

    @router.get("/guardrails")
    def guardrails(brand_id: UUID, actor: actor_type) -> dict[str, Any]:
        with db.transaction(actor) as conn:
            return one(conn, "SELECT * FROM guardrail WHERE brand_id=%s", (brand_id,))

    @router.put("/guardrails")
    def save_guardrails(brand_id: UUID, data: GuardrailInput, actor: actor_type) -> dict[str, Any]:
        with db.transaction(actor) as conn:
            locked_brand(conn, brand_id)
            require_role(conn, brand_id, {"owner", "admin"})
            previous = one(
                conn, "SELECT * FROM guardrail WHERE brand_id=%s FOR UPDATE", (brand_id,)
            )
            if previous["version"] != data.expected_version:
                raise DomainError(
                    "GuardrailsChanged", "These limits changed. Reload before saving.", 409
                )
            values = data.model_dump(exclude={"expected_version"})
            updated = one(
                conn,
                sql.SQL(
                    "UPDATE guardrail SET {},version=version+1,updated_at=now() "
                    "WHERE brand_id=%s RETURNING *"
                ).format(
                    sql.SQL(",").join(sql.SQL("{}=%s").format(sql.Identifier(k)) for k in values)
                ),
                (*values.values(), brand_id),
            )
            audit(
                conn,
                events,
                brand_id,
                "ceiling_change",
                "guardrail",
                brand_id,
                {"version": updated["version"], "limits": data.model_dump(mode="json")},
                "Updated autonomous operation limits",
            )
            return updated

    @router.get("/plans/{plan_id}/launch-scope")
    def scope(brand_id: UUID, plan_id: UUID, actor: actor_type) -> dict[str, Any]:
        with db.transaction(actor) as conn:
            return launch_scope(conn, brand_id, plan_id)

    @router.post("/plans/{plan_id}/authorize-launch")
    def authorize(
        brand_id: UUID,
        plan_id: UUID,
        data: LaunchApprovalInput,
        actor: actor_type,
        request: Request,
    ) -> dict[str, Any]:
        result = approval_request(
            config,
            f"/internal/brands/{brand_id}/plans/{plan_id}/authorize-launch",
            {
                "session": request.cookies.get("adjutant_session", ""),
                "approval": data.model_dump(mode="json"),
            },
        )
        if result.get("authorization_id"):
            consume_remotely(config, brand_id, result["authorization_id"])
        elif result.get("already_authorized") is not True:
            raise DomainError(
                "ApprovalUnavailable", "The approval service returned no authorization.", 503
            )
        with db.transaction(actor) as conn:
            return launch_scope(conn, brand_id, plan_id)

    return router
