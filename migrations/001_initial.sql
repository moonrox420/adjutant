-- =====================================================================
-- Adjutant — Database Schema
-- Target: PostgreSQL 16
-- Version: 1.0
-- Companion to: Adjutant PRD v1.0 / Technical Architecture v1.0
--
-- DESIGN INVARIANTS ENCODED HERE:
--   1. brand_id is the tenant key. RLS is FORCED on every brand-scoped
--      table, so application bugs cannot leak across tenants.
--   2. No spend-affecting write exists without an approval_token row whose
--      subject_hash matches the current subject hash.
--   3. action is append-only (no UPDATE/DELETE) and is the audit ledger.
--   4. brand_graph_assertion cannot reach state 'confirmed' without a
--      provenance_uri.
--   5. Creative is a scene graph; renditions are derived and must pass
--      spec validation before entering the approval queue.
--   6. Hourly metric facts are partitioned by month (ClickHouse is the
--      analytical store of record; this is the operational mirror).
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "citext";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "btree_gin";

CREATE SCHEMA IF NOT EXISTS adjutant;
SET search_path = adjutant, public;

-- =====================================================================
-- SECTION 1: ENUMERATED TYPES
-- =====================================================================

CREATE TYPE account_type        AS ENUM ('business', 'agency');
CREATE TYPE plan_tier           AS ENUM ('entry', 'growth', 'scale');
CREATE TYPE actor_kind          AS ENUM ('human', 'agent', 'system');

CREATE TYPE user_role AS ENUM (
    'owner', 'admin', 'buyer', 'creative',
    'reviewer', 'client_viewer', 'client_approver'
);

CREATE TYPE channel AS ENUM (
    'meta', 'google_ads', 'youtube', 'tiktok', 'linkedin',
    'microsoft', 'reddit', 'pinterest', 'snapchat', 'amazon_ads'
);

-- Access tier matters because it changes quota shape and write eligibility
-- (e.g. Meta dev=300 base points/hr vs standard=100000; LinkedIn dev tier
-- permits POST against only 5 ad accounts).
CREATE TYPE channel_access_tier AS ENUM ('development', 'standard', 'unknown');
CREATE TYPE credential_mode     AS ENUM ('platform_managed', 'bring_your_own_key');
CREATE TYPE connection_health   AS ENUM ('healthy', 'degraded', 'expired', 'revoked', 'error');

CREATE TYPE object_level AS ENUM ('campaign', 'ad_group', 'ad', 'asset_group', 'ad_squad');
CREATE TYPE object_state AS ENUM ('draft', 'pending_review', 'active', 'paused',
                                  'rejected', 'archived', 'deleted', 'unknown');

CREATE TYPE campaign_objective AS ENUM (
    'awareness', 'traffic', 'engagement', 'leads',
    'app_promotion', 'sales', 'store_visits', 'video_views'
);

CREATE TYPE goal_kind AS ENUM ('target_cpa', 'target_roas', 'lead_volume', 'efficient_spend');

CREATE TYPE plan_state AS ENUM (
    'draft', 'pending_approval', 'approved', 'deploying',
    'live', 'paused', 'rejected', 'expired', 'archived'
);

CREATE TYPE creative_format AS ENUM (
    'static_image', 'video', 'carousel', 'collection',
    'catalog_dynamic', 'text_only', 'lead_form', 'ar_lens'
);

CREATE TYPE creative_state AS ENUM (
    'generating', 'rendered', 'spec_failed', 'compliance_blocked',
    'pending_approval', 'approved', 'live', 'fatigued', 'retired', 'rejected'
);

CREATE TYPE approval_subject_type AS ENUM (
    'plan', 'creative_set', 'deployment', 'budget_change', 'structural_change'
);

CREATE TYPE approval_state AS ENUM (
    'draft', 'pending_internal', 'pending_client', 'approved',
    'consumed', 'rejected', 'changes_requested', 'expired', 'voided'
);

CREATE TYPE rejection_reason AS ENUM (
    'off_brand', 'wrong_claim', 'wrong_audience', 'wrong_offer',
    'poor_quality', 'compliance_concern', 'budget_too_high', 'other'
);

CREATE TYPE compliance_verdict AS ENUM ('pass', 'flag', 'block');

CREATE TYPE compliance_check_kind AS ENUM (
    'vertical_gate', 'lexical_scan', 'claim_substantiation',
    'likeness_detection', 'jurisdiction_resolution',
    'disclosure_application', 'policy_corpus', 'trademark_scan'
);

CREATE TYPE disclosure_kind AS ENUM (
    'platform_ai_label',        -- e.g. TikTok AIGC label
    'platform_self_disclosure', -- e.g. Meta social-issue/electoral AI disclosure
    'eu_ai_act_visible',        -- Art. 50 clear & visible label
    'eu_ai_act_machine_readable',
    'sponsored_disclosure',
    'legal_footer'
);

CREATE TYPE action_type AS ENUM (
    'brand_graph_confirm', 'plan_create', 'plan_approve', 'creative_approve',
    'deploy', 'pause', 'resume', 'budget_set', 'bid_set', 'targeting_change',
    'creative_swap', 'archive', 'kill_switch', 'rollback', 'connection_change',
    'override_compliance'
);

CREATE TYPE deployment_state AS ENUM (
    'preflight', 'queued', 'building', 'verifying',
    'live', 'partial', 'failed', 'rolled_back'
);

CREATE TYPE proposal_kind AS ENUM (
    'pause_ad', 'pause_ad_group', 'budget_increase', 'budget_decrease',
    'bid_adjust', 'reallocate_cross_channel', 'refresh_creative',
    'scale_winner', 'expand_audience', 'fix_tracking'
);

CREATE TYPE proposal_state AS ENUM (
    'proposed', 'auto_approved', 'pending_approval', 'approved',
    'executing', 'executed', 'rejected', 'expired', 'reverted'
);

CREATE TYPE comparability_class AS ENUM ('direct', 'caveated', 'first_party_only');

CREATE TYPE anomaly_kind AS ENUM (
    'overspend', 'pacing_miss', 'zero_delivery', 'delivery_collapse',
    'tracking_broken', 'cpa_drift', 'roas_drift', 'ad_disapproved',
    'account_restricted', 'payment_failed', 'connection_expired'
);

CREATE TYPE severity AS ENUM ('info', 'warning', 'critical');

CREATE TYPE asset_role AS ENUM (
    'logo_primary', 'logo_mark', 'product_photo', 'lifestyle_photo',
    'reference_creative', 'font_file', 'video_clip', 'audio_track',
    'generated_image', 'generated_video', 'rendition'
);

CREATE TYPE learning_scope AS ENUM ('brand', 'agency', 'global');

-- =====================================================================
-- SECTION 2: TENANCY HELPERS
-- =====================================================================

-- Session context is injected by core-api on every connection checkout.
-- A connection without context resolves to an empty set and can read nothing.
CREATE OR REPLACE FUNCTION current_brand_ids()
RETURNS uuid[] LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        string_to_array(NULLIF(current_setting('app.current_brand_ids', true), ''), ',')::uuid[],
        ARRAY[]::uuid[]
    );
$$;

CREATE OR REPLACE FUNCTION current_actor_id()
RETURNS uuid LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.current_actor_id', true), '')::uuid;
$$;

CREATE OR REPLACE FUNCTION touch_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

-- Applied to the audit ledger and other immutable tables.
CREATE OR REPLACE FUNCTION forbid_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Table %.% is append-only; % is not permitted',
        TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP;
END;
$$;

-- =====================================================================
-- SECTION 3: ACCOUNTS, USERS, BRANDS
-- =====================================================================

