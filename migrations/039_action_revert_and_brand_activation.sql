SET search_path=adjutant,public;

-- S6.5: Brand activation status and transition timestamp
ALTER TABLE brand ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'draft';
ALTER TABLE brand ADD COLUMN IF NOT EXISTS activated_at timestamptz;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'brand_status_valid'
    ) THEN
        ALTER TABLE brand ADD CONSTRAINT brand_status_valid
            CHECK (status IN ('draft', 'active', 'paused', 'archived'));
    END IF;
END $$;

-- S6.2: Action types for brand activation and state reversion
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'brand_activate';
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'action_revert';

-- S6.2 & S6.4: Enforce append-only action ledger while permitting atomic reverted_by_action_id pointer
CREATE OR REPLACE FUNCTION forbid_action_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Table %.% is append-only; DELETE is not permitted', TG_TABLE_SCHEMA, TG_TABLE_NAME;
    ELSIF TG_OP = 'UPDATE' THEN
        IF OLD.reverted_by_action_id IS NULL AND NEW.reverted_by_action_id IS NOT NULL
           AND NEW.id = OLD.id AND NEW.brand_id = OLD.brand_id AND NEW.actor_kind = OLD.actor_kind
           AND NEW.action_type = OLD.action_type AND NEW.target_kind = OLD.target_kind
           AND NEW.diff = OLD.diff AND NEW.revert_path = OLD.revert_path THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'Table %.% is append-only; UPDATE is not permitted', TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_action_append_only ON action;
CREATE TRIGGER trg_action_append_only
    BEFORE UPDATE OR DELETE ON action
    FOR EACH ROW EXECUTE FUNCTION forbid_action_mutation();
