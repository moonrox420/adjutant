SET search_path=adjutant,public;

CREATE FUNCTION version_guardrail() RETURNS trigger LANGUAGE plpgsql
SET search_path=adjutant,pg_temp AS $$
BEGIN
    IF NEW.brand_id<>OLD.brand_id THEN
        RAISE EXCEPTION 'Guardrails cannot change tenant' USING ERRCODE='23514';
    END IF;
    NEW.version=OLD.version+1;
    NEW.updated_at=now();
    IF EXISTS(SELECT 1 FROM unnest(NEW.blocked_claims) phrase
              WHERE phrase IS NULL OR length(trim(phrase))=0 OR length(phrase)>500)
    THEN RAISE EXCEPTION 'Blocked claims must be nonempty phrases' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guardrail_revision BEFORE UPDATE ON guardrail
FOR EACH ROW EXECUTE FUNCTION version_guardrail();

CREATE FUNCTION verify_launch_approver() RETURNS trigger LANGUAGE plpgsql
SET search_path=adjutant,pg_temp AS $$
BEGIN
    IF NOT EXISTS(
        SELECT 1 FROM launch_authorization a JOIN brand b ON b.id=a.brand_id
        JOIN seat s ON s.account_id=b.account_id AND (s.brand_id IS NULL OR s.brand_id=b.id)
        WHERE a.id=NEW.authorization_id AND a.brand_id=NEW.brand_id
        AND b.campaigns_enabled AND cardinality(b.restricted_flags)=0
        AND s.user_id=a.approver_id AND s.revoked_at IS NULL AND s.accepted_at IS NOT NULL
        AND s.role IN ('owner','admin','client_approver')
        AND s.approval_daily_usd_cap>=(SELECT sum(daily_budget_usd) FROM plan_allocation WHERE plan_id=a.plan_id)
        AND s.approval_total_usd_cap>=(SELECT sum(monthly_budget_usd) FROM plan_allocation WHERE plan_id=a.plan_id)
    ) THEN RAISE EXCEPTION 'Approver no longer has launch authority' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER enforce_launch_approver BEFORE INSERT ON channel_launch_grant
FOR EACH ROW EXECUTE FUNCTION verify_launch_approver();

CREATE FUNCTION enforce_studio_blocked_claims() RETURNS trigger LANGUAGE plpgsql
SET search_path=adjutant,pg_temp AS $$
DECLARE phrase text; content text;
BEGIN
    FOR phrase IN SELECT value FROM brand_constraint WHERE brand_id=NEW.brand_id AND is_active
        AND kind IN ('banned_word','banned_claim')
        UNION SELECT unnest(blocked_claims) FROM guardrail WHERE brand_id=NEW.brand_id
    LOOP
        phrase=lower(regexp_replace(normalize(trim(phrase),NFKC),'\s+',' ','g'));
        IF length(phrase)=0 THEN CONTINUE; END IF;
        FOR content IN SELECT value #>> '{}' FROM jsonb_path_query(
            jsonb_build_array(NEW.document,NEW.scene_graph), 'strict $.** ? (@.type() == "string")') value
        LOOP
            IF position(phrase IN lower(regexp_replace(normalize(content,NFKC),'\s+',' ','g')))>0 THEN
                RAISE EXCEPTION 'Creative contains a blocked claim' USING ERRCODE='23514';
            END IF;
        END LOOP;
    END LOOP;
    RETURN NEW;
END;
$$;
CREATE TRIGGER studio_claim_guardrail BEFORE INSERT OR UPDATE OF document,scene_graph ON studio_draft
FOR EACH ROW EXECUTE FUNCTION enforce_studio_blocked_claims();
