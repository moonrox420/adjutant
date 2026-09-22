SET search_path=adjutant,public;

ALTER TABLE placement_spec ADD COLUMN source_url text;
ALTER TABLE placement_spec ADD COLUMN verified_on date;

INSERT INTO placement_spec(channel,placement_key,registry_version,format,aspect_ratio,
    min_width_px,min_height_px,max_bytes,safe_zone_insets,text_limits,codec_constraints,
    fatigue_thresholds,source_url)
VALUES('meta','meta.facebook_feed.square','adjutant-v2.2026-09-22','static_image','1:1',
    1080,1080,30000000,'{"top":0,"right":0,"bottom":0,"left":0}',
    '{"primary":125,"headline":40,"description":30}',
    '{"mime_types":["image/png","image/jpeg"],"publisher_platforms":["facebook"],"facebook_positions":["feed"]}',
    '{"ctr_decline_pct":25,"frequency":3,"age_days":14}',
    'https://www.facebook.com/business/ads-guide/image/facebook-feed');
