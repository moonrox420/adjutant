SET search_path=adjutant,public;

GRANT UPDATE(status, activated_at, campaigns_enabled) ON adjutant.brand TO adjutant_gateway;
GRANT UPDATE(status, activated_at, campaigns_enabled) ON adjutant.brand TO adjutant_app;
