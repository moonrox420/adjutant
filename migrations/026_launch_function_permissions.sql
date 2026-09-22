SET search_path=adjutant,public;

REVOKE ALL ON FUNCTION verify_launch_approver() FROM PUBLIC;
REVOKE ALL ON FUNCTION void_pending_launch_authorizations() FROM PUBLIC;
