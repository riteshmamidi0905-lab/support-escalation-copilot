# Test strategy

Principle: **tests try to violate the invariants; they do not merely assert that the design prevents violation.** A detector that cannot fail proves nothing, so each detector has a positive control.

## Layers
| Layer | What | Needs | Gate |
|---|---|---|---|
| Contract tests | schemas valid; every validator rule has a failing mutation | none | CI |
| Unit tests | redaction, policy decisions, state machine transitions, ScopeGuard | none | CI |
| Database tests | RLS, signed scope, FTS, vector, migrations, roles/privilege audits, clean rebuild — against real PostgreSQL 16 + pgvector | Postgres | CI (**must not skip**) |
| Mutation checks (manual, M1; M2: `scripts/mutation_check_m2.py`, 9 mutations of retrieval defences) | deliberately break each defence (signature, expiry, case consistency, FORCE, pool reset, excess grant) and confirm a test fails | Postgres | recorded in docs/risks.md; positive controls kept as tests |
| Invariant attack tests | the catalogue in `copilot/invariants.py` (I1–I4) | varies | CI |
| Scenario suite | S1–S16 with expected terminal outcomes, deterministic providers | Postgres | CI from M4 |
| Fault-injection suite | timeouts, 500s, malformed, rate limits, breaker open | mocks | CI (M4 workflow faults; M5 dependency/breaker telemetry) |
| Workflow tests (M4) | state machine, trust boundary, stand-in/faulty models, fault injection, restart/resume, end-to-end injection (detectors on and OFF); `scripts/run_m4_scenarios.py` (S1–S16, 315 cases), `scripts/run_m4_injection.py`, `scripts/mutation_check_m4.py` (20 mutations) | Postgres | CI (not the mutation/scenario scripts' numbers) |
| Control-plane tests (M3) | action validation, canonical identity, policy matrix (incl. positive controls that fail under blanket denial), approvals, idempotency/concurrency, audit chain tampering, mocks, I1–I4 attacks; `scripts/mutation_check_m3.py` (31 mutations) and the clean-database run `scripts/run_m3_scenarios.py` | Postgres | CI |
| Retrieval infrastructure tests | chunking, lifecycle/duplicate/conflict governance, citation resolution, evidence schema, tenant + injection attacks; real-model embeddings replayed from a committed cache, or the labelled NON-SEMANTIC plumbing embedder | Postgres | CI |
| Retrieval benchmark (measured) | A/B/C/D on the frozen hand set and the dev set; replay from caches must reproduce the committed results; live mode (real models) is manual | Postgres (+ models for live) | replay in CI; **quality numbers are reported, not gated** |
| Operator-app tests (M5) | the browser as an attacker: CSRF, forged/widened/expired/agent cookies, tenant probing through every page/API, wrong role/account approvals, replays, no direct-execute route, no email capability, XSS/CSP, canary secrets, amendment/review/reconciliation flows, demos A-F, event-driven dashboard | Postgres | CI |
| Access/ops/recovery tests (M5) | A-I2-10 audit scoping in SQL; metrics derived from real events (zeros on an empty system); correlation ids; concurrent recovery workers incl. workers that ignore leases | Postgres | CI |
| Draft-steering evaluation (M5) | grounding rules vs steered / evasion / residual draft sets (`scripts/run_draft_steering_eval.py`); residual weakness pinned | none | CI (tests), manual (report) |
| Real-model readiness (M5) | `ConfiguredProvider` against an OpenAI-protocol stub that misbehaves like real local models; frozen protocol hash-lock; probe | none / Postgres | CI |
| Mutation checks (manual): M3 `scripts/mutation_check_m3.py`, M4 `scripts/mutation_check_m4.py` (20), M5 `scripts/mutation_check_m5.py` (28+2) | deliberately break each defence and require a failing test | Postgres | before each milestone report |
| Browser verification (M5, manual) | real browser at desktop and 375 px: navigation, evidence, approve, deny, amend, reconcile, review, degraded, tenant isolation, audit, a11y script, contrast, console/network | browser | **not authoritative**: the automated tests are; screenshots in `docs/m5/screenshots` |
| Release-integrity tests (M6) | public claims manifest validates against its schema; every claim names real artifacts and an ancestor commit; no stand-in or development result is offered for a CV; no `real_llm` claim without a recorded result; the evidence is current (clean tree at its commit, no code or tests changed since); generated blocks equal what the evidence produces; no action type can reach a customer; a few negation-aware overclaim checks | none | CI |
| Demo walk-through (M6) | `scripts/demo_smoke.py`: demos A-F over real HTTP with 21 outcome checks | Postgres | CI (compose job, against the Compose database) and locally |
| Real-model evaluation | local/hosted model on the same sets (frozen protocol `docs/m5-real-model-protocol.md`) | model | **never in CI**; separate evidence class; **not executed** |
| Compose smoke | stack comes up healthy; end-to-end run | Docker | CI |

## Invariant attack catalogue
`copilot/invariants.py` lists 92 attacks (I1: 34, I2: 37, I3: 9, I4: 12); **after M5 all 92 have executable tests** (M5 implemented A-I2-10 audit-query scoping and added 16 attacks against the operator surface, recovery and drafts). History: after M3, 61 of 68 had executable tests (I1 19 of 20, I2 29 of 32, I3 5 of 7, I4 8 of 9; the 7 still planned are A-I1-07, A-I2-06, A-I2-07, A-I2-10, A-I3-04, A-I3-05, A-I4-02: they need the real case workflow/model (M4) or audit queries (M5)). History: after M2, 32 had executable tests (M1: 22; M2 added A-I2-09, A-I2-25..30, A-I1-11, A-I3-06, A-I4-07). Before M2 it was: 22 have executable tests (all in I2 and I4) and 23 are planned (I1/I3 arrive with M3–M4). `tests/test_invariant_catalog.py` fails if an attack is missing from the threat model, if a test references an unknown attack, or if an attack is marked *implemented* without a test naming it.

## Principles
1. Fail first: write the violating test, watch it fail against a deliberately weak implementation where practical, then make it pass. Where a mutation is cheap, include it in the suite (e.g. weaken the policy and assert the invariant test notices).
2. Never weaken a test to make it pass; investigate. (M0 example: the FTS spike *failed* on `resync` vs `re-sync`; the finding was recorded as risk R-3 instead of changing the query.)
3. Deterministic by default: seeded data, scripted providers, injected clocks.
4. Positive controls for detectors (leaky logger, planted canaries, weakened policy).
5. Separate evidence classes in every report; no blended accuracy.
6. Do not tune against held-out data (`tuning_view`).
7. Flaky tests are bugs, not retries.

## Coverage is not the goal
We track which invariants/attacks/scenarios have tests (the catalogue), not line coverage percentages.
