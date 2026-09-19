"""Exercise the worker's complete SECURITY DEFINER surface against real PostgreSQL."""

from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb


@pytest.fixture
def other_brand(admin):
    account, brand = uuid4(), uuid4()
    admin.execute(
        "INSERT INTO account(id,account_type,display_name) VALUES(%s,'agency','Other tenant')",
        (account,),
    )
    admin.execute(
        "INSERT INTO brand(id,account_id,display_name) VALUES(%s,%s,'Private tenant')",
        (brand, account),
    )
    return brand


def test_worker_definer_allowlist_and_no_function_replacement(worker_url, identity):
    with psycopg.connect(worker_url, autocommit=True) as conn:
        functions = conn.execute("""SELECT p.proname,p.proconfig,
            pg_get_function_identity_arguments(p.oid),pg_get_function_result(p.oid),
            pg_has_role(current_user,p.proowner,'USAGE')
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='adjutant' AND p.prosecdef
            AND has_function_privilege(current_user,p.oid,'EXECUTE')
            ORDER BY p.proname""").fetchall()
        assert [(row[0], row[2], row[3]) for row in functions] == [
            ("consume_activity_batch", "p_limit integer", "integer"),
            ("reap_abandoned_jobs", "", "integer"),
        ]
        for _, configuration, _, _, owns_function in functions:
            assert "search_path=adjutant,pg_temp" in [
                value.replace(" ", "") for value in configuration
            ]
            assert owns_function is False
        assert conn.execute(
            "SELECT has_schema_privilege(current_user,'adjutant','CREATE')"
        ).fetchone() == (False,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM adjutant.login_identity(%s)", (identity["email"],))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM adjutant.cancelled_session_jobs('arbitrary-session')")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "ALTER FUNCTION adjutant.consume_activity_batch(integer) RESET search_path"
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "CREATE FUNCTION adjutant.worker_escalation() RETURNS integer "
                "LANGUAGE sql AS 'SELECT 1'"
            )
        with pytest.raises(psycopg.errors.InvalidTextRepresentation):
            conn.execute(
                "SELECT adjutant.consume_activity_batch(%s)",
                ("1); SELECT * FROM adjutant.brand; --",),
            )


def test_activity_function_is_bounded_metadata_only_and_resists_temp_shadowing(
    worker_url,
    admin,
    brand,
    other_brand,
):
    with psycopg.connect(worker_url, autocommit=True) as conn:
        while conn.execute("SELECT adjutant.consume_activity_batch(100)").fetchone()[0]:
            continue
        ids = []
        for tenant in (brand, other_brand):
            for _ in range(61):
                event = uuid4()
                ids.append(event)
                admin.execute(
                    """INSERT INTO event_outbox(event_id,brand_id,event_type,topic,
                    partition_key,envelope,occurred_at)
                    VALUES(%s,%s,'boundary.test','test',%s,%s,now())""",
                    (
                        event,
                        tenant,
                        str(tenant),
                        Jsonb(
                            {
                                "event_type": "boundary.test",
                                "payload": {"private": "tenant-private-payload"},
                            }
                        ),
                    ),
                )
        before = admin.execute(
            "SELECT * FROM event_outbox WHERE event_id=ANY(%s) ORDER BY id", (ids,)
        ).fetchall()
        conn.execute(
            "CREATE TEMP TABLE event_outbox(id bigint,event_id uuid,brand_id uuid,"
            "event_type text,activity_consumed_at timestamptz)"
        )
        conn.execute(
            "CREATE TEMP TABLE consumer_receipt("
            "event_id uuid PRIMARY KEY,brand_id uuid,event_type text)"
        )
        conn.execute(
            "INSERT INTO pg_temp.event_outbox VALUES(1,%s,%s,'attacker-shadow',NULL)",
            (uuid4(), other_brand),
        )
        conn.execute("SET search_path=pg_temp,public")
        for limit, expected in [
            (0, 1),
            (-100, 1),
            (2147483647, 100),
            (None, 1),
            (100, 19),
            (100, 0),
        ]:
            assert conn.execute(
                "SELECT adjutant.consume_activity_batch(%s)", (limit,)
            ).fetchone() == (expected,)
        assert conn.execute("SELECT activity_consumed_at FROM pg_temp.event_outbox").fetchone() == (
            None,
        )
        assert conn.execute("SELECT count(*) FROM pg_temp.consumer_receipt").fetchone() == (0,)
        for table in ("event_outbox", "consumer_receipt", "brand", "agent_run", "local_credential"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql.SQL("SELECT * FROM adjutant.{}").format(sql.Identifier(table)))
        after = admin.execute(
            "SELECT * FROM event_outbox WHERE event_id=ANY(%s) ORDER BY id", (ids,)
        ).fetchall()
        for original, updated in zip(before, after, strict=True):
            assert updated.pop("activity_consumed_at") is not None
            assert original.pop("activity_consumed_at") is None
            assert updated == original
        receipts = admin.execute(
            "SELECT * FROM consumer_receipt WHERE event_id=ANY(%s)", (ids,)
        ).fetchall()
        assert len(receipts) == len(ids)
        assert {str(row["brand_id"]) for row in receipts} == {str(brand), str(other_brand)}
        assert all(
            set(row) == {"event_id", "brand_id", "event_type", "processed_at"} for row in receipts
        )


