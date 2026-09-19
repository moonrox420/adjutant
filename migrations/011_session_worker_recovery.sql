SET search_path=adjutant,public;

CREATE OR REPLACE FUNCTION lock_session(p_hash text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
BEGIN
    -- Match password reset's lock order: identity, then session, then application rows.
    PERFORM 1 FROM app_user u JOIN auth_session s ON s.user_id=u.id
        WHERE s.token_hash=p_hash AND u.is_active AND u.email_verified_at IS NOT NULL
        FOR UPDATE OF u;
    IF NOT FOUND THEN RETURN false; END IF;
    PERFORM 1 FROM auth_session WHERE token_hash=p_hash AND expires_at>now() FOR UPDATE;
    RETURN FOUND;
END;
$$;
CREATE FUNCTION reap_abandoned_jobs() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
DECLARE n integer;
BEGIN
    UPDATE agent_run SET cancel_requested_at=COALESCE(cancel_requested_at,now()),
        finished_at=now(),error_code='WorkerHeartbeatLost',schema_valid=false,usage_complete=false
    WHERE finished_at IS NULL AND session_hash IS NOT NULL
      AND COALESCE(worker_heartbeat_at,started_at)<now()-interval '30 seconds';
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;
ALTER TABLE consumer_process ALTER COLUMN heartbeat_at SET DEFAULT '1970-01-01 00:00:00+00'::timestamptz;
REVOKE ALL ON FUNCTION reap_abandoned_jobs() FROM PUBLIC;
