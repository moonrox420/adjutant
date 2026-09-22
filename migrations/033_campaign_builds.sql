SET search_path=adjutant,public;

CREATE TABLE campaign_build (
    id uuid PRIMARY KEY,
    brand_id uuid NOT NULL REFERENCES brand(id),
    plan_id uuid NOT NULL REFERENCES plan(id),
    connection_id uuid NOT NULL REFERENCES channel_connection(id),
    channel channel NOT NULL,
    plan_hash text NOT NULL CHECK(plan_hash ~ '^[a-f0-9]{64}$'),
    guardrail_version integer NOT NULL,
    authorization_generation integer NOT NULL,
    requested_by uuid NOT NULL REFERENCES app_user(id),
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object'),
    daily_budget_usd numeric(12,2) NOT NULL CHECK(daily_budget_usd>0),
    monthly_budget_usd numeric(12,2) NOT NULL CHECK(monthly_budget_usd>=daily_budget_usd),
    state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','paused','failed','cancelled')),
    cancellation_requested boolean NOT NULL DEFAULT false,
    error_code text,
    error_message text,
    attempt_count integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    verified_at timestamptz,
    UNIQUE(id,brand_id),
    CHECK(state<>'paused' OR verified_at IS NOT NULL)
);
CREATE UNIQUE INDEX active_campaign_build ON campaign_build(plan_id,connection_id)
    WHERE state IN ('queued','running','paused');

CREATE TABLE campaign_build_step (
    build_id uuid NOT NULL,
    brand_id uuid NOT NULL,
    step_key text NOT NULL,
    request jsonb NOT NULL,
    native_id text,
    response jsonb,
    sent_at timestamptz NOT NULL DEFAULT now(),
    verified_at timestamptz,
    PRIMARY KEY(build_id,step_key),
    FOREIGN KEY(build_id,brand_id) REFERENCES campaign_build(id,brand_id),
    CHECK(verified_at IS NULL OR native_id IS NOT NULL)
);
ALTER TABLE campaign_object ADD COLUMN build_id uuid REFERENCES campaign_build(id);

CREATE FUNCTION validate_campaign_build(target uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
DECLARE job campaign_build; limits guardrail; connection channel_connection;
    allocation plan_allocation; daily numeric; monthly numeric; channel_monthly numeric;
BEGIN
    SELECT * INTO STRICT job FROM campaign_build WHERE id=target;
    IF NOT job.brand_id=ANY(current_brand_ids()) THEN
        RAISE EXCEPTION 'Campaign is outside tenant scope' USING ERRCODE='42501';
    END IF;
    PERFORM lock_runner_brand(job.brand_id);
    SELECT * INTO STRICT limits FROM guardrail WHERE brand_id=job.brand_id;
    SELECT * INTO STRICT connection FROM channel_connection WHERE id=job.connection_id;
    SELECT * INTO STRICT allocation FROM plan_allocation
        WHERE plan_id=job.plan_id AND brand_id=job.brand_id AND channel=job.channel;
    IF job.cancellation_requested OR job.state NOT IN ('queued','running')
       OR limits.version<>job.guardrail_version
       OR connection.brand_id<>job.brand_id OR connection.channel<>job.channel
       OR connection.authorization_generation<>job.authorization_generation
       OR NOT connection.selected OR connection.health<>'healthy'
       OR connection.verified_at IS NULL OR connection.token_expires_at<=now()
       OR NOT EXISTS(SELECT 1 FROM plan WHERE id=job.plan_id AND brand_id=job.brand_id
           AND plan_hash=job.plan_hash)
       OR EXISTS(SELECT 1 FROM brand_kill_switch WHERE brand_id=job.brand_id AND released_at IS NULL)
       OR NOT EXISTS(SELECT 1 FROM brand WHERE id=job.brand_id AND campaigns_enabled)
       OR allocation.daily_budget_usd<>job.daily_budget_usd
       OR allocation.monthly_budget_usd<>job.monthly_budget_usd
    THEN RAISE EXCEPTION 'Campaign scope, account, plan, or guardrail changed' USING ERRCODE='23514'; END IF;
    SELECT coalesce(sum(daily_budget_usd),0),coalesce(sum(monthly_budget_usd),0),
        coalesce(sum(monthly_budget_usd) FILTER(WHERE channel=job.channel),0)
    INTO daily,monthly,channel_monthly FROM campaign_build
    WHERE brand_id=job.brand_id AND (state<>'cancelled'
        OR EXISTS(SELECT 1 FROM campaign_build_step s WHERE s.build_id=campaign_build.id));
    IF daily>limits.daily_spend_cap_usd OR monthly>limits.monthly_spend_cap_usd
        OR channel_monthly*100>limits.monthly_spend_cap_usd*limits.per_channel_cap_pct
        OR channel_monthly*100<limits.monthly_spend_cap_usd*limits.min_channel_floor_pct
    THEN RAISE EXCEPTION 'Campaign reservations exceed brand or channel spend limits' USING ERRCODE='23514'; END IF;
END;
$$;
REVOKE ALL ON FUNCTION validate_campaign_build(uuid) FROM PUBLIC;

CREATE FUNCTION protect_campaign_build() RETURNS trigger LANGUAGE plpgsql
SET search_path=adjutant,pg_temp AS $$
BEGIN
    IF TG_OP='UPDATE' AND (NEW.id,NEW.brand_id,NEW.plan_id,NEW.connection_id,NEW.channel,
        NEW.plan_hash,NEW.guardrail_version,NEW.authorization_generation,NEW.requested_by,
        NEW.document,NEW.daily_budget_usd,NEW.monthly_budget_usd,NEW.created_at)
        IS DISTINCT FROM (OLD.id,OLD.brand_id,OLD.plan_id,OLD.connection_id,OLD.channel,
        OLD.plan_hash,OLD.guardrail_version,OLD.authorization_generation,OLD.requested_by,
        OLD.document,OLD.daily_budget_usd,OLD.monthly_budget_usd,OLD.created_at)
    THEN RAISE EXCEPTION 'Campaign execution scope is immutable' USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' AND NOT EXISTS(SELECT 1 FROM plan p JOIN channel_connection c
        ON c.brand_id=p.brand_id WHERE p.id=NEW.plan_id AND p.brand_id=NEW.brand_id
        AND c.id=NEW.connection_id AND c.channel=NEW.channel) THEN
        RAISE EXCEPTION 'Campaign ancestry crosses account or brand' USING ERRCODE='23514';
    END IF;
    NEW.updated_at=now();
    RETURN NEW;
