SET search_path=adjutant,public;

CREATE FUNCTION campaign_review_manifest(target_brand uuid,target_plan uuid) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=adjutant,pg_temp AS $$
    SELECT jsonb_build_object('plan',p.plan_document,'creatives',coalesce((
        SELECT jsonb_agg(jsonb_build_object(
            'id',c.id,'format',c.format,'scene_graph',c.scene_graph,'copy',c.copy_fields,
            'renditions',coalesce((SELECT jsonb_agg(jsonb_build_object(
                'id',r.id,'channel',r.channel,'spec',r.placement_spec_id,'asset_id',r.asset_id,
                'validated',r.spec_validation_passed) ORDER BY r.id)
                FROM rendition r WHERE r.brand_id=target_brand AND r.creative_id=c.id),'[]'),
            'studio_renditions',coalesce((SELECT jsonb_agg(jsonb_build_object(
                'id',r.id,'aspect_ratio',r.aspect_ratio,'png_key',r.png_key,
                'svg_key',r.svg_key,'scene_graph',r.scene_graph) ORDER BY r.id)
                FROM studio_plan_creative sp JOIN studio_rendition r
                ON r.brand_id=sp.brand_id AND r.draft_id=sp.draft_id AND r.draft_revision=sp.draft_revision
                WHERE sp.brand_id=target_brand AND sp.plan_id=target_plan AND sp.creative_id=c.id),'[]')
        ) ORDER BY c.id) FROM creative c JOIN creative_concept cc ON cc.id=c.concept_id
        WHERE c.brand_id=target_brand AND cc.plan_id=target_plan
    ),'[]')) FROM plan p WHERE p.id=target_plan AND p.brand_id=target_brand;
$$;

CREATE FUNCTION verify_launch_manifest() RETURNS trigger LANGUAGE plpgsql
SET search_path=adjutant,pg_temp AS $$
DECLARE approval launch_authorization;
BEGIN
    SELECT * INTO STRICT approval FROM launch_authorization WHERE id=NEW.authorization_id;
    IF approval.claims->'review' IS DISTINCT FROM campaign_review_manifest(NEW.brand_id,approval.plan_id) THEN
        RAISE EXCEPTION 'Creative or plan changed after launch review' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER enforce_launch_manifest BEFORE INSERT ON channel_launch_grant
FOR EACH ROW EXECUTE FUNCTION verify_launch_manifest();
