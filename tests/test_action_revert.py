"""Tests for append-only audit trail immutability, brand activation, and action reversion."""

from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest
from test_autonomy import review


def test_action_immutability_trigger(admin, brand):
    """Assert database-level trigger forbids any UPDATE or DELETE on action records,

    except for the single atomic transition of reverted_by_action_id from NULL to non-NULL.
    """
    row = admin.execute(
        """INSERT INTO action(brand_id, actor_kind, action_type, target_kind, target_id, rationale)
        VALUES(%s, 'system', 'ceiling_change', 'brand', %s, 'Initial ceiling audit test')
        RETURNING id""",
        (brand, brand),
    ).fetchone()
    action_id = row["id"]

    # Direct UPDATE of rationale must be rejected by the trigger
    with pytest.raises(psycopg.errors.RaiseException, match="is append-only"):
        admin.execute(
            "UPDATE action SET rationale='tampered rationale' WHERE id=%s", (action_id,)
        )

    # Direct UPDATE of target_id must be rejected
    with pytest.raises(psycopg.errors.RaiseException, match="is append-only"):
        admin.execute(
            "UPDATE action SET target_id=%s WHERE id=%s", (uuid4(), action_id)
        )

    # Direct DELETE must be rejected
    with pytest.raises(psycopg.errors.RaiseException, match="is append-only"):
        admin.execute("DELETE FROM action WHERE id=%s", (action_id,))

    # Setting reverted_by_action_id from NULL to another action is allowed
    reverter = admin.execute(
        """INSERT INTO action(brand_id, actor_kind, action_type, target_kind, target_id, rationale)
        VALUES(%s, 'system', 'action_revert', 'action', %s, 'Reverting ceiling test')
        RETURNING id""",
        (brand, action_id),
    ).fetchone()
    revert_action_id = reverter["id"]

    admin.execute(
        "UPDATE action SET reverted_by_action_id=%s WHERE id=%s",
        (revert_action_id, action_id),
    )
    updated = admin.execute(
        "SELECT reverted_by_action_id FROM action WHERE id=%s", (action_id,)
    ).fetchone()
    assert updated["reverted_by_action_id"] == revert_action_id

    # Updating reverted_by_action_id when already set must be rejected
    with pytest.raises(psycopg.errors.RaiseException, match="is append-only"):
        admin.execute(
            "UPDATE action SET reverted_by_action_id=%s WHERE id=%s",
            (uuid4(), action_id),
        )


def test_brand_activation_and_revert(
    client,
    admin,
    brand,
    plan,
    selected_account,
    launch_gateway,
):
    """Test brand activation upon launch authorization consumption, and reverting back to draft."""
    # Check initial brand state is draft with no activated_at
    b = admin.execute(
        "SELECT status, activated_at, campaigns_enabled FROM brand WHERE id=%s",
        (brand,),
    ).fetchone()
    assert b["status"] == "draft"
    assert b["activated_at"] is None

    # Perform launch authorization consumption
    admin.execute(
        "UPDATE brand SET brand_graph_confirmed_at=NULL WHERE id=%s", (brand,)
    )
    data = review(client, brand, plan)
    path = f"/api/brands/{brand}/plans/{plan['id']}/authorize-launch"
    res = client.post(path, json=data)
    assert res.status_code == 200, res.text

    # Brand should now be active with activated_at timestamp
    b_active = admin.execute(
        "SELECT status, activated_at, campaigns_enabled FROM brand WHERE id=%s",
        (brand,),
    ).fetchone()
    assert b_active["status"] == "active"
    assert b_active["activated_at"] is not None
    assert b_active["campaigns_enabled"] is True

    # Find the brand_activate action
    action = admin.execute(
        "SELECT * FROM action WHERE brand_id=%s AND action_type='brand_activate' "
        "ORDER BY executed_at DESC LIMIT 1",
        (brand,),
    ).fetchone()
    assert action is not None
    assert action["revert_path"] is not None
    assert action["reverted_by_action_id"] is None

    # Revert via API
    revert_res = client.post(
        f"/api/brands/{brand}/actions/{action['id']}/revert",
        json={"rationale": "Rollback brand launch activation"},
    )
    assert revert_res.status_code == 200, revert_res.text
    revert_data = revert_res.json()
    assert revert_data["original_action_id"] == str(action["id"])
    assert revert_data["revert_action_id"]

    # Verify brand state reverted to draft
    b_reverted = admin.execute(
        "SELECT status, campaigns_enabled FROM brand WHERE id=%s", (brand,)
    ).fetchone()
    assert b_reverted["status"] == "draft"

    # Verify original action is marked as reverted
    orig = admin.execute(
        "SELECT reverted_by_action_id FROM action WHERE id=%s", (action["id"],)
    ).fetchone()
    assert orig["reverted_by_action_id"] == UUID(revert_data["revert_action_id"])

    # Double-revert of the same action must return 409 Conflict
    conflict_res = client.post(
        f"/api/brands/{brand}/actions/{action['id']}/revert",
        json={"rationale": "Attempting second revert"},
    )
    assert conflict_res.status_code == 409
    assert conflict_res.json()["error"]["code"] == "ActionAlreadyReverted"

    # Reverting a revert action itself must return 422 IrreversibleAction
    revert_on_revert = client.post(
        f"/api/brands/{brand}/actions/{revert_data['revert_action_id']}/revert",
        json={"rationale": "Attempting revert on revert"},
    )
    assert revert_on_revert.status_code in {400, 422}