END;
$$;
CREATE TRIGGER protect_campaign_build BEFORE INSERT OR UPDATE ON campaign_build
FOR EACH ROW EXECUTE FUNCTION protect_campaign_build();

CREATE FUNCTION guard_campaign_build_step() RETURNS trigger LANGUAGE plpgsql
SET search_path=adjutant,pg_temp AS $$
DECLARE limits guardrail; channel_row campaign_build; used integer;
BEGIN
    IF TG_OP='UPDATE' THEN
        IF (NEW.build_id,NEW.brand_id,NEW.step_key,NEW.request,NEW.sent_at)
            IS DISTINCT FROM (OLD.build_id,OLD.brand_id,OLD.step_key,OLD.request,OLD.sent_at)
            OR (OLD.native_id IS NOT NULL AND NEW.native_id IS DISTINCT FROM OLD.native_id)
        THEN RAISE EXCEPTION 'Provider operation identity is immutable' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    PERFORM validate_campaign_build(NEW.build_id);
    SELECT * INTO STRICT channel_row FROM campaign_build WHERE id=NEW.build_id;
    SELECT * INTO STRICT limits FROM guardrail WHERE brand_id=NEW.brand_id;
    IF NEW.step_key='campaign' THEN
        SELECT count(*) INTO used FROM campaign_build_step
            WHERE brand_id=NEW.brand_id AND step_key='campaign' AND sent_at>now()-interval '24 hours';
        IF used>=limits.max_new_campaigns_per_day THEN
            RAISE EXCEPTION 'Rolling campaign creation limit reached' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.step_key LIKE 'ad:%' THEN
        SELECT count(*) INTO used FROM campaign_build_step
            WHERE brand_id=NEW.brand_id AND step_key LIKE 'ad:%' AND sent_at>now()-interval '24 hours';
        IF used>=limits.max_new_ads_per_day THEN
            RAISE EXCEPTION 'Rolling ad creation limit reached' USING ERRCODE='23514';
        END IF;
    END IF;
    IF EXISTS(SELECT 1 FROM unnest(limits.blocked_claims) claim
        WHERE position(lower(claim) IN lower(NEW.request::text))>0) THEN
        RAISE EXCEPTION 'Provider request contains a blocked claim' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_campaign_build_step BEFORE INSERT OR UPDATE ON campaign_build_step
FOR EACH ROW EXECUTE FUNCTION guard_campaign_build_step();

DO $$ DECLARE target text; BEGIN
    FOREACH target IN ARRAY ARRAY['campaign_build','campaign_build_step'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',target);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',target);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING(brand_id=ANY(current_brand_ids()))
            WITH CHECK(brand_id=ANY(current_brand_ids()))',target);
    END LOOP;
END $$;
CREATE FUNCTION runnable_campaign_builds() RETURNS TABLE(id uuid,brand_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
    SELECT id,brand_id FROM campaign_build WHERE state IN ('queued','running')
    ORDER BY created_at LIMIT 32;
$$;
REVOKE ALL ON FUNCTION runnable_campaign_builds() FROM PUBLIC;
