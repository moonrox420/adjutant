"""Licensing management API endpoints for desktop subscription activation and validation."""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from adjutant.config import Settings
from adjutant.db import Principal
from adjutant.licensing import (
    activate_license,
    deactivate_license,
    get_license_status,
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

    return router
