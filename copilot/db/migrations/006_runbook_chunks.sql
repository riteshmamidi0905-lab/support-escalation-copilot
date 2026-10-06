-- 006: structure-aware runbook chunks with full-text and vector columns (M2).
-- Chunks carry only what chunking produces (doc id, ordinal, section, text, source offsets). Version, lifecycle status, owner and provenance
-- are NOT copied: they are read from runbook_docs at query time, so a status change can never leave a stale copy behind.
-- Runbooks are global knowledge (no tenant data); tenant evidence is read through the signed-scope queries, not from this table.
CREATE TABLE copilot.runbook_chunks (
  chunk_id text PRIMARY KEY,
  doc_id text NOT NULL REFERENCES copilot.runbook_docs(doc_id),
  ordinal int NOT NULL,
  section text NOT NULL,
  char_start int NOT NULL,
  char_end int NOT NULL,
  text text NOT NULL,
  embed_text text NOT NULL,
  embedding vector(384) NOT NULL,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', embed_text)) STORED,
  UNIQUE (doc_id, ordinal),
  CHECK (char_end > char_start)
);
CREATE INDEX runbook_chunks_tsv_idx ON copilot.runbook_chunks USING gin (tsv);
-- No ANN index on purpose: exact cosine scan is both faster to reason about and fast enough at this scale (see ADR 0013). Revisit above ~100k chunks.
ALTER TABLE copilot.runbook_chunks OWNER TO copilot_owner;
REVOKE ALL ON copilot.runbook_chunks FROM PUBLIC;
GRANT SELECT ON copilot.runbook_chunks TO copilot_app;
GRANT INSERT, TRUNCATE ON copilot.runbook_chunks TO copilot_loader;
