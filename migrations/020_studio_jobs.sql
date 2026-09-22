SET search_path=adjutant,public;
ALTER TABLE studio_draft ADD COLUMN work_checkpoint jsonb NOT NULL DEFAULT '{}';

CREATE TABLE studio_job (
    id uuid PRIMARY KEY,
    brand_id uuid NOT NULL REFERENCES brand(id),
    actor_id uuid NOT NULL REFERENCES app_user(id),
    session_hash text NOT NULL,
    request_key uuid NOT NULL,
    url_or_prompt text NOT NULL CHECK (length(url_or_prompt) BETWEEN 3 AND 10000),
    state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','running','completed','failed','cancelled')),
    cancel_requested_at timestamptz,
    error_code text,
    error_message text,
    attempts integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    FOREIGN KEY (brand_id,id) REFERENCES studio_draft(brand_id,id),
    UNIQUE (brand_id,request_key)
);
ALTER TABLE studio_job ENABLE ROW LEVEL SECURITY;
ALTER TABLE studio_job FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON studio_job
    USING (brand_id=ANY(current_brand_ids())) WITH CHECK (brand_id=ANY(current_brand_ids()));

CREATE FUNCTION runnable_studio_jobs() RETURNS TABLE(id uuid,brand_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
    SELECT id,brand_id FROM studio_job WHERE state IN ('queued','running')
    ORDER BY created_at,id LIMIT 64;
$$;
REVOKE ALL ON FUNCTION runnable_studio_jobs() FROM PUBLIC;
