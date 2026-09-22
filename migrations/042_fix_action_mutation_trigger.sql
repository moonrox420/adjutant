SET search_path=adjutant,public;

-- S6.2 & S6.4: Enforce append-only action ledger with NULL-safe comparison for atomic revert pointer
CREATE OR REPLACE FUNCTION forbid_action_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Table %.% is append-only; DELETE is not permitted', TG_TABLE_SCHEMA, TG_TABLE_NAME;
    ELSIF TG_OP = 'UPDATE' THEN
        IF OLD.reverted_by_action_id IS NULL AND NEW.reverted_by_action_id IS NOT NULL
           AND NEW.id = OLD.id
           AND NEW.brand_id = OLD.brand_id
           AND NEW.actor_kind = OLD.actor_kind
           AND NEW.actor_user_id IS NOT DISTINCT FROM OLD.actor_user_id
           AND NEW.actor_agent_name IS NOT DISTINCT FROM OLD.actor_agent_name
           AND NEW.action_type = OLD.action_type
           AND NEW.target_kind = OLD.target_kind
           AND NEW.target_id IS NOT DISTINCT FROM OLD.target_id
           AND NEW.target_native_id IS NOT DISTINCT FROM OLD.target_native_id
           AND NEW.channel IS NOT DISTINCT FROM OLD.channel
           AND NEW.diff IS NOT DISTINCT FROM OLD.diff
           AND NEW.rationale IS NOT DISTINCT FROM OLD.rationale
           AND NEW.token_id IS NOT DISTINCT FROM OLD.token_id
           AND NEW.revert_path IS NOT DISTINCT FROM OLD.revert_path
           AND NEW.channel_request_id IS NOT DISTINCT FROM OLD.channel_request_id
           AND NEW.channel_response_id IS NOT DISTINCT FROM OLD.channel_response_id
           AND NEW.usd_impact IS NOT DISTINCT FROM OLD.usd_impact
           AND NEW.executed_at IS NOT DISTINCT FROM OLD.executed_at THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'Table %.% is append-only; UPDATE is not permitted', TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END;
$$;
