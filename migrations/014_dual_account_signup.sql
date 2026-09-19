SET search_path=adjutant,public;

CREATE FUNCTION register_account(p_email text,p_name text,p_workspace text,p_password text,
                                p_hash text,p_body text,p_type account_type) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
DECLARE u uuid; a uuid;
BEGIN
    IF p_type IS NULL THEN
        RAISE EXCEPTION 'Account type is required' USING ERRCODE='23514';
    END IF;
    INSERT INTO app_user(email,full_name) VALUES(p_email,p_name)
        ON CONFLICT(email) DO NOTHING RETURNING id INTO u;
    IF u IS NULL THEN RETURN; END IF;
    INSERT INTO account(account_type,display_name) VALUES(p_type,p_workspace) RETURNING id INTO a;
    INSERT INTO seat(account_id,user_id,role,accepted_at,approval_daily_usd_cap,approval_total_usd_cap)
        VALUES(a,u,'owner',now(),1000,30000);
    INSERT INTO local_credential VALUES(u,p_password);
    INSERT INTO account_token(token_hash,user_id,purpose,expires_at)
        VALUES(p_hash,u,'verify',now()+interval '1 hour');
    INSERT INTO mail_outbox(recipient,subject,body) VALUES(p_email,'Verify your Adjutant account',p_body);
END;
$$;
REVOKE ALL ON FUNCTION register_account(text,text,text,text,text,text,account_type) FROM PUBLIC;

-- Preserve six-argument callers during an expand-first rolling upgrade.
CREATE OR REPLACE FUNCTION register_account(p_email text,p_name text,p_workspace text,p_password text,
                                           p_hash text,p_body text) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
    SELECT register_account(p_email,p_name,p_workspace,p_password,p_hash,p_body,'business'::account_type);
$$;
