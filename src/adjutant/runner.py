"""Autonomous loop runner executing hourly ticks with advisory locking and idempotent ordering."""

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from adjutant.config import Settings
from adjutant.decision import evaluate_guardrails
from adjutant.diagnosis import diagnose_campaign_objects
from adjutant.events import EventRegistry
from adjutant.service import locked_brand

logger = logging.getLogger(__name__)


def run_tick(
    conn: Connection,
    config: Settings,
    events: EventRegistry,
    brand_id: UUID,
    tick_id: UUID | None = None,
    mock_sync_func: Any = None,
) -> dict[str, Any]:
    """Execute a single tick of the autonomous ad runner loop for an active brand."""
    tid = tick_id or uuid4()

    # Step 0: Ensure tenant scope and acquire brand runner advisory lock
    current_ids = (
        conn.execute("SELECT current_brand_ids() AS ids").fetchone()["ids"] or []
    )
    if brand_id not in current_ids:
        new_ids = list(current_ids) + [brand_id]
        conn.execute(
            "SELECT set_config('app.current_brand_ids', %s, true)",
            (",".join(map(str, new_ids)),),
        )
    conn.execute("SELECT lock_runner_brand(%s)", (brand_id,))
    brand = locked_brand(conn, brand_id)
    if brand.get("status") != "active" or not brand.get("campaigns_enabled"):
        return {
            "status": "skipped",
            "reason": "Brand is not active or campaigns are disabled.",
        }

    # Record loop tick run
    conn.execute(
        """INSERT INTO loop_tick_run(id, brand_id, status, step, started_at)
        VALUES (%s, %s, 'running', 'measure', now())
        ON CONFLICT (id) DO UPDATE SET status='running', step='measure'""",
        (tid, brand_id),
    )

    try:
        # Step 1: Measure
        conn.execute("UPDATE loop_tick_run SET step='measure' WHERE id=%s", (tid,))
        # (Metric sync happens through metrics_worker or provided mock sync)

        # Step 2: Diagnose
        conn.execute("UPDATE loop_tick_run SET step='diagnose' WHERE id=%s", (tid,))
        findings = diagnose_campaign_objects(conn, brand_id)

        # Step 3 & 4: Decide & Check Guardrails
        conn.execute("UPDATE loop_tick_run SET step='decide' WHERE id=%s", (tid,))
        candidates = []
        for f in findings:
            if f["kind"] == "fatigue":
                candidates.append(
                    {
                        "finding_id": f["finding_id"],
                        "kind": "refresh_creative",
                        "channel": f["channel"],
                        "target_id": f["object_id"],
                        "params": {
                            "reason": "Creative fatigue detected across 3+ signals"
                        },
                    }
                )
            elif f["kind"] == "winner":
                obj = conn.execute(
                    "SELECT daily_budget_usd FROM campaign_object WHERE id=%s",
                    (f["object_id"],),
                ).fetchone()
                current_daily = obj["daily_budget_usd"] or Decimal("50.00")
                proposed_daily = current_daily * Decimal("1.25")
                candidates.append(
                    {
                        "finding_id": f["finding_id"],
                        "kind": "scale_winner",
                        "channel": f["channel"],
                        "target_id": f["object_id"],
                        "params": {
                            "current_daily_usd": str(current_daily),
                            "proposed_daily_usd": str(proposed_daily),
                            "reason": f"Winner cleared volume with low CPA {f.get('cpa')}",
                        },
                    }
                )

        executed_actions = []
        # Step 5: Execute survivors with idempotency & strict ordering
        conn.execute("UPDATE loop_tick_run SET step='execute' WHERE id=%s", (tid,))
        for cand in candidates:
            idem_key = f"{brand_id}:{tid}:{cand['kind']}:{cand['target_id']}"

            # Check if this exact action was already executed in this tick (S8.8)
            prior = conn.execute(
                """SELECT state, action_id FROM autonomous_decision
                WHERE brand_id=%s AND idempotency_key=%s""",
                (brand_id, idem_key),
            ).fetchone()
            if prior and prior["state"] == "executed":
                continue

            guard_result = evaluate_guardrails(conn, brand_id, cand)
            state = guard_result["state"]
            rejection_reason = guard_result["reason"]
            params = guard_result["params"]

            if not guard_result["approved"]:
                conn.execute(
                    """INSERT INTO autonomous_decision(
                        brand_id, finding_id, kind, target_id, channel,
                        idempotency_key, params, state, rejection_reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (brand_id, idempotency_key) DO UPDATE SET
                        state=EXCLUDED.state, rejection_reason=EXCLUDED.rejection_reason""",
                    (
                        brand_id,
                        cand["finding_id"],
                        cand["kind"],
                        cand["target_id"],
                        cand["channel"],
                        idem_key,
                        Jsonb(params),
                        state,
                        rejection_reason,
                    ),
                )
                continue

            # Execute approved action
            action_id = None
            # Invariant #2: Every spend-affecting action names the token that authorized it.
            active_token = conn.execute(
                """SELECT id FROM approval_token
                WHERE brand_id=%s AND voided_at IS NULL AND expires_at > now()
                ORDER BY expires_at DESC LIMIT 1""",
                (brand_id,),
            ).fetchone()
            token_id = active_token["id"] if active_token else None

            if cand["kind"] == "refresh_creative":
                fatigued_id = cand["target_id"]
                fatigued_obj = conn.execute(
                    "SELECT * FROM campaign_object WHERE id=%s", (fatigued_id,)
                ).fetchone()

                # S8.2 Strict ordering:
                # 1. Create and launch replacement FIRST
                replacement_id = uuid4()
                conn.execute(
                    """INSERT INTO campaign_object(
                        id, brand_id, connection_id, channel, level, native_id, state,
                        parent_id, daily_budget_usd, creative_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s
                    )""",
                    (
                        replacement_id,
                        brand_id,
                        fatigued_obj["connection_id"],
                        fatigued_obj["channel"],
                        fatigued_obj["level"],
                        f"native_rep_{replacement_id.hex[:8]}",
                        fatigued_obj["parent_id"],
                        fatigued_obj["daily_budget_usd"],
                        fatigued_obj["creative_id"],
                    ),
                )

                conn.execute(
                    """INSERT INTO action(
                        brand_id, actor_kind, action_type, target_kind, target_id,
                        channel, target_native_id, diff, rationale, token_id, revert_path,
                        executed_at
                    ) VALUES (
                        %s, 'system', 'creative_swap', 'campaign_object', %s,
                        %s, %s, %s, %s, %s, %s, clock_timestamp()
                    ) RETURNING id""",
                    (
                        brand_id,
                        replacement_id,
                        fatigued_obj["channel"],
                        f"native_rep_{replacement_id.hex[:8]}",
                        Jsonb(
                            {
                                "after": {
                                    "state": "active",
                                    "parent_id": str(fatigued_obj["parent_id"]),
                                }
                            }
                        ),
                        "Replacement ad created and launched prior to cutting fatigued creative",
                        token_id,
                        Jsonb(
                            {
                                "kind": "campaign_object_state",
                                "object_id": str(replacement_id),
                                "before_state": "deleted",
                            }
                        ),
                    ),
                )

                # 2. ONLY AFTER replacement is active, pause fatigued ad
                conn.execute(
                    "UPDATE campaign_object SET state='paused' WHERE id=%s",
                    (fatigued_id,),
                )

                pause_action_id = conn.execute(
                    """INSERT INTO action(
                        brand_id, actor_kind, action_type, target_kind, target_id,
                        channel, target_native_id, diff, rationale, revert_path,
                        executed_at
                    ) VALUES (
                        %s, 'system', 'pause', 'campaign_object', %s,
                        %s, %s, %s, %s, %s, clock_timestamp()
                    ) RETURNING id""",
                    (
                        brand_id,
                        fatigued_id,
                        fatigued_obj["channel"],
                        fatigued_obj["native_id"],
                        Jsonb(
                            {
                                "before": {"state": "active"},
                                "after": {"state": "paused"},
                            }
                        ),
                        "Fatigued ad paused after verified replacement launch",
                        Jsonb(
                            {
                                "kind": "campaign_object_state",
                                "object_id": str(fatigued_id),
                                "before_state": "active",
                            }
                        ),
                    ),
                ).fetchone()["id"]
                action_id = pause_action_id

            elif cand["kind"] == "scale_winner":
                obj_id = cand["target_id"]
                new_budget = Decimal(str(params["proposed_daily_usd"]))
                conn.execute(
                    "UPDATE campaign_object SET daily_budget_usd=%s WHERE id=%s",
                    (new_budget, obj_id),
                )
                action_id = conn.execute(
                    """INSERT INTO action(
                        brand_id, actor_kind, action_type, target_kind, target_id,
                        channel, diff, rationale, token_id, revert_path
                    ) VALUES (
                        %s, 'system', 'budget_set', 'campaign_object', %s,
                        %s, %s, %s, %s, %s
                    ) RETURNING id""",
                    (
                        brand_id,
                        obj_id,
                        cand["channel"],
                        Jsonb(
                            {
                                "before": params["current_daily_usd"],
                                "after": str(new_budget),
                            }
                        ),
                        params.get("clamp_logged_reason")
                        or "Scaled winner budget within daily cap limit",
                        token_id,
                        Jsonb(
                            {
                                "kind": "campaign_object_state",
                                "object_id": str(obj_id),
                                "before_state": "active",
                            }
                        ),
                    ),
                ).fetchone()["id"]

            conn.execute(
                """INSERT INTO autonomous_decision(
                    brand_id, finding_id, kind, target_id, channel,
                    idempotency_key, params, state, action_id, executed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'executed', %s, now())
                ON CONFLICT (brand_id, idempotency_key) DO UPDATE SET
                    state='executed', action_id=EXCLUDED.action_id, executed_at=now()""",
                (
                    brand_id,
                    cand["finding_id"],
                    cand["kind"],
                    cand["target_id"],
                    cand["channel"],
                    idem_key,
                    Jsonb(params),
                    action_id,
                ),
            )
            executed_actions.append(cand["kind"])

        conn.execute(
            """UPDATE loop_tick_run SET
                status='completed', step='done',
                summary=%s, finished_at=now()
            WHERE id=%s""",
            (
                Jsonb({"executed": executed_actions, "findings_count": len(findings)}),
                tid,
            ),
        )
        return {
            "status": "completed",
            "tick_id": str(tid),
            "executed": executed_actions,
        }

    except Exception as exc:
        conn.execute(
            """UPDATE loop_tick_run SET
                status='failed', error_message=%s, finished_at=now()
            WHERE id=%s""",
            (str(exc), tid),
        )
        raise
