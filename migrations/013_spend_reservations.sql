SET search_path=adjutant,public;

ALTER TABLE approval_token_consumption ADD COLUMN usd_daily_committed numeric(12,2)
    NOT NULL DEFAULT 0 CHECK(usd_daily_committed>=0);
ALTER TABLE approval_token_consumption ADD COLUMN subject_hash text;
ALTER TABLE approval_token_consumption ADD COLUMN payload_hash text;

CREATE FUNCTION lock_spend_authority(brand_uuid uuid, token_uuid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
BEGIN
    IF NOT brand_uuid=ANY(current_brand_ids()) THEN
        RAISE EXCEPTION 'Brand context required' USING ERRCODE='42501';
    END IF;
    PERFORM 1 FROM brand WHERE id=brand_uuid FOR UPDATE;
    PERFORM 1 FROM approval_token WHERE id=token_uuid AND brand_id=brand_uuid FOR UPDATE;
END;
$$;
REVOKE ALL ON FUNCTION lock_spend_authority(uuid,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION guard_token_consumption() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
DECLARE t approval_token%ROWTYPE; committed numeric; daily numeric;
BEGIN
    SELECT * INTO t FROM approval_token WHERE id=NEW.token_id FOR UPDATE;
    IF NOT FOUND OR t.voided_at IS NOT NULL OR t.expires_at<=now()
       OR NOT ('channel:'||NEW.channel::text=ANY(t.scopes))
       OR NOT ('op:'||NEW.operation=ANY(t.scopes))
       OR (NEW.subject_hash IS NOT NULL AND NEW.subject_hash<>t.subject_hash)
       OR NOT EXISTS(SELECT 1 FROM approval_request a WHERE a.id=t.approval_request_id
                     AND a.state='approved' AND a.subject_hash=t.subject_hash)
       OR NOT EXISTS(SELECT 1 FROM plan p WHERE p.id=t.subject_id AND p.brand_id=t.brand_id
                     AND p.plan_hash=t.subject_hash AND p.state IN ('approved','deploying','live'))
       OR EXISTS(SELECT 1 FROM brand_kill_switch k
                 WHERE k.brand_id=t.brand_id AND k.released_at IS NULL)
    THEN RAISE EXCEPTION 'Invalid spend authorization' USING ERRCODE='23514'; END IF;
    SELECT COALESCE(sum(usd_committed),0),COALESCE(sum(usd_daily_committed),0)
        INTO committed,daily FROM approval_token_consumption WHERE token_id=t.id;
    IF committed+NEW.usd_committed>t.usd_total_cap
       OR daily+NEW.usd_daily_committed>t.usd_daily_cap
    THEN RAISE EXCEPTION 'Cumulative spend authority exceeded' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
$$;
