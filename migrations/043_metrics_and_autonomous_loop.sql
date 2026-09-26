SET search_path = adjutant, public;

-- S7: Add conversion_event and view_through_policy to metric_fact_raw
ALTER TABLE metric_fact_raw
    ADD COLUMN IF NOT EXISTS conversion_event text NOT NULL DEFAULT 'purchase',
    ADD COLUMN IF NOT EXISTS view_through_policy text NOT NULL DEFAULT 'none';

-- Watermarks for hourly per-channel metric ingestion
CREATE TABLE IF NOT EXISTS metric_sync_watermark (
    brand_id uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    channel channel NOT NULL,
    watermark timestamptz NOT NULL,
    last_sync_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (brand_id, channel)
);

-- S7.5: Restatement history for late-arriving conversions
CREATE TABLE IF NOT EXISTS metric_restatement_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    campaign_object_id uuid NOT NULL REFERENCES campaign_object(id) ON DELETE CASCADE,
    date_hour timestamptz NOT NULL,
    prior_conversions numeric(14,4) NOT NULL,
    new_conversions numeric(14,4) NOT NULL,
    prior_conversion_value_usd numeric(14,4) NOT NULL,
    new_conversion_value_usd numeric(14,4) NOT NULL,
    restated_at timestamptz NOT NULL DEFAULT now(),
    reason text NOT NULL
);

-- S8: Diagnosis findings with mandatory 3-signal rule for fatigue
CREATE TABLE IF NOT EXISTS finding (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('fatigue', 'winner', 'loser', 'anomaly', 'inefficiency')),
    signals text[] NOT NULL CHECK (kind != 'fatigue' OR array_length(signals, 1) >= 3),
    subject_kind text NOT NULL DEFAULT 'campaign_object',
    subject_id uuid NOT NULL REFERENCES campaign_object(id) ON DELETE CASCADE,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- S8: Candidate and executed autonomous decisions
CREATE TABLE IF NOT EXISTS autonomous_decision (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    finding_id uuid REFERENCES finding(id) ON DELETE SET NULL,
    kind text NOT NULL CHECK (kind IN ('refresh_creative', 'scale_winner', 'cut_loser', 'reallocate_budget', 'pause_object')),
    target_id uuid NOT NULL REFERENCES campaign_object(id) ON DELETE CASCADE,
    channel channel NOT NULL,
    idempotency_key text NOT NULL,
    params jsonb NOT NULL DEFAULT '{}'::jsonb,
    state text NOT NULL DEFAULT 'proposed' CHECK (state IN ('proposed', 'executed', 'rejected', 'escalated')),
    rejection_reason text,
    action_id uuid REFERENCES action(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    executed_at timestamptz,
    UNIQUE (brand_id, idempotency_key)
);

-- S8.5: Human escalation triggers with bounded scope isolation
CREATE TABLE IF NOT EXISTS escalation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    trigger_type text NOT NULL CHECK (trigger_type IN (
        'spend_spike_24h',
        'cpa_degradation_40pct',
        'creative_policy_rejection',
        'guardrail_breach',
        'new_channel_connection',
        'compliance_flag',
        'channel_auth_failure'
    )),
    scope_kind text NOT NULL CHECK (scope_kind IN ('brand', 'channel', 'campaign_object')),
    scope_id uuid,
    context jsonb NOT NULL DEFAULT '{}'::jsonb,
    state text NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'resolved', 'dismissed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);

-- S8: Durable execution loop tick tracking
CREATE TABLE IF NOT EXISTS loop_tick_run (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    step text NOT NULL DEFAULT 'measure',
    watermark_start timestamptz,
    watermark_end timestamptz,
    summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

-- Enable & force Row Level Security across all new tables
DO $$
DECLARE
    tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'metric_sync_watermark',
        'metric_restatement_log',
        'finding',
        'autonomous_decision',
        'escalation',
        'loop_tick_run'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', tbl);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (brand_id = ANY(current_brand_ids())) WITH CHECK (brand_id = ANY(current_brand_ids()))', tbl);
    END LOOP;
END;
$$;

-- Grant permissions to non-superuser application roles for new tables
GRANT SELECT, INSERT, UPDATE, DELETE ON
    metric_sync_watermark,
    metric_restatement_log,
    finding,
    autonomous_decision,
    escalation,
    loop_tick_run
TO adjutant_app;

GRANT SELECT, INSERT, UPDATE ON
    metric_sync_watermark,
    metric_restatement_log,
    finding,
    autonomous_decision,
    escalation,
    loop_tick_run
TO adjutant_gateway;