def test_reaper_only_changes_abandoned_session_jobs_across_tenants(
    worker_url,
    admin,
    brand,
    other_brand,
    identity,
):
    ids = {}
    for label, tenant, session, heartbeat, finished in [
        ("stale", brand, "private-session-a", "1 minute", False),
        ("other-stale", other_brand, "private-session-b", "1 minute", False),
        ("live", brand, "private-session-c", "0 seconds", False),
        ("finished", brand, "private-session-d", "1 minute", True),
        ("without-session", brand, None, "1 minute", False),
    ]:
        ids[label] = admin.execute(
            """INSERT INTO agent_run(brand_id,agent_name,model_id,model_tier,
            actor_user_id,session_hash,started_at,worker_heartbeat_at,finished_at,schema_valid)
            VALUES(%s,'Strategist','private-model','local',%s,%s,now()-interval '1 minute',
                   now()-%s::interval,CASE WHEN %s THEN now() ELSE NULL END,true) RETURNING id""",
            (tenant, identity["user"], session, heartbeat, finished),
        ).fetchone()["id"]
    before = {
        label: admin.execute("SELECT * FROM agent_run WHERE id=%s", (run,)).fetchone()
        for label, run in ids.items()
    }
    with psycopg.connect(worker_url, autocommit=True) as conn:
        conn.execute("""CREATE TEMP TABLE agent_run(
            id integer,session_hash text,started_at timestamptz,
            worker_heartbeat_at timestamptz,finished_at timestamptz,cancel_requested_at timestamptz,
            error_code text,schema_valid boolean,usage_complete boolean)""")
        conn.execute("""INSERT INTO pg_temp.agent_run VALUES(1,'shadow',now()-interval '1 minute',
            now()-interval '1 minute',NULL,NULL,NULL,true,true)""")
        conn.execute("SET search_path=pg_temp,public")
        result = conn.execute("SELECT adjutant.reap_abandoned_jobs()").fetchone()
        assert len(result) == 1 and isinstance(result[0], int) and result[0] >= 2
        assert conn.execute("SELECT finished_at,error_code FROM pg_temp.agent_run").fetchone() == (
            None,
            None,
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM adjutant.agent_run")
    for label, run in ids.items():
        after = admin.execute("SELECT * FROM agent_run WHERE id=%s", (run,)).fetchone()
        if label in {"stale", "other-stale"}:
            assert after["finished_at"] and after["cancel_requested_at"]
            assert after["error_code"] == "WorkerHeartbeatLost"
            assert after["schema_valid"] is False and after["usage_complete"] is False
            allowed = {
                "finished_at",
                "cancel_requested_at",
                "error_code",
                "schema_valid",
                "usage_complete",
            }
            assert {key: value for key, value in after.items() if key not in allowed} == {
                key: value for key, value in before[label].items() if key not in allowed
            }
            assert after["worker_exit_code"] is None and after["worker_exit_verified_at"] is None
        else:
            assert after == before[label]
