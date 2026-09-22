SET search_path=adjutant,public;

-- Preserve saved agency branding during a temporary conversion to business.
ALTER TABLE account DROP CONSTRAINT white_label_agency_only;
CREATE TRIGGER immutable_account_type_history BEFORE UPDATE OR DELETE ON account_type_change
FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();

CREATE FUNCTION convert_account_type(target uuid, expected account_type, requested account_type)
RETURNS account LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
DECLARE current_account account;
BEGIN
    PERFORM 1 FROM seat WHERE account_id=target AND user_id=current_actor_id()
        AND brand_id IS NULL AND role IN ('owner','admin')
        AND accepted_at IS NOT NULL AND revoked_at IS NULL FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Account administrator authority is required' USING ERRCODE='42501';
    END IF;
    SELECT * INTO STRICT current_account FROM account WHERE id=target FOR UPDATE;
    IF current_account.account_type=requested THEN RETURN current_account; END IF;
    IF current_account.account_type<>expected THEN
        RAISE EXCEPTION 'Account type changed since review' USING ERRCODE='23514';
    END IF;
    INSERT INTO account_type_change(account_id,from_type,to_type,changed_by)
        VALUES(target,current_account.account_type,requested,current_actor_id());
    UPDATE account SET account_type=requested WHERE id=target RETURNING * INTO current_account;
    RETURN current_account;
END;
$$;
REVOKE ALL ON FUNCTION convert_account_type(uuid,account_type,account_type) FROM PUBLIC;
