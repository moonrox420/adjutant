SET search_path=adjutant,public;

ALTER TABLE campaign_build
    ADD COLUMN approval_token_id uuid REFERENCES approval_token(id) ON DELETE RESTRICT,
    ADD COLUMN approval_reservation_id uuid REFERENCES approval_token_consumption(id) ON DELETE RESTRICT,
    ADD COLUMN authority_reserved_at timestamptz;

ALTER TABLE campaign_build DROP CONSTRAINT IF EXISTS campaign_build_state_check;
ALTER TABLE campaign_build
    ADD CONSTRAINT campaign_build_state_check
    CHECK(state IN ('authorizing','queued','running','paused','failed','cancelled'));

DROP INDEX IF EXISTS active_campaign_build;
CREATE UNIQUE INDEX active_campaign_build ON campaign_build(plan_id,connection_id)
    WHERE state IN ('authorizing','queued','running','paused');
