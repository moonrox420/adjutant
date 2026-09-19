SET search_path = adjutant, public;

-- Definer-owned views must not bypass the caller's tenant policies.
ALTER VIEW v_approval_queue SET (security_invoker = true);
ALTER VIEW v_agency_portfolio SET (security_invoker = true);
ALTER VIEW v_brand_margin_monthly SET (security_invoker = true);
ALTER VIEW v_spend_authority_trail SET (security_invoker = true);
CREATE OR REPLACE VIEW v_approval_queue WITH (security_invoker = true) AS
SELECT ar.id, ar.brand_id, b.display_name AS brand_name, ar.subject_type, ar.subject_id,
       ar.state, ar.requested_daily_usd, ar.requested_total_usd, ar.rationale,
       ar.projected_impact, ar.requires_client_approval, ar.expires_at, ar.created_at,
       EXISTS (SELECT 1 FROM compliance_record cr
               WHERE (cr.plan_id=ar.subject_id OR cr.creative_id=ar.subject_id)
                 AND cr.overall_verdict='flag') AS has_compliance_flag
FROM approval_request ar JOIN brand b ON b.id=ar.brand_id
WHERE ar.state IN ('pending_internal', 'pending_client');

-- Aggregate each one-to-many relation before joining to prevent spend multiplication.
CREATE OR REPLACE VIEW v_agency_portfolio WITH (security_invoker = true) AS
SELECT b.account_id, b.id AS brand_id, b.display_name,
       COALESCE(m.total, 0) AS spend_last_30d, bc.maximum AS monthly_ceiling,
       COALESCE(co.total, 0) AS active_objects, COALESCE(an.total, 0) AS open_critical,
       COALESCE(ap.total, 0) AS pending_approvals
FROM brand b
LEFT JOIN (SELECT brand_id, sum(spend_usd) AS total FROM metric_fact_raw
           WHERE date_hour>=now()-interval '30 days' GROUP BY brand_id) m ON m.brand_id=b.id
LEFT JOIN (SELECT brand_id, max(monthly_usd_max) AS maximum FROM budget_ceiling
           WHERE scope_kind='brand' GROUP BY brand_id) bc ON bc.brand_id=b.id
LEFT JOIN (SELECT brand_id, count(*) AS total FROM campaign_object
           WHERE state='active' GROUP BY brand_id) co ON co.brand_id=b.id
LEFT JOIN (SELECT brand_id, count(*) AS total FROM anomaly
           WHERE severity='critical' AND resolved_at IS NULL GROUP BY brand_id) an ON an.brand_id=b.id
LEFT JOIN (SELECT brand_id, count(*) AS total FROM approval_request
           WHERE state IN ('pending_internal','pending_client') GROUP BY brand_id) ap ON ap.brand_id=b.id;

ALTER TABLE approval_token_consumption ADD CONSTRAINT consumption_operation_once
    UNIQUE (token_id, channel, operation);
ALTER TABLE approval_token_consumption ADD CONSTRAINT commitment_nonnegative
    CHECK (usd_committed >= 0);
CREATE UNIQUE INDEX ceiling_scope_unique ON budget_ceiling
    (brand_id, scope_kind, COALESCE(scope_ref, ''));
ALTER TABLE budget_ceiling ADD CONSTRAINT valid_ceiling_scope
    CHECK ((scope_kind='brand' AND scope_ref IS NULL)
        OR (scope_kind IN ('channel','campaign') AND length(scope_ref)>0));
ALTER TABLE brand_graph_assertion ADD CONSTRAINT confirmed_provenance_nonblank
    CHECK (human_confirmed_at IS NULL OR length(btrim(provenance_uri))>0);

