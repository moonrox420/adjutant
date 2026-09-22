SET search_path=adjutant,public;

CREATE TABLE guardrail (
    brand_id uuid PRIMARY KEY REFERENCES brand(id),
    version integer NOT NULL DEFAULT 1 CHECK (version>0),
    monthly_spend_cap_usd numeric(12,2) NOT NULL CHECK (monthly_spend_cap_usd>0),
    daily_spend_cap_usd numeric(12,2) NOT NULL CHECK (daily_spend_cap_usd>0),
    max_daily_spend_increase_pct numeric(6,2) NOT NULL DEFAULT 25
        CHECK (max_daily_spend_increase_pct BETWEEN 0 AND 100),
    max_new_campaigns_per_day integer NOT NULL DEFAULT 3 CHECK (max_new_campaigns_per_day>0),
    max_new_ads_per_day integer NOT NULL DEFAULT 20 CHECK (max_new_ads_per_day>0),
    per_channel_cap_pct numeric(5,2) NOT NULL DEFAULT 60 CHECK (per_channel_cap_pct>0 AND per_channel_cap_pct<=100),
    min_channel_floor_pct numeric(5,2) NOT NULL DEFAULT 0 CHECK (min_channel_floor_pct BETWEEN 0 AND per_channel_cap_pct),
    blocked_claims text[] NOT NULL DEFAULT '{}',
    requires_approval_above_usd numeric(12,2) CHECK (requires_approval_above_usd>0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (daily_spend_cap_usd<=monthly_spend_cap_usd)
);

INSERT INTO guardrail(brand_id,monthly_spend_cap_usd,daily_spend_cap_usd)
SELECT brand_id,monthly_usd_max,least(daily_usd_max,monthly_usd_max)
FROM budget_ceiling WHERE scope_kind='brand';

CREATE FUNCTION synchronize_brand_caps() RETURNS trigger LANGUAGE plpgsql
SET search_path=adjutant,pg_temp AS $$
BEGIN
    IF pg_trigger_depth()>1 THEN RETURN NEW; END IF;
    IF TG_TABLE_NAME='budget_ceiling' THEN
        IF NEW.scope_kind='brand' THEN
            INSERT INTO guardrail(brand_id,monthly_spend_cap_usd,daily_spend_cap_usd)
            VALUES(NEW.brand_id,NEW.monthly_usd_max,NEW.daily_usd_max)
            ON CONFLICT(brand_id) DO UPDATE SET
                monthly_spend_cap_usd=excluded.monthly_spend_cap_usd,
                daily_spend_cap_usd=excluded.daily_spend_cap_usd,
                version=guardrail.version+1,updated_at=now();
        END IF;
    ELSE
        UPDATE budget_ceiling SET monthly_usd_max=NEW.monthly_spend_cap_usd,
            daily_usd_max=NEW.daily_spend_cap_usd
        WHERE brand_id=NEW.brand_id AND scope_kind='brand';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER sync_guardrail_caps AFTER INSERT OR UPDATE ON budget_ceiling
FOR EACH ROW EXECUTE FUNCTION synchronize_brand_caps();
CREATE TRIGGER sync_ceiling_caps AFTER UPDATE ON guardrail
FOR EACH ROW EXECUTE FUNCTION synchronize_brand_caps();

CREATE TABLE launch_authorization (
    id uuid PRIMARY KEY,
    brand_id uuid NOT NULL REFERENCES brand(id),
    plan_id uuid NOT NULL REFERENCES plan(id),
    plan_hash text NOT NULL CHECK (plan_hash ~ '^[a-f0-9]{64}$'),
    request_key uuid NOT NULL,
    guardrail_version integer NOT NULL,
    approver_id uuid NOT NULL REFERENCES app_user(id),
    claims jsonb NOT NULL,
    signature bytea NOT NULL CHECK (octet_length(signature)=64),
    signing_key_id text NOT NULL,
    issued_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    UNIQUE (brand_id,request_key),
    UNIQUE (id,brand_id),
    CHECK (expires_at>issued_at AND expires_at<=issued_at+interval '72 hours')
);
CREATE TRIGGER immutable_launch_authorization BEFORE UPDATE OR DELETE ON launch_authorization
FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();

CREATE TABLE channel_launch_grant (
    brand_id uuid NOT NULL REFERENCES brand(id),
    connection_id uuid NOT NULL REFERENCES channel_connection(id),
    authorization_id uuid NOT NULL,
    authorized_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(brand_id,connection_id),
    FOREIGN KEY(authorization_id,brand_id) REFERENCES launch_authorization(id,brand_id)
);
CREATE TRIGGER immutable_channel_launch_grant BEFORE UPDATE OR DELETE ON channel_launch_grant
FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();

CREATE FUNCTION lock_runner_brand(target uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
BEGIN
    IF NOT target=ANY(current_brand_ids()) THEN
        RAISE EXCEPTION 'Brand is outside tenant scope' USING ERRCODE='42501';
    END IF;
    PERFORM 1 FROM brand WHERE id=target FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Brand not found' USING ERRCODE='23503'; END IF;
END;
$$;
REVOKE ALL ON FUNCTION lock_runner_brand(uuid) FROM PUBLIC;

CREATE FUNCTION validate_launch_grant() RETURNS trigger LANGUAGE plpgsql
SET search_path=adjutant,pg_temp AS $$
DECLARE approval launch_authorization; limits guardrail; channel_connection_row channel_connection;
BEGIN
    PERFORM lock_runner_brand(NEW.brand_id);
    SELECT * INTO STRICT approval FROM launch_authorization WHERE id=NEW.authorization_id;
    SELECT * INTO STRICT limits FROM guardrail WHERE brand_id=NEW.brand_id;
    SELECT * INTO STRICT channel_connection_row FROM channel_connection WHERE id=NEW.connection_id;
    IF approval.expires_at<=now() OR approval.issued_at>now()
       OR approval.guardrail_version<>limits.version
       OR channel_connection_row.brand_id<>NEW.brand_id OR NOT channel_connection_row.selected
       OR channel_connection_row.health<>'healthy' OR channel_connection_row.verified_at IS NULL
       OR channel_connection_row.token_expires_at<=now()
       OR NOT (approval.claims->'connections' ? NEW.connection_id::text)
       OR NOT EXISTS(SELECT 1 FROM plan WHERE id=approval.plan_id AND brand_id=NEW.brand_id
                     AND plan_hash=approval.plan_hash)
       OR EXISTS(SELECT 1 FROM brand_kill_switch WHERE brand_id=NEW.brand_id AND released_at IS NULL)
    THEN RAISE EXCEPTION 'Launch authorization is stale or outside its scope' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM plan_allocation WHERE plan_id=approval.plan_id
        AND (daily_budget_usd IS NULL
            OR monthly_budget_usd*100>limits.monthly_spend_cap_usd*limits.per_channel_cap_pct
            OR monthly_budget_usd*100<limits.monthly_spend_cap_usd*limits.min_channel_floor_pct))
        OR (SELECT sum(monthly_budget_usd)>limits.monthly_spend_cap_usd
            OR sum(daily_budget_usd)>limits.daily_spend_cap_usd
            FROM plan_allocation WHERE plan_id=approval.plan_id)
    THEN RAISE EXCEPTION 'Plan exceeds current guardrails' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER enforce_launch_grant BEFORE INSERT ON channel_launch_grant
FOR EACH ROW EXECUTE FUNCTION validate_launch_grant();

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['guardrail','launch_authorization','channel_launch_grant'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (brand_id=ANY(current_brand_ids()))
            WITH CHECK (brand_id=ANY(current_brand_ids()))',t);
    END LOOP;
END;
$$;
