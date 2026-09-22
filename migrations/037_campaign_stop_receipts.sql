SET search_path=adjutant,public;

CREATE OR REPLACE FUNCTION check_build_object_ancestry() RETURNS trigger LANGUAGE plpgsql
SET search_path=adjutant,pg_temp AS $$
DECLARE job campaign_build; operation campaign_build_step; key text; pause_verified boolean;
BEGIN
    IF NEW.build_id IS NULL THEN RETURN NEW; END IF;
    SELECT * INTO STRICT job FROM campaign_build WHERE id=NEW.build_id;
    IF (NEW.brand_id,NEW.connection_id,NEW.channel,NEW.plan_id)
        IS DISTINCT FROM (job.brand_id,job.connection_id,job.channel,job.plan_id)
        OR (NEW.parent_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM campaign_object
            WHERE id=NEW.parent_id AND brand_id=job.brand_id AND build_id=job.id))
        OR (NEW.level='campaign' AND NEW.parent_id IS NOT NULL)
        OR (NEW.level<>'campaign' AND NEW.parent_id IS NULL)
    THEN RAISE EXCEPTION 'Provider object ancestry does not match the deployment' USING ERRCODE='23514'; END IF;
    IF TG_OP='UPDATE' AND (NEW.brand_id,NEW.connection_id,NEW.channel,NEW.plan_id,NEW.level,
        NEW.native_id,NEW.parent_id,NEW.creative_id,NEW.idem_key,NEW.build_id)
        IS DISTINCT FROM (OLD.brand_id,OLD.connection_id,OLD.channel,OLD.plan_id,OLD.level,
        OLD.native_id,OLD.parent_id,OLD.creative_id,OLD.idem_key,OLD.build_id)
    THEN RAISE EXCEPTION 'Provider object identity is immutable' USING ERRCODE='23514'; END IF;
    key=CASE WHEN NEW.level='ad' THEN 'ad:'||NEW.creative_id::text ELSE NEW.level::text END;
    SELECT * INTO operation FROM campaign_build_step WHERE build_id=job.id AND step_key=key;
    pause_verified=coalesce(operation.verified_at IS NOT NULL
        AND operation.response->>'status'='PAUSED',false)
        OR EXISTS(SELECT 1 FROM remote_stop_item WHERE campaign_object_id=NEW.id
            AND brand_id=NEW.brand_id AND connection_id=NEW.connection_id
            AND native_id=NEW.native_id AND state='verified' AND observed_state='paused'
            AND verified_at IS NOT NULL);
    IF operation.native_id IS DISTINCT FROM NEW.native_id OR NEW.idem_key<>job.id::text||':'||key
        OR (NEW.state='paused' AND NOT pause_verified) OR NEW.state='active'
    THEN RAISE EXCEPTION 'Provider state has no verified execution receipt' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
$$;
