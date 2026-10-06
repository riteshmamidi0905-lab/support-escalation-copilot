# Evaluation evidence

Five kinds of evidence exist in this repository. They answer different questions, were produced differently and are **never merged into one number** (ADR-0007). Each section says what was measured, how
much it is worth, and what it does not show. The block directly below is **generated** from [`content/public-claims.json`](../content/public-claims.json), which is generated from a recorded run
(`reports/m6/release-evidence.json`); it is the one place the current counts live.

## Verified claims at a glance
<!-- claims:evaluation:begin -->
| Claim | Evidence | Limit |
|---|---|---|
| The customer, Meridian Freight Systems, is fictional and every account, ticket, runbook, incident and integration is synthetic, generated reproducibly from a seed. | limitation | Synthetic data has designed difficulty, not real-world distribution; no real customer or customer data was used. |
| This is a reference implementation run locally; it has never been deployed and has no production use or real customers. | limitation | Demo mode, simulated sign-in and mock customer systems are deliberate; see docs/real-vs-simulated.md for the full list and docs/security.md for what a real deployment would need. |
| Every model result in this repository comes from RuleCaseModel, a deterministic rule-based stand-in (plus scripted misbehaving wrappers), not a language model. | limitation | The stand-in's heuristics were developed while looking at the scenario set, so its outcome rates are development results about the orchestration, not model quality. |
| Real-model evaluation was not executed: no local language-model runtime was available, and no paid API was used; the method is frozen and hash-locked for a future run. | limitation | The provider boundary (ConfiguredProvider) is tested against a protocol stub only; nothing is known about any real model's behaviour here. |
| The model cannot choose the tenant, the approver, the required role, the approval expiry, the workflow state or the action vocabulary: its output is untrusted structured data validated by deterministic code, and a model that claims otherwise is ignored. | design + tests | Shown with the stand-in and with deliberately misbehaving scripted models (obedient to injected text, privileged fields, invented and forbidden actions); behaviour of a real LLM is not measured. |
| The only write vocabulary is 5 typed actions (3 human-gated: escalate_engineering, request_sla_credit, trigger_resync); no action, tool, client or code path can send customer email, and 16 dangerous request names are rejected by name. | design + tests | Customer email is excluded by construction (invariant I3); the mock escalation system only accepts internal destinations. |
| An approval is bound to the exact canonical action (hash), its role, tenant, case and expiry; policy is re-evaluated from fresh facts at execution; an amended action voids the old approval and needs a new one that its amender cannot give. | design + tests | Approvers are simulated (signed mock identities); there is no real identity provider. |
| Every customer write carries a deterministic idempotency key recorded in a ledger; a write whose outcome is unknown is never retried blindly, and a human reconciles it. | design + tests | Customer systems are deterministic mocks with fault injection; reconciliation trusts the operator's recorded note because the mocks offer no lookup. |
| Tenant isolation is enforced in the database: forced row-level security keyed to a signed, short-lived scope, a fixed query catalogue and a least-privilege role for the model-facing code; operator reads are authorised server-side by signed account grants with a uniform 404. | design + tests | Single PostgreSQL instance and a mock identity provider; signing-key custody and rotation are out of scope. |
| Every proposal, policy decision, approval, execution and refusal is written to an append-only, hash-chained audit log that also scrubs secrets and masks e-mail addresses. | design + tests | Tamper-evidence, not immutability: removing the most recent events is detectable only with an external anchor, which is not deployed. |
| A PostgreSQL lease-based recovery worker resumes cases from durable state; tests with concurrent workers, including workers that ignore the lease, produce exactly one effect per approved action. | design + tests | Tested on one database with threads/processes on one host; not tested under real network partitions or multiple hosts. |
| Operational metrics are derived from the application's own events and tables (an empty system reports zeros) and correlated by request, model invocation, action, approval and execution ids; telemetry never stores prompts, reasoning or secrets. | design + tests | No external metrics backend, tracing system, alerting or retention policy. |
| The operator UI is server-rendered with no JavaScript, a strict Content-Security-Policy and CSRF-protected forms; every read and action is re-authorised on the server because the browser is not trusted. | design + tests | Sign-in is simulated (a persona picker); no session revocation, rate limiting or MFA. |
| 472 automated tests pass with 1 documented expected failure (the frozen runtime's approver hook is a bool callback, wrapped at the control boundary) and 0 failures; 0 skipped. | deterministic tests | One full run at the evidence commit on one machine; the database tests need PostgreSQL 16 with pgvector and are executed (not skipped) in CI. Python 3.12.15. |
| 92 attacks against the four invariants are catalogued, and 92 have executable tests that attempt the violation (I1 34, I2 37, I3 9, I4 12). | deterministic tests | The catalogue is the author's own; tests attempt known attack classes, not an independent penetration test. |
| Deliberately breaking each defence makes the tests fail: M2 retrieval 9/9, M3 control plane 31/31, M4 workflow 20/20, M5 operator surface 30/30 mutations killed. | mutation checks | Hand-chosen mutations by the author (not a systematic mutation tool); a first M5 run had one survivor, which led to an added test. Mutation checks are manual, not run in CI. |
| Across 30 control-plane scenarios, 315 end-to-end case executions and 32 injection attack runs, no invariant was violated and the audit hash chain verified over 5385 events. | stand-in workflow runs | This measures the controls around the model, not the model: the model is the stand-in and, in the injection runs, a deliberately obedient scripted model. Containment is not the same as a correct outcome. |
| On a frozen 40-ticket hand-labelled held-out set, pgvector search (Hit@1 0.80, MRR@10 0.87) ranked better than lexical full-text (Hit@1 0.60, MRR@10 0.70), equal-weight hybrid fusion (0.68, 0.79) and hybrid plus cross-encoder rerank (0.76, 0.84); vector was chosen. | frozen held-out retrieval eval | Labelled by a single AI reviewer in one session, not independent human annotation; n=25 answerable tickets, so differences are descriptive and the 95% intervals overlap; one embedding model and one reranker; 60-document corpus. |
| The generator-labelled development set flattered every retrieval strategy: vector Hit@1 0.97 on dev vs 0.80 held-out, lexical 0.86 vs 0.60. | synthetic dev retrieval eval | Dev tickets are templated and labelled by the same generator (circular); they were used only to choose parameters; held-out remains single-reviewer and small. |
| Retrieval confidence does not detect missing evidence: on unanswerable held-out tickets every strategy returned look-alike pages as evidence 45%/55%/45%/36% of the time (lexical/vector/hybrid/rerank, n=11), so sufficiency is judged from content and policy, with escalation when unsure. | frozen held-out retrieval eval | n=11 unanswerable tickets, single AI reviewer; an engineering finding that shaped the design, not a benchmark score. |
| The cross-encoder reranker was not adopted: no ranking gain over plain vector search (MRR@10 0.84 vs 0.87) for about 107 ms of extra CPU per query and a 347 MB model snapshot. | frozen held-out retrieval eval | One reranker model, one laptop, 60 chunks; latency says nothing about scale. |
| The 16 acceptance scenarios were run as 315 end-to-end case executions with the deterministic stand-in; 280 reached the scenario's expected outcome, the rest are individually explained (label conflicts, retrieval false conflicts, stand-in intent misses). | stand-in workflow runs | NOT a model-accuracy figure: the stand-in is rule-based and its heuristics were tuned during development while looking at these scenarios; use it only to show the orchestration and controls run end to end. |
| Deterministic grounding checks on draft replies caught 20/20 steered drafts they were developed against, but 0/8 rephrasings of the same harms and 0/10 misleading drafts built only from grounded words (and flagged 0/13 benign drafts); the control for what rules cannot see is mandatory, itemised human review. | adversarial dev corpus | Hand-written corpus by the author of the rules (two rules were corrected after the first run): development results; the residual weakness is preserved on purpose; no human-review study exists. |
| A manual browser pass at desktop and 375 px found and fixed seven UI defects (including mobile overflow); scripted checks found one h1 per page, labelled controls, captioned tables, a first-tab skip link, and 0 WCAG AA contrast failures across 263 text elements in light and dark mode. | manual browser pass | One browser engine, one developer-operator, synthetic data; no screen-reader session; the automated tests, not this pass, are authoritative. |
| Pattern-based redaction removes labelled secrets (Bearer tokens, NAME=value, PEM blocks, DSN passwords) but not a bare unlabelled token typed into a ticket; that token is stored and shown to operators entitled to that tenant. | limitation | Pinned by a test as a documented residual (R-54); entropy heuristics would add false positives and were not attempted. |
| A convincing wrong draft made only of grounded words and numbers cannot be detected by any deterministic rule here; the only control is human review, which is recorded but is not a technical gate on use. | limitation | Human review effectiveness was not measured (R-58). |
| Sign-in is simulated: a persona picker mints a signed identity; there is no real authentication, session revocation, rate limiting or MFA. | limitation | Demo-only by design (R-59); a deployment needs an identity provider. |
| Reconciling an uncertain write as applied trusts the operator's recorded note, and the audit log has no external anchor, so deletion of the most recent events is detectable only with one. | limitation | Documented residuals (R-32, R-62). |

*Generated from [`content/public-claims.json`](../content/public-claims.json) at code commit `60850ee1ae`. 28 of 28 claims shown; the manifest holds source artifacts, commits and per-claim usage.*
<!-- claims:evaluation:end -->

## How to read the evidence classes
| Class | Question | Produced by | Worth |
|---|---|---|---|
| Retrieval, frozen held-out | which retrieval design finds the right evidence? | M2 benchmark on a hash-frozen 40-ticket hand-labelled set | descriptive; **single AI reviewer**, small n |
| Retrieval, synthetic dev | what parameters? | 300 generator-labelled tickets | circular; used only to choose parameters; **flatters every strategy** |
| Control plane | do the controls hold? | deterministic tests, attack tests, scenario runs, mutation checks | strong for the properties tested; says nothing about a model |
| Workflow with the stand-in | does the orchestration work end to end? | S1-S16 as 315 case executions with `RuleCaseModel` | orchestration and safety only; **not model quality** |
| Adversarial development corpus | how good are the draft grounding rules? | hand-written drafts by the rules' author | development result; preserved weakness |
| Real model | does a real LLM help or hurt? | **not executed** | nothing |

## 1. Retrieval evaluation (M2, frozen)
**Protocol:** rubric and evaluation protocol were written and hashed before any strategy was scored (`docs/eval-freeze.json`, enforced by `tests/test_eval_freeze.py`). Held-out set: 40 tickets
(25 answerable, 11 unanswerable, 4 conflicting) **hand-labelled by a single AI reviewer in one session, not independent human annotation**. Dev set: 300 generator tickets with the generator's own answer key,
used only to choose parameters. Four strategies over the same 60 chunks: A lexical (PostgreSQL FTS), B vector (pgvector, `bge-small-en-v1.5`), C hybrid (RRF), D hybrid + cross-encoder rerank.
Full tables: [`m2-results.md`](m2-results.md); decision: [ADR-0013](adr/0013-retrieval-architecture.md).

| held-out (n=25 answerable) | A lexical | B vector | C hybrid | D rerank |
|---|---|---|---|---|
| Hit@1 | 0.60 | **0.80** | 0.68 | 0.76 |
| MRR@10 | 0.70 | **0.87** | 0.79 | 0.84 |
| governed success (abstain-first, as published) | 44% | **88%** | 68% | 48% |
| false confidence on unanswerable (n=11) | 45% | 55% | 45% | 36% |
| extra cost per query | none | 7.7 ms embedding | 7.7 ms | 7.7 ms + ~107 ms rerank, 347 MB model |

What it shows: vector search ranked best on this corpus and was chosen; the equal-weight hybrid and the reranker did **not** earn their complexity; the synthetic dev set flattered everything (vector Hit@1 0.97
dev vs 0.80 held-out); similarity confidence is **not** an evidence-sufficiency detector (every strategy returned look-alike pages for 36-55% of unanswerable tickets), so sufficiency is judged from content and policy;
lifecycle governance, not ranking, removes superseded and draft documents (45-50% of tickets had one in the raw top 5, 0 after governance).
What it does not show: that this generalises to another corpus or real tickets (60 short documents, one chunk each); the 95% intervals overlap; one embedding model and one reranker were tried.
**Control-order correction (M3):** abstain-before-conflict could hide a known conflict (TCK-8018). The corrected conflict-first order was re-run on the same frozen benchmark
([`m2-conflict-order-rerun.md`](m2-conflict-order-rerun.md)); ranking metrics are identical, governed success for vector moves 0.88 → 0.84 and false conflicts appear (0 → 0.04); the original tables stay reproducible and nothing was retuned.

## 2. Control-plane evaluation (M3)
Typed actions, deterministic policy, role- and hash-bound approvals, idempotent gateway, hash-chained audit, mocks with fault injection. **30 control-plane scenarios** ([`m3-scenarios.md`](m3-scenarios.md)) and the attack tests run
against real PostgreSQL; an 8-thread duplicate-execution test produces one effect; mutation checks (31) break each defence and require a failing test. These are properties of deterministic code.

## 3. Workflow evaluation (M4): read this section with its warning
The 16 acceptance scenarios (S1-S16) were run as **315 end-to-end case executions** through the real workflow, control plane and database ([`m4-scenarios.md`](m4-scenarios.md)). The model in every one is
**`RuleCaseModel`, a deterministic rule-based stand-in, not an LLM**, and its heuristics **were tuned during development while looking at these scenarios** (R-43). Therefore the outcome-match rate
(**280 of 315**) is **not a measure of model accuracy and must not be quoted as one**; it shows that orchestration, trust boundary, policy and approvals reach the expected terminal states and that the mismatches are individually
explainable: 24 of 315 are scenario labels that disagree with the data itself (R-44), 10 are hostile tickets the stand-in did not recognise as out of policy (R-47; they still ended safely) and 1 is a retrieval false conflict, listed case by case.

| S1 | S2 | S3 | S4 | S5 | S6 | S7 | S8 | S9-S13 | S14 | S15 | S16 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 15/15 | 16/20 | 14/20 | 14/25 | 14/15 | 20/20 | 20/30 | 12/12 | 3/3 each | 127/130 | 8/8 | 5/5 |

What it does show: **0 invariant violations** over all 315 cases, a verified audit chain, no duplicated effect, and containment in 32 end-to-end injection runs (also with every injection detector switched off, and with a
deliberately obedient scripted model) ([`m4-injection.md`](m4-injection.md)). Containment is not correctness: some hostile tickets were not recognised as out of policy by the stand-in and still ended safely.

## 4. Operational and security evaluation (M5)
The verified counts (tests, threat tests, mutation checks, scenario re-runs) are in the generated table above. In words: the full suite passes with one documented expected failure (the frozen runtime's
approver hook is a bool callback; the project wraps it at its own boundary); every catalogued attack has an executable test; mutation checks of the M2, M3, M4 and M5 defences all kill their mutations
(the first M5 run had one survivor, which produced a new test); the M3 scenarios, M4 scenario matrix and injection matrix were **re-run against the final code** with no invariant violation.
**Recovery and concurrency:** four concurrent recovery workers over a mixed backlog (approved, expired, crashed-after-effect) produce exactly one effect per approved action; workers that deliberately ignore the lease still cannot duplicate an effect;
a worker that dies mid-run is taken over after the lease expires; a failing case is parked and reported, never force-failed. Not tested: real network partitions or multiple hosts.
**Invariants through the operator surface:** I1-I4 re-attacked through UI/API paths ([`m5-results.md`](m5-results.md) §9). **Browser pass:** manual, desktop and 375 px, accessibility scripts and contrast ([`m5-browser-verification.md`](m5-browser-verification.md)); the automated tests are authoritative.

## 5. Draft-steering evaluation (the uncomfortable result, preserved)
| set | n | flagged by the deterministic grounding rules |
|---|---|---|
| benign drafts | 13 | 0 (a flag here would be a false rejection) |
| steered drafts: invented numbers/versions, false effects, links, leaks, promises, policy leaks | 20 | **20 of 20** |
| the same harms rephrased | 8 | **0 of 8** |
| misleading drafts using only grounded words and numbers | 10 | **0 of 10** |

The rules check *surfaces*: whether a number, identifier, link, effect claim or phrase appears in the evidence, the ticket or the case facts. That catches an invented "45 minutes" and a false "we have re-synced it",
and it cannot catch "everything is back to normal on our side" (no new tokens), "forty five times" (number in words), "the credit is now on your account" (passive), or "your feed is healthy" (grounded words, wrong conclusion).
A lexical rule set is brittle against paraphrase and blind to meaning; closing the gap would need semantic checking, which would itself be a model whose reliability is unmeasured here. The control that remains is
mandatory, itemised human review (recorded, not a technical gate on use). The corpus was written by the author of the rules and two rules were corrected after the first run, so 20/20 is a **development result**.
Details: [`m5-draft-steering.md`](m5-draft-steering.md).

## 6. Real-model evaluation
**Not executed.** No local language-model runtime was available (probe: `reports/m5/real-model-probe.json`; an 8.6 GB machine) and no paid API was used. It is **not a release blocker**; the method is frozen and
hash-locked ([`m5-real-model-protocol.md`](m5-real-model-protocol.md), `docs/real-model-freeze.json`, `tests/test_real_model_freeze.py`) so that a future run is a measurement rather than a tuning exercise,
and the provider boundary is tested against a protocol stub that misbehaves the way local models do ([`m5-real-model-readiness.md`](m5-real-model-readiness.md)). Until a run exists, nothing in this repository says anything about any real model.

## Reproduce
```bash
python scripts/with_local_pg.py pytest -q                                                   # the suite
python scripts/with_local_pg.py python scripts/collect_release_evidence.py                  # tests + mutation checks + scenario re-runs -> reports/m6/release-evidence.json
python scripts/public_claims.py build                                                       # evidence -> content/public-claims.json and the generated blocks
python scripts/public_claims.py check                                                       # what CI runs: no drift between evidence, manifest and docs
make benchmark                                                                              # M2 retrieval benchmark, replaying the committed caches
```
Historical reports (`reports/m2`, `m2-m3-rerun`, `m3`, `m4`, `m5`) and the M3/M4 generated docs are point-in-time artefacts and were intentionally **not** overwritten by later runs; the later re-runs are recorded in `reports/m6/release-evidence.json`.
