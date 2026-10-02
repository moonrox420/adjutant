"""Diagnosis engine for autonomous ad loop: fatigue detection, winners, and efficiency shifts."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from adjutant.errors import DomainError


@dataclass(frozen=True)
class FatigueSignals:
    frequency_above_3: bool
    ctr_declining_15pct: bool
    cpa_rising_20pct: bool
    impressions_declining_bid_stable: bool
    half_life_exceeded: bool

    def active_signals(self) -> list[str]:
        signals = []
        if self.frequency_above_3:
            signals.append("frequency_above_3")
        if self.ctr_declining_15pct:
            signals.append("ctr_declining_15pct")
        if self.cpa_rising_20pct:
            signals.append("cpa_rising_20pct")
        if self.impressions_declining_bid_stable:
            signals.append("impressions_declining_bid_stable")
        if self.half_life_exceeded:
            signals.append("half_life_exceeded")
        return signals


def detect_fatigue(
    signals: FatigueSignals,
) -> list[str] | None:
    """The 3-signal rule: an ad is fatigued ONLY when at least three signals fire simultaneously."""
    active = signals.active_signals()
    if len(active) >= 3:
        return active
    return None


def record_finding(
    conn: Connection[Any],
    brand_id: UUID,
    kind: str,
    subject_id: UUID,
    signals: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> UUID:
    """Persist a diagnosis finding enforcing the database check constraint on fatigue signals."""
    sig_list = signals or []
    row: Any = conn.execute(
        """INSERT INTO finding(brand_id, kind, signals, subject_id, details)
        VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (brand_id, kind, sig_list, subject_id, Jsonb(details or {})),
    ).fetchone()
    if row is None:
        raise DomainError("DatabaseError", "Failed to record finding.", 500)
    return row["id"]


