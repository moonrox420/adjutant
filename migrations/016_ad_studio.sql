SET search_path=adjutant,public;

ALTER TABLE brand_graph_assertion DROP CONSTRAINT confirmed_requires_provenance;
ALTER TABLE brand_graph_assertion DROP CONSTRAINT confirmed_provenance_nonblank;

CREATE TABLE brand_context (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id uuid NOT NULL REFERENCES brand(id),
    version integer NOT NULL CHECK (version>0),
    input_hash text NOT NULL CHECK (input_hash ~ '^[a-f0-9]{64}$'),
    source_kind text NOT NULL CHECK (source_kind IN ('website','prompt')),
    source_url text,
    document jsonb NOT NULL CHECK (jsonb_typeof(document)='object'),
    accepted_automatically_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (brand_id,version), UNIQUE (brand_id,input_hash), UNIQUE (brand_id,id)
);

CREATE TABLE studio_draft (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id uuid NOT NULL REFERENCES brand(id),
    context_id uuid,
    actor_user_id uuid NOT NULL REFERENCES app_user(id),
    state text NOT NULL DEFAULT 'generating' CHECK (state IN ('generating','completed','failed')),
    document jsonb, scene_graph jsonb,
    image_key text CHECK (image_key ~ '^[a-f0-9]{64}$'),
    image_mime text CHECK (image_mime IN ('image/png','image/jpeg','image/webp')),
    image_model text NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision>0),
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (brand_id,context_id) REFERENCES brand_context(brand_id,id),
    CHECK (state<>'completed' OR (document IS NOT NULL AND scene_graph IS NOT NULL
        AND image_key IS NOT NULL AND image_mime IS NOT NULL AND context_id IS NOT NULL))
);
CREATE UNIQUE INDEX studio_one_generation_per_brand ON studio_draft(brand_id) WHERE state='generating';
CREATE INDEX studio_recent ON studio_draft(brand_id,created_at DESC);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['brand_context','studio_draft'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I
            USING (brand_id=ANY(current_brand_ids()))
            WITH CHECK (brand_id=ANY(current_brand_ids()))',t);
    END LOOP;
END;
$$;
