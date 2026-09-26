"""Append-only action ledger revert engine restoring prior states deterministically."""

from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from adjutant.db import one
from adjutant.errors import DomainError
from adjutant.events import EventRegistry
from adjutant.security import digest


def execute_revert(
    conn: Connection,
    events: EventRegistry,
    brand_id: UUID,
    action_id: UUID,
    actor_user_id: UUID,
) -> dict[str, Any]:
    """Execute a recorded revert path and preserve the immutable audit pointer."""
    action = conn.execute(
        "SELECT * FROM action WHERE id=%s AND brand_id=%s FOR UPDATE",
        (action_id, brand_id),
    ).fetchone()
    if action is None:
        raise DomainError("ActionNotFound", "The specified action was not found.", 404)

    if action["reverted_by_action_id"] is not None:
        raise DomainError(
            "ActionAlreadyReverted", "This action has already been reverted.", 409
        )

    if action["action_type"] == "action_revert":
        raise DomainError(
            "CannotRevertRevertAction",
            "A revert action cannot itself be reverted; execute a forward correction instead.",
            400,
        )

    revert_path = action["revert_path"]
    if not revert_path or not isinstance(revert_path, dict):
        raise DomainError(
            "IrreversibleAction",
            "This action does not possess an executable revert path.",
            422,
        )

    kind = revert_path.get("kind")
    if kind == "brand_status":
        status = revert_path["status"]
        campaigns_enabled = bool(revert_path.get("campaigns_enabled", False))
        conn.execute(
            "UPDATE brand SET status=%s, campaigns_enabled=%s WHERE id=%s",
            (status, campaigns_enabled, brand_id),
        )
    elif kind == "ceiling_set":
        daily = revert_path.get("daily_ceiling")
        monthly = revert_path["monthly_ceiling"]
        scope_kind = revert_path.get("scope_kind", "brand")
        conn.execute(
            """UPDATE budget_ceiling SET daily_usd_max=%s, monthly_usd_max=%s
            WHERE brand_id=%s AND scope_kind=%s""",
            (daily, monthly, brand_id, scope_kind),
        )
    elif kind == "plan_restore":
        plan_id = UUID(revert_path["plan_id"])
        plan_doc = revert_path["plan_document"]
        plan_hash = digest(plan_doc)
        conn.execute(
            """UPDATE plan SET name=%s, objective=%s, goal_kind=%s, goal_value=%s,
            rationale=%s, monthly_budget_usd=%s, plan_document=%s, plan_hash=%s, state='draft'
            WHERE id=%s AND brand_id=%s""",
            (
                plan_doc["name"],
                plan_doc["objective"],
                plan_doc["goal_kind"],
                plan_doc["goal_value"],
                plan_doc.get("rationale"),
                plan_doc["monthly_budget_usd"],
                Jsonb(plan_doc),
                plan_hash,
                plan_id,
                brand_id,
            ),
        )
        conn.execute("DELETE FROM plan_allocation WHERE plan_id=%s", (plan_id,))
        for allocation in plan_doc.get("allocations", []):
            conn.execute(
                """INSERT INTO plan_allocation(
                    plan_id, brand_id, channel, monthly_budget_usd, daily_budget_usd
                )
                VALUES(%s, %s, %s, %s, %s)""",
                (
                    plan_id,
                    brand_id,
                    allocation["channel"],
                    allocation["monthly_budget_usd"],
                    allocation["daily_budget_usd"],
                ),
            )
    elif kind in {"campaign_object_state", "budget_set", "pause", "creative_swap"}:
        obj_id = UUID(str(revert_path["object_id"]))
        state = revert_path.get("state") or revert_path.get("before_state")
        daily_budget = (
            revert_path.get("before_daily_budget_usd")
            or revert_path.get("before_daily_usd")
            or revert_path.get("daily_budget_usd")
        )

        if state is not None:
            # Map 'deleted' before_state to 'paused' or 'archived'
            effective_state = "paused" if state == "deleted" else state
            conn.execute(
                """UPDATE campaign_object SET state=%s, intended_state=%s
                WHERE id=%s AND brand_id=%s""",
                (effective_state, effective_state, obj_id, brand_id),
            )
        if daily_budget is not None:
            conn.execute(
                "UPDATE campaign_object SET daily_budget_usd=%s WHERE id=%s AND brand_id=%s",
                (Decimal(str(daily_budget)), obj_id, brand_id),
            )
    else:
        raise DomainError(
            "UnsupportedRevertKind",
            f"Revert kind '{kind}' is not supported by the execution engine.",
            422,
        )

    diff_data = {
        "reverts_action_id": str(action_id),
        "reverted_type": action["action_type"],
        "restored_state": revert_path,
    }
    revert_action_id = one(
        conn,
        """INSERT INTO action(brand_id, actor_kind, actor_user_id, action_type,
                      target_kind, target_id, diff, rationale, revert_path)
                      VALUES(%s, 'human', %s, 'action_revert', %s, %s, %s, %s, %s)
                      RETURNING id""",
        (
            brand_id,
            actor_user_id,
            action["target_kind"],
            action["target_id"],
            Jsonb(diff_data),
            f"Reverted action {action_id} ({action['action_type']})",
            None,
        ),
    )["id"]

    conn.execute(
        "UPDATE action SET reverted_by_action_id=%s WHERE id=%s",
        (revert_action_id, action_id),
    )

    payload = {
        "brand_id": str(brand_id),
        "action_id": str(revert_action_id),
        "action_type": "action_revert",
        "actor_kind": "human",
        "actor_user_id": str(actor_user_id),
        "target_kind": action["target_kind"],
        "reversible": False,
    }
    if action.get("target_id"):
        payload["target_id"] = str(action["target_id"])

    events.append(conn, "action.recorded", brand_id, payload)

    return {
        "revert_action_id": str(revert_action_id),
        "original_action_id": str(action_id),
        "reverted_action_id": str(action_id),
        "status": "reverted",
        "restored": revert_path,
    }
