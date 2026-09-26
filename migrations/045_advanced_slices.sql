-- Migration 045: S11-S14 Tables for Video Render Logs and Account Subscriptions
SET search_path = adjutant, public;

ALTER TABLE account ADD COLUMN IF NOT EXISTS billing_status text NOT NULL DEFAULT 'active';

CREATE TABLE IF NOT EXISTS account_subscription (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id             uuid NOT NULL UNIQUE REFERENCES account(id) ON DELETE CASCADE,
    tier_id                text NOT NULL,
    monthly_fee_usd        numeric(12,2) NOT NULL,
    status                 text NOT NULL DEFAULT 'active',
    current_period_start   timestamptz NOT NULL,
    current_period_end     timestamptz NOT NULL,
    payment_method_id      text NOT NULL,
    dunning_started_at     timestamptz,
    grace_period_end       timestamptz,
    last_payment_error     text,
    cancelled_at           timestamptz,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS video_render_log (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id             uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    concept_id           uuid NOT NULL,
    aspect_ratio         text NOT NULL,
    channel              text NOT NULL,
    status               text NOT NULL,
    duration_seconds     numeric(10,4) NOT NULL,
    cost_usd             numeric(12,4) NOT NULL,
    asset_uri            text,
    timeline_spec        jsonb,
    fallback_creative_id uuid,
    error_detail         text,
    created_at           timestamptz NOT NULL DEFAULT now()
);

-- Enable and force Row Level Security on brand-scoped video render logs
ALTER TABLE video_render_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE video_render_log FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON video_render_log;
CREATE POLICY tenant_isolation ON video_render_log
    USING (brand_id = ANY(current_brand_ids()))
    WITH CHECK (brand_id = ANY(current_brand_ids()));

GRANT SELECT, INSERT, UPDATE ON account_subscription TO adjutant_app;
GRANT SELECT, INSERT, UPDATE ON video_render_log TO adjutant_app;
GRANT UPDATE(billing_status) ON account TO adjutant_app;
