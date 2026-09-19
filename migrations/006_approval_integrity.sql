SET search_path=adjutant,public;

CREATE FUNCTION immutable_token_claims() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW)-ARRAY['voided_at','voided_reason'])
          IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['voided_at','voided_reason'])
       OR (OLD.voided_at IS NOT NULL AND NEW.voided_at IS DISTINCT FROM OLD.voided_at)
    THEN RAISE EXCEPTION 'Signed approval claims are immutable' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER token_claims_immutable BEFORE UPDATE ON approval_token
FOR EACH ROW EXECUTE FUNCTION immutable_token_claims();

CREATE FUNCTION require_new_plan_hash() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.name,NEW.objective,NEW.goal_kind,NEW.goal_value,NEW.plan_document,
           NEW.monthly_budget_usd,NEW.starts_on,NEW.ends_on,NEW.rationale)
       IS DISTINCT FROM
       ROW(OLD.name,OLD.objective,OLD.goal_kind,OLD.goal_value,OLD.plan_document,
           OLD.monthly_budget_usd,OLD.starts_on,OLD.ends_on,OLD.rationale)
       AND NEW.plan_hash=OLD.plan_hash
    THEN RAISE EXCEPTION 'Plan changes require a new subject hash' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER plan_hash_required BEFORE UPDATE ON plan
FOR EACH ROW EXECUTE FUNCTION require_new_plan_hash();

-- A spend ledger entry needs a live same-brand token, not merely any foreign key.
CREATE FUNCTION validate_spend_action() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t approval_token%ROWTYPE;
BEGIN
    IF NEW.action_type IN ('deploy','budget_set','bid_set','targeting_change','creative_swap','resume') THEN
        SELECT * INTO t FROM approval_token WHERE id=NEW.token_id;
        IF NOT FOUND OR t.brand_id<>NEW.brand_id OR t.voided_at IS NOT NULL
           OR t.expires_at<=now() OR (NEW.usd_impact IS NOT NULL AND NEW.usd_impact>t.usd_total_cap)
        THEN RAISE EXCEPTION 'Spend action requires current same-brand authority'
             USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER action_authority_required BEFORE INSERT ON action
FOR EACH ROW EXECUTE FUNCTION validate_spend_action();
