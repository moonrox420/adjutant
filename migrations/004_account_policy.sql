SET search_path=adjutant,public;
DROP POLICY member_accounts ON account;
CREATE POLICY member_accounts ON account FOR SELECT USING
    (EXISTS (SELECT 1 FROM seat s WHERE s.account_id=account.id AND s.revoked_at IS NULL
                                          AND s.accepted_at IS NOT NULL));
