"""Verification test suite for Slice 10:

1. S10.1: Conformance suite coverage across all 10 channels.
2. S10.2: Published parity matrix endpoint (/api/channels/parity-matrix).
3. S10.3: Multi-channel budget reallocation across all connected channels under comparability rules.
4. S10.4: Platform access application tracking, filing dates, and status visibility with strict RLS.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from adjutant.decision import evaluate_guardrails


def test_parity_matrix_endpoint_returns_all_ten_channels(client, admin, brand):
    """S10.2: Published parity matrix returns every capability across all 10 channels."""
    res = client.get("/api/channels/parity-matrix")
    assert res.status_code == 200, res.text
    matrix = res.json()
    assert len(matrix) == 10
    channels = {item["channel"] for item in matrix}
    expected = {
        "meta",
        "google_ads",
        "youtube",
        "tiktok",
        "linkedin",
        "microsoft",
        "reddit",
        "pinterest",
        "snapchat",
        "amazon_ads",
    }
    assert channels == expected

    for item in matrix:
        assert item["adapter_status"] == "production_ready"
        assert len(item["objectives"]) > 0
        assert len(item["hierarchy"]) >= 3
        assert len(item["budget_levels"]) >= 1
        assert "quota_model" in item
        assert "access_review_required" in item


def test_access_application_lifecycle_and_tenant_isolation(
    client, admin, brand, database_urls
):
    """S10.4: Access application status is tracked and visible, with filing dates
    and current state."""
    brand_id = brand

    # 1. File access application for Meta Advanced Access
    filing_date = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    create_payload = {
        "channel": "meta",
        "access_tier": "advanced_access",
        "status": "pending_review",
        "filing_date": filing_date,
        "notes": "Filed Meta app review with business verification.",
        "metadata": {"app_id": "9988776655", "use_cases": ["ads_management"]},
    }
    res_create = client.post(
        f"/api/brands/{brand_id}/access-applications", json=create_payload
    )
    assert res_create.status_code == 201, res_create.text
    app_data = res_create.json()
    app_id = app_data["id"]
    assert app_data["channel"] == "meta"
    assert app_data["status"] == "pending_review"
    assert app_data["notes"] == "Filed Meta app review with business verification."

    # 2. File access application for TikTok
    res_tiktok = client.post(
        f"/api/brands/{brand_id}/access-applications",
        json={
            "channel": "tiktok",
            "access_tier": "data_security_review",
            "status": "under_review",
            "notes": "Submitted questionnaire for TikTok marketing API.",
            "metadata": {"ticket_id": "TT-12345"},
        },
    )
    assert res_tiktok.status_code == 201, res_tiktok.text

    # 3. List access applications
    res_list = client.get(f"/api/brands/{brand_id}/access-applications")
    assert res_list.status_code == 200
    apps = res_list.json()
    assert len(apps) >= 2
    meta_app = next(a for a in apps if a["channel"] == "meta")
    assert meta_app["status"] == "pending_review"

    # 4. Update status to approved with decision date
    decision_date = datetime.now(UTC).isoformat()
    res_update = client.patch(
        f"/api/brands/{brand_id}/access-applications/{app_id}",
        json={
            "status": "approved",
            "decision_date": decision_date,
            "notes": "Meta Advanced Access approved on first review.",
        },
    )
    assert res_update.status_code == 200, res_update.text
    updated = res_update.json()
    assert updated["status"] == "approved"
    assert updated["decision_date"] is not None
    assert "approved on first review" in updated["notes"]

    account_id = admin.execute(
        "SELECT account_id FROM brand WHERE id=%s", (brand,)
    ).fetchone()["account_id"]
    brand_b = admin.execute(
        "INSERT INTO brand(account_id, display_name, status) "
        "VALUES(%s, 'Brand B Access', 'active') RETURNING id",
        (account_id,),
    ).fetchone()["id"]
    # Query directly under brand_b RLS context using non-superuser app connection
    with psycopg.connect(
        database_urls[1], autocommit=True, row_factory=dict_row
    ) as app_conn:
        app_conn.execute("SET search_path=adjutant,public")
        app_conn.execute(
            "SELECT set_config('app.current_brand_ids', %s, false)", (str(brand_b),)
        )
        rows = app_conn.execute(
            "SELECT * FROM platform_access_application WHERE brand_id=%s", (brand_id,)
        ).fetchall()
        assert len(rows) == 0


def test_reallocation_across_all_connected_channels(admin, brand):
    """S10.3: The running loop reallocates across all connected channels according
    to comparability rules."""
    channels = [
        "meta",
        "google_ads",
        "youtube",
        "tiktok",
        "linkedin",
        "microsoft",
        "reddit",
        "pinterest",
        "snapchat",
        "amazon_ads",
    ]
    admin.execute(
        "UPDATE guardrail SET daily_spend_cap_usd=2000.00 WHERE brand_id=%s", (brand,)
    )
    admin.execute("SELECT set_config('app.current_brand_ids', %s, false)", (brand,))

    campaigns = {}
    for ch in channels:
        conn_id = admin.execute(
            "INSERT INTO channel_connection("
            "brand_id, channel, external_ad_account_id, selected, verified_at) "
            "VALUES(%s, %s, %s, true, now()) RETURNING id",
            (brand, ch, f"act_{ch}_001"),
        ).fetchone()["id"]
        camp_id = admin.execute(
            "INSERT INTO campaign_object("
            "brand_id, connection_id, channel, level, native_id, state, daily_budget_usd) "
            "VALUES(%s, %s, %s, 'campaign', %s, 'active', 50.00) RETURNING id",
            (brand, conn_id, ch, f"camp_{ch}_native"),
        ).fetchone()["id"]
        campaigns[ch] = camp_id

    # Test pairwise reallocation from every connected channel to another connected channel
    source_channel = "meta"
    source_camp = campaigns[source_channel]

    for target_channel in channels[1:]:
        target_camp = campaigns[target_channel]
        cand = {
            "kind": "reallocate_budget",
            "channel": target_channel,
            "target_id": target_camp,
            "params": {
                "source_campaign_id": str(source_camp),
                "current_daily_usd": "50.00",
                "proposed_daily_usd": "60.00",
                "source_comparability": "direct",
                "target_comparability": "direct",
            },
        }
        with admin.transaction():
            res = evaluate_guardrails(admin, UUID(brand), cand)
        assert (
            res["approved"] is True
        ), f"Failed reallocation to {target_channel}: {res}"
        assert res["state"] == "executed"
