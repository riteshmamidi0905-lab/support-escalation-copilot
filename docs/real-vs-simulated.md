# What is real, what is simulated, what is only designed (M0)

| Item | Status |
|---|---|
| JSON Schema data contracts and validators (cross-record rules, synthetic markers, manifests, held-out filter) | **implemented and tested** |
| Typed action vocabulary (no email action) as a contract | **implemented and tested** (contract only; no executor yet) |
| Secret redaction + canary detector with positive control | **implemented and tested** |
| Invariant/attack catalogue and its consistency checks | **implemented and tested** (attack tests mostly *planned*; statuses are honest) |
| PostgreSQL 16 + pgvector + FTS + RLS behaviour for a real non-owner role | **demonstrated by spike tests** (locally on 16.2/pgvector 0.6.2 via an embedded server; CI uses `pgvector/pgvector:pg16`) |
| Pinned frozen runtime and a smoke run | **implemented and tested** |
| CI (lint, tests, database job, compose database) | **configured; status reported with the commit** |
| Synthetic dataset generator | designed (contracts exist; two tiny hand-written fixtures only) |
| Tenant-isolation implementation (schema, roles, ScopeGuard) | designed; mechanism spiked |
| Retrieval, policy engine, approvals, audit, workflow, mocks, UI, observability | designed only |
| Real-model behaviour | **nothing measured** |
Nothing in M0 has any performance or quality result.
