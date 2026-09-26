"""Deployment readiness built from persisted prerequisites and gateway verification."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx

from adjutant.adapters.builds import build_configuration
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
            """SELECT c.channel,c.health,c.token_expires_at,c.access_tier,c.selected,
            c.verified_at,g.authorized_at FROM channel_connection c
            LEFT JOIN channel_launch_grant g ON g.connection_id=c.id AND g.brand_id=c.brand_id
            AND g.authorization_generation=c.authorization_generation
            WHERE c.brand_id=%s""",
            (brand_id,),
        ).fetchall()
        renditions = conn.execute(
            """SELECT DISTINCT r.channel FROM rendition r
            JOIN creative c ON c.id=r.creative_id JOIN creative_concept cc ON cc.id=c.concept_id
            WHERE cc.plan_id=%s AND c.state IN ('rendered','approved','live')
                AND r.spec_validation_passed""",
            (plan_id,),
        ).fetchall()
        studio_renditions = conn.execute(
            "SELECT DISTINCT a.channel FROM studio_plan_creative c "
            "JOIN plan_allocation a ON a.plan_id=c.plan_id "
            "JOIN studio_rendition r ON r.brand_id=c.brand_id AND r.draft_id=c.draft_id "
            "AND r.draft_revision=c.draft_revision WHERE c.plan_id=%s "
            "GROUP BY a.channel,c.creative_id HAVING count(DISTINCT r.aspect_ratio)=4",
            (plan_id,),
        ).fetchall()
        studio_drafts = conn.execute(
            """SELECT d.id, d.document FROM studio_plan_creative c
            JOIN studio_draft d ON d.id=c.draft_id AND d.brand_id=c.brand_id
            WHERE c.plan_id=%s""",
            (plan_id,),
        ).fetchall()
        spec_limits_by_channel = {
            row["channel"]: row["text_limits"]
            for row in conn.execute(
                "SELECT channel, text_limits FROM placement_spec WHERE text_limits<>'{}'::jsonb"
            ).fetchall()
        }
    checks: list[dict[str, Any]] = []

    def check(key: str, passed: bool, message: str, channel: str | None = None) -> None:
        checks.append({"key": key, "passed": passed, "message": message, "channel": channel})

    authorized = bool(allocations) and all(
        any(
            c["channel"] == a["channel"] and c["selected"] and c["authorized_at"]
            for c in connections
        )
        for a in allocations
    )
    check(
        "launch_authorization",
        authorized or bool(token),
        (
            "The selected accounts have first-launch authorization."
            if authorized
            else "Review and authorize the selected accounts for first launch."
        ),
    )
    secret = ""
    if token and not authorized:
        try:
            secret = config.gateway_service_secret_path.read_text(encoding="utf-8").strip()
        except OSError:
            check(
                "gateway_configuration",
                False,
                "Configure the gateway with scripts/upgrade.py.",
            )
    now = datetime.now(UTC)
    with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
        for allocation in allocations:
            channel = allocation["channel"]
            connected = any(
                item["channel"] == channel
                and item["selected"]
                and item["verified_at"] is not None
                and item["health"] == "healthy"
                and item["access_tier"] != "denied"
                and (item["token_expires_at"] is None or item["token_expires_at"] > now)
                for item in connections
            )
            check(
                "channel_access",
                connected,
                (
                    "Channel access is recorded as healthy."
                    if connected
                    else "Connect an authorized advertising account for this channel."
                ),
                channel,
            )
            creative_attached = any(
                item["channel"] == channel for item in [*renditions, *studio_renditions]
            )
            check(
                "creative_attached",
                creative_attached,
                (
                    "Rendered creative is attached to this campaign plan."
                    if creative_attached
                    else "Attach rendered creative to this campaign plan."
                ),
                channel,
            )
            validated = any(item["channel"] == channel for item in renditions)
            check(
                "creative_approval",
                validated,
                (
                    "A channel-validated rendition is recorded."
                    if validated
                    else "Channel-specific creative validation is not recorded. "
                    "Attaching a Studio render does not establish platform conformance."
                ),
                channel,
            )
            channel_limits = spec_limits_by_channel.get(channel)
            if channel_limits and studio_drafts:
                limit_failures = []
                for draft in studio_drafts:
                    doc = draft.get("document", {})
                    channel_copy = doc.get(channel, {}) if isinstance(doc, dict) else {}
                    for field, max_len in channel_limits.items():
                        key = "primary_text" if field == "primary" else field
                        val = channel_copy.get(key)
                        if isinstance(val, str) and len(val) > max_len:
                            limit_failures.append(
                                f"Creative {draft['id']} field '{key}' length {len(val)} "
                                f"exceeds allowed limit of {max_len} characters."
                            )
                if limit_failures:
                    check("character_limits", False, "; ".join(limit_failures), channel)
                else:
                    check(
                        "character_limits",
                        True,
                        f"Creative copy satisfies {channel} character limits.",
                        channel,
                    )
            elif channel_limits:
                check(
                    "character_limits",
                    True,
                    f"Creative copy satisfies {channel} character limits.",
                    channel,
                )
            build_available = build_configuration(channel) is not None
            check(
                "adapter",
                build_available,
                (
                    "A durable paused-campaign construction path is registered."
                    if build_available
                    else "Campaign construction for this channel is unavailable."
                ),
                channel,
            )
            if authorized:
                check(
                    "spend_authority",
                    True,
                    "First-launch authority is recorded. "
                    "Each operation must satisfy current guardrails.",
                    channel,
                )
            if token and secret and not authorized:
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
            conn,
            "SELECT plan_hash FROM plan WHERE id=%s AND brand_id=%s",
            (plan_id, brand_id),
        )
        if latest["plan_hash"] != plan["plan_hash"]:
            raise DomainError(
                "PlanChanged",
                "The plan changed during preflight. Check the latest revision.",
                409,
            )
    return {
        "plan_id": plan_id,
        "subject_hash": plan["plan_hash"],
        "checked_at": now,
        "ready": bool(allocations) and all(item["passed"] for item in checks),
        "checks": checks,
        "authority_reserved": False,
    }