CREATE TABLE account (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_type        account_type NOT NULL,
    display_name        text NOT NULL,
    plan_tier           plan_tier NOT NULL DEFAULT 'entry',
    billing_customer_id text,
    -- White-label config is agency-only; enforced by CHECK below.
    white_label         jsonb NOT NULL DEFAULT '{}'::jsonb,
    generation_budget_usd_monthly numeric(12,2) NOT NULL DEFAULT 250.00,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT white_label_agency_only
        CHECK (account_type = 'agency' OR white_label = '{}'::jsonb),
    CONSTRAINT generation_budget_positive
        CHECK (generation_budget_usd_monthly > 0)
);
CREATE TRIGGER trg_account_touch BEFORE UPDATE ON account
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- Account type is mutable (PRD ACC-2: business -> agency without data loss).
CREATE TABLE account_type_change (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id    uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    from_type     account_type NOT NULL,
    to_type       account_type NOT NULL,
    changed_by    uuid NOT NULL,
    changed_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app_user (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         citext NOT NULL UNIQUE,
    full_name     text,
    is_active     boolean NOT NULL DEFAULT true,
    mfa_enabled   boolean NOT NULL DEFAULT false,
    last_seen_at  timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_app_user_touch BEFORE UPDATE ON app_user
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE brand (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id         uuid NOT NULL REFERENCES account(id) ON DELETE RESTRICT,
    display_name       text NOT NULL,
    website_url        text,
    vertical           text,
    primary_locale     text NOT NULL DEFAULT 'en-US',
    target_geos        text[] NOT NULL DEFAULT ARRAY['US'],
    -- Restricted verticals block campaign creation (PRD CP-6) while still
    -- allowing the account to exist.
    restricted_flags   text[] NOT NULL DEFAULT ARRAY[]::text[],
    campaigns_enabled  boolean NOT NULL DEFAULT false,
    brand_graph_confirmed_at timestamptz,
    -- Opt-in for cross-client learning inside an agency (PRD ADR-6 / Q8).
    allow_agency_learning boolean NOT NULL DEFAULT false,
    allow_global_learning boolean NOT NULL DEFAULT true,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_brand_account ON brand(account_id);
CREATE INDEX idx_brand_restricted ON brand USING gin(restricted_flags);
CREATE TRIGGER trg_brand_touch BEFORE UPDATE ON brand
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- Seat = (user, brand-or-account, role, spend authority).
-- Spend threshold is per-seat because PRD ACC-4 makes approval authority a
-- per-role, per-dollar permission.
CREATE TABLE seat (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id             uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    user_id                uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    -- NULL brand_id = account-wide seat (owner/admin).
    brand_id               uuid REFERENCES brand(id) ON DELETE CASCADE,
    role                   user_role NOT NULL,
    approval_daily_usd_cap numeric(12,2),
    approval_total_usd_cap numeric(12,2),
    invited_at             timestamptz NOT NULL DEFAULT now(),
    accepted_at            timestamptz,
    revoked_at             timestamptz,
    CONSTRAINT seat_unique UNIQUE (account_id, user_id, brand_id, role),
    CONSTRAINT account_wide_roles_only
        CHECK (brand_id IS NOT NULL OR role IN ('owner','admin')),
    CONSTRAINT caps_non_negative
        CHECK (COALESCE(approval_daily_usd_cap, 0) >= 0
           AND COALESCE(approval_total_usd_cap, 0) >= 0)
);
CREATE INDEX idx_seat_user ON seat(user_id) WHERE revoked_at IS NULL;
CREATE INDEX idx_seat_brand ON seat(brand_id) WHERE revoked_at IS NULL;

-- =====================================================================
-- SECTION 4: BRAND GRAPH & BRAND KIT
-- =====================================================================

-- Every assertion carries provenance. Invariant #4: cannot be 'confirmed'
-- without a provenance_uri (PRD BG-2).
CREATE TABLE brand_graph_assertion (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id        uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    field_path      text NOT NULL,          -- e.g. 'offerings[2].price_range'
    value           jsonb NOT NULL,
    provenance_uri  text,                   -- source URL or connected-account ref
    provenance_kind text,                   -- 'website' | 'ad_account' | 'user' | 'review_site'
    confidence      numeric(4,3),
    is_claim        boolean NOT NULL DEFAULT false,  -- substantiation target
    human_confirmed_at timestamptz,
    confirmed_by    uuid REFERENCES app_user(id),
    superseded_by   uuid REFERENCES brand_graph_assertion(id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT confirmed_requires_provenance
        CHECK (human_confirmed_at IS NULL OR provenance_uri IS NOT NULL),
    CONSTRAINT confidence_range
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
);
CREATE INDEX idx_bga_brand_field ON brand_graph_assertion(brand_id, field_path)
    WHERE superseded_by IS NULL;
CREATE INDEX idx_bga_claims ON brand_graph_assertion(brand_id)
    WHERE is_claim AND superseded_by IS NULL;

-- Generation constraints: banned words, banned claims, required disclaimers.
-- Fed both by user configuration (PRD ST-6) and by the rejection-feedback
-- loop (PRD AP-3, CP-7).
CREATE TABLE brand_constraint (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id       uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    kind           text NOT NULL,   -- 'banned_word'|'banned_claim'|'required_disclaimer'|
                                    -- 'competitor_policy'|'tone_rule'
    value          text NOT NULL,
    source         text NOT NULL DEFAULT 'user', -- 'user'|'rejection_feedback'|'policy_corpus'
    source_ref_id  uuid,
    is_active      boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT brand_constraint_unique UNIQUE (brand_id, kind, value)
);
CREATE INDEX idx_brand_constraint_active ON brand_constraint(brand_id, kind)
    WHERE is_active;

CREATE TABLE brand_kit (
    brand_id       uuid PRIMARY KEY REFERENCES brand(id) ON DELETE CASCADE,
    palette        jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{name,hex,role}]
    typefaces      jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{name,asset_id,weights,license}]
    voice_profile  jsonb NOT NULL DEFAULT '{}'::jsonb,
    style_refs     jsonb NOT NULL DEFAULT '{}'::jsonb,   -- named text/CTA styles
    updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_brand_kit_touch BEFORE UPDATE ON brand_kit
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE asset (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id      uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    role          asset_role NOT NULL,
    storage_uri   text NOT NULL,            -- s3://adjutant-assets/{brand_id}/...
    content_hash  text NOT NULL,
    mime_type     text NOT NULL,
    bytes         bigint,
    width_px      integer,
    height_px     integer,
    duration_ms   integer,
    has_alpha     boolean,
    -- AI provenance (PRD CP-3): which models and inputs produced this.
    ai_provenance jsonb,
    -- Consent artifact for real-person likeness (PRD CP-5 / Q10).
    consent_artifact_id uuid,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT asset_content_unique UNIQUE (brand_id, content_hash, role)
);
CREATE INDEX idx_asset_brand_role ON asset(brand_id, role);

CREATE TABLE consent_artifact (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id        uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    subject_name    text NOT NULL,
    document_uri    text NOT NULL,
    verified_by     uuid REFERENCES app_user(id),
    verified_at     timestamptz,
    expires_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE asset
    ADD CONSTRAINT asset_consent_fk
    FOREIGN KEY (consent_artifact_id) REFERENCES consent_artifact(id);

-- Vertical templates seed structure, objectives, and creative patterns.
CREATE TABLE vertical_template (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    vertical        text NOT NULL UNIQUE,
    default_objectives campaign_objective[] NOT NULL,
    channel_priors  jsonb NOT NULL,   -- {channel: {min_monthly_usd, weight}}
    creative_patterns jsonb NOT NULL,
    structure_template jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- =====================================================================
-- SECTION 5: CHANNEL REGISTRIES & CONNECTIONS
-- =====================================================================

-- Capability Registry (architecture §4.7a). Declarative and versioned, so
-- the Strategist plans against data and never against branching logic.
CREATE TABLE channel_capability (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    channel             channel NOT NULL,
    registry_version    text NOT NULL,
    objectives          campaign_objective[] NOT NULL,
    hierarchy           object_level[] NOT NULL,
    budget_levels       object_level[] NOT NULL,
    bid_strategies      text[] NOT NULL,
    targeting_dimensions text[] NOT NULL,
    supports            jsonb NOT NULL DEFAULT '{}'::jsonb,
    quota_model         jsonb NOT NULL,   -- {kind, formula, base:{dev,standard}, headers}
    prerequisites       text[] NOT NULL DEFAULT ARRAY[]::text[],
    effective_from      timestamptz NOT NULL DEFAULT now(),
    retired_at          timestamptz,
    CONSTRAINT channel_capability_version_unique UNIQUE (channel, registry_version)
);

-- Spec Registry (architecture §4.7b). render-svc and the validator both read
-- from here; no dimension is ever hard-coded.
CREATE TABLE placement_spec (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    channel           channel NOT NULL,
    placement_key     text NOT NULL,        -- 'meta.feed', 'tiktok.in_feed', ...
    registry_version  text NOT NULL,
    format            creative_format NOT NULL,
    aspect_ratio      text NOT NULL,        -- '1:1','9:16','16:9','4:5'
    min_width_px      integer NOT NULL,
    min_height_px     integer NOT NULL,
    max_bytes         bigint,
    duration_min_ms   integer,
    duration_max_ms   integer,
    safe_zone_insets  jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {top,right,bottom,left}
    text_limits       jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {primary:125,headline:40,...}
    codec_constraints jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Format-calibrated fatigue thresholds (PRD OP-3): short-form video
    -- fatigues materially faster than static feed.
    fatigue_thresholds jsonb NOT NULL DEFAULT '{}'::jsonb,
    effective_from    timestamptz NOT NULL DEFAULT now(),
    retired_at        timestamptz,
    CONSTRAINT placement_spec_unique UNIQUE (channel, placement_key, registry_version),
    CONSTRAINT spec_dimensions_positive CHECK (min_width_px > 0 AND min_height_px > 0)
);

CREATE TABLE channel_connection (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id          uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    channel           channel NOT NULL,
    external_ad_account_id text NOT NULL,
    external_account_name  text,
    credential_mode   credential_mode NOT NULL DEFAULT 'platform_managed',
    -- Envelope-encrypted under a per-tenant DEK; never plaintext, never logged.
    token_ciphertext  bytea,
    token_dek_id      text,
    scopes            text[] NOT NULL DEFAULT ARRAY[]::text[],
    access_tier       channel_access_tier NOT NULL DEFAULT 'unknown',
    token_expires_at  timestamptz,
    health            connection_health NOT NULL DEFAULT 'healthy',
    health_detail     text,
    last_structure_sync_at timestamptz,
    last_metric_sync_at    timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT connection_unique UNIQUE (brand_id, channel, external_ad_account_id),
    -- BYOK connections must not store server-side ciphertext (Pinterest
    -- developer guidelines permit end-user-key apps only with local storage).
    CONSTRAINT byok_no_server_token
        CHECK (credential_mode <> 'bring_your_own_key' OR token_ciphertext IS NULL)
);
CREATE INDEX idx_connection_brand ON channel_connection(brand_id, channel);
CREATE INDEX idx_connection_refresh ON channel_connection(token_expires_at)
    WHERE health = 'healthy';
CREATE TRIGGER trg_connection_touch BEFORE UPDATE ON channel_connection
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- Governor state mirror. Authoritative counter lives in Redis; this is the
-- durable write-through for observability and user-visible queue status.
CREATE TABLE rate_budget (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    channel           channel NOT NULL,
    external_ad_account_id text NOT NULL,
    window_start      timestamptz NOT NULL,
    capacity_units    bigint NOT NULL,
    consumed_units    bigint NOT NULL DEFAULT 0,
    active_object_count integer NOT NULL DEFAULT 0,
    observed_429_count  integer NOT NULL DEFAULT 0,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT rate_budget_unique UNIQUE (channel, external_ad_account_id, window_start),
    CONSTRAINT consumed_within_capacity CHECK (consumed_units >= 0)
);

-- =====================================================================
-- SECTION 6: PLANS, CONCEPTS, CREATIVE
-- =====================================================================

CREATE TABLE plan (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id          uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    name              text NOT NULL,
    objective         campaign_objective NOT NULL,
    goal_kind         goal_kind NOT NULL,
    goal_value        numeric(14,4),
    state             plan_state NOT NULL DEFAULT 'draft',
    -- Hash over the normalized plan document. Approval tokens bind to this;
    -- any mutation changes it and voids outstanding tokens (invariant #2).
    plan_hash         text NOT NULL,
    plan_document     jsonb NOT NULL,     -- structure, audiences, briefs, test design
    rationale         text,               -- Strategist reasoning, shown to user
    projected_outcome jsonb,              -- {metric, low, high, confidence}
    monthly_budget_usd numeric(12,2) NOT NULL,
    starts_on         date,
    ends_on           date,
    created_by_actor  actor_kind NOT NULL DEFAULT 'agent',
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plan_budget_positive CHECK (monthly_budget_usd > 0),
    CONSTRAINT plan_dates_ordered CHECK (ends_on IS NULL OR starts_on IS NULL
                                         OR ends_on >= starts_on)
);
CREATE INDEX idx_plan_brand_state ON plan(brand_id, state);
CREATE UNIQUE INDEX idx_plan_hash ON plan(brand_id, plan_hash);
CREATE TRIGGER trg_plan_touch BEFORE UPDATE ON plan
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE plan_allocation (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id         uuid NOT NULL REFERENCES plan(id) ON DELETE CASCADE,
    brand_id        uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    channel         channel NOT NULL,
    monthly_budget_usd numeric(12,2) NOT NULL,
    daily_budget_usd   numeric(12,2),
    bid_strategy    text,
    rationale       text,
    CONSTRAINT allocation_unique UNIQUE (plan_id, channel),
    CONSTRAINT allocation_positive CHECK (monthly_budget_usd > 0)
);
CREATE INDEX idx_allocation_brand ON plan_allocation(brand_id);

-- Hard ceilings (PRD ST-3). Enforced at the adapter layer immediately before
-- each HTTP call, never trusted from approval time (architecture §6.3).
CREATE TABLE budget_ceiling (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id        uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    scope_kind      text NOT NULL,      -- 'brand'|'channel'|'campaign'
    scope_ref       text,               -- channel value or campaign object id
    monthly_usd_max numeric(12,2) NOT NULL,
    daily_usd_max   numeric(12,2),
    set_by          uuid NOT NULL REFERENCES app_user(id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ceiling_unique UNIQUE (brand_id, scope_kind, scope_ref),
    CONSTRAINT ceiling_positive CHECK (monthly_usd_max > 0)
);
CREATE TRIGGER trg_ceiling_touch BEFORE UPDATE ON budget_ceiling
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE brand_kill_switch (
    brand_id     uuid PRIMARY KEY REFERENCES brand(id) ON DELETE CASCADE,
    engaged_at   timestamptz NOT NULL DEFAULT now(),
    engaged_by   uuid NOT NULL REFERENCES app_user(id),
    reason       text,
    released_at  timestamptz,
    released_by  uuid REFERENCES app_user(id),
    verification jsonb NOT NULL DEFAULT '{}'::jsonb   -- {channel: verified_paused_at}
);

CREATE TABLE creative_concept (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id        uuid NOT NULL REFERENCES plan(id) ON DELETE CASCADE,
    brand_id       uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    name           text NOT NULL,
    hypothesis     text NOT NULL,       -- PRD CR-1: every concept states one
    brief          jsonb NOT NULL,
    -- Axis definitions enable winner-scaling: vary one axis, hold the rest.
    axis_definitions jsonb NOT NULL DEFAULT '{}'::jsonb,
    parent_concept_id uuid REFERENCES creative_concept(id),
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_concept_plan ON creative_concept(plan_id);

CREATE TABLE creative (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    concept_id       uuid NOT NULL REFERENCES creative_concept(id) ON DELETE CASCADE,
    brand_id         uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    format           creative_format NOT NULL,
    state            creative_state NOT NULL DEFAULT 'generating',
    version          integer NOT NULL DEFAULT 1,
    -- Invariant #5: the scene graph is the source of truth; renditions derive.
    scene_graph      jsonb NOT NULL,
    creative_hash    text NOT NULL,     -- sha256(scene_graph) — binds approvals
    copy_fields      jsonb NOT NULL DEFAULT '{}'::jsonb,
    ai_provenance    jsonb NOT NULL DEFAULT '{}'::jsonb,
    contains_realistic_person boolean NOT NULL DEFAULT false,
    contains_synthetic_voice  boolean NOT NULL DEFAULT false,
    varied_axis      text,              -- set when derived for winner-scaling
    parent_creative_id uuid REFERENCES creative(id),
    embedding        vector(1536),
    generation_cost_usd numeric(12,6) NOT NULL DEFAULT 0,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT creative_version_unique UNIQUE (concept_id, creative_hash),
    CONSTRAINT creative_version_positive CHECK (version >= 1)
);
CREATE INDEX idx_creative_brand_state ON creative(brand_id, state);
CREATE INDEX idx_creative_concept ON creative(concept_id);
CREATE INDEX idx_creative_embedding ON creative
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 200);
CREATE TRIGGER trg_creative_touch BEFORE UPDATE ON creative
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- One row per (creative, placement). Spec validation gates the approval
-- queue: PRD CR-2 requires zero validation failures before review.
CREATE TABLE rendition (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    creative_id        uuid NOT NULL REFERENCES creative(id) ON DELETE CASCADE,
    brand_id           uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    channel            channel NOT NULL,
    placement_spec_id  uuid NOT NULL REFERENCES placement_spec(id),
    asset_id           uuid REFERENCES asset(id),
    render_cache_key   text NOT NULL,   -- hash(scene_graph, spec_version)
    spec_validation_passed boolean NOT NULL DEFAULT false,
    spec_validation_detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Explicit failure modes, so a rendition never silently crops.
    infeasible_reason  text,
    text_contrast_ratio numeric(5,2),
    rendered_at        timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT rendition_unique UNIQUE (creative_id, placement_spec_id),
    CONSTRAINT passed_requires_asset
        CHECK (NOT spec_validation_passed OR asset_id IS NOT NULL),
    CONSTRAINT contrast_minimum
        CHECK (NOT spec_validation_passed
               OR text_contrast_ratio IS NULL
               OR text_contrast_ratio >= 4.5)
);
CREATE INDEX idx_rendition_creative ON rendition(creative_id);
CREATE INDEX idx_rendition_cache ON rendition(render_cache_key);

-- =====================================================================
-- SECTION 7: COMPLIANCE
-- =====================================================================

CREATE TABLE policy_corpus_rule (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    channel         channel,             -- NULL = cross-channel / regulatory
    jurisdiction    text,                -- 'US','EU','US-NY', NULL = global
    rule_key        text NOT NULL,
    description     text NOT NULL,
    citation_uri    text NOT NULL,       -- every rule cites its source
    detection       jsonb NOT NULL,      -- patterns / classifier refs
    default_verdict compliance_verdict NOT NULL,
    corpus_version  text NOT NULL,
    owner_user_id   uuid REFERENCES app_user(id),
    effective_from  timestamptz NOT NULL DEFAULT now(),
    retired_at      timestamptz,
    CONSTRAINT policy_rule_unique UNIQUE (rule_key, corpus_version)
);

CREATE TABLE compliance_record (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id        uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    creative_id     uuid REFERENCES creative(id) ON DELETE CASCADE,
    plan_id         uuid REFERENCES plan(id) ON DELETE CASCADE,
    overall_verdict compliance_verdict NOT NULL,
    corpus_version  text NOT NULL,
    jurisdictions   text[] NOT NULL DEFAULT ARRAY[]::text[],
    evaluated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT compliance_subject_present
        CHECK (creative_id IS NOT NULL OR plan_id IS NOT NULL)
);
CREATE INDEX idx_compliance_creative ON compliance_record(creative_id);

CREATE TABLE compliance_check (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    compliance_record_id uuid NOT NULL REFERENCES compliance_record(id) ON DELETE CASCADE,
    kind                compliance_check_kind NOT NULL,
    verdict             compliance_verdict NOT NULL,
    policy_rule_id      uuid REFERENCES policy_corpus_rule(id),
    detail              text,
    -- Claim substantiation resolves to a Brand Graph assertion or it blocks.
    substantiating_assertion_id uuid REFERENCES brand_graph_assertion(id),
    evaluated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT substantiation_requires_assertion
        CHECK (kind <> 'claim_substantiation'
               OR verdict <> 'pass'
               OR substantiating_assertion_id IS NOT NULL)
);
CREATE INDEX idx_compliance_check_record ON compliance_check(compliance_record_id);

CREATE TABLE disclosure_application (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    creative_id     uuid NOT NULL REFERENCES creative(id) ON DELETE CASCADE,
    brand_id        uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    kind            disclosure_kind NOT NULL,
    jurisdiction    text,
    channel         channel,
    applied_in_creative boolean NOT NULL DEFAULT false,
    applied_in_payload  boolean NOT NULL DEFAULT false,
    machine_readable_mark text,
    applied_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT disclosure_unique UNIQUE (creative_id, kind, jurisdiction, channel),
    -- A required disclosure must actually land somewhere (PRD CP-4).
    CONSTRAINT disclosure_applied_somewhere
        CHECK (applied_in_creative OR applied_in_payload)
);

CREATE TABLE compliance_override (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    compliance_record_id uuid NOT NULL REFERENCES compliance_record(id),
    brand_id        uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    overridden_by   uuid NOT NULL REFERENCES app_user(id),
    -- Owner-only, with mandatory written justification (PRD CP-5).
    justification   text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT justification_substantive CHECK (length(btrim(justification)) >= 40)
);

-- =====================================================================
-- SECTION 8: APPROVAL — THE SPEND CHOKE POINT
-- =====================================================================

CREATE TABLE approval_request (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id        uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    subject_type    approval_subject_type NOT NULL,
    subject_id      uuid NOT NULL,
    -- Snapshot of the subject hash at request time. If the live subject hash
    -- diverges, the item returns to the queue flagged 'changed after approval'.
    subject_hash    text NOT NULL,
    state           approval_state NOT NULL DEFAULT 'pending_internal',
    requested_daily_usd  numeric(12,2),
    requested_total_usd  numeric(12,2),
    rationale       text,
    projected_impact jsonb,
    requires_client_approval boolean NOT NULL DEFAULT false,
    internal_approver_id uuid REFERENCES app_user(id),
    internal_approved_at timestamptz,
    client_approver_id   uuid REFERENCES app_user(id),
    client_approved_at   timestamptz,
    rejection_reason     rejection_reason,
    rejection_detail     text,
    expires_at      timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT approval_subject_unique UNIQUE (subject_type, subject_id, subject_hash),
    CONSTRAINT rejected_requires_reason
        CHECK (state <> 'rejected' OR rejection_reason IS NOT NULL),
    CONSTRAINT client_stage_requires_internal_first
        CHECK (state <> 'pending_client' OR internal_approved_at IS NOT NULL),
    CONSTRAINT approved_requires_chain
        CHECK (state NOT IN ('approved','consumed')
               OR (internal_approved_at IS NOT NULL
                   AND (NOT requires_client_approval OR client_approved_at IS NOT NULL)))
);
CREATE INDEX idx_approval_queue ON approval_request(brand_id, state, created_at)
    WHERE state IN ('pending_internal','pending_client');
CREATE INDEX idx_approval_expiry ON approval_request(expires_at)
    WHERE state IN ('pending_internal','pending_client','approved');
CREATE TRIGGER trg_approval_touch BEFORE UPDATE ON approval_request
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- Invariant #2 lives here. approval-svc is the only issuer; channel-gateway
-- is the only consumer; adapters assert validity before the first HTTP call.
CREATE TABLE approval_token (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    approval_request_id uuid NOT NULL REFERENCES approval_request(id) ON DELETE RESTRICT,
    brand_id          uuid NOT NULL REFERENCES brand(id) ON DELETE RESTRICT,
    subject_type      approval_subject_type NOT NULL,
    subject_id        uuid NOT NULL,
    subject_hash      text NOT NULL,
    scopes            text[] NOT NULL,     -- ['channel:meta','op:create',...]
    usd_daily_cap     numeric(12,2) NOT NULL,
    usd_total_cap     numeric(12,2) NOT NULL,
    approver_id       uuid NOT NULL REFERENCES app_user(id),
    approval_chain    uuid[] NOT NULL,
    signing_key_id    text NOT NULL,
    signature         bytea NOT NULL,
    nonce             text NOT NULL UNIQUE,
    issued_at         timestamptz NOT NULL DEFAULT now(),
    expires_at        timestamptz NOT NULL,
    voided_at         timestamptz,
    voided_reason     text,
    CONSTRAINT token_caps_positive CHECK (usd_daily_cap >= 0 AND usd_total_cap >= 0),
    CONSTRAINT token_scopes_nonempty CHECK (cardinality(scopes) > 0),
    -- Absolute expiry, max 72h (architecture §4.6). No refresh, no extension.
    CONSTRAINT token_expiry_bounded
        CHECK (expires_at > issued_at AND expires_at <= issued_at + interval '72 hours')
);
CREATE INDEX idx_token_subject ON approval_token(subject_type, subject_id, subject_hash)
    WHERE voided_at IS NULL;
CREATE INDEX idx_token_live ON approval_token(brand_id, expires_at)
    WHERE voided_at IS NULL;

-- Single-consumption per (token, channel, operation). Replay is rejected by
-- the unique constraint, not by application logic.
CREATE TABLE approval_token_consumption (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id      uuid NOT NULL REFERENCES approval_token(id) ON DELETE RESTRICT,
    channel       channel NOT NULL,
    operation     text NOT NULL,
    idem_key      text NOT NULL,
    usd_committed numeric(12,2) NOT NULL DEFAULT 0,
    consumed_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT consumption_unique UNIQUE (token_id, channel, operation, idem_key)
);

-- Tokens void automatically when the subject mutates.
CREATE OR REPLACE FUNCTION void_tokens_on_plan_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.plan_hash <> OLD.plan_hash THEN
        UPDATE approval_token
           SET voided_at = now(),
               voided_reason = 'subject_hash_changed'
         WHERE subject_type = 'plan'
           AND subject_id = NEW.id
           AND voided_at IS NULL;
        UPDATE approval_request
           SET state = 'voided'
         WHERE subject_type = 'plan'
           AND subject_id = NEW.id
           AND state IN ('pending_internal','pending_client','approved');
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_plan_void_tokens AFTER UPDATE ON plan
    FOR EACH ROW EXECUTE FUNCTION void_tokens_on_plan_change();

CREATE OR REPLACE FUNCTION void_tokens_on_creative_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.creative_hash <> OLD.creative_hash THEN
        UPDATE approval_token
           SET voided_at = now(),
               voided_reason = 'subject_hash_changed'
         WHERE subject_type = 'creative_set'
           AND subject_id = NEW.id
           AND voided_at IS NULL;
        UPDATE approval_request
           SET state = 'voided'
         WHERE subject_type = 'creative_set'
           AND subject_id = NEW.id
           AND state IN ('pending_internal','pending_client','approved');
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_creative_void_tokens AFTER UPDATE ON creative
    FOR EACH ROW EXECUTE FUNCTION void_tokens_on_creative_change();

-- Bounded autonomy (PRD AP-8): action types and thresholds that execute
-- without review. Everything else queues.
CREATE TABLE auto_approve_rule (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id          uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    proposal_kind     proposal_kind NOT NULL,
    max_daily_usd_delta numeric(12,2) NOT NULL DEFAULT 0,
    min_evidence_conversions integer NOT NULL DEFAULT 50,
    conditions        jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_active         boolean NOT NULL DEFAULT true,
    created_by        uuid NOT NULL REFERENCES app_user(id),
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT auto_rule_unique UNIQUE (brand_id, proposal_kind),
    CONSTRAINT evidence_non_negative CHECK (min_evidence_conversions >= 0)
);

-- =====================================================================
-- SECTION 9: DEPLOYMENT & CHANNEL OBJECTS
-- =====================================================================

CREATE TABLE deployment (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id        uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    plan_id         uuid NOT NULL REFERENCES plan(id) ON DELETE RESTRICT,
    token_id        uuid NOT NULL REFERENCES approval_token(id) ON DELETE RESTRICT,
    state           deployment_state NOT NULL DEFAULT 'preflight',
    workflow_id     text NOT NULL,        -- Temporal workflow id
    started_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz,
    rollback_deadline timestamptz,        -- PRD LN-5: 10-minute rollback window
    CONSTRAINT deployment_workflow_unique UNIQUE (workflow_id)
);
CREATE INDEX idx_deployment_brand ON deployment(brand_id, state);

-- Channels are independent units of success (PRD LN-2).
CREATE TABLE deployment_channel (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_id   uuid NOT NULL REFERENCES deployment(id) ON DELETE CASCADE,
    brand_id        uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    channel         channel NOT NULL,
    state           deployment_state NOT NULL DEFAULT 'preflight',
    preflight_result jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code      text,
    error_detail    text,
    attempt_count   integer NOT NULL DEFAULT 0,
    is_retryable    boolean NOT NULL DEFAULT true,
    governor_queued_at timestamptz,
    completed_at    timestamptz,
    CONSTRAINT deployment_channel_unique UNIQUE (deployment_id, channel)
);

CREATE TABLE campaign_object (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id          uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    connection_id     uuid NOT NULL REFERENCES channel_connection(id) ON DELETE CASCADE,
    channel           channel NOT NULL,
    level             object_level NOT NULL,
    native_id         text NOT NULL,
    parent_id         uuid REFERENCES campaign_object(id) ON DELETE CASCADE,
    plan_id           uuid REFERENCES plan(id) ON DELETE SET NULL,
    creative_id       uuid REFERENCES creative(id) ON DELETE SET NULL,
    name              text,
    state             object_state NOT NULL DEFAULT 'unknown',
    intended_state    object_state,
    daily_budget_usd  numeric(12,2),
    lifetime_budget_usd numeric(12,2),
    bid_strategy      text,
    targeting_snapshot jsonb,
    -- Idempotency key from architecture §4.7: prevents duplicate creates when
    -- a channel returns 504 after committing.
    idem_key          text,
    native_payload    jsonb,
    last_verified_at  timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT campaign_object_native_unique UNIQUE (connection_id, level, native_id),
    CONSTRAINT campaign_object_idem_unique UNIQUE (connection_id, idem_key),
    CONSTRAINT ad_level_requires_creative
        CHECK (level <> 'ad' OR creative_id IS NOT NULL OR state = 'unknown')
);
CREATE INDEX idx_campaign_object_brand ON campaign_object(brand_id, channel, level);
CREATE INDEX idx_campaign_object_parent ON campaign_object(parent_id);
CREATE INDEX idx_campaign_object_active ON campaign_object(brand_id)
    WHERE state = 'active';
CREATE TRIGGER trg_campaign_object_touch BEFORE UPDATE ON campaign_object
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE channel_asset_ref (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id        uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    rendition_id    uuid NOT NULL REFERENCES rendition(id) ON DELETE CASCADE,
    connection_id   uuid NOT NULL REFERENCES channel_connection(id) ON DELETE CASCADE,
    native_asset_id text NOT NULL,
    uploaded_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT channel_asset_unique UNIQUE (rendition_id, connection_id)
);

-- Verbatim rejection capture feeding the remediation loop (PRD CP-7, LN-3).
CREATE TABLE channel_rejection (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id          uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    campaign_object_id uuid REFERENCES campaign_object(id) ON DELETE CASCADE,
    creative_id       uuid REFERENCES creative(id) ON DELETE CASCADE,
    channel           channel NOT NULL,
    raw_code          text,
    raw_message       text NOT NULL,      -- stored verbatim, never normalized away
    classified_as     text,
    proposed_remediation text,
    promoted_to_rule_id uuid REFERENCES policy_corpus_rule(id),
    occurred_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_rejection_brand ON channel_rejection(brand_id, occurred_at DESC);
CREATE INDEX idx_rejection_class ON channel_rejection(channel, classified_as);

-- =====================================================================
-- SECTION 10: AUDIT LEDGER (APPEND-ONLY)
-- =====================================================================

CREATE TABLE action (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id          uuid NOT NULL REFERENCES brand(id) ON DELETE RESTRICT,
    actor_kind        actor_kind NOT NULL,
    actor_user_id     uuid REFERENCES app_user(id),
    actor_agent_name  text,
    action_type       action_type NOT NULL,
    target_kind       text NOT NULL,
    target_id         uuid,
    target_native_id  text,
    channel           channel,
    diff              jsonb NOT NULL DEFAULT '{}'::jsonb,   -- {before:{}, after:{}}
    rationale         text,
    -- Every spend-affecting action names the token that authorized it.
    token_id          uuid REFERENCES approval_token(id),
    revert_path       jsonb,
    reverted_by_action_id uuid REFERENCES action(id),
    channel_request_id  text,
    channel_response_id text,
    usd_impact        numeric(12,2),
    executed_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT actor_identified
        CHECK ((actor_kind = 'human' AND actor_user_id IS NOT NULL)
            OR (actor_kind = 'agent' AND actor_agent_name IS NOT NULL)
            OR actor_kind = 'system'),
    -- Invariant #2, restated as a database constraint: spend-affecting
    -- action types must reference an authorizing token.
    CONSTRAINT spend_actions_require_token
        CHECK (action_type NOT IN ('deploy','budget_set','bid_set',
                                   'targeting_change','creative_swap','resume')
               OR token_id IS NOT NULL)
);
CREATE INDEX idx_action_brand_time ON action(brand_id, executed_at DESC);
CREATE INDEX idx_action_target ON action(target_kind, target_id);
CREATE INDEX idx_action_token ON action(token_id);

CREATE TRIGGER trg_action_append_only
    BEFORE UPDATE OR DELETE ON action
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();

-- =====================================================================
-- SECTION 11: METRICS & OPTIMIZATION
-- =====================================================================

-- Raw channel metrics land immutable. Normalization happens in a separate
-- layer so cross-channel comparability stays explicit (architecture §4.9).
CREATE TABLE metric_fact_raw (
    id                 bigint GENERATED ALWAYS AS IDENTITY,
    brand_id           uuid NOT NULL,
    campaign_object_id uuid NOT NULL,
    channel            channel NOT NULL,
    date_hour          timestamptz NOT NULL,
    impressions        bigint NOT NULL DEFAULT 0,
    clicks             bigint NOT NULL DEFAULT 0,
    spend_usd          numeric(14,4) NOT NULL DEFAULT 0,
    conversions        numeric(14,4) NOT NULL DEFAULT 0,
    conversion_value_usd numeric(14,4) NOT NULL DEFAULT 0,
    frequency          numeric(8,4),
    reach              bigint,
    video_views        bigint,
    video_completions  bigint,
    engagements        bigint,
    -- Channel-specific fields preserved rather than discarded.
    native_metrics     jsonb NOT NULL DEFAULT '{}'::jsonb,
    attribution_window text,
    attribution_model  text,
    ingested_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (date_hour, campaign_object_id)
) PARTITION BY RANGE (date_hour);

CREATE INDEX idx_metric_raw_brand ON metric_fact_raw(brand_id, date_hour DESC);

CREATE TABLE metric_fact_raw_2026m09 PARTITION OF metric_fact_raw
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE metric_fact_raw_2026m10 PARTITION OF metric_fact_raw
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE metric_fact_raw_2026m11 PARTITION OF metric_fact_raw
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE metric_fact_raw_2026m12 PARTITION OF metric_fact_raw
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
-- Further partitions created by a monthly maintenance job.

-- Normalized layer. comparability_class is mandatory so no aggregate can be
-- emitted without a methodology annotation (PRD §14 risk).
CREATE TABLE metric_normalized (
    id                 bigint GENERATED ALWAYS AS IDENTITY,
    brand_id           uuid NOT NULL,
    campaign_object_id uuid NOT NULL,
    channel            channel NOT NULL,
    date_hour          timestamptz NOT NULL,
    metric_key         text NOT NULL,     -- 'spend','clicks','conversions','revenue'
    metric_value       numeric(18,6) NOT NULL,
    comparability      comparability_class NOT NULL,
    methodology_note   text,
    source             text NOT NULL DEFAULT 'channel', -- 'channel'|'first_party'
    PRIMARY KEY (date_hour, campaign_object_id, metric_key, source)
) PARTITION BY RANGE (date_hour);

CREATE TABLE metric_normalized_2026m09 PARTITION OF metric_normalized
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE metric_normalized_2026m10 PARTITION OF metric_normalized
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE metric_normalized_2026m11 PARTITION OF metric_normalized
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE metric_normalized_2026m12 PARTITION OF metric_normalized
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

-- First-party conversions (CRM, ecommerce, call tracking) — preferred for
-- cross-channel decisions because platform attribution is not comparable.
CREATE TABLE first_party_conversion (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id        uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    external_id     text NOT NULL,
    source_system   text NOT NULL,
    occurred_at     timestamptz NOT NULL,
    value_usd       numeric(14,4),
    quality_grade   text,               -- lead quality, when available
    utm_source      text,
    utm_medium      text,
    utm_campaign    text,
    utm_content     text,
    matched_campaign_object_id uuid REFERENCES campaign_object(id),
    match_confidence numeric(4,3),
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fpc_unique UNIQUE (brand_id, source_system, external_id)
);
CREATE INDEX idx_fpc_brand_time ON first_party_conversion(brand_id, occurred_at DESC);

-- Compound fatigue scoring (PRD OP-3): all three signals must fire.
CREATE TABLE fatigue_score (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id           uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    campaign_object_id uuid NOT NULL REFERENCES campaign_object(id) ON DELETE CASCADE,
    creative_id        uuid REFERENCES creative(id) ON DELETE CASCADE,
    evaluated_at       timestamptz NOT NULL DEFAULT now(),
    window_days        integer NOT NULL,
    frequency_current  numeric(8,4),
    frequency_trend    numeric(8,4),
    engagement_decay_pct numeric(8,4),
    cost_per_result_trend_pct numeric(8,4),
    signals_fired      text[] NOT NULL DEFAULT ARRAY[]::text[],
    is_fatigued        boolean NOT NULL DEFAULT false,
    threshold_source_spec_id uuid REFERENCES placement_spec(id),
    -- No single-signal or time-elapsed trigger exists.
    CONSTRAINT fatigue_requires_compound_signals
        CHECK (NOT is_fatigued OR cardinality(signals_fired) >= 3)
);
CREATE INDEX idx_fatigue_object ON fatigue_score(campaign_object_id, evaluated_at DESC);
CREATE INDEX idx_fatigue_active ON fatigue_score(brand_id) WHERE is_fatigued;

CREATE TABLE optimization_proposal (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id          uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    kind              proposal_kind NOT NULL,
    state             proposal_state NOT NULL DEFAULT 'proposed',
    target_object_id  uuid REFERENCES campaign_object(id) ON DELETE CASCADE,
    channel           channel,
    proposed_change   jsonb NOT NULL,
    rationale         text NOT NULL,
    evidence          jsonb NOT NULL,     -- sample sizes, conversions, windows
    projected_impact  jsonb,
    confidence        numeric(4,3),
    usd_daily_delta   numeric(12,2) NOT NULL DEFAULT 0,
    auto_approve_rule_id uuid REFERENCES auto_approve_rule(id),
    approval_request_id  uuid REFERENCES approval_request(id),
    executed_action_id   uuid REFERENCES action(id),
    proposed_at       timestamptz NOT NULL DEFAULT now(),
    expires_at        timestamptz,
    CONSTRAINT proposal_confidence_range
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    -- Auto-approval requires a matching rule; otherwise it queues.
    CONSTRAINT auto_approved_requires_rule
        CHECK (state <> 'auto_approved' OR auto_approve_rule_id IS NOT NULL)
);
CREATE INDEX idx_proposal_brand_state ON optimization_proposal(brand_id, state);

-- Statistical discipline (PRD OP-6): no winner below threshold.
CREATE TABLE creative_test (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id          uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    plan_id           uuid NOT NULL REFERENCES plan(id) ON DELETE CASCADE,
    hypothesis        text NOT NULL,
    varied_axis       text NOT NULL,
    min_impressions   bigint NOT NULL,
    min_conversions   integer NOT NULL,
    started_at        timestamptz NOT NULL DEFAULT now(),
    concluded_at      timestamptz,
    winner_creative_id uuid REFERENCES creative(id),
    p_value           numeric(8,6),
    inconclusive_reason text,
    CONSTRAINT winner_requires_conclusion
        CHECK (winner_creative_id IS NULL OR concluded_at IS NOT NULL),
    CONSTRAINT thresholds_positive
        CHECK (min_impressions > 0 AND min_conversions >= 0)
);

CREATE TABLE anomaly (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id          uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    kind              anomaly_kind NOT NULL,
    severity          severity NOT NULL,
    channel           channel,
    campaign_object_id uuid REFERENCES campaign_object(id) ON DELETE CASCADE,
    detail            jsonb NOT NULL DEFAULT '{}'::jsonb,
    detected_at       timestamptz NOT NULL DEFAULT now(),
    acknowledged_at   timestamptz,
    acknowledged_by   uuid REFERENCES app_user(id),
    resolved_at       timestamptz
);
CREATE INDEX idx_anomaly_open ON anomaly(brand_id, severity, detected_at DESC)
    WHERE resolved_at IS NULL;

-- =====================================================================
-- SECTION 12: LEARNING & COST METERING
-- =====================================================================

-- Tenant-isolated by default; global scope only after de-identification.
CREATE TABLE learning_signal (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id          uuid REFERENCES brand(id) ON DELETE CASCADE,
    account_id        uuid REFERENCES account(id) ON DELETE CASCADE,
    scope             learning_scope NOT NULL,
    vertical          text,
    channel           channel,
    creative_attributes jsonb NOT NULL,   -- hook_type, format, offer, length...
    outcome_metrics   jsonb NOT NULL,
    sample_size       integer NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    -- Global signals must be de-identified and meet k-anonymity.
    CONSTRAINT global_signals_deidentified
        CHECK (scope <> 'global' OR (brand_id IS NULL AND sample_size >= 30)),
    CONSTRAINT brand_signals_scoped
        CHECK (scope <> 'brand' OR brand_id IS NOT NULL)
);
CREATE INDEX idx_learning_scope ON learning_signal(scope, vertical, channel);

-- Metered at the call site (architecture §7). Margin per brand is a daily
-- rollup over this table.
CREATE TABLE agent_run (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id          uuid REFERENCES brand(id) ON DELETE CASCADE,
    workflow_id       text,
    activity_id       text,
    agent_name        text NOT NULL,
    model_id          text NOT NULL,
    model_tier        text NOT NULL,      -- 'frontier'|'mid'|'cheap'
    input_tokens      bigint NOT NULL DEFAULT 0,
    output_tokens     bigint NOT NULL DEFAULT 0,
    image_count       integer NOT NULL DEFAULT 0,
    video_seconds     numeric(10,2) NOT NULL DEFAULT 0,
    cost_usd          numeric(12,6) NOT NULL DEFAULT 0,
    latency_ms        integer,
    schema_valid      boolean NOT NULL DEFAULT true,
    retry_count       integer NOT NULL DEFAULT 0,
    escalated_to_human boolean NOT NULL DEFAULT false,
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    CONSTRAINT agent_cost_non_negative CHECK (cost_usd >= 0)
);
CREATE INDEX idx_agent_run_brand_time ON agent_run(brand_id, started_at DESC);
CREATE INDEX idx_agent_run_agent ON agent_run(agent_name, started_at DESC);

CREATE TABLE generation_budget_usage (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id      uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    period_month  date NOT NULL,
    budget_usd    numeric(12,2) NOT NULL,
    consumed_usd  numeric(12,6) NOT NULL DEFAULT 0,
    degraded_at   timestamptz,           -- when we fell back to cheaper tiers
    CONSTRAINT gen_budget_unique UNIQUE (brand_id, period_month)
);

-- =====================================================================
-- SECTION 13: REPORTING
-- =====================================================================

CREATE TABLE report (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id        uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    kind            text NOT NULL,       -- 'weekly_digest'|'client_report'|'portfolio'
    period_start    date NOT NULL,
    period_end      date NOT NULL,
    narrative       text,
    metrics_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    actions_summary  jsonb NOT NULL DEFAULT '{}'::jsonb,  -- PRD RP-4 transparency
    methodology_notes text,
    pdf_uri         text,
    web_token       text UNIQUE,
    white_labeled   boolean NOT NULL DEFAULT false,
    generated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT report_period_ordered CHECK (period_end >= period_start)
);
CREATE INDEX idx_report_brand ON report(brand_id, period_end DESC);

CREATE TABLE report_delivery (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id     uuid NOT NULL REFERENCES report(id) ON DELETE CASCADE,
    recipient_email citext NOT NULL,
    channel_kind  text NOT NULL DEFAULT 'email',  -- 'email'|'slack'|'link'
    sent_at       timestamptz,
    opened_at     timestamptz,
    error_detail  text
);

-- =====================================================================
-- SECTION 14: ROW-LEVEL SECURITY
-- Invariant #1. FORCE so even table owners are subject to policy.
-- =====================================================================

DO $$
DECLARE
    t text;
    -- NOTE: 'brand' is deliberately excluded here; it is keyed by id rather
    -- than brand_id and gets its own policy immediately below.
    brand_scoped text[] := ARRAY[
        'brand_graph_assertion','brand_constraint','brand_kit','asset',
        'consent_artifact','channel_connection','plan','plan_allocation',
        'budget_ceiling','brand_kill_switch','creative_concept','creative',
        'rendition','compliance_record','disclosure_application',
        'compliance_override','approval_request','approval_token','deployment',
        'deployment_channel','campaign_object','channel_asset_ref',
        'channel_rejection','action','first_party_conversion','fatigue_score',
        'optimization_proposal','creative_test','anomaly','auto_approve_rule',
        'agent_run','generation_budget_usage','report'
    ];
BEGIN
    FOREACH t IN ARRAY brand_scoped LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($f$
            CREATE POLICY tenant_isolation ON %I
            USING (brand_id = ANY (current_brand_ids()))
            WITH CHECK (brand_id = ANY (current_brand_ids()))
        $f$, t);
    END LOOP;
END $$;

-- 'brand' itself is keyed by id, not brand_id.
ALTER TABLE brand ENABLE ROW LEVEL SECURITY;
ALTER TABLE brand FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON brand
    USING (id = ANY (current_brand_ids()))
    WITH CHECK (id = ANY (current_brand_ids()));

-- Child tables reached only via their parent's brand-scoped row.
ALTER TABLE compliance_check ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance_check FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON compliance_check
    USING (EXISTS (SELECT 1 FROM compliance_record r
                    WHERE r.id = compliance_record_id
                      AND r.brand_id = ANY (current_brand_ids())));

ALTER TABLE approval_token_consumption ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_token_consumption FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON approval_token_consumption
    USING (EXISTS (SELECT 1 FROM approval_token t
                    WHERE t.id = token_id
                      AND t.brand_id = ANY (current_brand_ids())));

ALTER TABLE report_delivery ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_delivery FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON report_delivery
    USING (EXISTS (SELECT 1 FROM report r
                    WHERE r.id = report_id
                      AND r.brand_id = ANY (current_brand_ids())));

-- Learning signals: brand and agency scopes are tenant-bound; global is
-- readable by all because it is de-identified by constraint.
ALTER TABLE learning_signal ENABLE ROW LEVEL SECURITY;
ALTER TABLE learning_signal FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON learning_signal
    USING (scope = 'global' OR brand_id = ANY (current_brand_ids()));

-- Partitioned metric tables.
ALTER TABLE metric_fact_raw ENABLE ROW LEVEL SECURITY;
ALTER TABLE metric_fact_raw FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON metric_fact_raw
    USING (brand_id = ANY (current_brand_ids()))
    WITH CHECK (brand_id = ANY (current_brand_ids()));

ALTER TABLE metric_normalized ENABLE ROW LEVEL SECURITY;
ALTER TABLE metric_normalized FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON metric_normalized
    USING (brand_id = ANY (current_brand_ids()))
    WITH CHECK (brand_id = ANY (current_brand_ids()));

-- =====================================================================
-- SECTION 15: OPERATIONAL VIEWS
-- =====================================================================

-- Approval queue, one row per pending item with everything a reviewer needs
-- on one screen (PRD AP-1).
CREATE VIEW v_approval_queue AS
SELECT ar.id,
       ar.brand_id,
       b.display_name        AS brand_name,
       ar.subject_type,
       ar.subject_id,
       ar.state,
       ar.requested_daily_usd,
       ar.requested_total_usd,
       ar.rationale,
       ar.projected_impact,
       ar.requires_client_approval,
       ar.expires_at,
       ar.created_at,
       EXISTS (
           SELECT 1 FROM compliance_record cr
            WHERE cr.plan_id = ar.subject_id
               OR cr.creative_id = ar.subject_id
              AND cr.overall_verdict = 'flag'
       )                     AS has_compliance_flag
  FROM approval_request ar
  JOIN brand b ON b.id = ar.brand_id
 WHERE ar.state IN ('pending_internal', 'pending_client');

-- Agency portfolio view (PRD RP-3): spend, pacing, and exceptions per client.
CREATE VIEW v_agency_portfolio AS
SELECT b.account_id,
       b.id                          AS brand_id,
       b.display_name,
       COALESCE(SUM(m.spend_usd), 0) AS spend_last_30d,
       MAX(bc.monthly_usd_max)       AS monthly_ceiling,
       COUNT(DISTINCT co.id) FILTER (WHERE co.state = 'active') AS active_objects,
       COUNT(DISTINCT an.id) FILTER (WHERE an.resolved_at IS NULL
                                      AND an.severity = 'critical') AS open_critical,
       COUNT(DISTINCT ap.id) FILTER (WHERE ap.state IN ('pending_internal',
                                                        'pending_client'))
                                     AS pending_approvals
  FROM brand b
  LEFT JOIN campaign_object co ON co.brand_id = b.id
  LEFT JOIN metric_fact_raw m  ON m.campaign_object_id = co.id
                              AND m.date_hour >= now() - interval '30 days'
  LEFT JOIN budget_ceiling bc  ON bc.brand_id = b.id AND bc.scope_kind = 'brand'
  LEFT JOIN anomaly an         ON an.brand_id = b.id
  LEFT JOIN approval_request ap ON ap.brand_id = b.id
 GROUP BY b.account_id, b.id, b.display_name;

-- Gross margin per brand per month (architecture §7 / PRD generation-cost risk).
CREATE VIEW v_brand_margin_monthly AS
SELECT ar.brand_id,
       date_trunc('month', ar.started_at)::date AS period_month,
       SUM(ar.cost_usd)                         AS generation_cost_usd,
       gbu.budget_usd                           AS generation_budget_usd,
       gbu.degraded_at
  FROM agent_run ar
  LEFT JOIN generation_budget_usage gbu
         ON gbu.brand_id = ar.brand_id
        AND gbu.period_month = date_trunc('month', ar.started_at)::date
 GROUP BY ar.brand_id, date_trunc('month', ar.started_at), gbu.budget_usd,
          gbu.degraded_at;

-- Live spend authority audit: every action that moved money, with its token
-- and approver. This is the query that answers "why did this run?"
CREATE VIEW v_spend_authority_trail AS
SELECT a.id                AS action_id,
       a.brand_id,
       a.action_type,
       a.channel,
       a.usd_impact,
       a.executed_at,
       a.actor_kind,
       a.actor_agent_name,
       t.id                AS token_id,
       t.subject_type,
       t.subject_hash,
       t.usd_daily_cap,
       u.email             AS approver_email,
       areq.internal_approved_at,
       areq.client_approved_at
  FROM action a
  JOIN approval_token t   ON t.id = a.token_id
  JOIN app_user u         ON u.id = t.approver_id
  JOIN approval_request areq ON areq.id = t.approval_request_id
 WHERE a.token_id IS NOT NULL;

-- =====================================================================
-- SECTION 16: SEED DATA — CAPABILITY REGISTRY
-- Quota models are declared from published documentation. The governor
-- never probes limits empirically (Pinterest prohibits it outright).
-- =====================================================================

INSERT INTO channel_capability
    (channel, registry_version, objectives, hierarchy, budget_levels,
     bid_strategies, targeting_dimensions, supports, quota_model, prerequisites)
VALUES
('meta', '2026.09',
 ARRAY['awareness','traffic','engagement','leads','app_promotion','sales']::campaign_objective[],
 ARRAY['campaign','ad_group','ad']::object_level[],
 ARRAY['campaign','ad_group']::object_level[],
 ARRAY['lowest_cost','cost_cap','bid_cap','roas_goal'],
 ARRAY['geo','age','gender','language','custom_audience','lookalike',
       'placement','advantage_plus_auto'],
 '{"catalog_ads":true,"lead_forms":true,"dynamic_creative":true,
   "staged_rollout":true,"dayparting":false}'::jsonb,
 '{"kind":"points_per_ad_account_hour","formula":"base + 40 * active_ads",
   "base":{"development":300,"standard":100000},
   "usage_header":"X-Ad-Account-Usage","tier_field":"ads_api_access_tier"}'::jsonb,
 ARRAY['pixel_configured','billing_valid']),

('google_ads', '2026.09',
 ARRAY['awareness','traffic','leads','sales','app_promotion']::campaign_objective[],
 ARRAY['campaign','asset_group','ad']::object_level[],
 ARRAY['campaign']::object_level[],
 ARRAY['maximize_conversions','target_cpa','target_roas','maximize_clicks'],
 ARRAY['geo','language','audience_signal','url_expansion','device','schedule'],
 '{"catalog_ads":true,"lead_forms":true,"asset_groups_max":100,
   "asset_group_min_per_campaign":1,"asset_groups_shareable":false,
   "final_url_required":true,"staged_rollout":true,"dayparting":true}'::jsonb,
 '{"kind":"operations_per_day","notes":"basic/standard access levels; token-based"}'::jsonb,
 ARRAY['conversion_action_configured','billing_valid']),

('youtube', '2026.09',
 ARRAY['awareness','video_views','traffic','leads','sales']::campaign_objective[],
 ARRAY['campaign','ad_group','ad']::object_level[],
 ARRAY['campaign','ad_group']::object_level[],
 ARRAY['target_cpm','target_cpv','maximize_conversions','target_cpa'],
 ARRAY['geo','language','audience_signal','topic','placement','device','schedule'],
 '{"formats":["in_stream_skippable","in_stream_non_skippable","in_feed",
   "shorts","bumper"],"served_via":"google_ads","dayparting":true}'::jsonb,
 '{"kind":"operations_per_day","notes":"shares Google Ads API quota"}'::jsonb,
 ARRAY['conversion_action_configured','billing_valid']),

('tiktok', '2026.09',
 ARRAY['awareness','traffic','engagement','leads','app_promotion','sales','video_views']::campaign_objective[],
 ARRAY['campaign','ad_group','ad']::object_level[],
 ARRAY['campaign','ad_group']::object_level[],
 ARRAY['lowest_cost','cost_cap','bid_cap','value_optimization'],
 ARRAY['geo','age','gender','language','interest','behavior','custom_audience',
       'lookalike','placement'],
 '{"catalog_ads":true,"lead_forms":true,"spark_ads":true,
   "batch_creative_management":true,"async_campaign_copy":true,
   "dynamic_quota_endpoint":true}'::jsonb,
 '{"kind":"per_app_and_advertiser","notes":"dynamic quota on active ad groups queried via API; numeric limits not published"}'::jsonb,
 ARRAY['pixel_configured','terms_signed','account_verified','data_security_review']),

('linkedin', '2026.09',
 ARRAY['awareness','traffic','engagement','leads','video_views']::campaign_objective[],
 ARRAY['campaign','ad_group','ad']::object_level[],
 ARRAY['campaign']::object_level[],
 ARRAY['maximum_delivery','cost_cap','manual_bidding'],
 ARRAY['geo','job_title','job_function','seniority','company_size','industry',
       'skills','matched_audience'],
 '{"lead_forms":true,"document_ads":true,"catalog_ads":false,
   "dayparting":false}'::jsonb,
 '{"kind":"write_account_allowlist",
   "development":{"post_ad_accounts_max":5,"creatable_test_accounts":1,
                  "get_accounts":"unlimited"},
   "standard":{"post_ad_accounts_max":null,"creatable_accounts":"unlimited"}}'::jsonb,
 ARRAY['conversion_tracking_configured','billing_valid']),

