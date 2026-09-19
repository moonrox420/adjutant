SET search_path=adjutant,public;
CREATE TABLE website_evidence (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id uuid NOT NULL REFERENCES brand(id),
    source_url text NOT NULL, content_hash text NOT NULL,
    title text NOT NULL, text_content text NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(brand_id,source_url,content_hash)
);
ALTER TABLE website_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE website_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY evidence_tenant ON website_evidence
    USING(brand_id=ANY(current_brand_ids())) WITH CHECK(brand_id=ANY(current_brand_ids()));
CREATE TRIGGER evidence_append_only BEFORE UPDATE OR DELETE ON website_evidence
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
ALTER TABLE agent_run ADD COLUMN error_code text;
