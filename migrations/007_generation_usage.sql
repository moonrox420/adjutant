SET search_path=adjutant,public;
ALTER TABLE agent_run ADD COLUMN usage_complete boolean NOT NULL DEFAULT false;
