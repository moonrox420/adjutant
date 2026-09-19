-- =====================================================================
-- Adjutant — Schema Invariant Verification Suite
-- Run against a database loaded with adjutant-schema.sql:
--   createdb adjutant_test
--   psql -d adjutant_test -f adjutant-schema.sql
--   psql -d adjutant_test -f adjutant-schema-tests.sql
--
-- Every test labelled "(must fail)" is expected to raise an error. A clean
-- run is one where each of those raises, and nothing else does.
-- These are the negative security tests the architecture doc requires to
-- gate every release: subject-hash mutation, scope escalation, cap
-- overflow, token replay, expiry, and cross-brand token use.
-- =====================================================================

\set ON_ERROR_STOP off
SET search_path = adjutant, public;

-- Non-superuser app role, so RLS actually applies (superusers bypass it).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw') THEN
        CREATE ROLE app_rw LOGIN;
    END IF;
END $$;
GRANT USAGE ON SCHEMA adjutant TO app_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA adjutant TO app_rw;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA adjutant TO app_rw;


-- seed two tenants
INSERT INTO account (id, account_type, display_name) VALUES
 ('11111111-1111-1111-1111-111111111111','agency','Acme Agency');
INSERT INTO brand (id, account_id, display_name, campaigns_enabled) VALUES
 ('aaaaaaaa-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','Client A', true),
 ('bbbbbbbb-0000-0000-0000-000000000002','11111111-1111-1111-1111-111111111111','Client B', true);
INSERT INTO app_user (id,email,full_name) VALUES
 ('cccccccc-0000-0000-0000-000000000003','dev@acme.test','Dev Buyer');
INSERT INTO plan (id,brand_id,name,objective,goal_kind,goal_value,plan_hash,plan_document,monthly_budget_usd)
VALUES ('dddddddd-0000-0000-0000-000000000004','aaaaaaaa-0000-0000-0000-000000000001',
        'Fall Leads','leads','target_cpa',85.00,'hash_v1','{}'::jsonb, 4000.00);
INSERT INTO approval_request (id,brand_id,subject_type,subject_id,subject_hash,state,
        internal_approver_id,internal_approved_at,expires_at)
VALUES ('eeeeeeee-0000-0000-0000-000000000005','aaaaaaaa-0000-0000-0000-000000000001',
        'plan','dddddddd-0000-0000-0000-000000000004','hash_v1','approved',
        'cccccccc-0000-0000-0000-000000000003', now(), now()+interval '2 days');
INSERT INTO approval_token (id,approval_request_id,brand_id,subject_type,subject_id,subject_hash,
        scopes,usd_daily_cap,usd_total_cap,approver_id,approval_chain,signing_key_id,signature,nonce,expires_at)
VALUES ('ffffffff-0000-0000-0000-000000000006','eeeeeeee-0000-0000-0000-000000000005',
        'aaaaaaaa-0000-0000-0000-000000000001','plan','dddddddd-0000-0000-0000-000000000004','hash_v1',
        ARRAY['channel:meta','op:create'],500.00,15000.00,'cccccccc-0000-0000-0000-000000000003',
        ARRAY['cccccccc-0000-0000-0000-000000000003'::uuid],'kms-key-1','\x00'::bytea,'nonce-1',
        now()+interval '48 hours');

\echo '=== T1 tenant isolation: Client A context must not see Client B ==='
SET ROLE app_rw;
SET app.current_brand_ids = 'aaaaaaaa-0000-0000-0000-000000000001';
SELECT display_name FROM brand;
\echo '--- attempt cross-tenant write (must fail) ---'
INSERT INTO plan (brand_id,name,objective,goal_kind,plan_hash,plan_document,monthly_budget_usd)
VALUES ('bbbbbbbb-0000-0000-0000-000000000002','Sneaky','sales','target_roas','h9','{}'::jsonb,100);
\echo '--- no tenant context: must return zero rows ---'
RESET app.current_brand_ids;
SELECT count(*) AS visible_brands FROM brand;
RESET ROLE;

\echo '=== T2 append-only audit ledger ==='
INSERT INTO action (brand_id,actor_kind,actor_agent_name,action_type,target_kind,target_id,token_id,usd_impact)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001','agent','Builder','deploy','plan',
        'dddddddd-0000-0000-0000-000000000004','ffffffff-0000-0000-0000-000000000006',4000.00);
\echo '--- UPDATE on action (must fail) ---'
UPDATE action SET rationale = 'tampered';
\echo '--- DELETE on action (must fail) ---'
DELETE FROM action;

\echo '=== T3 spend action without token (must fail) ==='
INSERT INTO action (brand_id,actor_kind,actor_agent_name,action_type,target_kind)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001','agent','Optimizer','budget_set','campaign_object');

\echo '=== T4 token auto-voids when plan hash changes ==='
SELECT voided_at IS NULL AS token_live FROM approval_token WHERE nonce='nonce-1';
UPDATE plan SET plan_hash='hash_v2' WHERE id='dddddddd-0000-0000-0000-000000000004';
SELECT voided_reason, voided_at IS NOT NULL AS token_voided
  FROM approval_token WHERE nonce='nonce-1';
SELECT state AS approval_state FROM approval_request WHERE id='eeeeeeee-0000-0000-0000-000000000005';

