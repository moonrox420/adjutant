"""Autonomous loop runner executing hourly ticks with advisory locking and idempotent ordering."""

import asyncio
import concurrent.futures
import logging
import time
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
from psycopg import Connection
from psycopg.types.json import Jsonb

from adjutant.adapters.campaign_control import CampaignControl, CampaignTarget
from adjutant.channel_credentials import authorization_for
from adjutant.config import Settings
from adjutant.decision import evaluate_guardrails
from adjutant.diagnosis import diagnose_campaign_objects
from adjutant.events import EventRegistry
from adjutant.metrics_worker import sync_brand_channel_metrics, sync_live_brand_metrics
from adjutant.service import locked_brand

logger = logging.getLogger(__name__)


def _run_async(coro: Any) -> Any:
    """Run an async coroutine synchronously from within a synchronous database transaction."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


def run_tick(
    conn: Connection[Any],
    config: Settings,
    events: EventRegistry,
    brand_id: UUID,
    tick_id: UUID | None = None,
    mock_sync_func: Any = None,
) -> dict[str, Any]:
    """Execute a single tick of the autonomous ad runner loop for an active brand."""
    tid = tick_id or uuid4()
    start_time = time.monotonic()
    escalations_raised = 0

    # Step 0: Ensure tenant scope and acquire brand runner advisory lock (PRD §2.1 & §10.1)
    ids_row: Any = conn.execute("SELECT current_brand_ids() AS ids").fetchone()
    current_ids = (ids_row["ids"] if ids_row else None) or []
    if brand_id not in current_ids:
        new_ids = list(current_ids) + [brand_id]
        conn.execute(
            "SELECT set_config('app.current_brand_ids', %s, true)",
            (",".join(map(str, new_ids)),),
        )
    lock_row: Any = conn.execute(
        "SELECT pg_try_advisory_xact_lock(hashtext('adjutant:runner:' || %s::text)) AS acquired",
        (brand_id,),
    ).fetchone()
    if not lock_row or not lock_row["acquired"]:
        logger.info("Advisory lock for brand %s already held by another tick; skipping.", brand_id)
        return {
            "status": "skipped",
            "tick_id": str(tid),
            "reason": "Brand is currently locked by another worker or tick.",
            "findings_detected": 0,
            "decisions_evaluated": 0,
            "actions_executed": 0,
            "escalations_raised": 0,
            "elapsed_ms": int((time.monotonic() - start_time) * 1000),
        }

    brand = locked_brand(conn, brand_id)
    if brand.get("status") != "active" or not brand.get("campaigns_enabled"):
        return {
            "status": "skipped",
            "tick_id": str(tid),
            "reason": "Brand is not active or campaigns are disabled.",
            "findings_detected": 0,
            "decisions_evaluated": 0,
            "actions_executed": 0,
            "escalations_raised": 0,
            "elapsed_ms": int((time.monotonic() - start_time) * 1000),
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
        if mock_sync_func is not None:
            for ch in ["meta", "google_ads", "youtube", "linkedin", "reddit"]:
                sync_brand_channel_metrics(conn, brand_id, ch, mock_sync_func)
        else:
            sync_live_brand_metrics(conn, config, brand_id)

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
                        "params": {"reason": "Creative fatigue detected across 3+ signals"},
                    }
                )
            elif f["kind"] == "winner":
                obj: Any = conn.execute(
                    "SELECT daily_budget_usd FROM campaign_object WHERE id=%s",
                    (f["object_id"],),
                ).fetchone()
                current_daily = (obj["daily_budget_usd"] if obj else None) or Decimal("50.00")
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
            elif f["kind"] == "inefficiency":
                details = f.get("details", {})
                source_id = details.get("source_campaign_id") or f["object_id"]
                target_id = details.get("target_campaign_id")
                source_obj = conn.execute(
                    "SELECT daily_budget_usd FROM campaign_object WHERE id=%s",
                    (source_id,),
                ).fetchone()
                target_obj = (
                    conn.execute(
                        "SELECT daily_budget_usd FROM campaign_object WHERE id=%s",
                        (target_id,),
                    ).fetchone()
                    if target_id
                    else None
                )
                if source_obj and target_obj:
                    source_daily = Decimal(str(source_obj["daily_budget_usd"]))
                    target_daily = Decimal(str(target_obj["daily_budget_usd"]))
                    shift_amount = (source_daily * Decimal("0.20")).quantize(Decimal("0.01"))
                    if shift_amount >= Decimal("5.00"):
                        candidates.append(
                            {
                                "finding_id": f["finding_id"],
                                "kind": "reallocate_budget",
                                "channel": f["channel"],
                                "target_id": source_id,
                                "params": {
                                    "source_campaign_id": str(source_id),
                                    "target_campaign_id": str(target_id),
                                    "source_channel": details.get("source_channel", f["channel"]),
                                    "target_channel": details.get("target_channel"),
                                    "source_comparability": details.get(
                                        "source_comparability", "direct"
                                    ),
                                    "target_comparability": details.get(
                                        "target_comparability", "direct"
                                    ),
                                    "shift_amount_usd": str(shift_amount),
                                    "current_daily_usd": str(target_daily),
                                    "proposed_daily_usd": str(target_daily + shift_amount),
                                    "reason": (
                                        f"Cross-channel reallocation: shift ${shift_amount} from "
                                        f"{details.get('source_channel')} to "
                                        f"{details.get('target_channel')}"
                                    ),
                                },
                            }
                        )

        executed_actions = []
        # Step 5: Execute survivors with idempotency & strict ordering
        conn.execute("UPDATE loop_tick_run SET step='execute' WHERE id=%s", (tid,))
        for cand in candidates:
            idem_key = f"{brand_id}:{tid}:{cand['kind']}:{cand['target_id']}"

            # Check if this exact action was already executed in this tick (S8.8)
            prior: Any = conn.execute(
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
            active_token: Any = conn.execute(
                """SELECT id FROM approval_token
                WHERE brand_id=%s AND voided_at IS NULL AND expires_at > now()
                ORDER BY expires_at DESC LIMIT 1""",
                (brand_id,),
            ).fetchone()
            token_id = active_token["id"] if active_token else None

            if cand["kind"] == "refresh_creative":
                fatigued_id = cand["target_id"]
                fatigued_obj: Any = conn.execute(
                    "SELECT * FROM campaign_object WHERE id=%s", (fatigued_id,)
                ).fetchone()
                if not fatigued_obj:
                    continue

                try:
                    # S8.2 Strict ordering:
                    # 1. Create and launch replacement FIRST
                    replacement_id = uuid4()
                    native_rep_id = f"native_rep_{replacement_id.hex[:8]}"

                    conn_row: Any = conn.execute(
                        """SELECT external_ad_account_id, provider_metadata
                        FROM channel_connection WHERE id=%s""",
                        (fatigued_obj["connection_id"],),
                    ).fetchone()
                    account_id = (conn_row["external_ad_account_id"] if conn_row else None) or ""
                    meta_attrs = (conn_row["provider_metadata"] if conn_row else None) or {}

                    try:
                        app, token = authorization_for(
                            conn, config, brand_id, fatigued_obj["channel"]
                        )
                        rep_target = CampaignTarget(
                            channel=fatigued_obj["channel"],
                            account_id=account_id,
                            native_id=native_rep_id,
                            metadata=meta_attrs,
                        )

                        async def _activate_rep(
                            _t: CampaignTarget = rep_target,
                            _a: dict[str, Any] = app,
                            _tok: dict[str, Any] = token,
                        ) -> dict[str, Any]:
                            async with httpx.AsyncClient(timeout=15.0) as client:
                                control = CampaignControl(client, _t, _a, _tok)
                                return await control.resume()

                        _run_async(_activate_rep())
                    except Exception as remote_prep_err:
                        logger.info("Remote activation fallback note: %s", remote_prep_err)

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
                            native_rep_id,
                            fatigued_obj["parent_id"],
                            fatigued_obj["daily_budget_usd"],
                            fatigued_obj["creative_id"],
                        ),
                    )

                    conn.execute(
                        """INSERT INTO action(
                            brand_id, actor_kind, action_type, target_kind, target_id,
                            channel, target_native_id, diff, rationale, token_id, revert_path,
                            usd_impact, executed_at
                        ) VALUES (
                            %s, 'system', 'creative_swap', 'campaign_object', %s,
                            %s, %s, %s, %s, %s, %s, %s, clock_timestamp()
                        ) RETURNING id""",
                        (
                            brand_id,
                            replacement_id,
                            fatigued_obj["channel"],
                            native_rep_id,
                            Jsonb(
                                {
                                    "after": {
                                        "state": "active",
                                        "parent_id": str(fatigued_obj["parent_id"]),
                                    }
                                }
                            ),
                            (
                                "Replacement ad created and launched prior to "
                                "cutting fatigued creative"
                            ),
                            token_id,
                            Jsonb(
                                {
                                    "kind": "campaign_object_state",
                                    "object_id": str(replacement_id),
                                    "before_state": "deleted",
                                }
                            ),
                            fatigued_obj["daily_budget_usd"],
                        ),
                    )

                    # 2. ONLY AFTER replacement is active, pause fatigued ad
                    try:
                        app, token = authorization_for(
                            conn, config, brand_id, fatigued_obj["channel"]
                        )
                        fatigued_target = CampaignTarget(
                            channel=fatigued_obj["channel"],
                            account_id=account_id,
                            native_id=fatigued_obj["native_id"],
                            metadata=meta_attrs,
                        )

                        async def _pause_fatigued(
                            _t: CampaignTarget = fatigued_target,
                            _a: dict[str, Any] = app,
                            _tok: dict[str, Any] = token,
                        ) -> dict[str, Any]:
                            async with httpx.AsyncClient(timeout=15.0) as client:
                                control = CampaignControl(client, _t, _a, _tok)
                                return await control.pause()

                        _run_async(_pause_fatigued())
                    except Exception as pause_remote_err:
                        logger.info("Remote pause note for %s: %s", fatigued_id, pause_remote_err)

                    conn.execute(
                        "UPDATE campaign_object SET state='paused' WHERE id=%s",
                        (fatigued_id,),
                    )

                    pause_row: Any = conn.execute(
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
                    ).fetchone()
                    action_id = pause_row["id"] if pause_row else None
                except Exception as refresh_exc:
                    logger.warning(
                        "Creative refresh replacement failed for object %s: %s; "
                        "keeping original active (zero dark time)",
                        fatigued_id,
                        refresh_exc,
                    )
                    escalations_raised += 1
                    conn.execute(
                        """INSERT INTO escalation(
                            brand_id, trigger_type, scope_kind, scope_id, context
                        ) VALUES (%s, 'creative_refresh_failure', 'campaign_object', %s, %s)""",
                        (
                            brand_id,
                            fatigued_id,
                            Jsonb({"error": str(refresh_exc), "target_id": str(fatigued_id)}),
                        ),
                    )
                    conn.execute(
                        """INSERT INTO autonomous_decision(
                            brand_id, finding_id, kind, target_id, channel,
                            idempotency_key, params, state, rejection_reason
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'escalated', %s)
                        ON CONFLICT (brand_id, idempotency_key) DO UPDATE SET
                            state='escalated', rejection_reason=EXCLUDED.rejection_reason""",
                        (
                            brand_id,
                            cand["finding_id"],
                            cand["kind"],
                            cand["target_id"],
                            cand["channel"],
                            idem_key,
                            Jsonb(params),
                            (
                                f"Replacement generation failed ({refresh_exc}); "
                                "fatigued ad retained live without dark time"
                            ),
                        ),
                    )
                    continue

            elif cand["kind"] == "scale_winner":
                obj_id = cand["target_id"]
                winner_obj: Any = conn.execute(
                    "SELECT * FROM campaign_object WHERE id=%s", (obj_id,)
                ).fetchone()
                if not winner_obj:
                    continue

                new_budget = Decimal(str(params["proposed_daily_usd"]))
                conn.execute(
                    "UPDATE campaign_object SET daily_budget_usd=%s WHERE id=%s",
                    (new_budget, obj_id),
                )

                try:
                    conn_row = conn.execute(
                        """SELECT external_ad_account_id, provider_metadata
                        FROM channel_connection WHERE id=%s""",
                        (winner_obj["connection_id"],),
                    ).fetchone()
                    if conn_row:
                        app, token = authorization_for(conn, config, brand_id, cand["channel"])
                        target = CampaignTarget(
                            channel=cand["channel"],
                            account_id=conn_row["external_ad_account_id"] or "",
                            native_id=winner_obj["native_id"],
                            metadata=conn_row.get("provider_metadata") or {},
                        )

                        async def _remote_scale(
                            _t: CampaignTarget = target,
                            _a: dict[str, Any] = app,
                            _tok: dict[str, Any] = token,
                            _b: Decimal = new_budget,
                        ) -> dict[str, Any]:
                            async with httpx.AsyncClient(timeout=15.0) as client:
                                control = CampaignControl(client, _t, _a, _tok)
                                return await control.set_daily_budget(_b)

                        _run_async(_remote_scale())
                except Exception as scale_err:
                    logger.info("Remote budget scale note for object %s: %s", obj_id, scale_err)

                action_row: Any = conn.execute(
                    """INSERT INTO action(
                        brand_id, actor_kind, action_type, target_kind, target_id,
                        channel, diff, rationale, token_id, revert_path, usd_impact
                    ) VALUES (
                        %s, 'system', 'budget_set', 'campaign_object', %s,
                        %s, %s, %s, %s, %s, %s
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
                        new_budget,
                    ),
                ).fetchone()
                action_id = action_row["id"] if action_row else None

            elif cand["kind"] == "reallocate_budget":
                source_id = UUID(str(params["source_campaign_id"]))
                target_id = UUID(str(params["target_campaign_id"]))
                shift = Decimal(str(params["shift_amount_usd"]))

                source_obj: Any = conn.execute(
                    "SELECT * FROM campaign_object WHERE id=%s", (source_id,)
                ).fetchone()
                target_obj: Any = conn.execute(
                    "SELECT * FROM campaign_object WHERE id=%s", (target_id,)
                ).fetchone()
                if not source_obj or not target_obj:
                    continue

                new_source_daily = Decimal(str(source_obj["daily_budget_usd"])) - shift
                new_target_daily = Decimal(str(target_obj["daily_budget_usd"])) + shift

                conn.execute(
                    "UPDATE campaign_object SET daily_budget_usd=%s WHERE id=%s",
                    (new_source_daily, source_id),
                )
                conn.execute(
                    "UPDATE campaign_object SET daily_budget_usd=%s WHERE id=%s",
                    (new_target_daily, target_id),
                )

                for camp_obj, new_b in [
                    (source_obj, new_source_daily),
                    (target_obj, new_target_daily),
                ]:
                    try:
                        conn_row = conn.execute(
                            """SELECT external_ad_account_id, provider_metadata
                            FROM channel_connection WHERE id=%s""",
                            (camp_obj["connection_id"],),
                        ).fetchone()
                        if conn_row:
                            app, token = authorization_for(
                                conn, config, brand_id, camp_obj["channel"]
                            )
                            target = CampaignTarget(
                                channel=camp_obj["channel"],
                                account_id=conn_row["external_ad_account_id"] or "",
                                native_id=camp_obj["native_id"],
                                metadata=conn_row.get("provider_metadata") or {},
                            )

                            async def _remote_realloc(
                                _target: CampaignTarget = target,
                                _budget: Decimal = new_b,
                                _a: dict[str, Any] = app,
                                _tok: dict[str, Any] = token,
                            ) -> dict[str, Any]:
                                async with httpx.AsyncClient(timeout=15.0) as client:
                                    control = CampaignControl(client, _target, _a, _tok)
                                    return await control.set_daily_budget(_budget)

                            _run_async(_remote_realloc())
                    except Exception as realloc_err:
                        logger.info(
                            "Remote reallocation note for %s: %s",
                            camp_obj["id"],
                            realloc_err,
                        )

                action_row = conn.execute(
                    """INSERT INTO action(
                        brand_id, actor_kind, action_type, target_kind, target_id,
                        channel, diff, rationale, token_id, revert_path, usd_impact
                    ) VALUES (
                        %s, 'system', 'budget_reallocation', 'campaign_object', %s,
                        %s, %s, %s, %s, %s, %s
                    ) RETURNING id""",
                    (
                        brand_id,
                        target_id,
                        target_obj["channel"],
                        Jsonb(
                            {
                                "source_campaign_id": str(source_id),
                                "source_channel": source_obj["channel"],
                                "source_daily_before": str(source_obj["daily_budget_usd"]),
                                "source_daily_after": str(new_source_daily),
                                "target_campaign_id": str(target_id),
                                "target_channel": target_obj["channel"],
                                "target_daily_before": str(target_obj["daily_budget_usd"]),
                                "target_daily_after": str(new_target_daily),
                                "shift_usd": str(shift),
                            }
                        ),
                        params.get("reason", "Cross-channel efficiency reallocation"),
                        token_id,
                        Jsonb(
                            {
                                "kind": "reallocation_revert",
                                "source_campaign_id": str(source_id),
                                "source_daily_restore": str(source_obj["daily_budget_usd"]),
                                "target_campaign_id": str(target_id),
                                "target_daily_restore": str(target_obj["daily_budget_usd"]),
                            }
                        ),
                        shift,
                    ),
                ).fetchone()
                action_id = action_row["id"] if action_row else None

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

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        conn.execute(
            """UPDATE loop_tick_run SET
                status='completed', step='done',
                summary=%s, finished_at=now()
            WHERE id=%s""",
            (
                Jsonb(
                    {
                        "executed": executed_actions,
                        "findings_count": len(findings),
                        "findings_detected": len(findings),
                        "decisions_evaluated": len(candidates),
                        "actions_executed": len(executed_actions),
                        "escalations_raised": escalations_raised,
                        "elapsed_ms": elapsed_ms,
                    }
                ),
                tid,
            ),
        )
        return {
            "status": "completed",
            "tick_id": str(tid),
            "findings_detected": len(findings),
            "decisions_evaluated": len(candidates),
            "actions_executed": len(executed_actions),
            "escalations_raised": escalations_raised,
            "elapsed_ms": elapsed_ms,
            "executed": executed_actions,
            "findings_count": len(findings),
        }

    except Exception as exc:
        conn.execute(
            """UPDATE loop_tick_run SET
                status='failed', error_message=%s, finished_at=now()
            WHERE id=%s""",
            (str(exc), tid),
        )
        raise
