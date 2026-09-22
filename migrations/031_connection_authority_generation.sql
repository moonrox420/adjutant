SET search_path=adjutant,public;

ALTER TABLE channel_connection ADD COLUMN authorization_generation integer NOT NULL DEFAULT 1
    CHECK (authorization_generation>0);
ALTER TABLE channel_launch_grant ADD COLUMN authorization_generation integer NOT NULL DEFAULT 1
    CHECK (authorization_generation>0);
ALTER TABLE channel_launch_grant DROP CONSTRAINT channel_launch_grant_pkey;
ALTER TABLE channel_launch_grant ADD PRIMARY KEY (brand_id,connection_id,authorization_generation);

CREATE FUNCTION advance_connection_authority_generation() RETURNS trigger
LANGUAGE plpgsql SET search_path=adjutant,pg_temp AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        NEW.authorization_generation:=1;
    ELSE
        NEW.authorization_generation:=OLD.authorization_generation;
        IF (NEW.health='revoked' AND OLD.health<>'revoked')
           OR NEW.external_ad_account_id IS DISTINCT FROM OLD.external_ad_account_id THEN
            NEW.authorization_generation:=OLD.authorization_generation+1;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION advance_connection_authority_generation() FROM PUBLIC;
CREATE TRIGGER preserve_connection_authority_generation BEFORE INSERT OR UPDATE ON channel_connection
FOR EACH ROW EXECUTE FUNCTION advance_connection_authority_generation();

CREATE FUNCTION void_revoked_connection_authority() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
BEGIN
    INSERT INTO launch_authorization_void(authorization_id,brand_id,reason)
    SELECT a.id,a.brand_id,'connection_authorization_revoked'
    FROM launch_authorization a WHERE a.brand_id=NEW.brand_id
      AND a.claims->'connections' ? NEW.id::text
      AND NOT EXISTS(SELECT 1 FROM channel_launch_grant g WHERE g.authorization_id=a.id)
    ON CONFLICT(authorization_id) DO NOTHING;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION void_revoked_connection_authority() FROM PUBLIC;
CREATE TRIGGER revoke_pending_connection_launch AFTER UPDATE ON channel_connection
FOR EACH ROW WHEN (NEW.authorization_generation<>OLD.authorization_generation)
EXECUTE FUNCTION void_revoked_connection_authority();

CREATE FUNCTION verify_connection_authority_generation() RETURNS trigger
LANGUAGE plpgsql SET search_path=adjutant,pg_temp AS $$
DECLARE current_generation integer; signed_generation integer;
BEGIN
    SELECT authorization_generation INTO STRICT current_generation FROM channel_connection
        WHERE id=NEW.connection_id AND brand_id=NEW.brand_id;
    SELECT COALESCE((claims->'connection_generations'->>NEW.connection_id::text)::integer,1)
        INTO STRICT signed_generation FROM launch_authorization
        WHERE id=NEW.authorization_id AND brand_id=NEW.brand_id;
    IF NEW.authorization_generation<>current_generation OR signed_generation<>current_generation THEN
        RAISE EXCEPTION 'The connection launch authority was revoked' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION verify_connection_authority_generation() FROM PUBLIC;
CREATE TRIGGER enforce_connection_authority_generation BEFORE INSERT ON channel_launch_grant
FOR EACH ROW EXECUTE FUNCTION verify_connection_authority_generation();
