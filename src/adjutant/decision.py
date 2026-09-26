"""Decision generation, guardrail evaluation, clamping, and scope-bounded escalations."""

from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from adjutant.db import one
from adjutant.errors import DomainError


def record_escalation(
    conn: Connection[Any],
    brand_id: UUID,
    trigger_type: str,
    scope_kind: str,
    scope_id: UUID | None = None,
    context: dict[str, Any] | None = None,
) -> UUID:
    """Record an escalation halting autonomous action on the affected scope only."""
    row: Any = conn.execute(
        """INSERT INTO escalation(brand_id, trigger_type, scope_kind, scope_id, context, state)
        VALUES (%s, %s, %s, %s, %s, 'open') RETURNING id""",
        (brand_id, trigger_type, scope_kind, scope_id, Jsonb(context or {})),
    ).fetchone()
    if row is None:
        raise DomainError("DatabaseError", "Failed to record escalation.", 500)
    return row["id"]


def is_scope_halted(
    conn: Connection[Any],
    brand_id: UUID,
    channel: str | None = None,
    object_id: UUID | None = None,
) -> bool:
    """Check if open escalations halt autonomous actions on this specific scope."""
    # Brand-level open escalation halts the brand
    brand_halt = conn.execute(
        "SELECT 1 FROM escalation WHERE brand_id=%s AND state='open' AND scope_kind='brand'",
        (brand_id,),
    ).fetchone()
    if brand_halt:
        return True

    # Object-level open escalation halts this object only
    if object_id:
        obj_halt = conn.execute(
            """SELECT 1 FROM escalation
            WHERE brand_id=%s AND state='open' AND scope_kind='campaign_object' AND scope_id=%s""",
            (brand_id, object_id),
        ).fetchone()
        if obj_halt:
            return True

    return False


def evaluate_guardrails(
    conn: Connection[Any],
    brand_id: UUID,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Verify candidate decision against guardrails.
    Clamp budget increases where allowed; reject hard breaches.
    """
    limits = one(conn, "SELECT * FROM guardrail WHERE brand_id=%s", (brand_id,))
    kind = candidate["kind"]
    channel = candidate["channel"]
    target_id = candidate["target_id"]
    params = dict(candidate.get("params", {}))

    # Check scope-level halt first (S8.5)
    if is_scope_halted(conn, brand_id, channel, target_id):
        return {
            "approved": False,
            "state": "escalated",
            "reason": f"Scope {target_id} has an active escalation halting autonomous actions.",
            "params": params,
        }

    # S8.6: Comparability class verification for budget moves
    if kind == "reallocate_budget":
        source_comparability = params.get("source_comparability")
        target_comparability = params.get("target_comparability")
        if (
            source_comparability
            and target_comparability
            and source_comparability != target_comparability
        ):
            return {
                "approved": False,
                "state": "rejected",
                "reason": (
                    "MismatchedComparabilityClass: Budget cannot move between objects with "
                    f"mismatched comparability classes ({source_comparability} != "
                    f"{target_comparability})."
                ),
                "params": params,
            }

    # Hard guardrail: daily_spend_cap_usd
    daily_cap = Decimal(str(limits["daily_spend_cap_usd"]))
    raw_proposed = Decimal(str(params.get("proposed_daily_usd", "0.00")))
    raw_current = Decimal(str(params.get("current_daily_usd", "0.00")))
    if kind in {"scale_winner", "reallocate_budget"}:
        if raw_proposed > daily_cap:
            return {
                "approved": False,
                "state": "rejected",
                "reason": f"Breaches daily_spend_cap_usd of {daily_cap}.",
                "params": params,
            }
        total_row: Any = conn.execute(
            "SELECT COALESCE(sum(daily_budget_usd), 0) AS total FROM campaign_object "
            "WHERE brand_id=%s AND state='active' AND level IN ('campaign', 'ad')",
            (brand_id,),
        ).fetchone()
        current_total_daily = Decimal(str(total_row["total"])) if total_row else Decimal("0.00")
        delta = raw_proposed - raw_current
        if kind == "reallocate_budget" and params.get("source_campaign_id"):
            # Reallocation moves budget between campaigns; net addition to total account spend is 0
            delta = Decimal("0.00")
        if (
            current_total_daily + delta > daily_cap
            and raw_proposed > raw_current
            and not params.get("source_campaign_id")
        ):
            return {
                "approved": False,
                "state": "rejected",
                "reason": f"Breaches daily_spend_cap_usd of {daily_cap}.",
                "params": params,
            }

    # S8.7: Clamping rule for daily spend increase
    if kind in {"scale_winner", "reallocate_budget"}:
        current_daily = Decimal(str(params.get("current_daily_usd", "0.00")))
        proposed_daily = Decimal(str(params.get("proposed_daily_usd", "0.00")))
        if current_daily > 0 and proposed_daily > current_daily:
            increase_pct = (proposed_daily - current_daily) / current_daily * Decimal("100.0")
            max_increase_pct = Decimal(str(limits["max_daily_spend_increase_pct"]))
            if increase_pct > max_increase_pct:
                # Clamp rather than reject
                clamped_daily = current_daily * (
                    Decimal("1.0") + max_increase_pct / Decimal("100.0")
                )
                params["clamped"] = True
                params["original_proposed_daily_usd"] = str(proposed_daily)
                params["clamp_logged_reason"] = (
                    f"Clamped spend increase from {increase_pct:.1f}% to allowed limit "
                    f"of {float(max_increase_pct):.1f}%."
                )
                params["proposed_daily_usd"] = str(clamped_daily.quantize(Decimal("0.01")))
                proposed_daily = clamped_daily

    # Hard guardrail: requires_approval_above_usd
    req_above = limits.get("requires_approval_above_usd")
    if req_above is not None:
        proposed_delta = Decimal(str(params.get("proposed_daily_usd", "0.00"))) - Decimal(
            str(params.get("current_daily_usd", "0.00"))
        )
        if proposed_delta > Decimal(str(req_above)):
            esc_id = record_escalation(
                conn,
                brand_id,
                "guardrail_breach",
                "campaign_object",
                target_id,
                {
                    "reason": "Budget increase exceeds requires_approval_above_usd",
                    "delta": float(proposed_delta),
                },
            )
            return {
                "approved": False,
                "state": "escalated",
                "escalation_id": esc_id,
                "reason": (
                    f"Budget delta {proposed_delta} exceeds threshold {req_above}; "
                    "escalated for approval."
                ),
                "params": params,
            }

    # Hard guardrail: max_new_ads_per_day
    if kind == "refresh_creative":
        ads_row: Any = conn.execute(
            """SELECT count(*) AS n FROM campaign_object
            WHERE brand_id=%s AND level='ad' AND created_at >= now() - interval '24 hours'""",
            (brand_id,),
        ).fetchone()
        ads_today = ads_row["n"] if ads_row else 0
        if ads_today >= limits["max_new_ads_per_day"]:
            return {
                "approved": False,
                "state": "rejected",
                "reason": f"Breaches max_new_ads_per_day limit of {limits['max_new_ads_per_day']}.",
                "params": params,
            }

    # Hard guardrail: blocked_claims
    blocked = limits.get("blocked_claims") or []
    proposed_copy = params.get("proposed_copy", "")
    if any(phrase.lower() in proposed_copy.lower() for phrase in blocked):
        return {
            "approved": False,
            "state": "rejected",
            "reason": "Proposed copy contains a blocked claim.",
            "params": params,
        }

    return {
        "approved": True,
        "state": "executed",
        "reason": None,
        "params": params,
    }
