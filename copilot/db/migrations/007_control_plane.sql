-- 007: control plane (M3): approvals, idempotency ledger, append-only audit log, internal case artifacts.
-- Written ONLY by the trusted control service role (copilot_control). The model-facing application role has NO privilege on any of these tables: a model
-- (or anything holding only the app role) cannot read, create, change or approve anything here.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'copilot_control') THEN CREATE ROLE copilot_control NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE; END IF;
END $$;
ALTER TABLE copilot.cases ADD CONSTRAINT cases_case_account_uniq UNIQUE (case_id, account_id);

CREATE TABLE copilot.approvals (
  approval_id text PRIMARY KEY,
  case_id text NOT NULL,
  account_id text NOT NULL,
  action_id text NOT NULL,
  action_type text NOT NULL,
  action_hash text NOT NULL CHECK (action_hash ~ '^[0-9a-f]{64}$'),
  action_canonical text NOT NULL,              -- the exact canonical bytes that were hashed (and are executed): execution never re-reads model text
  required_role text NOT NULL,
  requester_kind text NOT NULL CHECK (requester_kind = 'agent'),
  requester_id text NOT NULL,
  evidence jsonb NOT NULL,                     -- what the approver was shown (redacted)
  created_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  status text NOT NULL CHECK (status IN ('pending','approved','denied','expired')),
  approver_id text,
  approver_role text,
  decision_reason text,
  decided_at timestamptz,
  FOREIGN KEY (case_id, account_id) REFERENCES copilot.cases(case_id, account_id),
  CHECK (expires_at > created_at),
  CHECK ((status = 'pending') = (decided_at IS NULL)),
  CHECK ((status IN ('approved','denied')) = (approver_id IS NOT NULL)),
  CHECK (approver_id IS NULL OR approver_id <> requester_id),                  -- nobody approves their own request
  CHECK (status <> 'approved' OR approver_role = required_role)                  -- exact role, no hierarchy
);
CREATE INDEX approvals_case_idx ON copilot.approvals(case_id);
CREATE UNIQUE INDEX approvals_one_open ON copilot.approvals(account_id, action_hash) WHERE status = 'pending';   -- re-requesting the same action returns the open request

CREATE TABLE copilot.idempotency_records (
  account_id text NOT NULL,
  idempotency_key text NOT NULL,
  case_id text NOT NULL,
  action_type text NOT NULL,
  action_hash text NOT NULL CHECK (action_hash ~ '^[0-9a-f]{64}$'),
  approval_id text REFERENCES copilot.approvals(approval_id),
  status text NOT NULL CHECK (status IN ('in_progress','succeeded','failed_transient','failed_permanent','uncertain')),
  attempts integer NOT NULL DEFAULT 1,
  result jsonb,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  lease_expires_at timestamptz,
  PRIMARY KEY (account_id, idempotency_key),                                       -- per tenant: one tenant can never learn that another used a key
  FOREIGN KEY (case_id, account_id) REFERENCES copilot.cases(case_id, account_id)
);

CREATE TABLE copilot.case_artifacts (            -- internal drafts and notes. There is no 'sent' state and no column that could mean transmission (invariant I3).
  artifact_id text PRIMARY KEY,
  case_id text NOT NULL,
  account_id text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('draft_reply','internal_note')),
  status text NOT NULL DEFAULT 'draft' CHECK (status = 'draft'),
  body text NOT NULL,
  body_sha256 text NOT NULL,
  action_hash text NOT NULL,
  idempotency_key text NOT NULL,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL,
  UNIQUE (account_id, idempotency_key),
  FOREIGN KEY (case_id, account_id) REFERENCES copilot.cases(case_id, account_id)
);

CREATE TABLE copilot.audit_events (
  seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  event_id text NOT NULL UNIQUE,
  case_id text NOT NULL,
  account_id text,
  event_type text NOT NULL,
  ts text NOT NULL,
  body text NOT NULL,                               -- the exact canonical JSON that was hashed
  prev_hash text,
  hash text NOT NULL UNIQUE CHECK (hash ~ '^[0-9a-f]{64}$')
);
CREATE INDEX audit_case_idx ON copilot.audit_events(case_id, seq);

