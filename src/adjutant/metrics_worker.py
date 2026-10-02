"""Metric synchronization worker pulling and normalizing channel metrics."""

import asyncio
import concurrent.futures
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
from psycopg import Connection

from adjutant.adapters.metrics import fetch_live_channel_metrics
from adjutant.channel_credentials import authorization_for
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


def _create_channel_fetcher(
    channel_name: str,
    app_credentials: dict[str, Any],
    channel_token: dict[str, Any],
    account_identifier: str,
    meta_attributes: dict[str, Any],
) -> Any:
    """Create a thread-safe bound fetcher closure for a specific channel connection."""

    def _fetcher(
        channel: str,
        object_id: UUID,
        native_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> dict[str, Any]:
        async def _run() -> dict[str, Any]:
            async with httpx.AsyncClient(timeout=15.0) as client:
                return await fetch_live_channel_metrics(
                    channel=channel_name,
                    client=client,
                    app=app_credentials,
                    token=channel_token,
                    account_id=account_identifier,
                    native_id=native_id,
                    window_start=window_start,
                    window_end=window_end,
                    metadata=meta_attributes,
                )

        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(asyncio.run, _run()).result()
            else:
                return asyncio.run(_run())
        except Exception as fetch_exc:
            logger.warning(
                "Metric fetch failed for object %s (%s) on channel %s: %s",
                native_id,
                object_id,
                channel,
                fetch_exc,
            )
            return {}

    return _fetcher


def sync_live_brand_metrics(
    conn: Connection[Any],
    config: Any,
    brand_id: UUID,
    now: datetime | None = None,
) -> int:
    """Synchronize live metrics across all active connected ad channels for a brand."""
    connections: Any = conn.execute(
        """SELECT channel, external_ad_account_id, provider_metadata
        FROM channel_connection
        WHERE brand_id=%s AND selected=true AND health='healthy'""",
        (brand_id,),
    ).fetchall()

    if not connections:
        logger.debug("No active healthy channel connections found for brand %s", brand_id)
        return 0

    total_synced = 0
    for conn_row in connections:
        channel = conn_row["channel"]
        account_id = conn_row["external_ad_account_id"] or ""
        provider_meta = conn_row.get("provider_metadata") or {}

        try:
            app, token = authorization_for(conn, config, brand_id, channel)
        except Exception as auth_exc:
            logger.warning(
                "Skipping live metric sync for brand %s channel %s: authorization unavailable (%s)",
                brand_id,
                channel,
                auth_exc,
            )
            continue

        fetcher = _create_channel_fetcher(
            channel_name=channel,
            app_credentials=app,
            channel_token=token,
            account_identifier=account_id,
            meta_attributes=provider_meta,
        )

        try:
            facts_count = sync_brand_channel_metrics(
                conn=conn,
                brand_id=brand_id,
                channel=channel,
                fetch_func=fetcher,
                now=now,
            )
            total_synced += facts_count
            logger.info(
                "Synced %d live metric facts for brand %s on channel %s",
                facts_count,
                brand_id,
                channel,
            )
        except Exception as sync_exc:
            logger.error(
                "Failed to sync metrics for brand %s on channel %s: %s",
                brand_id,
                channel,
                sync_exc,
                exc_info=True,
            )

    return total_synced