def diagnose_campaign_objects(
    conn: Connection[Any],
    brand_id: UUID,
) -> list[dict[str, Any]]:
    """Scan active campaign objects for fatigue and winners."""
    findings = []
    objects: Any = conn.execute(
        """SELECT co.id, co.channel, co.native_id, co.state,
                  co.created_at, co.daily_budget_usd
        FROM campaign_object co
        WHERE co.brand_id=%s AND co.state='active'""",
        (brand_id,),
    ).fetchall()

    if not objects:
        return []

    agg_rows: Any = conn.execute(
        """SELECT
            campaign_object_id,
            COALESCE(sum(CASE
                WHEN date_hour >= now() - interval '7 days'
                THEN impressions ELSE 0 END), 0) AS total_impressions,
            COALESCE(sum(CASE
                WHEN date_hour >= now() - interval '7 days'
                THEN clicks ELSE 0 END), 0) AS total_clicks,
            COALESCE(sum(CASE
                WHEN date_hour >= now() - interval '7 days'
                THEN spend_usd ELSE 0 END), 0) AS total_spend,
            COALESCE(sum(CASE
                WHEN date_hour >= now() - interval '7 days'
                THEN conversions ELSE 0 END), 0) AS total_conversions,
            avg(CASE
                WHEN date_hour >= now() - interval '7 days'
                THEN frequency ELSE NULL END) AS avg_freq,
            COALESCE(sum(CASE
                WHEN date_hour >= now() - interval '14 days'
                     AND date_hour < now() - interval '7 days'
                THEN impressions ELSE 0 END), 0) AS prior_impressions,
            COALESCE(sum(CASE
                WHEN date_hour >= now() - interval '14 days'
                     AND date_hour < now() - interval '7 days'
                THEN clicks ELSE 0 END), 0) AS prior_clicks,
            COALESCE(sum(CASE
                WHEN date_hour >= now() - interval '14 days'
                     AND date_hour < now() - interval '7 days'
                THEN spend_usd ELSE 0 END), 0) AS prior_spend,
            COALESCE(sum(CASE
                WHEN date_hour >= now() - interval '14 days'
                     AND date_hour < now() - interval '7 days'
                THEN conversions ELSE 0 END), 0) AS prior_conversions
        FROM metric_fact_raw
        WHERE brand_id=%s AND date_hour >= now() - interval '14 days'
        GROUP BY campaign_object_id""",
        (brand_id,),
    ).fetchall()

    metrics_by_obj: dict[UUID, dict[str, Any]] = {
        row["campaign_object_id"]: dict(row) for row in agg_rows
    }

    for obj in objects:
        m = metrics_by_obj.get(obj["id"])
        if not m or not m["total_impressions"]:
            continue

        impressions = Decimal(str(m["total_impressions"]))
        clicks = Decimal(str(m["total_clicks"]))
        spend = Decimal(str(m["total_spend"]))
        conversions = Decimal(str(m["total_conversions"]))
        frequency = Decimal(str(m["avg_freq"] or "1.0"))

        ctr = clicks / impressions if impressions > 0 else Decimal("0.0")
        cpa = spend / conversions if conversions > 0 else Decimal("9999.0")

        prior_imp = Decimal(str(m["prior_impressions"] or 0))
        prior_clicks = Decimal(str(m["prior_clicks"] or 0))
        prior_spend = Decimal(str(m["prior_spend"] or 0))
        prior_conv = Decimal(str(m["prior_conversions"] or 0))

        prior_ctr = prior_clicks / prior_imp if prior_imp > 0 else ctr
        prior_cpa = prior_spend / prior_conv if prior_conv > 0 else cpa

        ctr_drop = (prior_ctr - ctr) / prior_ctr if prior_ctr > 0 else Decimal("0.0")
        cpa_rise = (cpa - prior_cpa) / prior_cpa if prior_cpa > 0 else Decimal("0.0")

        created_at = obj.get("created_at")
        half_life_exceeded = False
        if created_at:
            now_row: Any = conn.execute("SELECT now()").fetchone()
            now_dt = now_row["now"] if now_row else None
            if now_dt:
                half_life_exceeded = created_at < (now_dt - timedelta(days=10))

        # Evaluate fatigue signals
        signals = FatigueSignals(
            frequency_above_3=frequency > Decimal("3.0"),
            ctr_declining_15pct=ctr_drop >= Decimal("0.15"),
            cpa_rising_20pct=cpa_rise >= Decimal("0.20"),
            impressions_declining_bid_stable=(impressions < prior_imp and prior_imp > 0),
            half_life_exceeded=half_life_exceeded,
        )

        active_signals = detect_fatigue(signals)
        if active_signals:
            finding_id = record_finding(
                conn,
                brand_id,
                "fatigue",
                obj["id"],
                active_signals,
                {
                    "frequency": float(frequency),
                    "ctr_drop_pct": float(ctr_drop * 100),
                    "cpa_rise_pct": float(cpa_rise * 100),
                    "channel": obj["channel"],
                },
            )
            findings.append(
                {
                    "finding_id": finding_id,
                    "kind": "fatigue",
                    "object_id": obj["id"],
                    "channel": obj["channel"],
                    "signals": active_signals,
                }
            )

        # Evaluate winner detection
        # Cleared minimum conversion volume (>= 15 conversions) and CPA sitting well below target
        if conversions >= Decimal("15.0") and cpa < Decimal("50.0"):
            finding_id = record_finding(
                conn,
                brand_id,
                "winner",
                obj["id"],
                [],
                {
                    "conversions": float(conversions),
                    "cpa": float(cpa),
                    "channel": obj["channel"],
                },
            )
            findings.append(
                {
                    "finding_id": finding_id,
                    "kind": "winner",
                    "object_id": obj["id"],
                    "channel": obj["channel"],
                    "cpa": float(cpa),
                }
            )

    # Evaluate cross-channel reallocation opportunities
    campaigns: Any = conn.execute(
        """SELECT
            co.id, co.channel, co.daily_budget_usd,
            COALESCE(sum(m.spend_usd), 0) AS total_spend,
            COALESCE(sum(m.conversions), 0) AS total_conv
        FROM campaign_object co
        LEFT JOIN metric_fact_raw m ON m.campaign_object_id=co.id
          AND m.date_hour >= now() - interval '7 days'
        WHERE co.brand_id=%s AND co.level='campaign' AND co.state='active'
        GROUP BY co.id, co.channel, co.daily_budget_usd
        HAVING sum(m.conversions) > 0 AND sum(m.spend_usd) > 50.00""",
        (brand_id,),
    ).fetchall()

    if len(campaigns) >= 2:
        cooldown_row = conn.execute(
            """SELECT 1 FROM action
            WHERE brand_id=%s AND action_type='budget_reallocation'
              AND executed_at > now() - interval '72 hours'""",
            (brand_id,),
        ).fetchone()

        if not cooldown_row:
            evaluated = []
            for c in campaigns:
                sp = Decimal(str(c["total_spend"]))
                cv = Decimal(str(c["total_conv"]))
                cpa_val = sp / cv if cv > 0 else Decimal("99999.0")
                comp_row: Any = conn.execute(
                    """SELECT comparability FROM metric_normalized
                    WHERE campaign_object_id=%s ORDER BY date_hour DESC LIMIT 1""",
                    (c["id"],),
                ).fetchone()
                comp_class = comp_row["comparability"] if comp_row else "direct"
                evaluated.append({**c, "cpa": cpa_val, "comparability": comp_class})

            best = min(evaluated, key=lambda x: x["cpa"])
            worst = max(evaluated, key=lambda x: x["cpa"])

            if (
                best["id"] != worst["id"]
                and best["comparability"] == worst["comparability"]
                and best["cpa"] <= worst["cpa"] * Decimal("0.70")
            ):
                finding_id = record_finding(
                    conn,
                    brand_id,
                    "inefficiency",
                    worst["id"],
                    [],
                    {
                        "source_campaign_id": str(worst["id"]),
                        "target_campaign_id": str(best["id"]),
                        "source_channel": worst["channel"],
                        "target_channel": best["channel"],
                        "source_cpa": float(worst["cpa"]),
                        "target_cpa": float(best["cpa"]),
                        "source_comparability": worst["comparability"],
                        "target_comparability": best["comparability"],
                    },
                )
                findings.append(
                    {
                        "finding_id": finding_id,
                        "kind": "inefficiency",
                        "object_id": worst["id"],
                        "channel": worst["channel"],
                        "details": {
                            "source_campaign_id": str(worst["id"]),
                            "target_campaign_id": str(best["id"]),
                            "source_channel": worst["channel"],
                            "target_channel": best["channel"],
                            "source_cpa": float(worst["cpa"]),
                            "target_cpa": float(best["cpa"]),
                            "source_comparability": worst["comparability"],
                            "target_comparability": best["comparability"],
                        },
                    }
                )

    return findings
