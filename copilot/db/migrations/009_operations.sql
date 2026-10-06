-- 009: operations (M5): recovery leases on case runs and an append-only operational event stream.
ALTER TABLE copilot.case_runs
  ADD COLUMN lease_owner text,
  ADD COLUMN lease_expires_at timestamptz,
  ADD COLUMN next_attempt_at timestamptz,
  ADD COLUMN attempts integer NOT NULL DEFAULT 0,
  ADD COLUMN last_error text;
CREATE INDEX case_runs_recovery_idx ON copilot.case_runs (next_attempt_at NULLS FIRST) WHERE state NOT IN ('CLOSED','REFUSED','ABSTAINED','ESCALATED','HANDED_OFF','FAILED');

-- Operational telemetry produced by the application itself (model calls, dependency calls, breaker changes, retrieval fallbacks, recovery claims...). Append-only; carries correlation ids;
-- attributes are scrubbed before they are written. NOT hash-chained: this is telemetry, the audit log is the accountability record.
CREATE TABLE copilot.ops_events (
  seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ts timestamptz NOT NULL,
  kind text NOT NULL,
  case_id text,
  account_id text,
  run_id text,
  request_id text,
  invocation_id text,
  action_id text,
  approval_id text,
  duration_ms double precision,
  attrs jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX ops_events_kind_idx ON copilot.ops_events(kind, seq);
CREATE INDEX ops_events_case_idx ON copilot.ops_events(case_id, seq);
CREATE TRIGGER ops_no_change BEFORE UPDATE OR DELETE ON copilot.ops_events FOR EACH ROW EXECUTE FUNCTION copilot_private.deny_change();
CREATE TRIGGER ops_no_truncate BEFORE TRUNCATE ON copilot.ops_events FOR EACH STATEMENT EXECUTE FUNCTION copilot_private.deny_change();
ALTER TABLE copilot.ops_events OWNER TO copilot_owner;
REVOKE ALL ON copilot.ops_events FROM PUBLIC;
ALTER TABLE copilot.ops_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE copilot.ops_events FORCE ROW LEVEL SECURITY;
CREATE POLICY control_all ON copilot.ops_events FOR ALL TO copilot_control USING (true) WITH CHECK (true);
GRANT SELECT, INSERT ON copilot.ops_events TO copilot_control;
