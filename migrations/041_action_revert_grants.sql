SET search_path=adjutant,public;

GRANT UPDATE(reverted_by_action_id) ON adjutant.action TO adjutant_app;
GRANT UPDATE(reverted_by_action_id) ON adjutant.action TO adjutant_gateway;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='adjutant' AND c.relname='campaign_object'
    ) THEN
        GRANT UPDATE(state, intended_state) ON adjutant.campaign_object TO adjutant_app;
    END IF;
END $$;
