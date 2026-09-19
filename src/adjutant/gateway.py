"""Spend authority validation shared by gateway preflight and reservation boundaries."""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from psycopg import Connection
from pydantic import BaseModel, ConfigDict, Field

from adjutant.db import one
from adjutant.errors import DomainError
from adjutant.models import Channel
from adjutant.security import digest, verify_claims

logger = logging.getLogger(__name__)
Amount = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]


class SpendIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    brand_id: UUID
    token_id: UUID
    subject_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    channel: Channel
    operation: Literal[
        "create", "budget_set", "bid_set", "targeting_change", "creative_swap", "resume"
    ]
    daily_usd: Amount
    total_usd: Amount
    payload: dict[str, Any]

    @property
    def idempotency_key(self) -> str:
        """Bind retries to the complete immutable intent, including exact decimal amounts."""
        return digest(self.model_dump(mode="json"))


class SpendAuthority:
    """Validate public-key signatures against authoritative SQL state and cumulative caps."""

    def __init__(self, trusted_keys: dict[str, bytes]) -> None:
        if not trusted_keys or any(len(key) != 32 for key in trusted_keys.values()):
            raise ValueError("A nonempty trusted Ed25519 public-key ring is required")
        self.trusted_keys = dict(trusted_keys)

    def validate(self, conn: Connection, intent: SpendIntent) -> dict[str, Any]:
        """Lock brand before token, matching stop operations and serializing authority changes."""
        conn.execute("SELECT lock_spend_authority(%s,%s)", (intent.brand_id, intent.token_id))
        brand = one(conn, "SELECT * FROM brand WHERE id=%s", (intent.brand_id,))
        token = one(
            conn,
            "SELECT * FROM approval_token WHERE id=%s AND brand_id=%s",
            (intent.token_id, intent.brand_id),
        )
        key = self.trusted_keys.get(token["signing_key_id"])
        if key is None:
            raise DomainError("UnknownSigningKey", "Approval uses an untrusted signing key.", 403)
        claims = token["signed_claims"]
        if not isinstance(claims, dict):
            raise DomainError("InvalidSignature", "Approval has no verifiable signed claims.", 403)
        verify_claims(key, claims, bytes(token["signature"]))
        expected = {
            "tok": str(token["id"]),
            "brand_id": str(token["brand_id"]),
            "sub_type": token["subject_type"],
            "sub_id": str(token["subject_id"]),
            "sub_hash": token["subject_hash"],
            "scopes": token["scopes"],
            "usd_daily_cap": str(token["usd_daily_cap"]),
            "usd_total_cap": str(token["usd_total_cap"]),
            "nonce": token["nonce"],
            "approver_id": str(token["approver_id"]),
            "approval_chain": list(map(str, token["approval_chain"])),
        }
        if any(claims.get(name) != value for name, value in expected.items()):
            raise DomainError(
                "ClaimMismatch", "Stored authority does not match its signature.", 403
            )
        if token["voided_at"] or token["expires_at"] <= datetime.now(UTC):
            raise DomainError("TokenExpired", "Approval expired or was voided.", 403)
        if int(token["expires_at"].timestamp()) != claims["exp"]:
            raise DomainError("ClaimMismatch", "Approval expiry does not match its signature.", 403)
        if int(token["issued_at"].timestamp()) != claims["iat"] or token["subject_type"] != "plan":
            raise DomainError("ClaimMismatch", "Approval issuance or subject type is invalid.", 403)
        if intent.subject_hash != claims["sub_hash"]:
            raise DomainError("SubjectChanged", "The requested revision is not approved.", 403)
        if (
            f"channel:{intent.channel}" not in claims["scopes"]
            or f"op:{intent.operation}" not in claims["scopes"]
        ):
            raise DomainError(
                "ScopeDenied", "Approval does not authorize this channel operation.", 403
            )
        plan = one(
            conn,
            "SELECT * FROM plan WHERE id=%s AND brand_id=%s",
            (token["subject_id"], intent.brand_id),
        )
        if (
            plan["plan_hash"] != intent.subject_hash
            or digest(plan["plan_document"]) != intent.subject_hash
        ):
            raise DomainError("SubjectChanged", "Approved plan content has changed.", 403)
        if plan["state"] not in {"approved", "deploying", "live"}:
            raise DomainError("ApprovalRequired", "This plan is not approved for deployment.", 403)
        request = one(
            conn, "SELECT state FROM approval_request WHERE id=%s", (token["approval_request_id"],)
        )
        if request["state"] != "approved":
            raise DomainError("ApprovalRequired", "Approval is no longer valid.", 403)
        if (
            not brand["brand_graph_confirmed_at"]
            or not brand["campaigns_enabled"]
            or brand["restricted_flags"]
        ):
            raise DomainError("BrandNotReady", "The brand is not cleared for campaigns.", 403)
        if conn.execute(
            "SELECT 1 FROM brand_kill_switch WHERE brand_id=%s AND released_at IS NULL",
            (intent.brand_id,),
        ).fetchone():
            raise DomainError("KillSwitchActive", "This brand is stopped.", 403)
        allocation = one(
            conn,
            "SELECT * FROM plan_allocation WHERE plan_id=%s AND channel=%s",
            (plan["id"], intent.channel),
        )
        if (
            allocation["daily_budget_usd"] is None
            or intent.daily_usd > allocation["daily_budget_usd"]
            or intent.total_usd > allocation["monthly_budget_usd"]
        ):
            raise DomainError(
                "AllocationExceeded", "The operation exceeds its approved channel allocation.", 403
            )
        if conn.execute(
            """SELECT 1 FROM approval_token_consumption
            WHERE token_id=%s AND channel=%s AND operation=%s""",
            (token["id"], intent.channel, intent.operation),
        ).fetchone():
            logger.error(
                "Spend replay denied: brand_id=%s token_id=%s", intent.brand_id, intent.token_id
            )
            raise DomainError(
                "ReplayDenied", "This channel operation has already consumed its approval.", 409
            )
        totals = one(
            conn,
            """SELECT COALESCE(sum(usd_committed),0) AS total,
            COALESCE(sum(usd_daily_committed),0) AS daily
            FROM approval_token_consumption WHERE token_id=%s""",
            (token["id"],),
        )
        if (
            totals["total"] + intent.total_usd > token["usd_total_cap"]
            or totals["daily"] + intent.daily_usd > token["usd_daily_cap"]
        ):
            raise DomainError(
                "CapExceeded", "Cumulative commitments exceed the signed approval cap.", 403
            )
        ceilings = conn.execute(
            """SELECT * FROM budget_ceiling WHERE brand_id=%s
            AND (scope_kind='brand' OR (scope_kind='channel' AND scope_ref=%s))""",
            (intent.brand_id, intent.channel),
        ).fetchall()
        if not any(ceiling["scope_kind"] == "brand" for ceiling in ceilings):
            raise DomainError(
                "BudgetCeilingRequired", "A current brand budget ceiling is required.", 403
            )
        for ceiling in ceilings:
            committed = one(
                conn,
                """SELECT COALESCE(sum(c.usd_committed),0) AS total,
                COALESCE(sum(c.usd_daily_committed),0) AS daily
                FROM approval_token_consumption c JOIN approval_token t ON t.id=c.token_id
                WHERE t.brand_id=%s AND (%s='brand' OR c.channel::text=%s)""",
                (intent.brand_id, ceiling["scope_kind"], intent.channel),
            )
            if (
                ceiling["daily_usd_max"] is None
                or committed["daily"] + intent.daily_usd > ceiling["daily_usd_max"]
                or committed["total"] + intent.total_usd > ceiling["monthly_usd_max"]
            ):
                raise DomainError(
                    "BudgetCeilingExceeded",
                    "The current budget ceiling blocks this operation.",
                    403,
                )
        return {
            "valid": True,
            "token_id": str(token["id"]),
            "subject_id": str(token["subject_id"]),
            "expires_at": token["expires_at"],
            "idempotency_key": intent.idempotency_key,
        }

    def reserve(self, conn: Connection, intent: SpendIntent) -> dict[str, Any]:
        """Reserve in the caller's transaction; replays never allocate another commitment."""
        result = self.validate(conn, intent)
        row = one(
            conn,
            """INSERT INTO approval_token_consumption(token_id,channel,operation,idem_key,
            usd_committed,usd_daily_committed,subject_hash,payload_hash)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,consumed_at""",
            (
                intent.token_id,
                intent.channel,
                intent.operation,
                intent.idempotency_key,
                intent.total_usd,
                intent.daily_usd,
                intent.subject_hash,
                digest(intent.payload),
            ),
        )
        return {**result, "reservation_id": str(row["id"]), "reserved_at": row["consumed_at"]}
