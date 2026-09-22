SET search_path=adjutant,public;
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'campaign_build';
