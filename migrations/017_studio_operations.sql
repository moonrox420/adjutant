SET search_path=adjutant,public;

ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'studio_generate';
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'studio_edit';
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'context_edit';
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'creative_render';

ALTER TABLE brand_context DROP CONSTRAINT brand_context_source_kind_check;
ALTER TABLE brand_context ADD CHECK (source_kind IN ('website','prompt','edit'));
ALTER TABLE studio_draft ADD UNIQUE (brand_id,id);
ALTER TABLE plan ADD UNIQUE (brand_id,id);
ALTER TABLE creative ADD UNIQUE (brand_id,id);

CREATE TABLE studio_rendition (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id uuid NOT NULL REFERENCES brand(id),
    draft_id uuid NOT NULL,
    draft_revision integer NOT NULL CHECK (draft_revision>0),
    aspect_ratio text NOT NULL CHECK (aspect_ratio IN ('1:1','4:5','9:16','16:9')),
    png_key text NOT NULL CHECK (png_key ~ '^[a-f0-9]{64}$'),
    svg_key text NOT NULL CHECK (svg_key ~ '^[a-f0-9]{64}$'),
    scene_graph jsonb NOT NULL,
    width integer NOT NULL CHECK (width>0), height integer NOT NULL CHECK (height>0),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (brand_id,draft_id) REFERENCES studio_draft(brand_id,id),
    UNIQUE (brand_id,draft_id,draft_revision,aspect_ratio)
);

CREATE TABLE studio_plan_creative (
    brand_id uuid NOT NULL REFERENCES brand(id),
    draft_id uuid NOT NULL,
    draft_revision integer NOT NULL CHECK (draft_revision>0),
    plan_id uuid NOT NULL,
    creative_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (brand_id,draft_id) REFERENCES studio_draft(brand_id,id),
    FOREIGN KEY (brand_id,plan_id) REFERENCES plan(brand_id,id),
    FOREIGN KEY (brand_id,creative_id) REFERENCES creative(brand_id,id),
    PRIMARY KEY (brand_id,draft_id,draft_revision,plan_id)
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['studio_rendition','studio_plan_creative'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I
            USING (brand_id=ANY(current_brand_ids()))
            WITH CHECK (brand_id=ANY(current_brand_ids()))',t);
    END LOOP;
END;
$$;