def test_ceiling_update_and_revert(client, admin, brand):
    """Test modifying brand ceiling and reverting it back to previous limits."""
    # Get initial ceiling from budget_ceiling table
    b_init = admin.execute(
        "SELECT monthly_usd_max, daily_usd_max FROM budget_ceiling "
        "WHERE brand_id=%s AND scope_kind='brand'",
        (brand,),
    ).fetchone()
    old_monthly = Decimal(str(b_init["monthly_usd_max"]))
    old_daily = Decimal(str(b_init["daily_usd_max"]))

    # Update ceiling to new values
    new_monthly = old_monthly + Decimal("1000.00")
    new_daily = old_daily + Decimal("50.00")
    put_res = client.put(
        f"/api/brands/{brand}/ceiling",
        json={"monthly_ceiling": str(new_monthly), "daily_ceiling": str(new_daily)},
    )
    assert put_res.status_code == 200, put_res.text

    # Verify updated ceiling
    b_new = admin.execute(
        "SELECT monthly_usd_max, daily_usd_max FROM budget_ceiling "
        "WHERE brand_id=%s AND scope_kind='brand'",
        (brand,),
    ).fetchone()
    assert b_new["monthly_usd_max"] == new_monthly
    assert b_new["daily_usd_max"] == new_daily

    # Find the ceiling_change action
    action = admin.execute(
        "SELECT * FROM action WHERE brand_id=%s AND action_type='ceiling_change' "
        "ORDER BY executed_at DESC LIMIT 1",
        (brand,),
    ).fetchone()
    assert action is not None
    assert action["revert_path"]["kind"] == "ceiling_set"

    # Revert the ceiling change
    revert_res = client.post(
        f"/api/brands/{brand}/actions/{action['id']}/revert",
        json={"rationale": "Restore previous spend ceilings"},
    )
    assert revert_res.status_code == 200, revert_res.text

    # Verify ceilings are restored to old values
    b_restored = admin.execute(
        "SELECT monthly_usd_max, daily_usd_max FROM budget_ceiling "
        "WHERE brand_id=%s AND scope_kind='brand'",
        (brand,),
    ).fetchone()
    assert b_restored["monthly_usd_max"] == old_monthly
    assert b_restored["daily_usd_max"] == old_daily


def test_plan_restore_revert(client, admin, confirmed_brand, plan, plan_input):
    """Test updating a plan and reverting the plan back to previous state."""
    # Edit the plan
    updated_name = "Modified Emergency plumbing leads"
    updated_plan_input = {
        **plan_input,
        "expected_hash": plan["plan_hash"],
        "name": updated_name,
    }
    put_res = client.put(
        f"/api/brands/{confirmed_brand}/plans/{plan['id']}", json=updated_plan_input
    )
    assert put_res.status_code == 200, put_res.text
    new_plan = put_res.json()
    assert new_plan["name"] == updated_name

    # Find the plan_edit action with revert_path
    action = admin.execute(
        "SELECT * FROM action WHERE brand_id=%s AND action_type='plan_edit' "
        "AND revert_path IS NOT NULL ORDER BY executed_at DESC LIMIT 1",
        (confirmed_brand,),
    ).fetchone()
    assert action is not None
    assert action["revert_path"]["kind"] == "plan_restore"

    # Revert the plan change
    revert_res = client.post(
        f"/api/brands/{confirmed_brand}/actions/{action['id']}/revert",
        json={"rationale": "Rollback plan modification"},
    )
    assert revert_res.status_code == 200, revert_res.text

    # Verify plan name is restored in DB
    restored_plan = admin.execute(
        "SELECT name, document FROM (SELECT name, plan_document as document "
        "FROM plan WHERE id=%s) sub",
        (plan["id"],),
    ).fetchone()
    assert restored_plan["name"] == plan_input["name"]
    assert restored_plan["document"]["name"] == plan_input["name"]


def test_list_actions_endpoint(client, brand):
    """Test querying actions for a brand."""
    res = client.get(f"/api/brands/{brand}/actions")
    assert res.status_code == 200, res.text
    data = res.json()
    assert isinstance(data, list)
    for act in data:
        assert "id" in act
        assert "action_type" in act
        assert "actor_kind" in act
        assert "target_kind" in act
        assert "executed_at" in act