-- Append-only: no UPDATE/DELETE/TRUNCATE for anyone through these triggers, and the control role is not even granted them. A database owner or superuser can still
-- disable a trigger or rewrite rows; the hash chain exists so that such tampering is DETECTABLE. This is tamper-evidence, not immutability.
CREATE FUNCTION copilot_private.deny_change() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN RAISE EXCEPTION '% on % is not allowed (append-only)', TG_OP, TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege'; END $$;
CREATE TRIGGER audit_no_update BEFORE UPDATE OR DELETE ON copilot.audit_events FOR EACH ROW EXECUTE FUNCTION copilot_private.deny_change();
CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON copilot.audit_events FOR EACH STATEMENT EXECUTE FUNCTION copilot_private.deny_change();
CREATE TRIGGER artifacts_no_update BEFORE UPDATE OR DELETE ON copilot.case_artifacts FOR EACH ROW EXECUTE FUNCTION copilot_private.deny_change();
CREATE TRIGGER approvals_no_delete BEFORE DELETE ON copilot.approvals FOR EACH ROW EXECUTE FUNCTION copilot_private.deny_change();
CREATE TRIGGER idem_no_delete BEFORE DELETE ON copilot.idempotency_records FOR EACH ROW EXECUTE FUNCTION copilot_private.deny_change();

-- Approvals: only pending -> approved/denied/expired, and only the decision columns may change. A decided approval is final.
CREATE FUNCTION copilot_private.approvals_guard() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  IF OLD.status <> 'pending' THEN RAISE EXCEPTION 'approval % is final (%)', OLD.approval_id, OLD.status USING ERRCODE = 'check_violation'; END IF;
  IF (NEW.approval_id, NEW.case_id, NEW.account_id, NEW.action_id, NEW.action_type, NEW.action_hash, NEW.action_canonical, NEW.required_role, NEW.requester_kind, NEW.requester_id, NEW.evidence, NEW.created_at, NEW.expires_at)
     IS DISTINCT FROM (OLD.approval_id, OLD.case_id, OLD.account_id, OLD.action_id, OLD.action_type, OLD.action_hash, OLD.action_canonical, OLD.required_role, OLD.requester_kind, OLD.requester_id, OLD.evidence, OLD.created_at, OLD.expires_at)
  THEN RAISE EXCEPTION 'the action an approval is bound to cannot change' USING ERRCODE = 'check_violation'; END IF;
  IF NEW.status = 'approved' AND NEW.decided_at > OLD.expires_at THEN RAISE EXCEPTION 'approval expired before it was decided' USING ERRCODE = 'check_violation'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER approvals_guard BEFORE UPDATE ON copilot.approvals FOR EACH ROW EXECUTE FUNCTION copilot_private.approvals_guard();

-- Idempotency: the identity of an effect (key, case, action hash, type) can never be rewritten, so a key cannot be re-pointed at a different payload.
CREATE FUNCTION copilot_private.idem_guard() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  IF (NEW.account_id, NEW.idempotency_key, NEW.case_id, NEW.action_type, NEW.action_hash) IS DISTINCT FROM (OLD.account_id, OLD.idempotency_key, OLD.case_id, OLD.action_type, OLD.action_hash)
  THEN RAISE EXCEPTION 'an idempotency record cannot be re-bound to another payload' USING ERRCODE = 'check_violation'; END IF;
  IF OLD.status IN ('succeeded','failed_permanent') AND NEW.status <> OLD.status THEN RAISE EXCEPTION 'terminal idempotency record' USING ERRCODE = 'check_violation'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER idem_guard BEFORE UPDATE ON copilot.idempotency_records FOR EACH ROW EXECUTE FUNCTION copilot_private.idem_guard();

-- ownership, RLS (enabled and forced; only the control role has policies, so nothing else sees a row) and grants
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['approvals','idempotency_records','case_artifacts','audit_events'] LOOP
    EXECUTE format('ALTER TABLE copilot.%I OWNER TO copilot_owner', t);
    EXECUTE format('ALTER TABLE copilot.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE copilot.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON copilot.%I FROM PUBLIC', t);
    EXECUTE format('CREATE POLICY control_all ON copilot.%I FOR ALL TO copilot_control USING (true) WITH CHECK (true)', t);
  END LOOP;
END $$;
ALTER FUNCTION copilot_private.deny_change() OWNER TO copilot_owner;
ALTER FUNCTION copilot_private.approvals_guard() OWNER TO copilot_owner;
ALTER FUNCTION copilot_private.idem_guard() OWNER TO copilot_owner;
REVOKE ALL ON FUNCTION copilot_private.deny_change(), copilot_private.approvals_guard(), copilot_private.idem_guard() FROM PUBLIC;

GRANT USAGE ON SCHEMA copilot TO copilot_control;
GRANT SELECT, INSERT, UPDATE ON copilot.approvals, copilot.idempotency_records TO copilot_control;
GRANT SELECT, INSERT ON copilot.case_artifacts, copilot.audit_events TO copilot_control;
-- The test/demo loader resets the environment; because these tables reference cases they must be truncated in the same statement. It can NOT touch the audit log.
GRANT TRUNCATE ON copilot.approvals, copilot.idempotency_records, copilot.case_artifacts TO copilot_loader;
ALTER ROLE copilot_control SET search_path = copilot, pg_catalog;
ALTER ROLE copilot_control SET statement_timeout = '10s';