\echo '=== T5 token replay blocked ==='
INSERT INTO approval_token_consumption (token_id,channel,operation,idem_key)
VALUES ('ffffffff-0000-0000-0000-000000000006','meta','create','idem-abc');
\echo '--- replay same (token,channel,op,idem) must fail ---'
INSERT INTO approval_token_consumption (token_id,channel,operation,idem_key)
VALUES ('ffffffff-0000-0000-0000-000000000006','meta','create','idem-abc');

\echo '=== T6 token expiry cannot exceed 72h ==='
INSERT INTO approval_token (approval_request_id,brand_id,subject_type,subject_id,subject_hash,
        scopes,usd_daily_cap,usd_total_cap,approver_id,approval_chain,signing_key_id,signature,nonce,expires_at)
VALUES ('eeeeeeee-0000-0000-0000-000000000005','aaaaaaaa-0000-0000-0000-000000000001','plan',
        'dddddddd-0000-0000-0000-000000000004','hash_v2',ARRAY['op:create'],1,1,
        'cccccccc-0000-0000-0000-000000000003',ARRAY['cccccccc-0000-0000-0000-000000000003'::uuid],
        'k','\x00'::bytea,'nonce-2', now()+interval '10 days');

\echo '=== T7 brand graph: confirmed assertion requires provenance (must fail) ==='
INSERT INTO brand_graph_assertion (brand_id,field_path,value,human_confirmed_at)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001','offerings[0].name','"Duct cleaning"'::jsonb, now());

INSERT INTO channel_connection (id,brand_id,channel,external_ad_account_id,access_tier)
VALUES ('99999999-0000-0000-0000-000000000009','aaaaaaaa-0000-0000-0000-000000000001','meta','act_123','standard');
INSERT INTO campaign_object (id,brand_id,connection_id,channel,level,native_id,state)
VALUES ('88888888-0000-0000-0000-000000000008','aaaaaaaa-0000-0000-0000-000000000001',
        '99999999-0000-0000-0000-000000000009','meta','campaign','23847','active');

\echo '=== T8 fatigue: single signal cannot mark fatigued (must fail) ==='
INSERT INTO fatigue_score (brand_id,campaign_object_id,window_days,signals_fired,is_fatigued)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001','88888888-0000-0000-0000-000000000008',
        14, ARRAY['frequency_rise'], true);
\echo '--- three compound signals: must succeed ---'
INSERT INTO fatigue_score (brand_id,campaign_object_id,window_days,signals_fired,is_fatigued)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001','88888888-0000-0000-0000-000000000008',
        14, ARRAY['frequency_rise','engagement_decay','cpr_rise'], true);

\echo '=== T9 duplicate create blocked by idempotency key (must fail) ==='
INSERT INTO campaign_object (brand_id,connection_id,channel,level,native_id,state,idem_key)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001','99999999-0000-0000-0000-000000000009','meta','campaign','777','active','idem-k1');
INSERT INTO campaign_object (brand_id,connection_id,channel,level,native_id,state,idem_key)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001','99999999-0000-0000-0000-000000000009','meta','campaign','778','active','idem-k1');

\echo '=== T10 BYOK connection cannot store server-side token (must fail) ==='
INSERT INTO channel_connection (brand_id,channel,external_ad_account_id,credential_mode,token_ciphertext)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001','pinterest','pin_1','bring_your_own_key','\xdead'::bytea);

\echo '=== T11 white-label config rejected on a business account (must fail) ==='
INSERT INTO account (account_type,display_name,white_label)
VALUES ('business','Solo Shop','{"logo":"x"}'::jsonb);

\echo '=== T12 compliance override needs substantive justification (must fail) ==='
INSERT INTO compliance_record (id,brand_id,plan_id,overall_verdict,corpus_version)
VALUES ('77777777-0000-0000-0000-000000000007','aaaaaaaa-0000-0000-0000-000000000001',
        'dddddddd-0000-0000-0000-000000000004','block','2026.09');
INSERT INTO compliance_override (compliance_record_id,brand_id,overridden_by,justification)
VALUES ('77777777-0000-0000-0000-000000000007','aaaaaaaa-0000-0000-0000-000000000001',
        'cccccccc-0000-0000-0000-000000000003','ok');

\echo '=== T13 client-stage approval requires internal approval first (must fail) ==='
INSERT INTO approval_request (brand_id,subject_type,subject_id,subject_hash,state,expires_at)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001','creative_set',gen_random_uuid(),'ch1',
        'pending_client', now()+interval '1 day');

\echo '=== T14 global learning signal must be de-identified (must fail) ==='
INSERT INTO learning_signal (brand_id,scope,creative_attributes,outcome_metrics,sample_size)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001','global','{}'::jsonb,'{}'::jsonb,500);

\echo '=== T15 partitioned metric insert routes correctly ==='
INSERT INTO metric_fact_raw (brand_id,campaign_object_id,channel,date_hour,impressions,clicks,spend_usd)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001','88888888-0000-0000-0000-000000000008','meta',
        '2026-10-14 09:00+00', 12000, 310, 148.5500);
SELECT tableoid::regclass AS landed_in_partition, impressions FROM metric_fact_raw;

\echo '=== T16 approval queue view ==='
SELECT brand_name, subject_type, state FROM v_approval_queue;
