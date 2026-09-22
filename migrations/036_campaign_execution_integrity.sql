SET search_path=adjutant,public;

CREATE FUNCTION check_campaign_build_insert() RETURNS trigger LANGUAGE plpgsql
SET search_path=adjutant,pg_temp AS $$
BEGIN
    IF NEW.requested_by IS DISTINCT FROM current_actor_id() OR NOT EXISTS(
        SELECT 1 FROM seat s JOIN brand b ON b.account_id=s.account_id
        WHERE b.id=NEW.brand_id AND s.user_id=current_actor_id()
        AND s.accepted_at IS NOT NULL AND s.revoked_at IS NULL
        AND (s.brand_id IS NULL OR s.brand_id=b.id) AND s.role IN ('owner','admin','buyer')
    ) THEN RAISE EXCEPTION 'Campaign creation requires a current operator seat' USING ERRCODE='42501'; END IF;
    PERFORM validate_campaign_build(NEW.id);
    RETURN NEW;
END;
$$;
CREATE TRIGGER campaign_build_insert_guard AFTER INSERT ON campaign_build
FOR EACH ROW EXECUTE FUNCTION check_campaign_build_insert();

CREATE FUNCTION check_campaign_build_snapshot() RETURNS trigger LANGUAGE plpgsql
SET search_path=adjutant,pg_temp AS $$
DECLARE job campaign_build;
BEGIN
    SELECT * INTO STRICT job FROM campaign_build WHERE id=NEW.build_id;
    IF NOT EXISTS(SELECT 1 FROM plan WHERE id=job.plan_id AND brand_id=job.brand_id
        AND objective::text=job.document->>'objective')
        OR (job.document->>'daily_budget_usd')::numeric<>job.daily_budget_usd
        OR (job.document->>'monthly_budget_usd')::numeric<>job.monthly_budget_usd
        OR jsonb_array_length(job.document->'creatives')<1
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(job.document->'creatives') item
            WHERE NOT EXISTS(SELECT 1 FROM creative c JOIN creative_concept cc ON cc.id=c.concept_id
                JOIN rendition r ON r.creative_id=c.id JOIN asset a ON a.id=r.asset_id
                WHERE c.id=(item->>'id')::uuid AND c.brand_id=job.brand_id
                AND cc.plan_id=job.plan_id AND r.id=(item->>'rendition_id')::uuid
                AND r.brand_id=job.brand_id AND r.channel=job.channel AND r.spec_validation_passed
                AND a.brand_id=job.brand_id AND a.storage_uri=item->>'storage_key'
                AND a.content_hash=item->>'content_hash' AND c.copy_fields=item->'copy'))
    THEN RAISE EXCEPTION 'Creative or plan snapshot no longer matches its persisted ancestry' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER campaign_step_snapshot BEFORE INSERT ON campaign_build_step
FOR EACH ROW EXECUTE FUNCTION check_campaign_build_snapshot();

CREATE FUNCTION check_build_object_ancestry() RETURNS trigger LANGUAGE plpgsql
SET search_path=adjutant,pg_temp AS $$
DECLARE job campaign_build; operation campaign_build_step; key text;
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
    IF operation.native_id IS DISTINCT FROM NEW.native_id OR NEW.idem_key<>job.id::text||':'||key
        OR (NEW.state='paused' AND (operation.verified_at IS NULL OR operation.response->>'status'<>'PAUSED'))
        OR NEW.state='active'
    THEN RAISE EXCEPTION 'Provider state has no verified execution receipt' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER build_object_ancestry BEFORE INSERT OR UPDATE ON campaign_object
FOR EACH ROW EXECUTE FUNCTION check_build_object_ancestry();

CREATE FUNCTION check_campaign_build_completion() RETURNS trigger LANGUAGE plpgsql
SET search_path=adjutant,pg_temp AS $$
DECLARE expected integer;
BEGIN
    IF NEW.state='paused' AND OLD.state<>'paused' THEN
        expected=jsonb_array_length(NEW.document->'creatives');
        IF EXISTS(SELECT 1 FROM campaign_build_step WHERE build_id=NEW.id AND verified_at IS NULL)
            OR (SELECT count(*) FROM campaign_build_step WHERE build_id=NEW.id)<>2+3*expected
            OR (SELECT count(*) FROM campaign_object WHERE build_id=NEW.id AND state='paused'
                AND last_verified_at IS NOT NULL)<>2+expected
        THEN RAISE EXCEPTION 'Campaign has not been fully verified in paused state' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER campaign_build_completion BEFORE UPDATE ON campaign_build
FOR EACH ROW EXECUTE FUNCTION check_campaign_build_completion();
