SET search_path=adjutant,public;

CREATE OR REPLACE FUNCTION campaign_review_manifest(target_brand uuid,target_plan uuid) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=adjutant,pg_temp AS $$
    SELECT jsonb_build_object('plan',p.plan_document,'creatives',coalesce((
        SELECT jsonb_agg(jsonb_build_object(
            'id',c.id,'format',c.format,'scene_graph',c.scene_graph,'copy',c.copy_fields,
            'renditions',coalesce((SELECT jsonb_agg(jsonb_build_object(
                'id',r.id,'channel',r.channel,'spec',r.placement_spec_id,'asset_id',r.asset_id,
                'asset',jsonb_build_object('hash',a.content_hash,'uri',a.storage_uri,
                'mime',a.mime_type,'bytes',a.bytes,'width',a.width_px,'height',a.height_px),
                'placement',to_jsonb(ps),'validated',r.spec_validation_passed) ORDER BY r.id)
                FROM rendition r LEFT JOIN asset a ON a.id=r.asset_id AND a.brand_id=r.brand_id
                JOIN placement_spec ps ON ps.id=r.placement_spec_id WHERE r.brand_id=target_brand AND r.creative_id=c.id),'[]'),
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

