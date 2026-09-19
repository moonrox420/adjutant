"""Deployment readiness built from persisted prerequisites and gateway verification."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx

from adjutant.config import Settings
from adjutant.db import Database, Principal, one, require_role
from adjutant.errors import DomainError
from adjutant.gateway import SpendIntent
from adjutant.service import EDIT_ROLES


def deployment_preflight(
    db: Database, config: Settings, actor: Principal, brand_id: UUID, plan_id: UUID
) -> dict[str, Any]:
    """Report a point-in-time preflight; this function never reserves authority or launches ads."""
    with db.transaction(actor) as conn:
        require_role(conn, brand_id, EDIT_ROLES)
        plan = one(conn, "SELECT * FROM plan WHERE id=%s AND brand_id=%s", (plan_id, brand_id))
        token = conn.execute(
            """SELECT id FROM approval_token WHERE brand_id=%s AND subject_type='plan'
            AND subject_id=%s AND subject_hash=%s AND voided_at IS NULL AND expires_at>now()
            ORDER BY issued_at DESC LIMIT 1""",
            (brand_id, plan_id, plan["plan_hash"]),
        ).fetchone()
        allocations = conn.execute(
            "SELECT * FROM plan_allocation WHERE plan_id=%s", (plan_id,)
        ).fetchall()
        connections = conn.execute(
            """SELECT channel,health,token_expires_at,access_tier FROM channel_connection
            WHERE brand_id=%s""",
            (brand_id,),
        ).fetchall()
        renditions = conn.execute(
            """SELECT DISTINCT r.channel FROM rendition r
            JOIN creative c ON c.id=r.creative_id JOIN creative_concept cc ON cc.id=c.concept_id
            WHERE cc.plan_id=%s AND c.state IN ('approved','live')
                AND r.spec_validation_passed""",
            (plan_id,),
        ).fetchall()
    checks: list[dict[str, Any]] = []

    def check(key: str, passed: bool, message: str, channel: str | None = None) -> None:
        checks.append({"key": key, "passed": passed, "message": message, "channel": channel})

    check(
        "plan_approval",
        bool(token),
        "Current signed plan approval found."
        if token
        else "This revision needs a current signed plan approval.",
    )
    secret = ""
    if token:
        try:
            secret = config.gateway_service_secret_path.read_text(encoding="utf-8").strip()
        except OSError:
            check("gateway_configuration", False, "Configure the gateway with scripts/upgrade.py.")
    now = datetime.now(UTC)
    with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
        for allocation in allocations:
            channel = allocation["channel"]
            connected = any(
                item["channel"] == channel
                and item["health"] == "healthy"
                and item["access_tier"] not in {"unknown", "denied"}
                and (item["token_expires_at"] is None or item["token_expires_at"] > now)
                for item in connections
            )
            check(
                "channel_access",
                connected,
                "Channel access is recorded as healthy."
                if connected
                else "Connect an authorized advertising account for this channel.",
                channel,
            )
            creative_ready = any(item["channel"] == channel for item in renditions)
            check(
                "creative_approval",
                creative_ready,
                "An approved, validated rendition is recorded."
                if creative_ready
                else "Create, validate, and approve a rendition for this channel.",
                channel,
            )
            check("adapter", False, "A production channel adapter has not been installed.", channel)
            if token and secret:
                if allocation["daily_budget_usd"] is None:
                    check(
                        "spend_authority",
                        False,
                        "The allocation needs an explicit daily budget.",
                        channel,
                    )
                    continue
                intent = SpendIntent(
                    brand_id=brand_id,
                    token_id=token["id"],
                    subject_hash=plan["plan_hash"],
                    channel=channel,
                    operation="create",
                    daily_usd=allocation["daily_budget_usd"],
                    total_usd=allocation["monthly_budget_usd"],
                    payload=plan["plan_document"],
                )
                try:
                    response = client.post(
                        config.gateway_url.rstrip("/") + "/internal/spend/validate",
                        headers={"Authorization": f"Bearer {secret}"},
                        json=intent.model_dump(mode="json"),
                    )
                    body = response.json()
                    valid = response.status_code == 200 and body.get("valid") is True
                    message = (
                        "Gateway verified the signature, revision, scope, and budget authority."
                    )
                    if not valid:
                        message = body.get("error", {}).get(
                            "message", "Gateway denied spend authority."
                        )
                    check("spend_authority", valid, message, channel)
                except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                    check(
                        "spend_authority",
                        False,
                        "Gateway could not verify spend authority. Check its service status.",
                        channel,
                    )
    with db.transaction(actor) as conn:
        latest = one(
            conn, "SELECT plan_hash FROM plan WHERE id=%s AND brand_id=%s", (plan_id, brand_id)
        )
        if latest["plan_hash"] != plan["plan_hash"]:
            raise DomainError(
                "PlanChanged", "The plan changed during preflight. Check the latest revision.", 409
            )
    return {
        "plan_id": plan_id,
        "subject_hash": plan["plan_hash"],
        "checked_at": now,
        "ready": bool(allocations) and all(item["passed"] for item in checks),
        "checks": checks,
        "authority_reserved": False,
    }
