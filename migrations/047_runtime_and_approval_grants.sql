SET search_path=adjutant,public;

-- Grant app runtime permissions to manage channels, credentials, remote stop, campaign builds, and runner workflows
GRANT SELECT, INSERT, UPDATE ON
    adjutant.channel_connection,
    adjutant.channel_authorization,
    adjutant.channel_oauth_state,
    adjutant.tenant_secret_key,
    adjutant.tenant_secret,
    adjutant.remote_stop_run,
    adjutant.remote_stop_item,
    adjutant.campaign_build,
    adjutant.campaign_build_step,
    adjutant.campaign_object,
    adjutant.workflow_run,
    adjutant.workflow_checkpoint
TO adjutant_app;

GRANT DELETE ON adjutant.tenant_secret TO adjutant_app;
GRANT EXECUTE ON FUNCTION adjutant.runnable_campaign_builds() TO adjutant_app;
GRANT EXECUTE ON FUNCTION adjutant.validate_campaign_build(uuid) TO adjutant_app;
GRANT EXECUTE ON FUNCTION adjutant.runnable_remote_stops() TO adjutant_app;

-- Grant approval service permissions to inspect scope and issue launch authorizations
GRANT SELECT, INSERT ON adjutant.launch_authorization TO adjutant_approval;
GRANT SELECT ON
    adjutant.guardrail,
    adjutant.channel_connection,
    adjutant.channel_launch_grant,
    adjutant.brand_constraint,
    adjutant.creative,
    adjutant.creative_concept,
    adjutant.rendition,
    adjutant.studio_plan_creative,
    adjutant.studio_rendition,
    adjutant.asset,
    adjutant.placement_spec
TO adjutant_approval;

GRANT EXECUTE ON FUNCTION adjutant.campaign_review_manifest(uuid,uuid) TO adjutant_approval;
