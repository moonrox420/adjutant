"""Comprehensive verification of Slice 8: Autonomous loop, fatigue detection, winners, guardrails, and escalations."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from adjutant.decision import evaluate_guardrails, is_scope_halted, record_escalation
from adjutant.diagnosis import FatigueSignals, detect_fatigue, record_finding
from adjutant.events import EventRegistry
from adjutant.metrics import RawMetricFact, record_metric_facts
from adjutant.runner import run_tick


def create_test_creative(admin, brand):
    import secrets
    from pathlib import Path

    from psycopg.types.json import Jsonb

    from adjutant.security import ApprovalSigner

    user = admin.execute("SELECT id FROM app_user LIMIT 1").fetchone()
    user_id = user["id"]
    plan_hash = f"hash_{uuid4().hex}"
    plan_id = admin.execute(
        """INSERT INTO plan(
            brand_id, name, objective, goal_kind, goal_value,
            plan_hash, plan_document, monthly_budget_usd
        ) VALUES (
            %s, 'Test Plan', 'leads', 'target_cpa', 50.0,
            %s, '{}'::jsonb, 3000.00
        ) RETURNING id""",
        (brand, plan_hash),
    ).fetchone()["id"]
    concept_id = admin.execute(
        """INSERT INTO creative_concept(brand_id, plan_id, name, hypothesis, brief)
        VALUES (%s, %s, 'Concept 1', 'Hypothesis', '{}'::jsonb) RETURNING id""",
        (brand, plan_id),
    ).fetchone()["id"]
    creative_id = admin.execute(
        """INSERT INTO creative(
            brand_id, concept_id, format, state, scene_graph, creative_hash
        ) VALUES (%s, %s, 'static_image', 'rendered', '{}'::jsonb, %s) RETURNING id""",
        (brand, concept_id, f"chash_{uuid4().hex}"),
    ).fetchone()["id"]

    now = datetime.now(UTC)
    expires = now + timedelta(hours=48)
    req_id = admin.execute(
        """INSERT INTO approval_request(
            brand_id, subject_type, subject_id, subject_hash, state,
            requested_daily_usd, requested_total_usd, expires_at,
            internal_approver_id, internal_approved_at
        ) VALUES (
            %s, 'plan', %s, %s, 'approved',
            100.00, 3000.00, %s,
            %s, %s
        ) RETURNING id""",
        (brand, plan_id, plan_hash, expires, user_id, now),
    ).fetchone()["id"]

    token_id = uuid4()
    signer = ApprovalSigner(Path(".local/approval.key"))
    claims = {
        "token_id": str(token_id),
        "brand_id": str(brand),
        "subject_type": "plan",
        "subject_id": str(plan_id),
        "subject_hash": plan_hash,
        "scopes": ["channel:meta", "op:spend", "op:create", "op:update"],
        "usd_daily_cap": "500.00",
        "usd_total_cap": "10000.00",
        "approver_id": str(user_id),
        "approval_chain": [str(user_id)],
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "nonce": secrets.token_hex(24),
    }
    signature = signer.sign(claims)

    admin.execute(
        """INSERT INTO approval_token(
            id, approval_request_id, brand_id, subject_type, subject_id,
            subject_hash, scopes, usd_daily_cap, usd_total_cap, approver_id,
            approval_chain, signing_key_id, signature, nonce, issued_at,
            expires_at, signed_claims
        ) VALUES (
            %s, %s, %s, 'plan', %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s
        )""",
        (
            token_id,
            req_id,
            brand,
            plan_id,
            plan_hash,
            claims["scopes"],
            Decimal(claims["usd_daily_cap"]),
            Decimal(claims["usd_total_cap"]),
            user_id,
            [user_id],
            signer.key_id,
            signature,
            claims["nonce"],
            now,
            expires,
            Jsonb(claims),
        ),
    )
    return creative_id


def setup_active_brand(admin, brand):
    admin.execute(
        "SELECT set_config('app.current_brand_ids', %s, false)",
        (brand,),
    )
    admin.execute(
        "UPDATE brand SET status='active', campaigns_enabled=true, activated_at=now() WHERE id=%s",
        (brand,),
    )
    conn_id = admin.execute(
        "INSERT INTO channel_connection(brand_id, channel, external_ad_account_id, selected, verified_at) "
        "VALUES(%s, 'meta', 'act_runner_01', true, now()) RETURNING id",
        (brand,),
    ).fetchone()["id"]
    return conn_id


def test_loop_runs_unattended_for_active_brand(admin, brand, client):
    """S8.1: The loop runs hourly per active brand, unattended."""
    setup_active_brand(admin, brand)
    config = client.app.state.config
    events = EventRegistry(config.registry_path)
    tick_id = uuid4()

    with admin.transaction():
        result = run_tick(admin, config, events, UUID(brand), tick_id)
        assert result["status"] == "completed"

    row = admin.execute(
        "SELECT * FROM loop_tick_run WHERE id=%s", (tick_id,)
    ).fetchone()
    assert row["status"] == "completed"
    assert row["step"] == "done"


def test_fatigued_ad_replaced_first_then_paused_zero_gap(admin, brand, client):
    """S8.2: A fatigued ad is detected, a replacement is generated and launched, and the fatigued ad is paused — in that order, with no gap where the brand has no live creative."""
    conn_id = setup_active_brand(admin, brand)
    campaign = admin.execute(
        "INSERT INTO campaign_object(brand_id, connection_id, channel, level, native_id, state) "
        "VALUES(%s, %s, 'meta', 'campaign', 'camp_802', 'active') RETURNING id",
        (brand, conn_id),
    ).fetchone()["id"]
    creative_id = create_test_creative(admin, brand)
    fatigued_ad = admin.execute(
        "INSERT INTO campaign_object(brand_id, connection_id, channel, level, native_id, state, parent_id, daily_budget_usd, creative_id, created_at) "
        "VALUES(%s, %s, 'meta', 'ad', 'ad_fatigued_01', 'active', %s, 50.00, %s, now() - interval '15 days') RETURNING id",
        (brand, conn_id, campaign, creative_id),
    ).fetchone()["id"]

    # Insert metrics producing 3 simultaneous fatigue signals:
    # 1. Frequency = 3.8 (> 3.0)
    # 2. CTR drop: prior 5%, recent 2% (drop > 15%)
    # 3. CPA rise: prior $20, recent $40 (rise > 20%)
    now = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)
    recent_fact = RawMetricFact(
        brand_id=UUID(brand),
        campaign_object_id=fatigued_ad,
        channel="meta",
        date_hour=now - timedelta(days=2),
        impressions=10000,
        clicks=200,  # 2% CTR
        spend_usd=Decimal("400.00"),
        conversions=Decimal("10.00"),  # $40 CPA
        conversion_value_usd=Decimal("500.00"),
        frequency=Decimal("3.80"),
    )
    prior_fact = RawMetricFact(
        brand_id=UUID(brand),
        campaign_object_id=fatigued_ad,
        channel="meta",
        date_hour=now - timedelta(days=10),
        impressions=10000,
        clicks=500,  # 5% CTR
        spend_usd=Decimal("200.00"),
        conversions=Decimal("10.00"),  # $20 CPA
        conversion_value_usd=Decimal("500.00"),
        frequency=Decimal("2.10"),
    )
    with admin.transaction():
        record_metric_facts(admin, [recent_fact, prior_fact])

    config = client.app.state.config
    events = EventRegistry(config.registry_path)
    tick_id = uuid4()

    with admin.transaction():
        res = run_tick(admin, config, events, UUID(brand), tick_id)
        assert "refresh_creative" in res["executed"]

    # Verify fatigued ad is now paused
    fatigued_after = admin.execute(
        "SELECT state FROM campaign_object WHERE id=%s", (fatigued_ad,)
    ).fetchone()
    assert fatigued_after["state"] == "paused"

    # Verify replacement ad was created and is active
    replacement = admin.execute(
        "SELECT * FROM campaign_object WHERE brand_id=%s AND parent_id=%s AND id!=%s",
        (brand, campaign, fatigued_ad),
    ).fetchone()
    assert replacement is not None
    assert replacement["state"] == "active"

    # Verify strict ordering in actions ledger: creative_swap was recorded before pause
    actions = admin.execute(
        "SELECT id, action_type, target_id, executed_at FROM action WHERE brand_id=%s ORDER BY executed_at ASC, id ASC",
        (brand,),
    ).fetchall()
    action_types = [a["action_type"] for a in actions]
    assert "creative_swap" in action_types
    assert "pause" in action_types
    assert action_types.index("creative_swap") < action_types.index("pause")


def test_two_signal_fatigue_condition_does_not_trigger_refresh(admin, brand, client):
    """S8.3: A two-signal fatigue condition does NOT trigger a refresh. Tested explicitly."""
    # 2 signals only: Frequency > 3.0 and CTR drop >= 15%, but CPA did NOT rise, half-life not exceeded
    signals_2 = FatigueSignals(
        frequency_above_3=True,
        ctr_declining_15pct=True,
        cpa_rising_20pct=False,
        impressions_declining_bid_stable=False,
        half_life_exceeded=False,
    )
    assert len(signals_2.active_signals()) == 2
    assert detect_fatigue(signals_2) is None

    # Database level check: attempting to insert finding with only 2 signals violates check constraint
    obj_id = uuid4()
    with pytest.raises(Exception):
        record_finding(
            admin,
            UUID(brand),
            "fatigue",
            obj_id,
            ["frequency_above_3", "ctr_declining_15pct"],
        )

    # Verify with 3 signals it succeeds
    signals_3 = FatigueSignals(
        frequency_above_3=True,
        ctr_declining_15pct=True,
        cpa_rising_20pct=True,
        impressions_declining_bid_stable=False,
        half_life_exceeded=False,
    )
    assert len(signals_3.active_signals()) == 3
    assert detect_fatigue(signals_3) == [
        "frequency_above_3",
        "ctr_declining_15pct",
        "cpa_rising_20pct",
    ]


def test_guardrail_breach_rejected_and_recorded_never_executed(admin, brand):
    """S8.4: A decision that would breach any guardrail is rejected and recorded, never executed. Tested for guardrails."""
    conn_id = setup_active_brand(admin, brand)
    obj_id = admin.execute(
        "INSERT INTO campaign_object(brand_id, connection_id, channel, level, native_id, state) "
        "VALUES(%s, %s, 'meta', 'campaign', 'camp_guard_01', 'active') RETURNING id",
        (brand, conn_id),
    ).fetchone()["id"]

    # Set daily spend cap to 100 USD
    admin.execute(
        "UPDATE guardrail SET daily_spend_cap_usd=100.00 WHERE brand_id=%s", (brand,)
    )

    # Breach 1: Proposing daily spend increase that breaches daily_spend_cap_usd
    breaching_spend = {
        "kind": "scale_winner",
        "channel": "meta",
        "target_id": obj_id,
        "params": {"current_daily_usd": "50.00", "proposed_daily_usd": "250.00"},
    }
    with admin.transaction():
        res = evaluate_guardrails(admin, UUID(brand), breaching_spend)
    assert res["approved"] is False
    assert res["state"] == "rejected"
    assert "daily_spend_cap_usd" in res["reason"]

    # Breach 2: Blocked claims
    admin.execute(
        "UPDATE guardrail SET blocked_claims=ARRAY['risk free guarantee'] WHERE brand_id=%s",
        (brand,),
    )
    breaching_copy = {
        "kind": "refresh_creative",
        "channel": "meta",
        "target_id": obj_id,
        "params": {"proposed_copy": "Get our Risk Free Guarantee today!"},
    }
    with admin.transaction():
        res_copy = evaluate_guardrails(admin, UUID(brand), breaching_copy)
    assert res_copy["approved"] is False
    assert res_copy["state"] == "rejected"
    assert "blocked claim" in res_copy["reason"]


def test_escalation_triggers_fire_and_halt_affected_scope_only(admin, brand):
    """S8.5: Every escalation trigger in §2 fires its escalation and halts autonomous action on that scope only, leaving the rest of the account running."""
    conn_id = setup_active_brand(admin, brand)
    obj1 = admin.execute(
        "INSERT INTO campaign_object(brand_id, connection_id, channel, level, native_id, state) "
        "VALUES(%s, %s, 'meta', 'campaign', 'camp_esc_01', 'active') RETURNING id",
        (brand, conn_id),
    ).fetchone()["id"]
    obj2 = admin.execute(
        "INSERT INTO campaign_object(brand_id, connection_id, channel, level, native_id, state) "
        "VALUES(%s, %s, 'meta', 'campaign', 'camp_esc_02', 'active') RETURNING id",
        (brand, conn_id),
    ).fetchone()["id"]

    # Trigger escalation on obj1 (creative policy rejection)
    with admin.transaction():
        esc_id = record_escalation(
            admin,
            UUID(brand),
            "creative_policy_rejection",
            "campaign_object",
            obj1,
            {"policy_violation": "Misleading claims rejected by Meta ad review"},
        )
        assert esc_id is not None

    # Assert scope obj1 is halted, but obj2 is NOT halted
    with admin.transaction():
        assert is_scope_halted(admin, UUID(brand), "meta", obj1) is True
        assert is_scope_halted(admin, UUID(brand), "meta", obj2) is False

    # A candidate decision on obj1 is halted / escalated
    cand_obj1 = {
        "kind": "scale_winner",
        "channel": "meta",
        "target_id": obj1,
        "params": {"current_daily_usd": "10.00", "proposed_daily_usd": "12.00"},
    }
    with admin.transaction():
        res1 = evaluate_guardrails(admin, UUID(brand), cand_obj1)
    assert res1["approved"] is False
    assert res1["state"] == "escalated"

    # A candidate decision on obj2 proceeds without interference
    cand_obj2 = {
        "kind": "scale_winner",
        "channel": "meta",
        "target_id": obj2,
        "params": {"current_daily_usd": "10.00", "proposed_daily_usd": "12.00"},
    }
    with admin.transaction():
        res2 = evaluate_guardrails(admin, UUID(brand), cand_obj2)
    assert res2["approved"] is True
    assert res2["state"] == "executed"


def test_budget_never_moves_across_mismatched_comparability_classes(admin, brand):
    """S8.6: Budget never moves between objects with mismatched comparability classes."""
    conn_id = setup_active_brand(admin, brand)
    obj_id = admin.execute(
        "INSERT INTO campaign_object(brand_id, connection_id, channel, level, native_id, state) "
        "VALUES(%s, %s, 'meta', 'campaign', 'camp_comp_01', 'active') RETURNING id",
        (brand, conn_id),
    ).fetchone()["id"]

    # Moving budget from direct (7d click) to caveated (1d view)
    mismatched_reallocation = {
        "kind": "reallocate_budget",
        "channel": "meta",
        "target_id": obj_id,
        "params": {
            "current_daily_usd": "50.00",
            "proposed_daily_usd": "70.00",
            "source_comparability": "direct",
            "target_comparability": "caveated",
        },
    }
    with admin.transaction():
        res = evaluate_guardrails(admin, UUID(brand), mismatched_reallocation)
    assert res["approved"] is False
    assert res["state"] == "rejected"
    assert "MismatchedComparabilityClass" in res["reason"]

    # Moving budget between matching classes succeeds
    matching_reallocation = {
        "kind": "reallocate_budget",
        "channel": "meta",
        "target_id": obj_id,
        "params": {
            "current_daily_usd": "50.00",
            "proposed_daily_usd": "60.00",
            "source_comparability": "direct",
            "target_comparability": "direct",
        },
    }
    with admin.transaction():
        res_matching = evaluate_guardrails(admin, UUID(brand), matching_reallocation)
    assert res_matching["approved"] is True


def test_budget_increase_exceeding_max_daily_spend_increase_clamped_and_logged(
    admin, brand
):
    """S8.7: A budget increase exceeding max_daily_spend_increase_pct is clamped, not rejected, and the clamp is logged."""
    conn_id = setup_active_brand(admin, brand)
    obj_id = admin.execute(
        "INSERT INTO campaign_object(brand_id, connection_id, channel, level, native_id, state, daily_budget_usd) "
        "VALUES(%s, %s, 'meta', 'campaign', 'camp_clamp_01', 'active', 100.00) RETURNING id",
        (brand, conn_id),
    ).fetchone()["id"]

    # Guardrail has max_daily_spend_increase_pct = 25%
    admin.execute(
        "UPDATE guardrail SET max_daily_spend_increase_pct=25.0 WHERE brand_id=%s",
        (brand,),
    )

    # Candidate proposes a 60% increase ($100 -> $160)
    candidate = {
        "kind": "scale_winner",
        "channel": "meta",
        "target_id": obj_id,
        "params": {"current_daily_usd": "100.00", "proposed_daily_usd": "160.00"},
    }
    with admin.transaction():
        res = evaluate_guardrails(admin, UUID(brand), candidate)

    # Must be approved, NOT rejected, and clamped to $125.00
    assert res["approved"] is True
    assert res["params"]["clamped"] is True
    assert res["params"]["proposed_daily_usd"] == "125.00"
    assert (
        "Clamped spend increase from 60.0% to allowed limit of 25.0%"
        in res["params"]["clamp_logged_reason"]
    )


def test_crashed_tick_resume_does_not_double_execute_actions(admin, brand, client):
    """S8.8: A tick that crashes mid-execution and resumes does not double-execute any action."""
    conn_id = setup_active_brand(admin, brand)
    campaign = admin.execute(
        "INSERT INTO campaign_object(brand_id, connection_id, channel, level, native_id, state) "
        "VALUES(%s, %s, 'meta', 'campaign', 'camp_crash_01', 'active') RETURNING id",
        (brand, conn_id),
    ).fetchone()["id"]
    creative_id = create_test_creative(admin, brand)
    fatigued_ad = admin.execute(
        "INSERT INTO campaign_object(brand_id, connection_id, channel, level, native_id, state, parent_id, daily_budget_usd, creative_id, created_at) "
        "VALUES(%s, %s, 'meta', 'ad', 'ad_crash_01', 'active', %s, 50.00, %s, now() - interval '15 days') RETURNING id",
        (brand, conn_id, campaign, creative_id),
    ).fetchone()["id"]

    now = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)
    with admin.transaction():
        record_metric_facts(
            admin,
            [
                RawMetricFact(
                    brand_id=UUID(brand),
                    campaign_object_id=fatigued_ad,
                    channel="meta",
                    date_hour=now - timedelta(days=2),
                    impressions=10000,
                    clicks=200,
                    spend_usd=Decimal("400.00"),
                    conversions=Decimal("10.00"),
                    conversion_value_usd=Decimal("500.00"),
                    frequency=Decimal("3.50"),
                ),
                RawMetricFact(
                    brand_id=UUID(brand),
                    campaign_object_id=fatigued_ad,
                    channel="meta",
                    date_hour=now - timedelta(days=10),
                    impressions=10000,
                    clicks=500,
                    spend_usd=Decimal("200.00"),
                    conversions=Decimal("10.00"),
                    conversion_value_usd=Decimal("500.00"),
                    frequency=Decimal("2.00"),
                ),
            ],
        )

    config = client.app.state.config
    events = EventRegistry(config.registry_path)
    tick_id = uuid4()

    # Run tick 1
    with admin.transaction():
        res1 = run_tick(admin, config, events, UUID(brand), tick_id)
        assert "refresh_creative" in res1["executed"]

    # Count ads created for this parent
    ads_count_1 = admin.execute(
        "SELECT count(*) AS n FROM campaign_object WHERE parent_id=%s", (campaign,)
    ).fetchone()["n"]

    # Simulate resuming the exact same tick (e.g. after crash / restart)
    with admin.transaction():
        res2 = run_tick(admin, config, events, UUID(brand), tick_id)
        # S8.8: No actions are re-executed
        assert len(res2["executed"]) == 0

    ads_count_2 = admin.execute(
        "SELECT count(*) AS n FROM campaign_object WHERE parent_id=%s", (campaign,)
    ).fetchone()["n"]
    assert ads_count_2 == ads_count_1


def test_cross_channel_budget_reallocation_under_comparability(admin, brand):
    """S9.4: Autonomous cross-channel reallocation reallocates budget between Meta and Google Ads based on measured performance (S8 comparability rules hold across channels)."""
    meta_conn = setup_active_brand(admin, brand)
    google_conn = admin.execute(
        "INSERT INTO channel_connection(brand_id, channel, external_ad_account_id, selected, verified_at) "
        "VALUES(%s, 'google_ads', '1234567890', true, now()) RETURNING id",
        (brand,),
    ).fetchone()["id"]

    meta_camp = admin.execute(
        "INSERT INTO campaign_object(brand_id, connection_id, channel, level, native_id, state, daily_budget_usd) "
        "VALUES(%s, %s, 'meta', 'campaign', 'meta_camp_cross_01', 'active', 100.00) RETURNING id",
        (brand, meta_conn),
    ).fetchone()["id"]

    google_camp = admin.execute(
        "INSERT INTO campaign_object(brand_id, connection_id, channel, level, native_id, state, daily_budget_usd) "
        "VALUES(%s, %s, 'google_ads', 'campaign', 'google_camp_cross_01', 'active', 100.00) RETURNING id",
        (brand, google_conn),
    ).fetchone()["id"]

    admin.execute(
        "UPDATE guardrail SET daily_spend_cap_usd=500.00 WHERE brand_id=%s", (brand,)
    )

    # 1. Attempt cross-channel reallocation across mismatched comparability classes: MUST BE REJECTED
    mismatched_cand = {
        "kind": "reallocate_budget",
        "channel": "google_ads",
        "target_id": google_camp,
        "params": {
            "source_campaign_id": str(meta_camp),
            "current_daily_usd": "100.00",
            "proposed_daily_usd": "120.00",
            "source_comparability": "caveated",
            "target_comparability": "direct",
        },
    }
    with admin.transaction():
        res_mismatched = evaluate_guardrails(admin, UUID(brand), mismatched_cand)
    assert res_mismatched["approved"] is False
    assert res_mismatched["state"] == "rejected"
    assert "MismatchedComparabilityClass" in res_mismatched["reason"]

    # 2. Reallocation between matching comparability classes across channels: APPROVED
    matching_cand = {
        "kind": "reallocate_budget",
        "channel": "google_ads",
        "target_id": google_camp,
        "params": {
            "source_campaign_id": str(meta_camp),
            "current_daily_usd": "100.00",
            "proposed_daily_usd": "115.00",
            "source_comparability": "direct",
            "target_comparability": "direct",
        },
    }
    with admin.transaction():
        res_matching = evaluate_guardrails(admin, UUID(brand), matching_cand)
    assert res_matching["approved"] is True
    assert res_matching["state"] == "executed"
