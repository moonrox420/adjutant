"""Versioned guardrails and one-time, account-bound launch authorization."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import Field, model_validator

from adjutant.creative_policy import enforce_blocked_claims
from adjutant.db import one, require_role
from adjutant.errors import DomainError
from adjutant.events import EventRegistry
from adjutant.models import Input, Money
from adjutant.security import ApprovalSigner, digest, verify_claims
from adjutant.service import audit, check_plan_integrity, generation_gate, locked_brand

events = EventRegistry(Path(__file__).with_name("event_registry.json"))


class GuardrailInput(Input):
    expected_version: int = Field(ge=1)
    monthly_spend_cap_usd: Money
    daily_spend_cap_usd: Money
    max_daily_spend_increase_pct: Decimal = Field(
        default=Decimal("25.00"),
        ge=Decimal("0.00"),
        le=Decimal("100.00"),
        decimal_places=2,
    )
    max_new_campaigns_per_day: int = Field(default=3, ge=1, le=1000)
    max_new_ads_per_day: int = Field(default=20, ge=1, le=10000)
    per_channel_cap_pct: Decimal = Field(
        default=Decimal("60.00"),
        gt=Decimal("0.00"),
        le=Decimal("100.00"),
        decimal_places=2,
    )
    min_channel_floor_pct: Decimal = Field(
        default=Decimal("0.00"),
        ge=Decimal("0.00"),
        le=Decimal("100.00"),
        decimal_places=2,
    )
    blocked_claims: list[str] = Field(default_factory=list, max_length=500)
    requires_approval_above_usd: Money | None = None

    @model_validator(mode="after")
    def consistent_limits(self):
        if self.daily_spend_cap_usd > self.monthly_spend_cap_usd:
            raise ValueError("Daily cap cannot exceed the monthly cap")
        if self.min_channel_floor_pct > self.per_channel_cap_pct:
            raise ValueError("Channel floor cannot exceed the channel cap")
        self.blocked_claims = list(dict.fromkeys(item.strip() for item in self.blocked_claims))
        if any(not item or len(item) > 500 for item in self.blocked_claims):
            raise ValueError("Blocked claims must contain between 1 and 500 characters")
        return self


class LaunchApprovalInput(Input):
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_guardrail_version: int = Field(ge=1)
    request_key: UUID
    expected_review_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def launch_scope(conn: Connection[Any], brand_id: UUID, plan_id: UUID) -> dict[str, Any]:
    """Resolve actual selected accounts; a plan revision never revokes a consumed grant."""
    plan = one(conn, "SELECT * FROM plan WHERE brand_id=%s AND id=%s", (brand_id, plan_id))
    limits = one(conn, "SELECT * FROM guardrail WHERE brand_id=%s", (brand_id,))
    manifest = one(conn, "SELECT campaign_review_manifest(%s,%s) AS document", (brand_id, plan_id))[
        "document"
    ]
    rows: Any = conn.execute(
        """SELECT a.channel,c.id AS connection_id,c.external_ad_account_id,
        c.external_account_name,c.health,c.verified_at,c.token_expires_at,c.authorization_generation,
        g.authorized_at,g.authorization_id
        FROM plan_allocation a LEFT JOIN channel_connection c
        ON c.brand_id=a.brand_id AND c.channel=a.channel AND c.selected
        LEFT JOIN channel_launch_grant g ON g.connection_id=c.id AND g.brand_id=c.brand_id
        AND g.authorization_generation=c.authorization_generation
        WHERE a.plan_id=%s AND a.brand_id=%s ORDER BY a.channel""",
        (plan_id, brand_id),
    ).fetchall()
    return {
        "plan_id": plan_id,
        "plan_hash": plan["plan_hash"],
        "guardrails": limits,
        "channels": rows,
        "review": manifest,
        "review_hash": digest(
            {
                "creative": manifest,
                "guardrail_version": limits["version"],
                "accounts": [
                    {
                        "channel": row["channel"],
                        "connection_id": (
                            str(row["connection_id"]) if row["connection_id"] else None
                        ),
                        "authorization_generation": row["authorization_generation"],
                    }
                    for row in rows
                ],
            }
        ),
    }


def issue_launch_authorization(
    conn: Connection[Any],
    events: EventRegistry,
    signer: ApprovalSigner,
    brand_id: UUID,
    plan_id: UUID,
    actor_id: UUID,
    data: LaunchApprovalInput,
) -> dict[str, Any]:
    """Sign exactly the first-launch scope the authenticated human reviewed."""
    brand = locked_brand(conn, brand_id)
    seat = require_role(conn, brand_id, {"owner", "admin", "client_approver"})
    previous: Any = conn.execute(
        "SELECT * FROM launch_authorization WHERE brand_id=%s AND request_key=%s",
        (brand_id, data.request_key),
    ).fetchone()
    if previous:
        if (
            previous["plan_id"] != plan_id
            or previous["plan_hash"] != data.expected_hash
            or previous["guardrail_version"] != data.expected_guardrail_version
            or previous["approver_id"] != actor_id
            or previous["claims"].get("review_hash") != data.expected_review_hash
        ):
            raise DomainError(
                "IdempotencyConflict",
                "This approval key belongs to another review.",
                409,
            )
        consumed: Any = conn.execute(
            "SELECT g.connection_id FROM channel_launch_grant g JOIN channel_connection c "
            "ON c.id=g.connection_id AND c.brand_id=g.brand_id "
            "AND c.authorization_generation=g.authorization_generation WHERE authorization_id=%s",
            (previous["id"],),
        ).fetchall()
        if consumed and sorted(str(r["connection_id"]) for r in consumed) == sorted(
            previous["claims"]["connections"]
        ):
            return {"authorization_id": None, "already_authorized": True}
        if conn.execute(
            "SELECT 1 FROM channel_launch_grant WHERE authorization_id=%s",
            (previous["id"],),
        ).fetchone():
            raise DomainError(
                "AuthorizationRevoked",
                "Account consent changed. Review and authorize it again.",
                409,
            )
        return {
            "authorization_id": previous["id"],
            "expires_at": previous["expires_at"],
        }
    generation_gate(conn, brand)
    scope = launch_scope(conn, brand_id, plan_id)
    limits = scope["guardrails"]
    if (
        scope["plan_hash"] != data.expected_hash
        or scope["review_hash"] != data.expected_review_hash
        or limits["version"] != data.expected_guardrail_version
    ):
        raise DomainError(
            "ReviewChanged",
            "The plan or guardrails changed. Review the current values.",
            409,
        )
    plan = one(conn, "SELECT * FROM plan WHERE id=%s", (plan_id,))
    document = check_plan_integrity(plan)
    daily = sum(a.daily_budget_usd or Decimal(0) for a in document.allocations)
    if any(a.daily_budget_usd is None for a in document.allocations):
        raise DomainError("DailyBudgetRequired", "Set a daily budget for every channel.", 422)
    if (
        document.monthly_budget_usd > limits["monthly_spend_cap_usd"]
        or daily > limits["daily_spend_cap_usd"]
    ):
        raise DomainError("GuardrailExceeded", "The plan exceeds the brand spend caps.", 409)
    if (
        seat["approval_daily_usd_cap"] is None
        or seat["approval_total_usd_cap"] is None
        or daily > seat["approval_daily_usd_cap"]
        or document.monthly_budget_usd > seat["approval_total_usd_cap"]
    ):
        raise DomainError(
            "SpendAuthorityExceeded", "The plan exceeds your approval authority.", 403
        )
    for allocation in document.allocations:
        share = allocation.monthly_budget_usd * 100 / limits["monthly_spend_cap_usd"]
        if not limits["min_channel_floor_pct"] <= share <= limits["per_channel_cap_pct"]:
            raise DomainError(
                "ChannelBudgetShare",
                "A channel allocation is outside its guardrail share.",
                409,
            )
    now = datetime.now(UTC).replace(microsecond=0)
    connections = []
    for item in scope["channels"]:
        if (
            not item["connection_id"]
            or item["health"] != "healthy"
            or not item["verified_at"]
            or (item["token_expires_at"] and item["token_expires_at"] <= now)
        ):
            raise DomainError(
                "AccountAuthorizationRequired",
                "Connect and verify every selected ad account before first launch.",
                409,
            )
        if not item["authorized_at"]:
            connections.append(str(item["connection_id"]))
    if not connections:
        return {"authorization_id": None, "already_authorized": True}
    creatives = scope["review"]["creatives"]
    enforce_blocked_claims(conn, brand_id, {"creatives": creatives})
    if not creatives or any(
        not c["studio_renditions"] and not any(r["validated"] for r in c["renditions"])
        for c in creatives
    ):
        raise DomainError(
            "FinishedCreativeRequired",
            "Attach finished, rendered creative before approving first launch.",
            409,
        )
    authorization_id = uuid4()
    expires = now + timedelta(hours=72)
    claims = {
        "purpose": "brand_channel_first_launch",
        "review": scope["review"],
        "review_hash": scope["review_hash"],
        "id": str(authorization_id),
        "brand_id": str(brand_id),
        "plan_id": str(plan_id),
        "plan_hash": data.expected_hash,
        "guardrail_version": limits["version"],
        "approver_id": str(actor_id),
        "connections": sorted(connections),
        "connection_generations": {
            str(item["connection_id"]): item["authorization_generation"]
            for item in scope["channels"]
            if str(item["connection_id"]) in connections
        },
        "request_key": str(data.request_key),
        "operations": ["activate"],
        "usd_daily_cap": str(daily),
        "usd_total_cap": str(document.monthly_budget_usd),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    conn.execute(
        """INSERT INTO launch_authorization(id,brand_id,plan_id,plan_hash,request_key,
        guardrail_version,approver_id,claims,signature,signing_key_id,issued_at,expires_at)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            authorization_id,
            brand_id,
            plan_id,
            data.expected_hash,
            data.request_key,
            limits["version"],
            actor_id,
            Jsonb(claims),
            signer.sign(claims),
            signer.key_id,
            now,
            expires,
        ),
    )
    audit(
        conn,
        events,
        brand_id,
        "plan_approve",
        "launch_authorization",
        authorization_id,
        {
            "connections": connections,
            "plan_id": str(plan_id),
            "plan_hash": data.expected_hash,
            "guardrail_version": limits["version"],
        },
        "First-launch authorization within brand guardrails",
    )
    return {"authorization_id": authorization_id, "expires_at": expires}


