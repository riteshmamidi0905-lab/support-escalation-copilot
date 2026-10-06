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
| Fault-injection suite | timeouts, 500s, malformed, rate limits, breaker open | mocks | CI from M5 |
| Retrieval infrastructure tests | chunking, lifecycle/duplicate/conflict governance, citation resolution, evidence schema, tenant + injection attacks; real-model embeddings replayed from a committed cache, or the labelled NON-SEMANTIC plumbing embedder | Postgres | CI |
| Retrieval benchmark (measured) | A/B/C/D on the frozen hand set and the dev set; replay from caches must reproduce the committed results; live mode (real models) is manual | Postgres (+ models for live) | replay in CI; **quality numbers are reported, not gated** |
| Real-model evaluation | local/hosted model on the same sets | model | **never in CI**; separate evidence class |
| Compose smoke | stack comes up healthy; end-to-end run | Docker | CI |

## Invariant attack catalogue
`copilot/invariants.py` lists 54 attacks (I1: 11, I2: 30, I3: 6, I4: 7) with a milestone and honest status; after M2, 32 have executable tests (M1: 22; M2 added A-I2-09, A-I2-25..30, A-I1-11, A-I3-06, A-I4-07). Before M2 it was: 22 have executable tests (all in I2 and I4) and 23 are planned (I1/I3 arrive with M3–M4). `tests/test_invariant_catalog.py` fails if an attack is missing from the threat model, if a test references an unknown attack, or if an attack is marked *implemented* without a test naming it.

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
