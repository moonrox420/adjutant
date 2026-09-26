"""Test suite for S7: Honest measurement, attribution normalization,
and comparability enforcement."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from adjutant.errors import DomainError
from adjutant.metrics import (
    RawMetricFact,
    aggregate_metric_sum,
    determine_comparability_class,
    get_channel_watermark,
    record_metric_facts,
)
from adjutant.metrics_worker import sync_brand_channel_metrics


def test_comparability_class_determination():
    assert determine_comparability_class("7d_click", "none") == "direct"
    assert determine_comparability_class("1d_click", "none") == "direct"
    assert determine_comparability_class("1d_view", "1d_view") == "caveated"
    assert determine_comparability_class("7d_click", "1d_view") == "caveated"
    assert (
        determine_comparability_class("7d_click", "none", source="first_party")
        == "first_party_only"
    )


def test_metrics_land_hourly_no_duplicates_on_replay(admin, brand):
    """S7.1: Metrics land hourly with no duplicates across re-runs,
    verified by replaying a sync window."""
    conn_id = admin.execute(
        "INSERT INTO channel_connection("
        "brand_id, channel, external_ad_account_id, selected, verified_at) "
        "VALUES(%s, 'meta', 'act_101', true, now()) RETURNING id",
        (brand,),
    ).fetchone()["id"]
    campaign_id = admin.execute(
        "INSERT INTO campaign_object(brand_id, connection_id, channel, level, native_id, state) "
        "VALUES(%s, %s, 'meta', 'campaign', '101', 'active') RETURNING id",
        (brand, conn_id),
    ).fetchone()["id"]

    hour = datetime(2026, 9, 22, 10, 0, 0, tzinfo=UTC)
    fact = RawMetricFact(
        brand_id=UUID(brand),
        campaign_object_id=campaign_id,
        channel="meta",
        date_hour=hour,
        impressions=1000,
        clicks=50,
        spend_usd=Decimal("25.00"),
        conversions=Decimal("2.00"),
        conversion_value_usd=Decimal("150.00"),
        attribution_window="7d_click",
        attribution_model="last_click",
        conversion_event="purchase",
        view_through_policy="none",
    )

    # First ingestion
    with admin.transaction():
        count1 = record_metric_facts(admin, [fact])
        assert count1 == 1

    row = admin.execute(
        "SELECT * FROM metric_fact_raw WHERE date_hour=%s AND campaign_object_id=%s",
        (hour, campaign_id),
    ).fetchone()
    assert row["impressions"] == 1000
    assert row["clicks"] == 50
    assert row["spend_usd"] == Decimal("25.0000")

    # Replay exact same sync window
    with admin.transaction():
        count2 = record_metric_facts(admin, [fact])
        assert count2 == 1

    # Verify no duplicates
    rows = admin.execute(
        "SELECT count(*) AS n FROM metric_fact_raw WHERE date_hour=%s AND campaign_object_id=%s",
        (hour, campaign_id),
    ).fetchone()
    assert rows["n"] == 1

    # Verify normalized table also has no duplicates
    norm_rows = admin.execute(
        "SELECT count(*) AS n FROM metric_normalized WHERE date_hour=%s AND campaign_object_id=%s",
        (hour, campaign_id),
    ).fetchone()
    assert norm_rows["n"] == 5  # spend, clicks, impressions, conversions, revenue


def test_every_fact_carries_attribution_window_conversion_event_and_view_policy(
    admin, brand
):
    """S7.2: Every fact carries attribution window, conversion event, and view-through policy."""
    conn_id = admin.execute(
        "INSERT INTO channel_connection("
        "brand_id, channel, external_ad_account_id, selected, verified_at) "
        "VALUES(%s, 'google_ads', 'act_102', true, now()) RETURNING id",
        (brand,),
    ).fetchone()["id"]
    campaign_id = admin.execute(
        "INSERT INTO campaign_object(brand_id, connection_id, channel, level, native_id, state) "
        "VALUES(%s, %s, 'google_ads', 'campaign', '102', 'active') RETURNING id",
        (brand, conn_id),
    ).fetchone()["id"]

    hour = datetime(2026, 9, 22, 11, 0, 0, tzinfo=UTC)
    fact = RawMetricFact(
        brand_id=UUID(brand),
        campaign_object_id=campaign_id,
        channel="google_ads",
        date_hour=hour,
        impressions=5000,
        clicks=200,
        spend_usd=Decimal("100.00"),
        conversions=Decimal("10.00"),
        conversion_value_usd=Decimal("800.00"),
        attribution_window="30d_click",
        attribution_model="data_driven",
        conversion_event="lead_submitted",
        view_through_policy="none",
    )

    with admin.transaction():
        record_metric_facts(admin, [fact])

    row = admin.execute(
        "SELECT attribution_window, attribution_model, conversion_event, view_through_policy "
        "FROM metric_fact_raw WHERE date_hour=%s AND campaign_object_id=%s",
        (hour, campaign_id),
    ).fetchone()

    assert row["attribution_window"] == "30d_click"
    assert row["attribution_model"] == "data_driven"
    assert row["conversion_event"] == "lead_submitted"
    assert row["view_through_policy"] == "none"


def test_sum_metrics_across_different_comparability_classes_raises():
    """S7.3: Attempting to sum metrics across different comparability classes raises
    rather than returning a number."""
    direct_fact = {
        "metric_value": Decimal("100.00"),
        "comparability": "direct",
        "attribution_window": "7d_click",
        "view_through_policy": "none",
    }
    caveated_fact = {
        "metric_value": Decimal("200.00"),
        "comparability": "caveated",
        "attribution_window": "1d_view",
        "view_through_policy": "1d_view",
    }

    # Same class works
    assert aggregate_metric_sum([direct_fact, direct_fact]) == Decimal("200.00")

    # Mismatched comparability class MUST raise DomainError with code IncomparableMetrics
    with pytest.raises(DomainError) as exc_info:
        aggregate_metric_sum([direct_fact, caveated_fact])
    assert exc_info.value.code == "IncomparableMetrics"

    # Mismatched attribution window MUST raise
    mismatched_window = {
        "metric_value": Decimal("50.00"),
        "comparability": "direct",
        "attribution_window": "1d_click",
        "view_through_policy": "none",
    }
    with pytest.raises(DomainError) as exc_info:
        aggregate_metric_sum([direct_fact, mismatched_window])
    assert exc_info.value.code == "IncomparableMetrics"


def test_48_hour_outage_backfills_completely_without_duplicates(admin, brand):
    """S7.4: A 48-hour outage backfills completely on recovery without duplicating facts."""
    conn_id = admin.execute(
        "INSERT INTO channel_connection("
        "brand_id, channel, external_ad_account_id, selected, verified_at) "
        "VALUES(%s, 'tiktok', 'act_103', true, now()) RETURNING id",
        (brand,),
    ).fetchone()["id"]
    admin.execute(
        "INSERT INTO campaign_object(brand_id, connection_id, channel, level, native_id, state) "
        "VALUES(%s, %s, 'tiktok', 'campaign', '103', 'active') RETURNING id",
        (brand, conn_id),
    )

    now = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)
    outage_start = now - timedelta(hours=48)

    # Set watermark to 48 hours ago
    with admin.transaction():
        admin.execute(
            "INSERT INTO metric_sync_watermark(brand_id, channel, watermark, last_sync_at) "
            "VALUES(%s, 'tiktok', %s, %s)",
            (brand, outage_start, outage_start),
        )

    def mock_fetch(channel, object_id, native_id, window_start, window_end):
        return {
            "impressions": 100,
            "clicks": 5,
            "spend_usd": "2.50",
            "conversions": "1.00",
            "conversion_value_usd": "20.00",
            "attribution_window": "7d_click",
            "attribution_model": "last_click",
            "conversion_event": "purchase",
            "view_through_policy": "none",
        }

    # Run backfill
    with admin.transaction():
        synced = sync_brand_channel_metrics(
            admin, UUID(brand), "tiktok", mock_fetch, now=now
        )
        assert synced == 48

    # Verify all 48 hours are stored
    count_row = admin.execute(
        "SELECT count(*) AS n FROM metric_fact_raw WHERE brand_id=%s AND channel='tiktok'",
        (brand,),
    ).fetchone()
    assert count_row["n"] == 48

    # Verify watermark updated to now
    watermark = get_channel_watermark(admin, UUID(brand), "tiktok")
    assert watermark == now

    # Running again right now does 0 windows and duplicates nothing
    with admin.transaction():
        synced_again = sync_brand_channel_metrics(
            admin, UUID(brand), "tiktok", mock_fetch, now=now
        )
        assert synced_again == 0

    count_after = admin.execute(
        "SELECT count(*) AS n FROM metric_fact_raw WHERE brand_id=%s AND channel='tiktok'",
        (brand,),
    ).fetchone()
    assert count_after["n"] == 48


def test_restating_late_conversions_preserves_history(admin, brand):
    """S7.5: Restating late conversions updates the fact without corrupting the
    history of what was known when."""
    conn_id = admin.execute(
        "INSERT INTO channel_connection("
        "brand_id, channel, external_ad_account_id, selected, verified_at) "
        "VALUES(%s, 'linkedin', 'act_104', true, now()) RETURNING id",
        (brand,),
    ).fetchone()["id"]
    campaign_id = admin.execute(
        "INSERT INTO campaign_object(brand_id, connection_id, channel, level, native_id, state) "
        "VALUES(%s, %s, 'linkedin', 'campaign', '104', 'active') RETURNING id",
        (brand, conn_id),
    ).fetchone()["id"]

    hour = datetime(2026, 9, 20, 14, 0, 0, tzinfo=UTC)

    # Day 1: 3 conversions reported
    initial_fact = RawMetricFact(
        brand_id=UUID(brand),
        campaign_object_id=campaign_id,
        channel="linkedin",
        date_hour=hour,
        impressions=1000,
        clicks=40,
        spend_usd=Decimal("50.00"),
        conversions=Decimal("3.00"),
        conversion_value_usd=Decimal("300.00"),
    )
    with admin.transaction():
        record_metric_facts(admin, [initial_fact])

    # Assert no restatement log on initial insert
    restatements_0 = admin.execute(
        "SELECT count(*) AS n FROM metric_restatement_log WHERE campaign_object_id=%s",
        (campaign_id,),
    ).fetchone()["n"]
    assert restatements_0 == 0

    # Day 3: Platform attribution restatement updates conversions to 7
    restated_fact = RawMetricFact(
        brand_id=UUID(brand),
        campaign_object_id=campaign_id,
        channel="linkedin",
        date_hour=hour,
        impressions=1000,
        clicks=40,
        spend_usd=Decimal("50.00"),
        conversions=Decimal("7.00"),
        conversion_value_usd=Decimal("700.00"),
    )
    with admin.transaction():
        record_metric_facts(
            admin,
            [restated_fact],
            restatement_reason="LinkedIn delayed B2B conversion match",
        )

    # Verify metric fact is updated to 7 conversions
    current_row = admin.execute(
        "SELECT conversions, conversion_value_usd FROM metric_fact_raw "
        "WHERE date_hour=%s AND campaign_object_id=%s",
        (hour, campaign_id),
    ).fetchone()
    assert current_row["conversions"] == Decimal("7.0000")
    assert current_row["conversion_value_usd"] == Decimal("700.0000")

    # Verify history of what was known when is preserved in restatement log
    log_row = admin.execute(
        "SELECT * FROM metric_restatement_log WHERE campaign_object_id=%s",
        (campaign_id,),
    ).fetchone()
    assert log_row is not None
    assert log_row["prior_conversions"] == Decimal("3.0000")
    assert log_row["new_conversions"] == Decimal("7.0000")
    assert log_row["prior_conversion_value_usd"] == Decimal("300.0000")
    assert log_row["new_conversion_value_usd"] == Decimal("700.0000")
    assert log_row["reason"] == "LinkedIn delayed B2B conversion match"
    assert log_row["restated_at"] is not None
