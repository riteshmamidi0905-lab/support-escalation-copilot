# What is real, what is simulated, what is only designed (M1)

| Item | Status |
|---|---|
| Seeded Meridian dataset generator (deterministic, contract-valid, designed difficulty) | **implemented and tested**; output is synthetic and fictional |
| Committed dataset `data/meridian-seed-20260101` and its manifest/SHA-256s | **real files**, regenerated and compared in CI |
| PostgreSQL schema, migrations, roles, FORCE'd RLS, signed scope function | **implemented and tested against real PostgreSQL** |
| Fixed query catalogue, ScopeGuard, scoped sessions, pool reset, trusted intake, loader | **implemented and tested** |
| Tenant-isolation attack tests (22 of 45 catalogued attacks now executable) | **implemented**; mutation-tested |
| Clean-database rebuild (migrate → bootstrap → generate → validate → load → sweep) | **implemented**; run in tests, CI and the Compose job |
| FTS / pgvector infrastructure | **functions** (queries and an index exist; no embeddings, no tuned retrieval) |
| R-3 (`resync` vs `re-sync`) | **observed baseline weakness, deliberately unfixed** (a test records it) |
| Hand-labelled evaluation set | **does not exist yet** (authored blind in M2) |
| Typed actions as a contract only; approvals, policy engine, audit log, tools | designed (M3) |
| Case workflow, diagnosis, drafts, scenario runs, mocks, observability, UI, real-model path | designed (M4–M6) |
| Retrieval quality, task completion, latency, approval behaviour, business metrics | **nothing measured** |
