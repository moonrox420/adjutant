SET search_path=adjutant,public;

-- A local activity projection, not an acknowledgement from an external message broker.
CREATE FUNCTION consume_activity_batch(p_limit integer) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
DECLARE n integer;
BEGIN
    WITH batch AS (
        SELECT e.event_id,e.brand_id,e.event_type FROM event_outbox e
        WHERE NOT EXISTS(SELECT 1 FROM consumer_receipt r WHERE r.event_id=e.event_id)
        ORDER BY e.id LIMIT LEAST(GREATEST(p_limit,1),100)
        FOR UPDATE OF e SKIP LOCKED
    )
    INSERT INTO consumer_receipt(event_id,brand_id,event_type)
        SELECT event_id,brand_id,event_type FROM batch ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;
REVOKE ALL ON FUNCTION consume_activity_batch(integer) FROM PUBLIC;
