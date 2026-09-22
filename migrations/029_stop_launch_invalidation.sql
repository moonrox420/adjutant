SET search_path=adjutant,public;

CREATE FUNCTION void_launch_authority_on_stop() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
BEGIN
    IF NEW.released_at IS NULL THEN
        INSERT INTO launch_authorization_void(authorization_id,brand_id,reason)
        SELECT a.id,a.brand_id,'kill_switch' FROM launch_authorization a
        WHERE a.brand_id=NEW.brand_id
          AND NOT EXISTS(SELECT 1 FROM channel_launch_grant g WHERE g.authorization_id=a.id)
        ON CONFLICT(authorization_id) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION void_launch_authority_on_stop() FROM PUBLIC;
CREATE TRIGGER stop_voids_unconsumed_launch_authority
AFTER INSERT OR UPDATE ON brand_kill_switch
FOR EACH ROW EXECUTE FUNCTION void_launch_authority_on_stop();
