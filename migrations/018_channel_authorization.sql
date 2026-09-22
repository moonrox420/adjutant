SET search_path=adjutant,public;

ALTER TABLE channel_connection ALTER COLUMN health SET DEFAULT 'degraded';
ALTER TABLE channel_connection ADD COLUMN verified_at timestamptz;
ALTER TABLE channel_connection ADD COLUMN selected boolean NOT NULL DEFAULT false;
ALTER TABLE channel_connection ADD COLUMN provider_metadata jsonb NOT NULL DEFAULT '{}';
CREATE UNIQUE INDEX selected_channel_account ON channel_connection(brand_id,channel) WHERE selected;

CREATE TABLE channel_authorization (
    brand_id uuid NOT NULL REFERENCES brand(id),
    channel channel NOT NULL,
    authorized_at timestamptz,
    discovered_at timestamptz,
    accounts jsonb NOT NULL DEFAULT '[]',
    last_error text,
    disconnected_at timestamptz,
    PRIMARY KEY (brand_id,channel)
);

CREATE TABLE channel_oauth_state (
    state_hash text PRIMARY KEY CHECK (state_hash ~ '^[a-f0-9]{64}$'),
    brand_id uuid NOT NULL REFERENCES brand(id),
    channel channel NOT NULL,
    actor_id uuid NOT NULL REFERENCES app_user(id),
    expires_at timestamptz NOT NULL DEFAULT now()+interval '10 minutes',
    consumed_at timestamptz
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['channel_authorization','channel_oauth_state'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I
            USING (brand_id=ANY(current_brand_ids()))
            WITH CHECK (brand_id=ANY(current_brand_ids()))',t);
    END LOOP;
END;
$$;
