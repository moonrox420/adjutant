"""Weekly automated executive result summaries for business owners and media buyers."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection

from adjutant.db import one


def generate_weekly_result_summary(
    conn: Connection,
    brand_id: UUID,
    end_date: datetime | None = None,
) -> dict[str, Any]:
    """S14.4: Generate an automated plain-English weekly result summary
    comprehensible to non-technical business owners.
    """
    end = (end_date or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=7)
    prior_start = start - timedelta(days=7)

    brand = one(
        conn, "SELECT display_name, account_id FROM brand WHERE id=%s", (brand_id,)
    )

    # Current 7 days metrics
    cur_metrics = conn.execute(
        """SELECT
            COALESCE(sum(spend_usd), 0) AS spend,
            COALESCE(sum(conversions), 0) AS conversions,
            COALESCE(sum(conversion_value_usd), 0) AS revenue,
            COALESCE(sum(clicks), 0) AS clicks,
            COALESCE(sum(impressions), 0) AS impressions
        FROM metric_fact_raw
        WHERE brand_id=%s AND date_hour >= %s AND date_hour < %s""",
        (brand_id, start, end),
    ).fetchone()

    # Prior 7 days metrics
    prior_metrics = conn.execute(
        """SELECT
            COALESCE(sum(spend_usd), 0) AS spend,
            COALESCE(sum(conversions), 0) AS conversions,
            COALESCE(sum(conversion_value_usd), 0) AS revenue
        FROM metric_fact_raw
        WHERE brand_id=%s AND date_hour >= %s AND date_hour < %s""",
        (brand_id, prior_start, start),
    ).fetchone()

    spend = Decimal(str(cur_metrics["spend"]))
    convs = Decimal(str(cur_metrics["conversions"]))
    revenue = Decimal(str(cur_metrics["revenue"]))
    cpa = (spend / convs) if convs > 0 else Decimal("0.00")
    roas = (revenue / spend) if spend > 0 else Decimal("0.00")

    prior_spend = Decimal(str(prior_metrics["spend"]))
    prior_convs = Decimal(str(prior_metrics["conversions"]))
    prior_cpa = (prior_spend / prior_convs) if prior_convs > 0 else Decimal("0.00")

    cpa_change_pct = (
        ((cpa - prior_cpa) / prior_cpa * Decimal("100.0"))
        if prior_cpa > 0
        else Decimal("0.00")
    )

    # Autonomous actions taken in this window
    actions = conn.execute(
        """SELECT action_type, count(*) AS count FROM action
        WHERE brand_id=%s AND executed_at >= %s AND executed_at < %s
        GROUP BY action_type""",
        (brand_id, start, end),
    ).fetchall()
    action_counts = {a["action_type"]: a["count"] for a in actions}

    refreshes = action_counts.get("creative_swap", 0)
    budget_shifts = action_counts.get("budget_set", 0)
    pauses = action_counts.get("pause", 0)

    # Top performing channel
    channel_perf = conn.execute(
        """SELECT channel, COALESCE(sum(spend_usd), 0) AS spend,
        COALESCE(sum(conversions), 0) AS convs
        FROM metric_fact_raw
        WHERE brand_id=%s AND date_hour >= %s AND date_hour < %s
        GROUP BY channel
        ORDER BY convs DESC LIMIT 1""",
        (brand_id, start, end),
    ).fetchone()

    top_channel = channel_perf["channel"] if channel_perf else "None"

    # Human-readable plain English narrative
    headline = (
        f"Your ads generated {int(convs)} conversions this week at an average cost "
        f"of ${cpa:.2f} per conversion."
    )
    if cpa_change_pct < -5:
        trend_note = (
            f"Efficiency improved: CPA dropped by {abs(float(cpa_change_pct)):.1f}% "
            "compared to last week."
        )
    elif cpa_change_pct > 5:
        trend_note = (
            f"CPA increased by {float(cpa_change_pct):.1f}% compared to last week. "
            "The optimizer is reallocating budget toward top performers."
        )
    else:
        trend_note = "Performance remained stable and consistent week-over-week."

    autonomous_work_summary = (
        f"Adjutant worked in the background this week: refreshed {refreshes} worn-out ads, "
        f"adjusted budget {budget_shifts} times toward high-converting placements, "
        f"and paused {pauses} inefficient ads."
    )

    return {
        "brand_id": str(brand_id),
        "brand_name": brand["display_name"],
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "headline": headline,
        "trend_summary": trend_note,
        "autonomous_work_summary": autonomous_work_summary,
        "key_metrics": {
            "total_spend_usd": str(spend.quantize(Decimal("0.01"))),
            "conversions": int(convs),
            "cost_per_acquisition_usd": str(cpa.quantize(Decimal("0.01"))),
            "return_on_ad_spend": f"{float(roas):.2f}x" if revenue > 0 else "N/A",
            "top_converting_channel": top_channel,
        },
        "autonomous_actions_count": {
            "creative_refreshes": refreshes,
            "budget_adjustments": budget_shifts,
            "underperformer_pauses": pauses,
            "total_actions": sum(action_counts.values()),
        },
    }
