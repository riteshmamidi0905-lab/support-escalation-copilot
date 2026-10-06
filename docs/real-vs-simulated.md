# What is real, what is simulated, what is only designed (M2)

| Item | Status |
|---|---|
| Seeded Meridian dataset generator (deterministic, contract-valid, designed difficulty) | **implemented and tested**; output is synthetic and fictional |
| Committed dataset `data/meridian-seed-20260101` and its manifest/SHA-256s | **real files**, regenerated and compared in CI |
| PostgreSQL schema, migrations, roles, FORCE'd RLS, signed scope function | **implemented and tested against real PostgreSQL** |
| Fixed query catalogue, ScopeGuard, scoped sessions, pool reset, trusted intake, loader | **implemented and tested** |
| Tenant-isolation and retrieval attack tests (32 of 54 catalogued attacks executable) | **implemented**; mutation-tested (M1 defences; M2 retrieval defences via `scripts/mutation_check_m2.py`) |
| Clean-database rebuild (migrate → bootstrap → generate → validate → load → sweep) | **implemented**; run in tests, CI and the Compose job |
| Retrieval: chunker, FTS (A), pgvector (B), RRF hybrid (C), cross-encoder rerank (D), lifecycle/duplicate/conflict governance, structured evidence + JSON contract | **implemented and measured** (see below) |
| Embeddings | **real**: BAAI/bge-small-en-v1.5 (quantised ONNX, pinned revision) computed locally; committed hash-keyed cache replays them. The plumbing embedder is **non-semantic**, used only in infrastructure tests, refused by the benchmark |
| R-3 (`resync` vs `re-sync`) | **measured under all four strategies**; lexical miss preserved as a baseline and guarded by a mutation test |
| Hand-labelled evaluation set (40 tickets) | **exists**, frozen by hash before scoring; **single AI reviewer, not independent human annotation**; two passes in one session |
| Dev set (300 generator tickets) | `synthetic_label` evidence only: circular, templated, used for parameter choice; flatters every strategy |
| Typed actions as a contract only; approvals, policy engine, audit log, tools | designed (M3) |
| Case workflow, diagnosis, drafts, scenario runs, mocks, observability, UI, real-model path | designed (M4–M6) |
| Retrieval quality (hand-labelled held-out), abstention, conflicts, lifecycle, latency/resources on one laptop | **measured** (`docs/m2-results.md`); descriptive, small n |
| Real-model (LLM) behaviour, task completion, approval behaviour, business metrics | **nothing measured** |
| Evidence-gap detection by similarity thresholds | **measured and found unreliable** (ADR-0013 §5) |
| That a model or workflow ignores injected text in retrieved documents | **not demonstrated** (M3/M4); only that retrieval is inert |
