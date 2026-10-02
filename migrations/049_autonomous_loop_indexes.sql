SET search_path = adjutant, public;

-- S8 & S7: High-frequency query indexes for autonomous loop tables and metric facts under RLS

CREATE INDEX IF NOT EXISTS idx_metric_raw_object_hour
    ON metric_fact_raw(brand_id, campaign_object_id, date_hour DESC);

CREATE INDEX IF NOT EXISTS idx_finding_brand_time
    ON finding(brand_id, created_at DESC, kind);

CREATE INDEX IF NOT EXISTS idx_decision_brand_state
    ON autonomous_decision(brand_id, state, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_escalation_brand_state
    ON escalation(brand_id, state, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_loop_tick_brand_time
    ON loop_tick_run(brand_id, started_at DESC, status);
