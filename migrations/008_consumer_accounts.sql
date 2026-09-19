SET search_path=adjutant,public;

ALTER TABLE app_user ADD COLUMN email_verified_at timestamptz;
UPDATE app_user SET email_verified_at=created_at;
CREATE TABLE account_token (
    token_hash text PRIMARY KEY, user_id uuid NOT NULL REFERENCES app_user(id),
    purpose text NOT NULL CHECK(purpose IN ('verify','reset')),
    expires_at timestamptz NOT NULL, used_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX account_token_user ON account_token(user_id,purpose);
CREATE TABLE mail_outbox (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), recipient text NOT NULL,
    subject text NOT NULL, body text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz, attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(), last_error text
);
CREATE INDEX mail_pending ON mail_outbox(next_attempt_at) WHERE delivered_at IS NULL;
CREATE TABLE consumer_receipt (
    event_id uuid PRIMARY KEY REFERENCES event_outbox(event_id),
    brand_id uuid NOT NULL REFERENCES brand(id), event_type text NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE consumer_receipt ENABLE ROW LEVEL SECURITY;
ALTER TABLE consumer_receipt FORCE ROW LEVEL SECURITY;
CREATE POLICY receipt_tenant ON consumer_receipt USING(brand_id=ANY(current_brand_ids()));
CREATE TABLE consumer_process (
    instance_id uuid PRIMARY KEY, pid integer NOT NULL, started_at timestamptz NOT NULL DEFAULT now(),
    heartbeat_at timestamptz NOT NULL DEFAULT now(), exited_at timestamptz,
    exit_code integer, exit_verified_at timestamptz
);
ALTER TABLE agent_run ADD COLUMN actor_user_id uuid REFERENCES app_user(id);
ALTER TABLE agent_run ADD COLUMN session_hash text;
ALTER TABLE agent_run ADD COLUMN cancel_requested_at timestamptz;
ALTER TABLE agent_run ADD COLUMN worker_pid integer;
ALTER TABLE agent_run ADD COLUMN worker_exit_code integer;
ALTER TABLE agent_run ADD COLUMN worker_exit_verified_at timestamptz;
ALTER TABLE agent_run ADD COLUMN worker_heartbeat_at timestamptz;

CREATE OR REPLACE FUNCTION login_identity(p_email text)
RETURNS TABLE(user_id uuid,password_hash text)
LANGUAGE sql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
    SELECT u.id,c.password_hash FROM app_user u JOIN local_credential c ON c.user_id=u.id
    WHERE u.email=p_email::public.citext AND u.is_active AND u.email_verified_at IS NOT NULL FOR UPDATE OF u;
$$;
CREATE OR REPLACE FUNCTION create_session(p_user uuid,p_hash text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
BEGIN
    PERFORM 1 FROM app_user WHERE id=p_user AND is_active AND email_verified_at IS NOT NULL
        FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Identity unavailable' USING ERRCODE='23514'; END IF;
    INSERT INTO auth_session(token_hash,user_id,expires_at)
    VALUES(p_hash,p_user,now()+interval '12 hours');
END;
$$;
CREATE FUNCTION lock_session(p_hash text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
BEGIN
    PERFORM 1 FROM auth_session s JOIN app_user u ON u.id=s.user_id
    WHERE s.token_hash=p_hash AND s.expires_at>now() AND u.is_active
      AND u.email_verified_at IS NOT NULL FOR UPDATE OF s;
    RETURN FOUND;
END;
$$;
CREATE FUNCTION register_account(p_email text,p_name text,p_workspace text,p_password text,
                                p_hash text,p_body text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
DECLARE u uuid; a uuid;
BEGIN
    INSERT INTO app_user(email,full_name) VALUES(p_email,p_name)
        ON CONFLICT(email) DO NOTHING RETURNING id INTO u;
    IF u IS NULL THEN RETURN; END IF;
    INSERT INTO account(account_type,display_name) VALUES('business',p_workspace) RETURNING id INTO a;
    INSERT INTO seat(account_id,user_id,role,accepted_at,approval_daily_usd_cap,approval_total_usd_cap)
        VALUES(a,u,'owner',now(),1000,30000);
    INSERT INTO local_credential VALUES(u,p_password);
    INSERT INTO account_token(token_hash,user_id,purpose,expires_at)
        VALUES(p_hash,u,'verify',now()+interval '1 hour');
    INSERT INTO mail_outbox(recipient,subject,body) VALUES(p_email,'Verify your Adjutant account',p_body);
END;
$$;
CREATE FUNCTION request_account_token(p_email text,p_purpose text,p_hash text,p_body text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
DECLARE u uuid;
BEGIN
    SELECT id INTO u FROM app_user WHERE email=p_email::public.citext AND is_active
        AND ((p_purpose='verify' AND email_verified_at IS NULL)
          OR (p_purpose='reset' AND email_verified_at IS NOT NULL)) FOR UPDATE;
    IF u IS NULL THEN RETURN; END IF;
    UPDATE account_token SET used_at=now() WHERE user_id=u AND purpose=p_purpose AND used_at IS NULL;
    INSERT INTO account_token(token_hash,user_id,purpose,expires_at)
        VALUES(p_hash,u,p_purpose,now()+interval '1 hour');
    INSERT INTO mail_outbox(recipient,subject,body)
        VALUES(p_email,CASE p_purpose WHEN 'verify' THEN 'Verify your Adjutant account'
                                     ELSE 'Reset your Adjutant password' END,p_body);
END;
$$;
CREATE FUNCTION consume_account_token(p_hash text,p_purpose text,p_password text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
DECLARE u uuid;
BEGIN
    SELECT user_id INTO u FROM account_token WHERE token_hash=p_hash AND purpose=p_purpose;
    IF u IS NULL THEN RETURN false; END IF;
    PERFORM 1 FROM app_user WHERE id=u AND is_active FOR UPDATE;
    IF NOT FOUND THEN RETURN false; END IF;
    UPDATE account_token SET used_at=now() WHERE token_hash=p_hash AND purpose=p_purpose
        AND used_at IS NULL AND expires_at>now();
    IF NOT FOUND THEN RETURN false; END IF;
    IF p_purpose='verify' THEN
        UPDATE app_user SET email_verified_at=now() WHERE id=u;
    ELSIF p_purpose='reset' AND p_password IS NOT NULL THEN
        UPDATE local_credential SET password_hash=p_password WHERE user_id=u;
        DELETE FROM auth_session WHERE user_id=u;
        UPDATE agent_run SET cancel_requested_at=now() WHERE actor_user_id=u AND finished_at IS NULL;
        UPDATE account_token SET used_at=now() WHERE user_id=u AND purpose='reset' AND used_at IS NULL;
    ELSE RAISE EXCEPTION 'Invalid token purpose'; END IF;
    RETURN true;
END;
$$;
CREATE OR REPLACE FUNCTION revoke_session(p_hash text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
BEGIN
    DELETE FROM auth_session WHERE token_hash=p_hash;
    UPDATE agent_run SET cancel_requested_at=now() WHERE session_hash=p_hash AND finished_at IS NULL;
END;
$$;
CREATE FUNCTION revoke_all_sessions(p_hash text) RETURNS text[]
LANGUAGE plpgsql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
DECLARE u uuid; hashes text[];
BEGIN
    SELECT user_id INTO u FROM auth_session WHERE token_hash=p_hash AND expires_at>now();
    IF u IS NULL THEN RETURN ARRAY[p_hash]; END IF;
    PERFORM 1 FROM app_user WHERE id=u FOR UPDATE;
    SELECT array_agg(token_hash) INTO hashes FROM auth_session WHERE user_id=u;
    DELETE FROM auth_session WHERE user_id=u;
    UPDATE agent_run SET cancel_requested_at=now() WHERE actor_user_id=u AND finished_at IS NULL;
    RETURN hashes;
END;
$$;
CREATE FUNCTION cancelled_session_jobs(p_hash text)
RETURNS TABLE(id uuid,worker_pid integer,worker_exit_code integer,worker_exit_verified_at timestamptz,
              finished_at timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path=adjutant,pg_temp AS $$
    SELECT id,worker_pid,worker_exit_code,worker_exit_verified_at,finished_at
    FROM agent_run WHERE session_hash=p_hash AND cancel_requested_at IS NOT NULL;
$$;

REVOKE ALL ON account_token,mail_outbox,consumer_process FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA adjutant FROM PUBLIC;
