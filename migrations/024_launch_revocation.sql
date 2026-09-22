SET search_path=adjutant,public;

-- This trigger reads only seats belonging to the signed approval's brand/account.
ALTER FUNCTION verify_launch_approver() SECURITY DEFINER;

CREATE TABLE launch_authorization_void (
    authorization_id uuid PRIMARY KEY REFERENCES launch_authorization(id),
    brand_id uuid NOT NULL REFERENCES brand(id),
    voided_at timestamptz NOT NULL DEFAULT now(),
    reason text NOT NULL
);
ALTER TABLE launch_authorization_void ENABLE ROW LEVEL SECURITY;
ALTER TABLE launch_authorization_void FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON launch_authorization_void
USING (brand_id=ANY(current_brand_ids())) WITH CHECK (brand_id=ANY(current_brand_ids()));
CREATE TRIGGER immutable_launch_void BEFORE UPDATE OR DELETE ON launch_authorization_void
FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();

CREATE FUNCTION void_pending_launch_authorizations() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
BEGIN
    INSERT INTO launch_authorization_void(authorization_id,brand_id,reason)
    SELECT a.id,a.brand_id,TG_TABLE_NAME || '_changed' FROM launch_authorization a
    WHERE a.brand_id=NEW.brand_id AND a.plan_id=NEW.id
      AND NOT EXISTS(SELECT 1 FROM channel_launch_grant g WHERE g.authorization_id=a.id)
    ON CONFLICT(authorization_id) DO NOTHING;
    RETURN NEW;
END;
$$;
CREATE TRIGGER invalidate_first_launch_on_plan_edit AFTER UPDATE OF plan_hash ON plan
FOR EACH ROW WHEN (OLD.plan_hash IS DISTINCT FROM NEW.plan_hash)
EXECUTE FUNCTION void_pending_launch_authorizations();

CREATE FUNCTION refuse_void_launch_grant() RETURNS trigger LANGUAGE plpgsql
SET search_path=adjutant,pg_temp AS $$
BEGIN
    IF EXISTS(SELECT 1 FROM launch_authorization_void WHERE authorization_id=NEW.authorization_id) THEN
        RAISE EXCEPTION 'Launch authorization was voided' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER enforce_launch_not_void BEFORE INSERT ON channel_launch_grant
FOR EACH ROW EXECUTE FUNCTION refuse_void_launch_grant();