('microsoft', '2026.09',
 ARRAY['traffic','leads','sales','awareness']::campaign_objective[],
 ARRAY['campaign','ad_group','ad']::object_level[],
 ARRAY['campaign','ad_group']::object_level[],
 ARRAY['enhanced_cpc','maximize_conversions','target_cpa','target_roas',
       'manual_cpc'],
 ARRAY['geo','language','audience','device','schedule','keyword'],
 '{"shopping":true,"audience_network":true,"api_protocol":"rest_only",
   "soap_deprecated_on":"2027-01-31","rest_exclusive_features_from":"2026-10-01"}'::jsonb,
 '{"kind":"requests_per_minute","notes":"REST only; build no SOAP path"}'::jsonb,
 ARRAY['conversion_goal_configured','billing_valid']),

('reddit', '2026.09',
 ARRAY['awareness','traffic','engagement','leads','app_promotion','sales','video_views']::campaign_objective[],
 ARRAY['campaign','ad_group','ad']::object_level[],
 ARRAY['campaign','ad_group']::object_level[],
 ARRAY['lowest_cost','bid_cap','cost_cap'],
 ARRAY['geo','interest','community','device','placement','custom_audience'],
 '{"open_to_all_developers":true,"allowlisting_required":false,
   "conversion_pixel_required_on_ad_groups_and_cbo":true,
   "conversion_pixel_required_since":"2026-07-13",
   "objective_enums_updated":"2026-09-21",
   "legacy_objective_enums_supported":true}'::jsonb,
 '{"kind":"requests_per_minute","notes":"OAuth2 access + refresh tokens"}'::jsonb,
 ARRAY['conversion_pixel_id_required','billing_valid']),

