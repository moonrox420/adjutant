SET search_path=adjutant,public;
-- Windows exposes unsigned 32-bit termination codes (including crash status codes).
ALTER TABLE agent_run ALTER COLUMN worker_exit_code TYPE bigint;
ALTER TABLE consumer_process ALTER COLUMN exit_code TYPE bigint;
DROP FUNCTION cancelled_session_jobs(text);
CREATE FUNCTION cancelled_session_jobs(p_hash text)
RETURNS TABLE(id uuid,worker_pid integer,worker_exit_code bigint,worker_exit_verified_at timestamptz,
              finished_at timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
    SELECT id,worker_pid,worker_exit_code,worker_exit_verified_at,finished_at
    FROM agent_run WHERE session_hash=p_hash AND cancel_requested_at IS NOT NULL;
$$;
REVOKE ALL ON FUNCTION cancelled_session_jobs(text) FROM PUBLIC;
