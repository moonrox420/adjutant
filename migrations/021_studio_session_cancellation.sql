SET search_path=adjutant,public;

CREATE FUNCTION cancel_studio_on_session_delete() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
BEGIN
    UPDATE studio_job SET cancel_requested_at=COALESCE(cancel_requested_at,now()),updated_at=now()
    WHERE session_hash=OLD.token_hash AND state IN ('queued','running');
    RETURN OLD;
END;
$$;
REVOKE ALL ON FUNCTION cancel_studio_on_session_delete() FROM PUBLIC;
CREATE TRIGGER cancel_studio_after_session_delete AFTER DELETE ON auth_session
FOR EACH ROW EXECUTE FUNCTION cancel_studio_on_session_delete();

CREATE OR REPLACE FUNCTION cancelled_session_jobs(p_hash text)
RETURNS TABLE(id uuid,worker_pid integer,worker_exit_code bigint,worker_exit_verified_at timestamptz,
              finished_at timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
    SELECT id,worker_pid,worker_exit_code,worker_exit_verified_at,finished_at
    FROM agent_run WHERE session_hash=p_hash AND cancel_requested_at IS NOT NULL
    UNION ALL
    SELECT id,NULL::integer,NULL::bigint,NULL::timestamptz,finished_at
    FROM studio_job WHERE session_hash=p_hash AND cancel_requested_at IS NOT NULL;
$$;

CREATE INDEX studio_job_session_pending ON studio_job(session_hash)
WHERE state IN ('queued','running');
