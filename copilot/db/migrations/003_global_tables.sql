-- 003: global (non-tenant) knowledge. NOTE what is deliberately absent: incidents carry no affected-account list, runbooks carry no tenant data,
-- and no answer key (labels, scenario ids, the 'adversarial' test flag) is stored where the runtime can read it.
CREATE TABLE copilot.incidents (
  incident_id text PRIMARY KEY,
  title text NOT NULL,
  component text NOT NULL,
  severity text NOT NULL,
  status text NOT NULL,
  started_at timestamptz NOT NULL,
  resolved_at timestamptz,
  summary text NOT NULL
);
ALTER TABLE copilot.incident_accounts ADD FOREIGN KEY (incident_id) REFERENCES copilot.incidents(incident_id);
CREATE TABLE copilot.deployments (
  deployment_id text PRIMARY KEY,
  component text NOT NULL,
  version text NOT NULL,
  deployed_at timestamptz NOT NULL,
  change_summary text NOT NULL
);
CREATE TABLE copilot.release_notes (
  release_id text PRIMARY KEY,
  component text NOT NULL,
  version text NOT NULL,
  released_at date NOT NULL,
  notes text NOT NULL
);
CREATE TABLE copilot.runbook_docs (
  doc_id text PRIMARY KEY,
  title text NOT NULL,
  product_area text NOT NULL,
  version text NOT NULL,
  status text NOT NULL CHECK (status IN ('active','superseded','draft')),
  effective_from date NOT NULL,
  supersedes text REFERENCES copilot.runbook_docs(doc_id),
  owner text NOT NULL,
  source_path text NOT NULL,
  body_markdown text NOT NULL,
  -- Full-text search infrastructure only (M1). Default 'english' configuration, no synonyms or normalisation: the R-3 baseline weakness
  -- (resync vs re-sync) is intentionally left in place until the retrieval evaluation in M2 measures it.
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || body_markdown)) STORED
);
CREATE INDEX runbook_docs_tsv_idx ON copilot.runbook_docs USING gin (tsv);
