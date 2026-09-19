SET search_path=adjutant,public;

ALTER TABLE event_outbox ADD COLUMN activity_consumed_at timestamptz;
UPDATE event_outbox e SET activity_consumed_at=r.processed_at
FROM consumer_receipt r WHERE r.event_id=e.event_id;
CREATE INDEX activity_pending ON event_outbox(id) WHERE activity_consumed_at IS NULL;
CREATE OR REPLACE FUNCTION consume_activity_batch(p_limit integer) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
DECLARE n integer;
BEGIN
    WITH batch AS MATERIALIZED (
        SELECT id,event_id,brand_id,event_type FROM event_outbox
        WHERE activity_consumed_at IS NULL
        ORDER BY id LIMIT LEAST(GREATEST(p_limit,1),100) FOR UPDATE SKIP LOCKED
    ), receipts AS (
        INSERT INTO consumer_receipt(event_id,brand_id,event_type)
        SELECT event_id,brand_id,event_type FROM batch ON CONFLICT DO NOTHING RETURNING event_id
    )
    UPDATE event_outbox e SET activity_consumed_at=now() FROM batch WHERE e.id=batch.id;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;
