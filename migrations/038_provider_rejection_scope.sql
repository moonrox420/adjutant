SET search_path=adjutant,public;
ALTER TABLE channel_rejection ADD COLUMN build_id uuid;
ALTER TABLE channel_rejection ADD CONSTRAINT rejection_build_scope
    FOREIGN KEY(build_id,brand_id) REFERENCES campaign_build(id,brand_id);
