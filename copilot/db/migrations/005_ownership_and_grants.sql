-- 005: ownership and least-privilege grants. Tables are owned by copilot_owner, which nobody logs in as. The app role owns nothing and cannot do DDL.
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT schemaname, tablename FROM pg_tables WHERE schemaname IN ('copilot','copilot_private') LOOP
    EXECUTE format('ALTER TABLE %I.%I OWNER TO copilot_owner', r.schemaname, r.tablename);
  END LOOP;
  FOR r IN SELECT sequence_schema AS schemaname, sequence_name AS tablename FROM information_schema.sequences WHERE sequence_schema = 'copilot' LOOP
    EXECUTE format('ALTER SEQUENCE %I.%I OWNER TO copilot_owner', r.schemaname, r.tablename);
  END LOOP;
  FOR r IN SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid) AS args FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'copilot_private' LOOP
    EXECUTE format('ALTER FUNCTION %I.%I(%s) OWNER TO copilot_owner', r.nspname, r.proname, r.args);
  END LOOP;
END $$;
ALTER SCHEMA copilot OWNER TO copilot_owner;
ALTER SCHEMA copilot_private OWNER TO copilot_owner;

REVOKE ALL ON SCHEMA copilot, copilot_private FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA copilot, copilot_private FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA copilot_private FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- application (model-facing tools): read-only, one executable function, nothing else
GRANT USAGE ON SCHEMA copilot, copilot_private TO copilot_app;
GRANT SELECT ON copilot.accounts, copilot.account_contacts, copilot.contracts, copilot.integrations, copilot.tickets, copilot.ticket_history,
      copilot.incident_accounts, copilot.cases, copilot.incidents, copilot.deployments, copilot.release_notes, copilot.runbook_docs TO copilot_app;
GRANT EXECUTE ON FUNCTION copilot_private.scope_account() TO copilot_app;
ALTER ROLE copilot_app SET search_path = copilot, pg_catalog;
ALTER ROLE copilot_app SET default_transaction_read_only = on;
ALTER ROLE copilot_app SET statement_timeout = '10s';

-- loader: inserts and truncates the data tables (test/demo data loading only); reads nothing
GRANT USAGE ON SCHEMA copilot TO copilot_loader;
GRANT TRUNCATE ON copilot.cases TO copilot_loader;   -- needed because cases reference tickets
GRANT INSERT, TRUNCATE ON copilot.accounts, copilot.account_contacts, copilot.contracts, copilot.integrations, copilot.tickets, copilot.ticket_history,
      copilot.incident_accounts, copilot.incidents, copilot.deployments, copilot.release_notes, copilot.runbook_docs TO copilot_loader;
ALTER ROLE copilot_loader SET search_path = copilot, pg_catalog;

-- intake: the trusted case service. May learn a ticket's account and create cases. Nothing else.
GRANT USAGE ON SCHEMA copilot TO copilot_intake;
GRANT SELECT ON copilot.tickets TO copilot_intake;
GRANT SELECT, INSERT ON copilot.cases TO copilot_intake;
GRANT USAGE ON SEQUENCE copilot.case_seq TO copilot_intake;
ALTER ROLE copilot_intake SET search_path = copilot, pg_catalog;
