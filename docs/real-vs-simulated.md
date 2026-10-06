# What is real, what is simulated, what is only designed (M3)

| Item | Status |
|---|---|
| Seeded Meridian dataset generator (deterministic, contract-valid, designed difficulty) | **implemented and tested**; output is synthetic and fictional |
| Committed dataset `data/meridian-seed-20260101` and its manifest/SHA-256s | **real files**, regenerated and compared in CI |
| PostgreSQL schema, migrations, roles, FORCE'd RLS, signed scope function | **implemented and tested against real PostgreSQL** |
| Fixed query catalogue, ScopeGuard, scoped sessions, pool reset, trusted intake, loader | **implemented and tested** |
| Tenant-isolation, retrieval and control-plane attack tests (61 of 68 catalogued attacks executable) | **implemented**; mutation-tested (M1 defences; M2 retrieval defences via `scripts/mutation_check_m2.py`) |
| Clean-database rebuild (migrate → bootstrap → generate → validate → load → sweep) | **implemented**; run in tests, CI and the Compose job |
| Retrieval: chunker, FTS (A), pgvector (B), RRF hybrid (C), cross-encoder rerank (D), lifecycle/duplicate/conflict governance, structured evidence + JSON contract | **implemented and measured** (see below) |
| Embeddings | **real**: BAAI/bge-small-en-v1.5 (quantised ONNX, pinned revision) computed locally; committed hash-keyed cache replays them. The plumbing embedder is **non-semantic**, used only in infrastructure tests, refused by the benchmark |
| R-3 (`resync` vs `re-sync`) | **measured under all four strategies**; lexical miss preserved as a baseline and guarded by a mutation test |
| Hand-labelled evaluation set (40 tickets) | **exists**, frozen by hash before scoring; **single AI reviewer, not independent human annotation**; two passes in one session |
| Dev set (300 generator tickets) | `synthetic_label` evidence only: circular, templated, used for parameter choice; flatters every strategy |
| Typed actions (5), tiers READ/PROPOSE/GATED_WRITE/FORBIDDEN, validation before policy | **implemented and tested** |
| Policy engine (decisions, reason codes, deterministic evidence requirements) | **implemented and tested** against real PostgreSQL facts; *semantic* sufficiency (does the runbook fit the symptoms) is **not** implemented |
| Approval service (role/tenant/case/expiry/action-hash binding; DB-level guards) | **implemented and tested**; approvers are **mock signed identities** (no real authentication) |
| Idempotent gateway, retries, uncertain-outcome handling, concurrency | **implemented and tested** (8-thread duplicate test); customer systems are **deterministic mocks** with fault injection, not real carrier/billing systems |
| Audit log (hash chain, scrubbing, tamper tests) | **implemented**; guarantee is tamper-**evidence**, not immutability; no external anchor is deployed |
| Runtime approver hook (R-1) | wrapped, tested with the real frozen `agent.security.Policy`; runtime unchanged |
| Case state machine, case-file generation, diagnosis, real LLM calls, scenario suite S1–S16 as end-to-end runs, UI | designed (M4–M6) |
| Retrieval quality (hand-labelled held-out), abstention, conflicts, lifecycle, latency/resources on one laptop | **measured** (`docs/m2-results.md`); descriptive, small n |
| Real-model (LLM) behaviour, task completion, business metrics | **nothing measured** |
| Approval/refusal/idempotency behaviour of the control plane | **demonstrated by deterministic tests and the clean-database run** (`docs/m3-scenarios.md`): a property of the controls, not a performance metric |
| Evidence-gap detection by similarity thresholds | **measured and found unreliable** (ADR-0013 §5) |
| That a model or workflow ignores injected text in retrieved documents | **not demonstrated** (M3/M4); only that retrieval is inert |