def consume_launch_authorization(
    conn: Connection[Any],
    keys: dict[str, bytes],
    brand_id: UUID,
    authorization_id: UUID,
) -> dict[str, Any]:
    """Verify the signature once; grants remain valid for later revisions and campaigns."""
    conn.execute("SELECT lock_runner_brand(%s)", (brand_id,))
    row = one(
        conn,
        "SELECT * FROM launch_authorization WHERE id=%s AND brand_id=%s",
        (authorization_id, brand_id),
    )
    claims = row["claims"]
    expected = {
        "purpose": "brand_channel_first_launch",
        "id": str(row["id"]),
        "brand_id": str(row["brand_id"]),
        "plan_id": str(row["plan_id"]),
        "plan_hash": row["plan_hash"],
        "guardrail_version": row["guardrail_version"],
        "approver_id": str(row["approver_id"]),
        "request_key": str(row["request_key"]),
        "iat": int(row["issued_at"].timestamp()),
        "exp": int(row["expires_at"].timestamp()),
    }
    if any(claims.get(k) != v for k, v in expected.items()) or not isinstance(
        claims.get("connections"), list
    ):
        raise DomainError(
            "InvalidClaims",
            "Launch authorization does not match its signed record.",
            403,
        )
    existing: Any = conn.execute(
        "SELECT connection_id FROM channel_launch_grant WHERE authorization_id=%s AND brand_id=%s",
        (authorization_id, brand_id),
    ).fetchall()
    if existing and sorted(str(r["connection_id"]) for r in existing) == sorted(
        claims["connections"]
    ):
        raise DomainError("LaunchTokenReplay", "This launch token has already been consumed.", 409)
    key = keys.get(row["signing_key_id"])
    if key is None:
        raise DomainError("UnknownSigningKey", "The authorization signing key is not trusted.", 403)
    verify_claims(key, claims, bytes(row["signature"]))
    if claims.get("operations") != ["activate"]:
        raise DomainError("InvalidScope", "The token does not authorize brand activation.", 403)
    plan = one(
        conn,
        "SELECT * FROM plan WHERE id=%s AND brand_id=%s",
        (row["plan_id"], brand_id),
    )
    document = check_plan_integrity(plan)
    if claims.get("usd_daily_cap") != str(
        sum(a.daily_budget_usd for a in document.allocations)
    ) or claims.get("usd_total_cap") != str(document.monthly_budget_usd):
        raise DomainError(
            "CapMismatch", "The plan differs from the token's signed spend caps.", 403
        )
    if not claims["connections"] or len(set(claims["connections"])) != len(claims["connections"]):
        raise DomainError("InvalidScope", "Launch authorization has an invalid account scope.", 403)
    for connection in claims["connections"]:
        conn.execute(
            "INSERT INTO channel_launch_grant(brand_id,connection_id,authorization_id,"
            "authorization_generation) VALUES(%s,%s,%s,%s)",
            (
                brand_id,
                UUID(connection),
                authorization_id,
                claims.get("connection_generations", {}).get(connection, 1),
            ),
        )
    brand = one(conn, "SELECT * FROM brand WHERE id=%s FOR UPDATE", (brand_id,))
    if brand.get("status") != "active":
        conn.execute(
            """UPDATE brand SET status='active', activated_at=now(), campaigns_enabled=true
            WHERE id=%s""",
            (brand_id,),
        )
        action_row: Any = conn.execute(
            """INSERT INTO action(
                brand_id, actor_kind, action_type, target_kind, target_id, diff,
                rationale, revert_path
            )
            VALUES(%s, 'system', 'brand_activate', 'brand', %s, %s, %s, %s) RETURNING id""",
            (
                brand_id,
                brand_id,
                Jsonb(
                    {
                        "before": {
                            "status": brand.get("status", "draft"),
                            "campaigns_enabled": brand["campaigns_enabled"],
                        },
                        "after": {"status": "active", "campaigns_enabled": True},
                    }
                ),
                "Brand activated on first launch authorization",
                Jsonb(
                    {
                        "kind": "brand_status",
                        "brand_id": str(brand_id),
                        "status": brand.get("status", "draft"),
                        "campaigns_enabled": brand["campaigns_enabled"],
                    }
                ),
            ),
        ).fetchone()
        if events is not None and action_row is not None:
            events.append(
                conn,
                "action.recorded",
                brand_id,
                {
                    "brand_id": str(brand_id),
                    "action_id": str(action_row["id"]),
                    "action_type": "brand_activate",
                    "actor_kind": "system",
                    "target_kind": "brand",
                },
            )
    return {"authorization_id": authorization_id, "consumed": True, "replayed": False}
