"""Metric synchronization worker pulling and normalizing channel metrics."""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection

from adjutant.metrics import (
    RawMetricFact,
    advance_channel_watermark,
    compute_sync_windows,
    get_channel_watermark,
    record_metric_facts,
)

logger = logging.getLogger(__name__)


def sync_brand_channel_metrics(
    conn: Connection[Any],
    brand_id: UUID,
    channel: str,
    fetch_func: Any,
    now: datetime | None = None,
) -> int:
    """Sync metrics for a brand's channel across missing hourly windows since watermark."""
    current_time = now or datetime.now(UTC)
    last_watermark = get_channel_watermark(conn, brand_id, channel)
    windows = compute_sync_windows(last_watermark, current_time)
    if not windows:
        return 0

    campaign_objects: Any = conn.execute(
        """SELECT id, native_id FROM campaign_object
        WHERE brand_id=%s AND channel=%s AND state NOT IN ('deleted', 'archived')""",
        (brand_id, channel),
    ).fetchall()

    if not campaign_objects:
        latest_window_end = windows[-1][1]
        advance_channel_watermark(conn, brand_id, channel, latest_window_end)
        return 0

    total_facts_synced = 0
    for window_start, window_end in windows:
        raw_facts: list[RawMetricFact] = []
        for obj in campaign_objects:
            metrics_payload = fetch_func(
                channel=channel,
                object_id=obj["id"],
                native_id=obj["native_id"],
                window_start=window_start,
                window_end=window_end,
            )
            if metrics_payload:
                fact = RawMetricFact(
                    brand_id=brand_id,
                    campaign_object_id=obj["id"],
                    channel=channel,
                    date_hour=window_start,
                    impressions=int(metrics_payload.get("impressions", 0)),
                    clicks=int(metrics_payload.get("clicks", 0)),
                    spend_usd=Decimal(str(metrics_payload.get("spend_usd", "0.00"))),
                    conversions=Decimal(str(metrics_payload.get("conversions", "0.00"))),
                    conversion_value_usd=Decimal(
                        str(metrics_payload.get("conversion_value_usd", "0.00"))
                    ),
                    frequency=(
                        Decimal(str(metrics_payload.get("frequency", "1.00")))
                        if metrics_payload.get("frequency") is not None
                        else None
                    ),
                    reach=(
                        int(metrics_payload.get("reach", 0))
                        if metrics_payload.get("reach") is not None
                        else None
                    ),
                    video_views=(
                        int(metrics_payload.get("video_views", 0))
                        if metrics_payload.get("video_views") is not None
                        else None
                    ),
                    video_completions=(
                        int(metrics_payload.get("video_completions", 0))
                        if metrics_payload.get("video_completions") is not None
                        else None
                    ),
                    engagements=(
                        int(metrics_payload.get("engagements", 0))
                        if metrics_payload.get("engagements") is not None
                        else None
                    ),
                    native_metrics=metrics_payload.get("native_metrics", {}),
                    attribution_window=str(metrics_payload.get("attribution_window", "7d_click")),
                    attribution_model=str(metrics_payload.get("attribution_model", "last_click")),
                    conversion_event=str(metrics_payload.get("conversion_event", "purchase")),
                    view_through_policy=str(metrics_payload.get("view_through_policy", "none")),
                )
                raw_facts.append(fact)

        if raw_facts:
            total_facts_synced += record_metric_facts(conn, raw_facts)
        advance_channel_watermark(conn, brand_id, channel, window_end)

    return total_facts_synced
