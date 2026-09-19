"""Core API transport for decisions; no private key or issuance connection is loaded here."""

from typing import Any
from uuid import UUID

import httpx

from adjutant.config import Settings
from adjutant.errors import DomainError
from adjutant.models import Decision


def decide_remotely(
    config: Settings, brand_id: UUID, approval_id: UUID, session: str, decision: Decision
) -> dict[str, Any]:
    """Forward authenticated intent without retrying an uncertain approval transaction."""
    body = approval_request(
        config,
        f"/internal/brands/{brand_id}/approvals/{approval_id}/decide",
        {"session": session, "decision": decision.model_dump(mode="json")},
    )
    if body.get("state") not in {"approved", "pending_client", "rejected", "changes_requested"}:
        raise DomainError("ApprovalUnavailable", "Approval service returned an invalid state.", 503)
    return body


def approval_request(config: Settings, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Send a bounded authenticated request to the configured approval origin only."""
    try:
        secret = config.approval_service_secret_path.read_text(encoding="utf-8").strip()
        if len(secret) < 32:
            raise ValueError("Invalid service credential")
        with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
            response = client.post(
                config.approval_url.rstrip("/") + path,
                headers={"Authorization": f"Bearer {secret}"},
                json=payload,
            )
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Invalid service response")
        if response.status_code != 200:
            error = body.get("error")
            if (
                not isinstance(error, dict)
                or not isinstance(error.get("code"), str)
                or not isinstance(error.get("message"), str)
            ):
                raise ValueError("Invalid service error")
            raise DomainError(error["code"], error["message"], response.status_code)
        return body
    except (OSError, ValueError, httpx.HTTPError) as exc:
        raise DomainError(
            "ApprovalUnavailable",
            "Approval service is unavailable. Reload the request before trying again.",
            503,
        ) from exc
