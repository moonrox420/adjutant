"""Licensing management API endpoints for desktop subscription activation and validation."""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import status as http_status
from pydantic import BaseModel, ConfigDict, Field

from adjutant.config import Settings
from adjutant.db import Principal
from adjutant.licensing import (
    activate_license,
    deactivate_license,
    get_license_status,
    process_license_webhook,
    verify_license_webhook_signature,
)


class ActivateLicenseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    license_key: str = Field(min_length=4, max_length=256)


def licensing_router(config: Settings, authenticate: Any) -> APIRouter:
    router = APIRouter(prefix="/api/license", tags=["licensing"])

    @router.get("/status")
    def status() -> dict[str, Any]:
        """Public status check for license state; returns masked key and grace period."""
        return get_license_status(config)

    @router.post("/activate")
    def activate(
        body: ActivateLicenseRequest,
        actor: Principal = Depends(authenticate),
    ) -> dict[str, Any]:
        """Activate a monthly subscription license key on this machine."""
        return activate_license(config, body.license_key)

    @router.post("/deactivate")
    def deactivate(
        actor: Principal = Depends(authenticate),
    ) -> dict[str, Any]:
        """Deactivate license from this machine instance."""
        return deactivate_license(config)

    @router.post("/webhook")
    async def webhook(
        request: Request,
    ) -> dict[str, Any]:
        """Receive and process incoming subscription lifecycle webhooks from payment provider."""
        secret = config.license_webhook_secret.get_secret_value()
        if not secret:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Webhook processing is not configured on this appliance.",
            )

        raw_body = await request.body()
        signature = (
            request.headers.get("x-signature")
            or request.headers.get("webhook-signature")
            or request.headers.get("x-polar-signature")
            or ""
        )

        if not verify_license_webhook_signature(raw_body, signature, secret):
            raise HTTPException(
                status_code=http_status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature.",
            )

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSON payload: {exc}",
            ) from exc

        return process_license_webhook(config, payload)

    return router
