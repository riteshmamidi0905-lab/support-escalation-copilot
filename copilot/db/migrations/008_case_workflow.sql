-- 008: durable case workflow (M4). One row per case holds the CURRENT STATE and the case file; every transition is also an append-only row.
-- The allowed transitions are data (workflow_edges) enforced by a trigger, so a forged or buggy state change fails even if application code is bypassed.
CREATE TABLE copilot.workflow_edges (
  src text NOT NULL,
  dst text NOT NULL,
  PRIMARY KEY (src, dst)
);
INSERT INTO copilot.workflow_edges (src, dst) VALUES
  ('NEW','INTAKE'),('INTAKE','SCOPE'),('SCOPE','RETRIEVE'),('RETRIEVE','VERIFY'),('VERIFY','DIAGNOSE'),('DIAGNOSE','PLAN'),
  ('PLAN','REVIEW'),('PLAN','DRAFT'),('REVIEW','EXECUTE'),('REVIEW','DRAFT'),('EXECUTE','DRAFT'),
  ('DRAFT','CLOSED'),('DRAFT','REFUSED'),('DRAFT','ABSTAINED'),('DRAFT','ESCALATED'),('DRAFT','HANDED_OFF'),
  ('INTAKE','FAILED'),('SCOPE','FAILED'),('RETRIEVE','FAILED'),('VERIFY','FAILED'),('DIAGNOSE','FAILED'),('PLAN','FAILED'),('REVIEW','FAILED'),('EXECUTE','FAILED'),('DRAFT','FAILED'),
  ('DIAGNOSE','DRAFT');                      -- degraded path: no usable model output, retrieval-only case file
CREATE TABLE copilot.case_runs (
  case_id text PRIMARY KEY,
  account_id text NOT NULL,
  ticket_id text NOT NULL,
  state text NOT NULL DEFAULT 'NEW',
  version integer NOT NULL DEFAULT 0,
  outcome text,
  disposition text,
  file jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  FOREIGN KEY (case_id, account_id) REFERENCES copilot.cases(case_id, account_id),
  CHECK (outcome IS NULL OR outcome IN ('ANSWER','APPROVAL','REFUSE','ESCALATE','INSUFFICIENT_EVIDENCE','CLARIFY','DEGRADED'))
);
CREATE TABLE copilot.case_transitions (
  seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  case_id text NOT NULL,
  from_state text NOT NULL,
  to_state text NOT NULL,
  version integer NOT NULL,
  inputs_sha256 text NOT NULL,
  ts text NOT NULL,
  UNIQUE (case_id, version)
);
CREATE FUNCTION copilot_private.case_runs_guard() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, copilot AS $$
BEGIN
  IF (NEW.case_id, NEW.account_id, NEW.ticket_id, NEW.created_at) IS DISTINCT FROM (OLD.case_id, OLD.account_id, OLD.ticket_id, OLD.created_at) THEN
    RAISE EXCEPTION 'case identity cannot change' USING ERRCODE = 'check_violation'; END IF;
  IF NEW.state IS DISTINCT FROM OLD.state THEN
    IF NOT EXISTS (SELECT 1 FROM copilot.workflow_edges e WHERE e.src = OLD.state AND e.dst = NEW.state) THEN
      RAISE EXCEPTION 'illegal workflow transition % -> %', OLD.state, NEW.state USING ERRCODE = 'check_violation'; END IF;
    IF NEW.version <> OLD.version + 1 THEN RAISE EXCEPTION 'a transition must advance the version by exactly one' USING ERRCODE = 'check_violation'; END IF;
  ELSIF NEW.version <> OLD.version THEN
    RAISE EXCEPTION 'version only changes with a transition' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER case_runs_guard BEFORE UPDATE ON copilot.case_runs FOR EACH ROW EXECUTE FUNCTION copilot_private.case_runs_guard();
CREATE TRIGGER case_runs_no_delete BEFORE DELETE ON copilot.case_runs FOR EACH ROW EXECUTE FUNCTION copilot_private.deny_change();
CREATE TRIGGER transitions_no_change BEFORE UPDATE OR DELETE ON copilot.case_transitions FOR EACH ROW EXECUTE FUNCTION copilot_private.deny_change();
CREATE TRIGGER transitions_no_truncate BEFORE TRUNCATE ON copilot.case_transitions FOR EACH STATEMENT EXECUTE FUNCTION copilot_private.deny_change();

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['workflow_edges','case_runs','case_transitions'] LOOP
    EXECUTE format('ALTER TABLE copilot.%I OWNER TO copilot_owner', t);
    EXECUTE format('REVOKE ALL ON copilot.%I FROM PUBLIC', t);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['case_runs','case_transitions'] LOOP
    EXECUTE format('ALTER TABLE copilot.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE copilot.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY control_all ON copilot.%I FOR ALL TO copilot_control USING (true) WITH CHECK (true)', t);
  END LOOP;
END $$;
ALTER FUNCTION copilot_private.case_runs_guard() OWNER TO copilot_owner;
REVOKE ALL ON FUNCTION copilot_private.case_runs_guard() FROM PUBLIC;
GRANT SELECT ON copilot.workflow_edges TO copilot_control;
GRANT SELECT, INSERT, UPDATE ON copilot.case_runs TO copilot_control;
GRANT SELECT, INSERT ON copilot.case_transitions TO copilot_control;
GRANT TRUNCATE ON copilot.case_runs TO copilot_loader;
