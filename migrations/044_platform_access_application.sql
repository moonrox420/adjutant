-- Migration 044: Platform Access Application Tracking (Slice 10)
SET search_path = adjutant, public;

CREATE TABLE platform_access_application (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id uuid NOT NULL REFERENCES brand(id),
    channel channel NOT NULL,
    access_tier text NOT NULL,
    status text NOT NULL CHECK (status IN ('not_filed', 'pending_review', 'under_review', 'approved', 'rejected', 'appealed')) DEFAULT 'not_filed',
    filing_date timestamptz,
    decision_date timestamptz,
    notes text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT platform_access_app_unique UNIQUE (brand_id, channel, access_tier)
);

CREATE INDEX idx_platform_access_app_brand_channel ON platform_access_application(brand_id, channel);

-- Enable and force Row Level Security
ALTER TABLE platform_access_application ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_access_application FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON platform_access_application;
CREATE POLICY tenant_isolation ON platform_access_application
    USING (brand_id = ANY(current_brand_ids()))
    WITH CHECK (brand_id = ANY(current_brand_ids()));

-- Grant permissions to application roles
GRANT SELECT, INSERT, UPDATE, DELETE ON platform_access_application TO adjutant_app, adjutant_gateway;
