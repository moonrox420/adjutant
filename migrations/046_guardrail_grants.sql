SET search_path=adjutant,public;

-- S5.1 & S6.5: Grant app runtime permissions to manage guardrail caps and studio entities
GRANT SELECT, INSERT, UPDATE ON adjutant.guardrail TO adjutant_app;
GRANT SELECT, INSERT, UPDATE ON adjutant.brand_context TO adjutant_app;
GRANT SELECT, INSERT, UPDATE ON adjutant.studio_draft TO adjutant_app;
GRANT SELECT, INSERT, UPDATE ON adjutant.studio_rendition TO adjutant_app;
GRANT SELECT, INSERT, UPDATE ON adjutant.studio_plan_creative TO adjutant_app;
GRANT SELECT, INSERT, UPDATE ON adjutant.studio_job TO adjutant_app;

-- Ensure synchronize_brand_caps executes with definer privileges to prevent caller privilege escalation failures
CREATE OR REPLACE FUNCTION synchronize_brand_caps() RETURNS trigger LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=adjutant,pg_temp AS $$
BEGIN
    IF pg_trigger_depth()>1 THEN RETURN NEW; END IF;
    IF TG_TABLE_NAME='budget_ceiling' THEN
        IF NEW.scope_kind='brand' THEN
            INSERT INTO guardrail(brand_id,monthly_spend_cap_usd,daily_spend_cap_usd)
            VALUES(NEW.brand_id,NEW.monthly_usd_max,NEW.daily_usd_max)
            ON CONFLICT(brand_id) DO UPDATE SET
                monthly_spend_cap_usd=excluded.monthly_spend_cap_usd,
                daily_spend_cap_usd=excluded.daily_spend_cap_usd,
                version=guardrail.version+1,updated_at=now();
        END IF;
    ELSE
        UPDATE budget_ceiling SET monthly_usd_max=NEW.monthly_spend_cap_usd,
            daily_usd_max=NEW.daily_spend_cap_usd
        WHERE brand_id=NEW.brand_id AND scope_kind='brand';
    END IF;
    RETURN NEW;
END;
$$;
