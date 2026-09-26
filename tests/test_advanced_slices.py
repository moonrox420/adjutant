"""Tests for S11 (Video), S12 (Agency Platform), S13 (Compliance), and S14 (Billing & Reports)."""

from uuid import UUID, uuid4

import pytest

from adjutant.agency import (
    verify_client_approver_brand_access,
)
from adjutant.billing import (
    cancel_subscription,
    create_subscription,
    handle_billing_failure,
)
from adjutant.compliance import (
    apply_ai_disclosure,
    verify_claim_substantiation,
)
from adjutant.errors import DomainError
from adjutant.reports import generate_weekly_result_summary
from adjutant.video import (
    VideoScene,
    VideoTimeline,
    generate_hook_variants,
    validate_safe_areas,
)

# --- S11: Video Pipeline Tests ---


def test_video_timeline_safe_area_validation():
    """S11.1: Verify scene graph text layers inside and outside safe areas."""
    valid_scene_graph = {
        "layers": [
            {
                "id": "headline",
                "type": "headline",
                "position": {
                    "top_pct": 0.15,
                    "bottom_pct": 0.30,
                    "left_pct": 0.10,
                    "right_pct": 0.20,
                },
            },
            {
                "id": "cta",
                "type": "cta_button",
                "position": {
                    "top_pct": 0.70,
                    "bottom_pct": 0.85,
                    "left_pct": 0.10,
                    "right_pct": 0.20,
                },
            },
        ]
    }
    violations = validate_safe_areas(valid_scene_graph, "9:16", "tiktok")
    assert len(violations) == 0

    # Violating layer (top too high into TikTok chrome)
    overflow_scene_graph = {
        "layers": [
            {
                "id": "headline",
                "type": "headline",
                "position": {
                    "top_pct": 0.02,
                    "bottom_pct": 0.10,
                    "left_pct": 0.02,
                    "right_pct": 0.02,
                },
            },
        ]
    }
    violations_bad = validate_safe_areas(overflow_scene_graph, "9:16", "tiktok")
    assert len(violations_bad) > 0
    assert "safe area" in violations_bad[0].lower()


def test_video_hook_variants_generation():
    """S11.2: Generate 3 hook variants from one concept without altering body scenes."""
    concept_id = uuid4()
    base_hook = VideoScene(
        id="hook_base",
        duration_seconds=3.0,
        scene_graph={
            "layers": [{"id": "h1", "type": "headline", "content": "Original Hook"}]
        },
    )
    body1 = VideoScene(
        id="body_1",
        duration_seconds=5.0,
        scene_graph={
            "layers": [{"id": "b1", "type": "text", "content": "Core Value Prop"}]
        },
    )
    cta = VideoScene(
        id="cta",
        duration_seconds=2.0,
        scene_graph={
            "layers": [{"id": "c1", "type": "text", "content": "Claim Offer"}]
        },
    )
    timeline = VideoTimeline(
        concept_id=concept_id,
        aspect_ratio="9:16",
        hook_scene=base_hook,
        body_scenes=[body1],
        cta_scene=cta,
    )

    hook_copies = [
        {"headline": "Variant 1 Hook"},
        {"headline": "Variant 2 Hook"},
        {"headline": "Variant 3 Hook"},
    ]
    variants = generate_hook_variants(timeline, hook_copies)
    assert len(variants) == 3
    # Body scene references remain identical
    assert variants[0].body_scenes == timeline.body_scenes
    assert variants[1].body_scenes == timeline.body_scenes
    assert (
        variants[0].hook_scene.scene_graph["layers"][0]["content"] == "Variant 1 Hook"
    )
    assert (
        variants[2].hook_scene.scene_graph["layers"][0]["content"] == "Variant 3 Hook"
    )


# --- S12: Agency Platform Tests ---


