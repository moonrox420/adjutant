SET search_path=adjutant,public;
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'brand_create';
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'brand_graph_edit';
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'plan_edit';
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'plan_submit';
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'approval_reject';
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'approval_request_changes';
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'ceiling_change';

-- Direct partition reads need their own policies, even when parent reads are safe.
DO $$
DECLARE partition_name text;
BEGIN
    FOR partition_name IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                          WHERE n.nspname='adjutant' AND c.relispartition AND c.relkind='r' LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',partition_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',partition_name);
        EXECUTE format('CREATE POLICY partition_tenant ON %I USING(brand_id=ANY(current_brand_ids()))
                        WITH CHECK(brand_id=ANY(current_brand_ids()))',partition_name);
    END LOOP;
END $$;