('pinterest', '2026.09',
 ARRAY['awareness','traffic','engagement','sales','video_views']::campaign_objective[],
 ARRAY['campaign','ad_group','ad']::object_level[],
 ARRAY['campaign','ad_group']::object_level[],
 ARRAY['automatic_bid','target_cpc','maximize_conversions'],
 ARRAY['geo','interest','keyword','audience','device','placement'],
 '{"catalog_ads":true,"shopping_ads":true,"idea_ads":true,
   "access_tier":"v5_standard","rate_limit_probing_prohibited":true,
   "byok_requires_local_credential_storage":true}'::jsonb,
 '{"kind":"undisclosed","notes":"limits not published; never probe — respect 429 headers and back off"}'::jsonb,
 ARRAY['privacy_policy_linked','billing_valid']),

('snapchat', '2026.09',
 ARRAY['awareness','traffic','engagement','leads','app_promotion','sales','video_views']::campaign_objective[],
 ARRAY['campaign','ad_squad','ad']::object_level[],
 ARRAY['campaign','ad_squad']::object_level[],
 ARRAY['lowest_cost','target_cost','max_bid'],
 ARRAY['geo','age','gender','interest','behavior','custom_audience','lookalike',
       'placement'],
 '{"catalog_ads":true,"dynamic_product_ads":true,"lead_forms":true,
   "ar_lenses":true,"open_to_all_developers":true,
   "reporting_granularity":["hourly","daily","total"],
   "ad_create_path":"POST /v1/adsquads/{ad_squad_id}/ads"}'::jsonb,
 '{"kind":"requests_per_minute","notes":"per-endpoint limits documented"}'::jsonb,
 ARRAY['pixel_configured','billing_valid']),

