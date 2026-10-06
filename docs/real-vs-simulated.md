# What is real, what is simulated, what is only designed (M5)

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
| Case state machine (durable, DB-enforced edges), stages, resume, case file contract | **implemented and tested** against real PostgreSQL |
| Frozen runtime integration (provider abstraction, structured-output repair, Agent loop in DIAGNOSE, budgets/retries, tracer) | **used**, unmodified (pinned `231b186`) |
| Model decisions in the workflow (diagnosis, applicability, proposals, drafts) | produced by a **deterministic stand-in (`RuleCaseModel`) and deliberately misbehaving wrappers — NOT an LLM**. The OpenAI-compatible local path is tested against a protocol stub only |
| S1–S16 end-to-end runs (315 case executions) | **real workflow/control plane, scripted stand-in model**; measures orchestration and safety, not model quality; stand-in tuned during development |
| Real local model evaluation (Ollama etc.) | **NOT EXECUTED** (probe: no local model server; 8.6 GB RAM machine); frozen protocol, harness (`scripts/run_real_model_eval.py`, exercised only against a stub server) and provider boundary exist |
| Status / Carrier / Ticketing APIs | **deterministic mocks** with fault injection |
| Operator UI (server-rendered, no JavaScript): case file, evidence/provenance, diagnosis, contradictions, policy, approvals, executions, draft review, audit trace, queue, dashboard | **implemented; browser-verified** at desktop and 375 px; the automated tests are authoritative |
| Operator sign-in | **simulated**: pick a persona, the server mints a signed identity (grants signed); **no real authentication**, no revocation, no rate limiting |
| Server-side authorization of every read and action (role, account grant, case state, CSRF), uniform 404, strict CSP | **implemented and attacked** (A-I1-27..34, A-I2-34..37, A-I3-08, A-I4-11/12) |
| Approval amendment (void + re-approve), reconciliation of uncertain writes, itemised review of risky drafts | **implemented and tested**; reconciliation trusts the operator's recorded note (mock systems have no lookup) |
| Demo entry points A–F | **real workflow on synthetic data**; C uses a deliberately obedient *scripted* model, E/F script customer-system faults; model = stand-in |
| Observability: ops events, metrics derived from real tables, Prometheus text, correlation ids | **implemented**; no external metrics backend, tracing system, alerting or retention policy |
| Recovery worker (PostgreSQL leases, `SKIP LOCKED`, back-off, parking) | **implemented; tested with concurrent workers** and workers that ignore leases; not tested under real network partitions or multi-host |
| Draft grounding checks | **implemented**; evaluation is on a hand-written corpus by the rules' author (development result); **misleading-but-grounded drafts are not detected** (0/10) |
| `ConfiguredProvider`, frozen real-model protocol, probe, measured prompt sizes | **implemented / measured** (token figures are estimates); **no real model was run** |
| Deployment (Compose app image, runbook), real identity provider | designed / **not built** (M6) |
| Retrieval quality (hand-labelled held-out), abstention, conflicts, lifecycle, latency/resources on one laptop | **measured** (`docs/m2-results.md`); descriptive, small n |
| Real-model (LLM) behaviour, task completion, business metrics | **nothing measured** |
| Approval/refusal/idempotency behaviour of the control plane | **demonstrated by deterministic tests and the clean-database run** (`docs/m3-scenarios.md`): a property of the controls, not a performance metric |
| Evidence-gap detection by similarity thresholds | **measured and found unreliable** (ADR-0013 §5) |
| That a model or workflow ignores injected text in retrieved documents | **not demonstrated** (M3/M4); only that retrieval is inert |
