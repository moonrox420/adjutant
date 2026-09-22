SET search_path=adjutant,public;

CREATE TABLE remote_stop_run (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id uuid NOT NULL REFERENCES brand(id),
    requested_by uuid NOT NULL REFERENCES app_user(id),
    state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','completed','failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    error_code text,
    UNIQUE(brand_id,id)
);
CREATE TABLE remote_stop_item (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id uuid NOT NULL REFERENCES brand(id),
    run_id uuid NOT NULL,
    campaign_object_id uuid NOT NULL REFERENCES campaign_object(id),
    connection_id uuid NOT NULL REFERENCES channel_connection(id),
    channel channel NOT NULL,
    native_id text NOT NULL,
    account_id text NOT NULL,
    covered_object_ids uuid[] NOT NULL,
    state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','verified','failed')),
    observed_state text,
    provider_status text,
    verified_at timestamptz,
    error_code text,
    error_message text,
    FOREIGN KEY(brand_id,run_id) REFERENCES remote_stop_run(brand_id,id),
    UNIQUE(run_id,campaign_object_id),
    CHECK(state<>'verified' OR (verified_at IS NOT NULL AND observed_state IN ('paused','archived','draft')))
);
CREATE INDEX remote_stop_pending ON remote_stop_run(created_at) WHERE state IN ('queued','running');
ALTER TABLE remote_stop_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE remote_stop_run FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON remote_stop_run
    USING(brand_id=ANY(current_brand_ids())) WITH CHECK(brand_id=ANY(current_brand_ids()));
ALTER TABLE remote_stop_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE remote_stop_item FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON remote_stop_item
    USING(brand_id=ANY(current_brand_ids())) WITH CHECK(brand_id=ANY(current_brand_ids()));

CREATE FUNCTION runnable_remote_stops() RETURNS TABLE(id uuid,brand_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
    SELECT id,brand_id FROM remote_stop_run WHERE state IN ('queued','running')
    ORDER BY created_at,id LIMIT 64;
$$;
REVOKE ALL ON FUNCTION runnable_remote_stops() FROM PUBLIC;

CREATE FUNCTION verify_remote_stop_scope() RETURNS trigger LANGUAGE plpgsql
SET search_path=adjutant,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM campaign_object o JOIN channel_connection c
        ON c.id=o.connection_id AND c.brand_id=o.brand_id
        WHERE o.id=NEW.campaign_object_id AND o.brand_id=NEW.brand_id
        AND c.id=NEW.connection_id AND c.channel=NEW.channel AND o.channel=NEW.channel
        AND o.native_id=NEW.native_id AND c.external_ad_account_id=NEW.account_id) THEN
        RAISE EXCEPTION 'Remote stop identity does not match its tenant account' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER enforce_remote_stop_scope BEFORE INSERT OR UPDATE ON remote_stop_item
FOR EACH ROW EXECUTE FUNCTION verify_remote_stop_scope();
