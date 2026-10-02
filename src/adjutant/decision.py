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

        # Mandatory 72-hour reallocation cooldown
        recent_realloc = conn.execute(
            """SELECT executed_at FROM action
            WHERE brand_id=%s AND action_type='budget_reallocation'
            AND executed_at > now() - interval '72 hours'""",
            (brand_id,),
        ).fetchone()
        if recent_realloc:
            return {
                "approved": False,
                "state": "rejected",
                "reason": (
                    "ReallocationInCooldown: A mandatory 72-hour cooldown must elapse "
                    "between cross-channel budget reallocations."
                ),
                "params": params,
            }

    # Hard guardrail: monthly_spend_cap_usd (PRD §3.2)
    monthly_cap = Decimal(str(limits["monthly_spend_cap_usd"]))
    month_spend_row: Any = conn.execute(
        """SELECT COALESCE(sum(spend_usd), 0) AS total_spend FROM metric_fact_raw
        WHERE brand_id=%s AND date_hour >= date_trunc('month', now())""",
        (brand_id,),
    ).fetchone()
    current_month_spend = (
        Decimal(str(month_spend_row["total_spend"])) if month_spend_row else Decimal("0.00")
    )
    if current_month_spend >= monthly_cap:
        return {
            "approved": False,
            "state": "rejected",
            "reason": (
                f"Breaches monthly_spend_cap_usd of {monthly_cap} "
                f"(current month spend: {current_month_spend})."
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

    # Hard guardrails: per_channel_cap_pct & min_channel_floor_pct (PRD §3.2)
    if kind == "reallocate_budget":
        per_channel_cap = Decimal(str(limits.get("per_channel_cap_pct") or 60))
        min_channel_floor = Decimal(str(limits.get("min_channel_floor_pct") or 0))
        target_channel = params.get("target_channel") or channel
        source_channel = params.get("source_channel")
        shift_amount = Decimal(str(params.get("shift_amount_usd", "0.00")))

        channel_spends: Any = conn.execute(
            """SELECT channel, COALESCE(sum(daily_budget_usd), 0) AS channel_daily
            FROM campaign_object
            WHERE brand_id=%s AND state='active' AND level IN ('campaign', 'ad')
            GROUP BY channel""",
            (brand_id,),
        ).fetchall()
        channel_map = {row["channel"]: Decimal(str(row["channel_daily"])) for row in channel_spends}
        total_active_daily = sum(channel_map.values(), Decimal("0.00"))

        if total_active_daily > 0:
            if source_channel and min_channel_floor > 0:
                cur_src = channel_map.get(source_channel, Decimal("0.00"))
                new_src = cur_src - shift_amount
                new_src_pct = (new_src / total_active_daily) * Decimal("100.0")
                if new_src_pct < min_channel_floor:
                    return {
                        "approved": False,
                        "state": "rejected",
                        "reason": (
                            f"Breaches min_channel_floor_pct of {min_channel_floor}% for channel "
                            f"'{source_channel}' (projected share: {new_src_pct:.1f}%)."
                        ),
                        "params": params,
                    }

            cur_tgt = channel_map.get(target_channel, Decimal("0.00"))
            new_tgt = cur_tgt + shift_amount
            new_tgt_pct = (new_tgt / total_active_daily) * Decimal("100.0")
            if new_tgt_pct > per_channel_cap:
                esc_id = record_escalation(
                    conn,
                    brand_id,
                    "guardrail_breach",
                    "channel",
                    None,
                    {
                        "reason": f"Channel '{target_channel}' would exceed per_channel_cap_pct",
                        "channel": target_channel,
                        "projected_pct": float(new_tgt_pct),
                        "cap_pct": float(per_channel_cap),
                    },
                )
                return {
                    "approved": False,
                    "state": "escalated",
                    "escalation_id": esc_id,
                    "reason": (
                        f"Reallocation would breach per_channel_cap_pct ({per_channel_cap}%) "
                        f"for channel '{target_channel}' ({new_tgt_pct:.1f}% projected); escalated."
                    ),
                    "params": params,
                }

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

    # Hard guardrail: max_new_campaigns_per_day (PRD §3.2)
    if kind in {"create_campaign", "new_campaign"}:
        camps_row: Any = conn.execute(
            """SELECT count(*) AS n FROM campaign_object
            WHERE brand_id=%s AND level='campaign' AND created_at >= now() - interval '24 hours'""",
            (brand_id,),
        ).fetchone()
        camps_today = camps_row["n"] if camps_row else 0
        max_camps = limits.get("max_new_campaigns_per_day", 3)
        if camps_today >= max_camps:
            return {
                "approved": False,
                "state": "rejected",
                "reason": f"Breaches max_new_campaigns_per_day limit of {max_camps}.",
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
