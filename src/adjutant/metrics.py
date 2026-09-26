"""Measurement, attribution normalization, and strict comparability class enforcement."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from adjutant.errors import DomainError


@dataclass(frozen=True)
class RawMetricFact:
    brand_id: UUID
    campaign_object_id: UUID
    channel: str
    date_hour: datetime
    impressions: int
    clicks: int
    spend_usd: Decimal
    conversions: Decimal
    conversion_value_usd: Decimal
    frequency: Decimal | None = None
    reach: int | None = None
    video_views: int | None = None
    video_completions: int | None = None
    engagements: int | None = None
    native_metrics: dict[str, Any] | None = None
    attribution_window: str = "7d_click"
    attribution_model: str = "last_click"
    conversion_event: str = "purchase"
    view_through_policy: str = "none"


def determine_comparability_class(
    attribution_window: str,
    view_through_policy: str,
    source: str = "channel",
) -> str:
    """Determine comparability class according to measurement methodology."""
    if source == "first_party":
        return "first_party_only"
    if view_through_policy != "none" or "view" in attribution_window.lower():
        return "caveated"
    if attribution_window in {"7d_click", "1d_click", "30d_click"}:
        return "direct"
    return "caveated"


def get_channel_watermark(
    conn: Connection[Any], brand_id: UUID, channel: str
) -> datetime | None:
    """Read the latest metric sync watermark for a channel."""
    row: Any = conn.execute(
        "SELECT watermark FROM metric_sync_watermark WHERE brand_id=%s AND channel=%s",
        (brand_id, channel),
    ).fetchone()
    return row["watermark"] if row else None


def advance_channel_watermark(
    conn: Connection[Any], brand_id: UUID, channel: str, watermark: datetime
) -> None:
    """Update or insert channel watermark."""
    conn.execute(
        """INSERT INTO metric_sync_watermark(brand_id, channel, watermark, last_sync_at)
        VALUES(%s, %s, %s, now())
        ON CONFLICT (brand_id, channel) DO UPDATE SET
            watermark = EXCLUDED.watermark,
            last_sync_at = now()""",
        (brand_id, channel, watermark),
    )


def compute_sync_windows(
    last_watermark: datetime | None,
    current_time: datetime,
    max_lookback_hours: int = 48,
) -> list[tuple[datetime, datetime]]:
    """Slice the missing metric interval into hourly windows for deterministic backfill."""
    now = current_time.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    if last_watermark is None:
        start = now - timedelta(hours=max_lookback_hours)
    else:
        start = last_watermark.astimezone(UTC).replace(
            minute=0, second=0, microsecond=0
        )
        if start < now - timedelta(hours=max_lookback_hours):
            start = now - timedelta(hours=max_lookback_hours)

    windows = []
    cursor = start
    while cursor < now:
        next_hour = cursor + timedelta(hours=1)
        windows.append((cursor, next_hour))
        cursor = next_hour
    return windows


def record_metric_facts(
    conn: Connection[Any],
    facts: list[RawMetricFact],
    restatement_reason: str = "Platform attribution sync update",
) -> int:
    """Idempotently insert or update metric facts with restatement history tracking."""
    if not facts:
        return 0

    inserted_or_updated = 0
    for fact in facts:
        date_hour = fact.date_hour.astimezone(UTC).replace(
            minute=0, second=0, microsecond=0
        )
        existing: Any = conn.execute(
            """SELECT conversions, conversion_value_usd
            FROM metric_fact_raw
            WHERE date_hour=%s AND campaign_object_id=%s""",
            (date_hour, fact.campaign_object_id),
        ).fetchone()

        if existing is not None:
            old_conv = Decimal(str(existing["conversions"]))
            old_val = Decimal(str(existing["conversion_value_usd"]))
            if old_conv != fact.conversions or old_val != fact.conversion_value_usd:
                conn.execute(
                    """INSERT INTO metric_restatement_log(
                        brand_id, campaign_object_id, date_hour,
                        prior_conversions, new_conversions,
                        prior_conversion_value_usd, new_conversion_value_usd,
                        restated_at, reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s)""",
                    (
                        fact.brand_id,
                        fact.campaign_object_id,
                        date_hour,
                        old_conv,
                        fact.conversions,
                        old_val,
                        fact.conversion_value_usd,
                        restatement_reason,
                    ),
                )

        conn.execute(
            """INSERT INTO metric_fact_raw(
                brand_id, campaign_object_id, channel, date_hour,
                impressions, clicks, spend_usd, conversions, conversion_value_usd,
                frequency, reach, video_views, video_completions, engagements,
                native_metrics, attribution_window, attribution_model,
                conversion_event, view_through_policy, ingested_at
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, now()
            )
            ON CONFLICT (date_hour, campaign_object_id) DO UPDATE SET
                impressions = EXCLUDED.impressions,
                clicks = EXCLUDED.clicks,
                spend_usd = EXCLUDED.spend_usd,
                conversions = EXCLUDED.conversions,
                conversion_value_usd = EXCLUDED.conversion_value_usd,
                frequency = EXCLUDED.frequency,
                reach = EXCLUDED.reach,
                video_views = EXCLUDED.video_views,
                video_completions = EXCLUDED.video_completions,
                engagements = EXCLUDED.engagements,
                native_metrics = EXCLUDED.native_metrics,
                attribution_window = EXCLUDED.attribution_window,
                attribution_model = EXCLUDED.attribution_model,
                conversion_event = EXCLUDED.conversion_event,
                view_through_policy = EXCLUDED.view_through_policy,
                ingested_at = now()""",
            (
                fact.brand_id,
                fact.campaign_object_id,
                fact.channel,
                date_hour,
                fact.impressions,
                fact.clicks,
                fact.spend_usd,
                fact.conversions,
                fact.conversion_value_usd,
                fact.frequency,
                fact.reach,
                fact.video_views,
                fact.video_completions,
                fact.engagements,
                Jsonb(fact.native_metrics or {}),
                fact.attribution_window,
                fact.attribution_model,
                fact.conversion_event,
                fact.view_through_policy,
            ),
        )

        comparability = determine_comparability_class(
            fact.attribution_window, fact.view_through_policy, source="channel"
        )
        note = (
            f"Attribution window: {fact.attribution_window}, model: {fact.attribution_model}, "
            f"event: {fact.conversion_event}, view-through: {fact.view_through_policy}"
        )

        metrics_map = {
            "spend": fact.spend_usd,
            "clicks": Decimal(fact.clicks),
            "impressions": Decimal(fact.impressions),
            "conversions": fact.conversions,
            "revenue": fact.conversion_value_usd,
        }

        for metric_key, metric_val in metrics_map.items():
            conn.execute(
                """INSERT INTO metric_normalized(
                    brand_id, campaign_object_id, channel, date_hour,
                    metric_key, metric_value, comparability, methodology_note, source
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, 'channel'
                )
                ON CONFLICT (date_hour, campaign_object_id, metric_key, source) DO UPDATE SET
                    metric_value = EXCLUDED.metric_value,
                    comparability = EXCLUDED.comparability,
                    methodology_note = EXCLUDED.methodology_note""",
                (
                    fact.brand_id,
                    fact.campaign_object_id,
                    fact.channel,
                    date_hour,
                    metric_key,
                    metric_val,
                    comparability,
                    note,
                ),
            )

        inserted_or_updated += 1

    return inserted_or_updated


def aggregate_metric_sum(
    facts: list[dict[str, Any]],
    metric_key: str = "metric_value",
) -> Decimal:
    """Sum metrics strictly enforcing comparability class matching.

    Raises DomainError if comparability classes, attribution windows, or view policies mismatch.
    """
    if not facts:
        return Decimal("0.00")

    classes = {f.get("comparability") for f in facts if f.get("comparability")}
    if len(classes) > 1:
        raise DomainError(
            "IncomparableMetrics",
            f"Attempting to sum metrics across different comparability classes "
            f"({sorted(classes)}) is strictly prohibited. Blended cross-channel aggregations "
            "require unified methodology.",
            422,
        )

    windows = {
        f.get("attribution_window") for f in facts if f.get("attribution_window")
    }
    if len(windows) > 1:
        raise DomainError(
            "IncomparableMetrics",
            f"Attempting to sum metrics across mismatched attribution windows "
            f"({sorted(windows)}) is strictly prohibited.",
            422,
        )

    policies = {
        f.get("view_through_policy") for f in facts if f.get("view_through_policy")
    }
    if len(policies) > 1:
        raise DomainError(
            "IncomparableMetrics",
            f"Attempting to sum metrics across mismatched view-through policies "
            f"({sorted(policies)}) is strictly prohibited.",
            422,
        )

    total = Decimal("0.00")
    for f in facts:
        val = f.get(metric_key)
        if val is not None:
            total += Decimal(str(val))
    return total


def aggregate_blended_cpa(
    spend_facts: list[dict[str, Any]],
    conversion_facts: list[dict[str, Any]],
) -> Decimal:
    """Calculate blended CPA only when conversion facts share a valid comparability class."""
    total_spend = aggregate_metric_sum(spend_facts, "metric_value")
    total_conversions = aggregate_metric_sum(conversion_facts, "metric_value")
    if total_conversions <= 0:
        return Decimal("0.00")
    return total_spend / total_conversions