CREATE TABLE local_credential (
    user_id uuid PRIMARY KEY REFERENCES app_user(id),
    password_hash text NOT NULL
);
CREATE TABLE auth_session (
    token_hash text PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES app_user(id),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX session_expiry ON auth_session(expires_at);
CREATE TABLE login_attempt (
    identity_hash text PRIMARY KEY,
    attempts integer NOT NULL DEFAULT 1,
    window_started_at timestamptz NOT NULL DEFAULT now()
);

-- Auth helpers are the only way the runtime accesses credentials and sessions.
CREATE FUNCTION login_identity(p_email text)
RETURNS TABLE(user_id uuid, password_hash text)
LANGUAGE sql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
    SELECT u.id, c.password_hash FROM app_user u JOIN local_credential c ON c.user_id=u.id
    WHERE u.email=p_email::public.citext AND u.is_active;
$$;
CREATE FUNCTION check_login_rate(p_identity text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
DECLARE n integer;
BEGIN
    INSERT INTO login_attempt(identity_hash) VALUES(p_identity)
    ON CONFLICT(identity_hash) DO UPDATE SET
        attempts=CASE WHEN login_attempt.window_started_at<now()-interval '15 minutes'
                      THEN 1 ELSE login_attempt.attempts+1 END,
        window_started_at=CASE WHEN login_attempt.window_started_at<now()-interval '15 minutes'
                               THEN now() ELSE login_attempt.window_started_at END
    RETURNING attempts INTO n;
    RETURN n<=10;
END;
$$;
CREATE FUNCTION create_session(p_user uuid, p_hash text) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
    INSERT INTO auth_session(token_hash,user_id,expires_at)
    VALUES(p_hash,p_user,now()+interval '12 hours');
$$;
CREATE FUNCTION revoke_session(p_hash text) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
    DELETE FROM auth_session WHERE token_hash=p_hash;
$$;
CREATE FUNCTION authenticate_session(p_hash text)
RETURNS TABLE(user_id uuid,email text,full_name text,brand_ids uuid[])
LANGUAGE sql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
    SELECT u.id,u.email::text,COALESCE(u.full_name,''),
           COALESCE((SELECT array_agg(DISTINCT b.id) FROM seat s
              JOIN brand b ON b.account_id=s.account_id AND (s.brand_id IS NULL OR s.brand_id=b.id)
              WHERE s.user_id=u.id AND s.revoked_at IS NULL AND s.accepted_at IS NOT NULL),'{}'::uuid[])
    FROM auth_session a JOIN app_user u ON u.id=a.user_id
    WHERE a.token_hash=p_hash AND a.expires_at>now() AND u.is_active;
$$;

ALTER TABLE account ENABLE ROW LEVEL SECURITY;
ALTER TABLE account FORCE ROW LEVEL SECURITY;
ALTER TABLE seat ENABLE ROW LEVEL SECURITY;
ALTER TABLE seat FORCE ROW LEVEL SECURITY;
CREATE POLICY own_seats ON seat FOR SELECT USING (user_id=current_actor_id());
CREATE POLICY member_accounts ON account FOR SELECT USING
    (EXISTS (SELECT 1 FROM seat s WHERE s.account_id=id AND s.revoked_at IS NULL
                                          AND s.accepted_at IS NOT NULL));
ALTER TABLE app_user ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_user FORCE ROW LEVEL SECURITY;
CREATE POLICY own_identity ON app_user USING (id=current_actor_id());
ALTER TABLE account_type_change ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_type_change FORCE ROW LEVEL SECURITY;
CREATE POLICY own_account_changes ON account_type_change USING
    (EXISTS(SELECT 1 FROM account a WHERE a.id=account_id));
ALTER TABLE rate_budget ENABLE ROW LEVEL SECURITY;
ALTER TABLE rate_budget FORCE ROW LEVEL SECURITY;
CREATE POLICY rate_budget_tenant ON rate_budget USING
    (EXISTS(SELECT 1 FROM channel_connection c WHERE c.channel=rate_budget.channel
              AND c.external_ad_account_id=rate_budget.external_ad_account_id));

-- Cross-tenant references must fail even when both tenant IDs are in an agency context.
DO $$
DECLARE child text;
BEGIN
    ALTER TABLE plan ADD CONSTRAINT plan_id_brand_unique UNIQUE(id,brand_id);
    FOREACH child IN ARRAY ARRAY['plan_allocation','creative_concept','deployment'] LOOP
        EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY(plan_id,brand_id)
                        REFERENCES plan(id,brand_id)', child, child||'_tenant_parent');
    END LOOP;
END $$;
ALTER TABLE approval_token ADD COLUMN signed_claims jsonb;

CREATE FUNCTION guard_token_consumption() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t approval_token%ROWTYPE;
BEGIN
    SELECT * INTO t FROM approval_token WHERE id=NEW.token_id FOR UPDATE;
    IF NOT FOUND OR t.voided_at IS NOT NULL OR t.expires_at<=now()
       OR NOT ('channel:'||NEW.channel::text=ANY(t.scopes))
       OR NOT ('op:'||NEW.operation=ANY(t.scopes))
       OR NEW.usd_committed>t.usd_total_cap
       OR EXISTS(SELECT 1 FROM brand_kill_switch k
                 WHERE k.brand_id=t.brand_id AND k.released_at IS NULL)
    THEN RAISE EXCEPTION 'Invalid spend authorization' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER validate_consumption BEFORE INSERT ON approval_token_consumption
FOR EACH ROW EXECUTE FUNCTION guard_token_consumption();

CREATE TABLE event_outbox (
    id bigserial PRIMARY KEY, event_id uuid NOT NULL UNIQUE,
    brand_id uuid NOT NULL REFERENCES brand(id), event_type text NOT NULL,
    event_version integer NOT NULL DEFAULT 1, topic text NOT NULL,
    partition_key text NOT NULL, envelope jsonb NOT NULL,
    occurred_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz, publish_attempts integer NOT NULL DEFAULT 0, last_error text,
    CHECK (envelope ? 'payload' AND envelope ? 'event_type')
);
CREATE INDEX event_outbox_unpublished ON event_outbox(created_at) WHERE published_at IS NULL;
ALTER TABLE event_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE event_outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY outbox_tenant ON event_outbox
    USING(brand_id=ANY(current_brand_ids())) WITH CHECK(brand_id=ANY(current_brand_ids()));

REVOKE ALL ON local_credential,auth_session,login_attempt FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA adjutant FROM PUBLIC;