def test_client_approver_brand_isolation(admin, brand):
    """S12.1: Client approver can access only their assigned brand and no other brand."""
    user_id = admin.execute("""INSERT INTO app_user(email, full_name)
        VALUES('client_approver_isolated@example.com', 'Client Approver')
        ON CONFLICT (email) DO UPDATE SET full_name='Client Approver'
        RETURNING id""").fetchone()["id"]
    other_brand_id = uuid4()

    # Create seat for client_approver
    admin.execute(
        """INSERT INTO seat(account_id, user_id, role, brand_id)
        SELECT account_id, %s, 'client_approver', %s FROM brand WHERE id=%s
        ON CONFLICT (account_id, user_id, brand_id, role) DO NOTHING""",
        (user_id, brand, brand),
    )

    # Allowed for assigned brand
    assert verify_client_approver_brand_access(admin, user_id, UUID(brand)) is True

    # Denied for any other brand
    with pytest.raises(DomainError) as exc_info:
        verify_client_approver_brand_access(admin, user_id, other_brand_id)
    assert exc_info.value.code == "AccessDenied"


# --- S13: Compliance Surface Tests ---


def test_claim_substantiation_blocks_unsubstantiated_superlatives():
    """S13.2: Unsubstantiated superlative or health claims block render."""
    # Unsubstantiated superlative
    valid, err = verify_claim_substantiation("We have the world's best pizza in town!")
    assert valid is False
    assert "UnsubstantiatedClaimError" in err

    # Substantiated with explicit proof point
    valid_sub, err_sub = verify_claim_substantiation(
        "We have the world's best pizza in town!",
        brand_proof_points=["Voted world's best pizza by Culinary Magazine 2026"],
    )
    assert valid_sub is True
    assert err_sub is None


def test_ai_disclosure_layer_application():
    """S13.1: AI disclosure layer applied at render time."""
    scene_graph = {"layers": [{"id": "img", "type": "image"}]}
    eu_graph = apply_ai_disclosure(scene_graph, "EU", "meta")
    assert any(
        layer.get("id") == "compliance_ai_disclosure" for layer in eu_graph["layers"]
    )
    disclosure_layer = next(
        layer
        for layer in eu_graph["layers"]
        if layer["id"] == "compliance_ai_disclosure"
    )
    assert "EU AI Act" in disclosure_layer["text"]


# --- S14: Billing & Result Summaries Tests ---


def test_flat_tier_billing_and_safe_cancellation(admin, brand):
    """S14.3: Flat tier billing creation, dunning, and cancellation without stranding spend."""
    account_id = admin.execute(
        "SELECT account_id FROM brand WHERE id=%s", (brand,)
    ).fetchone()["account_id"]

    # Create growth tier subscription
    sub = create_subscription(admin, account_id, "growth", "pm_card_test_123")
    assert sub["status"] == "active"
    assert sub["monthly_fee_usd"] == "499.00"

    # Handle payment failure -> transitions to dunning
    dunning_res = handle_billing_failure(admin, account_id, "Card expired")
    assert dunning_res["status"] == "dunning"

    # Cancel subscription -> safely pauses live campaigns across all brands in account
    cancel_res = cancel_subscription(admin, account_id, immediate_pause_campaigns=True)
    assert cancel_res["status"] == "cancelled"
    assert cancel_res["campaigns_safely_paused"] is True
    assert str(brand) in cancel_res["paused_brand_ids"]

    # Verify brand in DB is paused
    b = admin.execute(
        "SELECT status, campaigns_enabled FROM brand WHERE id=%s", (brand,)
    ).fetchone()
    assert b["status"] == "paused"
    assert b["campaigns_enabled"] is False


def test_weekly_result_summary_generation(admin, brand):
    """S14.4: Automated weekly result summary generation in plain English."""
    summary = generate_weekly_result_summary(admin, UUID(brand))
    assert summary["brand_name"]
    assert "conversions this week" in summary["headline"]
    assert "autonomous_work_summary" in summary
    assert "key_metrics" in summary
    assert "autonomous_actions_count" in summary
