SET search_path=adjutant,public;

CREATE TABLE tenant_secret_key (
    brand_id uuid PRIMARY KEY REFERENCES brand(id),
    wrapped_key bytea NOT NULL CHECK (octet_length(wrapped_key)=60),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tenant_secret (
    brand_id uuid NOT NULL REFERENCES tenant_secret_key(brand_id),
    name text NOT NULL CHECK (name ~ '^[a-z][a-z0-9_]{0,63}$'),
    ciphertext bytea NOT NULL CHECK (octet_length(ciphertext)>28),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (brand_id,name)
);

CREATE TABLE workflow_run (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id uuid NOT NULL REFERENCES brand(id),
    request_key uuid NOT NULL,
    kind text NOT NULL DEFAULT 'checkpoint' CHECK (kind='checkpoint'),
    state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','waiting','completed')),
    delay_seconds integer NOT NULL CHECK (delay_seconds BETWEEN 0 AND 300),
    trace_id text NOT NULL CHECK (trace_id ~ '^[a-f0-9]{32}$'),
    ready_at timestamptz NOT NULL DEFAULT now(),
    result_key text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (brand_id,request_key),
    UNIQUE (brand_id,id),
    CHECK ((state='completed') = (completed_at IS NOT NULL AND result_key IS NOT NULL)),
    CHECK (state='completed' OR (completed_at IS NULL AND result_key IS NULL))
);
CREATE INDEX workflow_pending ON workflow_run(ready_at,id) WHERE state<>'completed';

CREATE TABLE workflow_checkpoint (
    brand_id uuid NOT NULL,
    workflow_id uuid NOT NULL,
    step integer NOT NULL CHECK (step IN (1,2)),
    committed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workflow_id,step),
    FOREIGN KEY (brand_id,workflow_id) REFERENCES workflow_run(brand_id,id)
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['tenant_secret_key','tenant_secret','workflow_run',
                             'workflow_checkpoint'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I
            USING (brand_id=ANY(current_brand_ids()))
            WITH CHECK (brand_id=ANY(current_brand_ids()))', t);
    END LOOP;
END;
$$;

-- Scheduling needs identities only; payload reads and writes still go through forced RLS.
CREATE FUNCTION runnable_workflows() RETURNS TABLE(id uuid,brand_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
    SELECT id,brand_id FROM workflow_run
    WHERE state<>'completed' AND ready_at<=now() ORDER BY ready_at,id LIMIT 32;
$$;
REVOKE ALL ON FUNCTION runnable_workflows() FROM PUBLIC;
