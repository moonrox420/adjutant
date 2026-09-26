"""Diagnosis engine for autonomous ad loop: fatigue detection, winners, and efficiency shifts."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb


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
    conn: Connection,
    brand_id: UUID,
    kind: str,
    subject_id: UUID,
    signals: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> UUID:
    """Persist a diagnosis finding enforcing the database check constraint on fatigue signals."""
    sig_list = signals or []
    row = conn.execute(
        """INSERT INTO finding(brand_id, kind, signals, subject_id, details)
        VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (brand_id, kind, sig_list, subject_id, Jsonb(details or {})),
    ).fetchone()
    return row["id"]


def diagnose_campaign_objects(
    conn: Connection,
    brand_id: UUID,
) -> list[dict[str, Any]]:
    """Scan active campaign objects for fatigue and winners."""
    findings = []
    objects = conn.execute(
        """SELECT co.id, co.channel, co.native_id, co.state,
                  co.created_at, co.daily_budget_usd
        FROM campaign_object co
        WHERE co.brand_id=%s AND co.state='active'""",
        (brand_id,),
    ).fetchall()

    for obj in objects:
        metrics = conn.execute(
            """SELECT
                sum(impressions) AS total_impressions,
                sum(clicks) AS total_clicks,
                sum(spend_usd) AS total_spend,
                sum(conversions) AS total_conversions,
                avg(frequency) AS avg_freq
            FROM metric_fact_raw
            WHERE brand_id=%s AND campaign_object_id=%s
              AND date_hour >= now() - interval '7 days'""",
            (brand_id, obj["id"]),
        ).fetchone()

        prior_metrics = conn.execute(
            """SELECT
                sum(impressions) AS prior_impressions,
                sum(clicks) AS prior_clicks,
                sum(spend_usd) AS prior_spend,
                sum(conversions) AS prior_conversions
            FROM metric_fact_raw
            WHERE brand_id=%s AND campaign_object_id=%s
              AND date_hour >= now() - interval '14 days'
              AND date_hour < now() - interval '7 days'""",
            (brand_id, obj["id"]),
        ).fetchone()

        if not metrics or not metrics["total_impressions"]:
            continue

        impressions = Decimal(str(metrics["total_impressions"]))
        clicks = Decimal(str(metrics["total_clicks"]))
        spend = Decimal(str(metrics["total_spend"]))
        conversions = Decimal(str(metrics["total_conversions"]))
        frequency = Decimal(str(metrics["avg_freq"] or "1.0"))

        ctr = clicks / impressions if impressions > 0 else Decimal("0.0")
        cpa = spend / conversions if conversions > 0 else Decimal("9999.0")

        prior_imp = Decimal(str(prior_metrics["prior_impressions"] or 0))
        prior_clicks = Decimal(str(prior_metrics["prior_clicks"] or 0))
        prior_spend = Decimal(str(prior_metrics["prior_spend"] or 0))
        prior_conv = Decimal(str(prior_metrics["prior_conversions"] or 0))

        prior_ctr = prior_clicks / prior_imp if prior_imp > 0 else ctr
        prior_cpa = prior_spend / prior_conv if prior_conv > 0 else cpa

        ctr_drop = (prior_ctr - ctr) / prior_ctr if prior_ctr > 0 else Decimal("0.0")
        cpa_rise = (cpa - prior_cpa) / prior_cpa if prior_cpa > 0 else Decimal("0.0")

        created_at = obj.get("created_at")
        half_life_exceeded = False
        if created_at:
            now_dt = conn.execute("SELECT now()").fetchone()["now"]
            half_life_exceeded = bool(created_at < (now_dt - timedelta(days=10)))

        # Evaluate fatigue signals
        signals = FatigueSignals(
            frequency_above_3=bool(frequency > Decimal("3.0")),
            ctr_declining_15pct=bool(ctr_drop >= Decimal("0.15")),
            cpa_rising_20pct=bool(cpa_rise >= Decimal("0.20")),
            impressions_declining_bid_stable=bool(
                impressions < prior_imp and prior_imp > 0
            ),
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

    return findings