('amazon_ads', '2026.09',
 ARRAY['sales','awareness','traffic']::campaign_objective[],
 ARRAY['campaign','ad_group','ad']::object_level[],
 ARRAY['campaign']::object_level[],
 ARRAY['dynamic_bids_down_only','dynamic_bids_up_and_down','fixed_bids'],
 ARRAY['keyword','product_target','category_target','audience','placement'],
 '{"sponsored_products":true,"sponsored_brands":true,
   "campaign_management_version":"v3","v2_deprecated":true,
   "required_headers":["Amazon-Advertising-API-ClientId",
                       "Amazon-Advertising-API-Scope"]}'::jsonb,
 '{"kind":"requests_per_second","notes":"profile-scoped; v3 endpoints"}'::jsonb,
 ARRAY['profile_scope_resolved','billing_valid']);

-- =====================================================================
-- END OF SCHEMA
--
-- MIGRATION NOTES
--   * Expand/contract only. RLS policies ship with the migration and are
--     covered by a cross-tenant leak suite that runs on every migration.
--   * metric_fact_raw / metric_normalized partitions are created by a
--     monthly maintenance job; ClickHouse is the analytical store of record
--     and these tables are the operational mirror with 13-month retention.
--   * Negative security suite must cover: subject-hash mutation, scope
--     escalation, cap overflow, token replay, expiry, and cross-brand token
--     use. All must fail closed. Regressions here block release.
-- =====================================================================
