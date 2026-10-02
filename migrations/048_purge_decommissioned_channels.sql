SET search_path=adjutant,public;

-- Migration 048: Purge decommissioned channels and restrict to 5 active channels:
-- meta, google_ads, youtube, linkedin, reddit.

-- 1. Remove child records referencing decommissioned channels
DELETE FROM adjutant.remote_stop_item 
WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads')
   OR connection_id IN (SELECT id FROM adjutant.channel_connection WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads'));

DELETE FROM adjutant.campaign_object
WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads')
   OR connection_id IN (SELECT id FROM adjutant.channel_connection WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads'));

DELETE FROM adjutant.campaign_build_step
WHERE build_id IN (SELECT id FROM adjutant.campaign_build WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads')
   OR connection_id IN (SELECT id FROM adjutant.channel_connection WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads')));

DELETE FROM adjutant.campaign_build
WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads')
   OR connection_id IN (SELECT id FROM adjutant.channel_connection WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads'));

ALTER TABLE adjutant.channel_launch_grant DISABLE TRIGGER immutable_channel_launch_grant;
DELETE FROM adjutant.channel_launch_grant
WHERE connection_id IN (SELECT id FROM adjutant.channel_connection WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads'));
ALTER TABLE adjutant.channel_launch_grant ENABLE TRIGGER immutable_channel_launch_grant;

DELETE FROM adjutant.plan_allocation
WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads');

DELETE FROM adjutant.action
WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads');

DELETE FROM adjutant.metric_fact_raw
WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads');

DELETE FROM adjutant.metric_normalized
WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads');

DELETE FROM adjutant.autonomous_decision
WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads');

DELETE FROM adjutant.placement_spec 
WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads');

DELETE FROM adjutant.channel_capability 
WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads');

DELETE FROM adjutant.platform_access_application 
WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads');

DELETE FROM adjutant.channel_authorization 
WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads');

DELETE FROM adjutant.channel_oauth_state 
WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads');

DELETE FROM adjutant.channel_connection 
WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads');

-- 2. Add CHECK constraints enforcing strictly the 5 active channels
ALTER TABLE adjutant.channel_capability
    ADD CONSTRAINT chk_channel_active CHECK (channel::text IN ('meta', 'google_ads', 'youtube', 'linkedin', 'reddit'));

ALTER TABLE adjutant.placement_spec
    ADD CONSTRAINT chk_placement_channel_active CHECK (channel::text IN ('meta', 'google_ads', 'youtube', 'linkedin', 'reddit'));

ALTER TABLE adjutant.channel_connection
    ADD CONSTRAINT chk_connection_channel_active CHECK (channel::text IN ('meta', 'google_ads', 'youtube', 'linkedin', 'reddit'));

ALTER TABLE adjutant.channel_authorization
    ADD CONSTRAINT chk_auth_channel_active CHECK (channel::text IN ('meta', 'google_ads', 'youtube', 'linkedin', 'reddit'));

ALTER TABLE adjutant.campaign_build
    ADD CONSTRAINT chk_build_channel_active CHECK (channel::text IN ('meta', 'google_ads', 'youtube', 'linkedin', 'reddit'));

ALTER TABLE adjutant.campaign_object
    ADD CONSTRAINT chk_object_channel_active CHECK (channel::text IN ('meta', 'google_ads', 'youtube', 'linkedin', 'reddit'));

ALTER TABLE adjutant.action
    ADD CONSTRAINT chk_action_channel_active CHECK (channel::text IN ('meta', 'google_ads', 'youtube', 'linkedin', 'reddit'));

ALTER TABLE adjutant.metric_fact_raw
    ADD CONSTRAINT chk_metric_raw_channel_active CHECK (channel::text IN ('meta', 'google_ads', 'youtube', 'linkedin', 'reddit'));

ALTER TABLE adjutant.metric_normalized
    ADD CONSTRAINT chk_metric_norm_channel_active CHECK (channel::text IN ('meta', 'google_ads', 'youtube', 'linkedin', 'reddit'));

ALTER TABLE adjutant.autonomous_decision
    ADD CONSTRAINT chk_decision_channel_active CHECK (channel::text IN ('meta', 'google_ads', 'youtube', 'linkedin', 'reddit'));

ALTER TABLE adjutant.platform_access_application
    ADD CONSTRAINT chk_platform_access_channel_active CHECK (channel::text IN ('meta', 'google_ads', 'youtube', 'linkedin', 'reddit'));

ALTER TABLE adjutant.plan_allocation
    ADD CONSTRAINT chk_plan_alloc_channel_active CHECK (channel::text IN ('meta', 'google_ads', 'youtube', 'linkedin', 'reddit'));

-- 3. Extend escalation trigger types for autonomous refresh recovery
ALTER TABLE adjutant.escalation DROP CONSTRAINT IF EXISTS escalation_trigger_type_check;
ALTER TABLE adjutant.escalation ADD CONSTRAINT escalation_trigger_type_check CHECK (
    trigger_type IN (
        'spend_spike_24h',
        'cpa_degradation_40pct',
        'creative_policy_rejection',
        'guardrail_breach',
        'new_channel_connection',
        'compliance_flag',
        'channel_auth_failure',
        'creative_refresh_failure'
    )
);
