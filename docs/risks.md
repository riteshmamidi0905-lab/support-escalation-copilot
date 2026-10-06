# Discovered risks and required changes to the approved specification

| ID | Finding | Evidence | Impact | Proposed handling |
|---|---|---|---|---|
| R-1 (RESOLVED at M3) | The frozen runtime's approver hook is a **bool callback** (`Policy.approver(name, args) -> bool`); it carries no role, expiry or action diff | `tests/test_dependency_pin.py::test_runtime_approver_hook_carries_role_and_diff` (strict xfail) | Role-aware approvals (I1) cannot be expressed inside the runtime | **Decided at M0 review: no change to the runtime; richer approvals in this project's own boundary (ADR-0006).** Wrap it: the approver callback looks up an approval record created by our approval service, which checks role/expiry/action hash. Documented here for approval; if a pre-tool hook with context proves necessary it is raised as a proposal first. |
| R-2 | The runtime's tracer/redactor knows only generic secret patterns; a DSN password passes through it | `test_runtime_tracer_gap_for_project_specific_formats_is_known` | I4 | Every log/audit/trace write in this project goes through `copilot.redact`; canary tests guard it. |
| R-3 | PostgreSQL FTS does not match `resync` to `re-sync` | `test_server_version_and_fulltext_search` | Lexical retrieval misses obvious variants | Expected finding for the lexical-vs-vector comparison; add normalisation/synonyms and measure, do not hide. |
| R-4 | The runtime installs top-level packages `agent` and `service` | `pip` layout | Namespace collisions | Never name our modules `agent` or `service`; our package is `copilot`. |
| R-5 (RESOLVED in M1) | RLS-by-session-setting does not stop code that can run SQL from choosing its own scope | A-I2-05 | I2 | Implemented: signed trusted scope verified in the database, fixed query catalogue, ScopeGuard (ADR-0012). Residual token-replay risk stated there. |
| R-6 | Local embedded Postgres (16.2 / pgvector 0.6.2) differs from the CI image's pgvector version | environment | subtle index/behaviour differences | CI is the source of truth; record versions in test output; avoid version-specific features. |
| R-7 | Docker is not available on the development machine | environment | the Compose path cannot be run locally | Verified in CI only; stated wherever relevant (as with the earlier project). |
| R-8 | A single hand-labeller | methodology | label bias | stated limitation; second review pass; document disagreements. |

**Spec changes requested at M0:** none required. Two refinements are recorded in ADRs: the case workflow uses the runtime loop but not its planner (ADR-0002), and signed scope may be added to tenant isolation (ADR-0004).

## New findings during M1
| ID | Finding | How found | Handling |
|---|---|---|---|
| R-9 | A signer that is not database-aware can mint a *validly signed* scope for an account/case pair that do not belong together | designing the mismatch test | the database now also checks the case exists and belongs to the account (mutation: removing it makes `test_case_and_account_that_do_not_belong_together...` fail) |
| R-10 | `default_transaction_read_only` and the absence of write policies **masked** an excess `GRANT` (mutation: granting UPDATE changed no test result) | mutation testing | exact-match `audit_privileges` added and tested with a positive control |
| R-11 | `DISCARD ALL` on pool return breaks psycopg's client-side prepared statements ("prepared statement does not exist") | first full test run | `prepare_threshold=None` on pooled connections |
| R-12 | Putting an `affected_account_ids` list on a global `incidents` table would have leaked which other accounts were affected | design review | the list lives only in tenant-scoped `incident_accounts` (A-I2-11) |
| R-13 | A runbook/answer-key flag (`adversarial`, labels) in the application DB would let the runtime read the answer key | design review | excluded from the database; test asserts it |
| R-14 | The intake role can read the ticket→account mapping for all tickets | design | trusted, never model-facing; module-boundary test; deployment uses separate credentials |
| R-15 | `test_sql_boundary` found two places building SQL by string concatenation/formatting (loader TRUNCATE, rebuild digest) | static check | replaced with `psycopg.sql.Identifier` composition; the exemption was removed |
| R-16 | A test fixture that inserted a hostile ticket polluted the shared environment and broke later tests | test failures | cleanup in `finally`; shared-environment hygiene is now a convention |

## New findings during M2
| ID | Finding | How found | Handling |
|---|---|---|---|
| R-17 | Similarity-based abstention does not transfer from templated dev tickets to free-language tickets (confidence AUC 0.95–1.0 on dev, 0.55–0.66 held-out); 36–55% of unanswerable held-out tickets still received evidence | held-out benchmark | ADR-0013 §5: sufficiency must be judged from content in M3/M4; thresholds are recorded but uncalibrated |
| R-18 | Injected-instruction runbooks are on-topic for natural queries ("operations notes", "maintenance mode") and are returned as evidence by every strategy | adversarial retrieval tests | retrieval is inert (data-only schema, read-only role, no egress); policy enforcement against model-following is M3/M4 and **not yet demonstrated** |
| R-19 | The generator's "conflict" topic (double-booking) has identical text in both active versions, so it is a duplicate, not a conflict | corpus review | governance is number-aware: only the retry-count and slot-length pairs are real conflicts; recorded in tests |
| R-20 | Near-duplicate grouping ignored a "(copy)" title suffix (one of three identical rate-limit runbooks was not collapsed) | unit test after the first held-out run | fixed; dev re-tuned (unchanged); first-run results kept (`reports/m2/results-first-run.json`) and disclosed |
| R-21 | The hand-labelled set is one AI reviewer who also designed the corpus tooling; two passes were in the same session, not a day apart | self-audit | stated in RUBRIC.md and every report; not removable without a human reviewer |
| R-22 | Held-out reveals dev inflation (Hit@1 0.86–0.97 dev vs 0.60–0.80 held-out) | benchmark | headline numbers are held-out only; dev is for parameters |
| R-23 | `ts_rank`/cosine/RRF/cross-encoder confidences have different scales and shift with ticket length | threshold analysis | per-strategy thresholds; no cross-strategy threshold claims |
| R-24 | Abstain-before-conflict can hide a conflict (TCK-8018 for lexical and hybrid) | held-out failure analysis | documented; not changed post hoc |
| R-25 | Coverage gap: M2 proves retrieval is inert, not that a model/approval workflow ignores injected text | scoping | A-I1-07 and A-I2-07 stay planned for M4 |
| R-26 | Chunking is trivial on this corpus (60 docs → 60 chunks): long-document chunk boundaries, section citations and ANN behaviour are untested | results review | stated; unit tests cover multi-section chunking |

## M3: R-1 status and new findings
**R-1 is resolved at this project's boundary** (ADR-0014): role-, tenant-, case-, expiry- and action-hash-bound approval records, enforced by the service and independently by the database; the frozen runtime is unchanged and its approver callback is wrapped (`runtime_bridge.py`, tested with the real `agent.security.Policy`). Residual: the xfail that documents the runtime's own limitation stays; approvals rely on a *mock* identity provider (symmetric HMAC claims), so real authentication is simulated.

| ID | Finding | How found | Handling |
|---|---|---|---|
| R-27 | M2 retrieval checked abstention before conflict, contradicting the frozen protocol; a known conflict could be hidden | M2 held-out failure analysis | corrected architecturally in M3; published results reproducible; cost disclosed (`docs/m2-conflict-order-rerun.md`) |
| R-28 | The M0 action schema accepted `case_id: "CASE-1"` and unbounded strings; no length caps, no key patterns | writing M3 validation | contract tightened (patterns, `maxLength`, `uniqueItems`); tests updated, none weakened |
| R-29 | A policy decision recorded earlier must not be trusted at execution time (facts change, e.g. cooldown, incident) | design review | the gateway re-evaluates policy from fresh facts on every execute |
| R-30 | `expire_due()` is a global sweeper: it expires every pending approval past its time, not just one case's | test run | by design (timeout = deny); callers must not assume it returns only their id |
| R-31 | Free text in action parameters can carry credentials or e-mail addresses | threat review | credentials: refused (`SECRET_IN_PARAMS`); addresses: masked in audit/events/evidence; the exact approved text is kept only in the control table needed to execute it |
| R-32 | A hash chain does not detect removal of the LAST events or the whole log | design analysis, tested | documented as tamper-evidence, not immutability; `anchor()` for an external anchor (not deployed here) |
| R-33 | The retrieval `knowledge` input to the policy is supplied by the caller; omitting it removes the escalate-on-conflict caution | design review | cannot create a permission (approval still required); M4 must derive it in trusted code |
| R-34 | The test loader must truncate tables that reference `cases`; this forced a loader TRUNCATE grant on three control tables (never on the audit log) | migration testing | documented; audit log has no FK to cases and no loader privilege |
| R-35 | Role passwords are cluster-wide and CI authenticates with passwords while local pgserver does not (M2's CI failure); M3 adds a fourth role | M2 CI, M3 setup | every test/script that bootstraps restores the shared environment's passwords |
| R-36 | The control service and the identity verifier hold broad power (all tenants' approvals; HMAC verifier can mint) | design | trusted, never model-facing; module-boundary and static tests; production needs separate credentials and an asymmetric IdP |
| R-37 | The SLA-breach rule trusts the `sla-monitor` author field of ticket history | design | acceptable for synthetic data; a real ticketing integration must guarantee that customers cannot author as the monitor |
| R-38 | Whether a runbook *applies* to the symptoms (semantic sufficiency) is not decided by any control | scoping | stated in each decision (`SEMANTIC_APPLICABILITY_NOT_ASSESSED`); human approver judges; real-model evaluation is separate |
| R-39 | A long customer-system call that outlives the 60 s lease will be treated as `uncertain` by a second request | design analysis | the second request never re-executes without confirmation (lookup) or a human; safe, but can strand a slow success as UNCERTAIN until reconciled |

## M4: new findings
| ID | Finding | How found | Handling |
|---|---|---|---|
| R-40 | The e-mail masking regex of M3 treated a citation such as `RBK-0019@2.0` as an address and masked it in case files/audit | first end-to-end case file | the TLD must now be alphabetic (a false-positive fix in `copilot.redact`; real addresses are still masked; M3 tests unchanged) |
| R-41 | Conflict-first retrieval (M2/M3) returned only the conflicting pair for an unrelated ticket and so hid the relevant document from the case (TCK-0064) | S8 scenario | opt-in `include_context` keeps the other best active matches next to conflict members for the workflow; frozen M2/M3 retrieval behaviour and results are unchanged by default |
| R-42 | The circuit breaker for external APIs never closed again (a Status API that recovered stayed 'unavailable') | S11 scenario | half-open after a cool-down on the injected clock; tested |
| R-43 | The scripted stand-in's heuristics were iterated during M4 after seeing scenario results (typo tolerance, topic gating, recent re-sync, applicability-aware draft) | development | the S1–S16 match rates are **development results of a stand-in**, not evidence of LLM quality or generalisation |
| R-44 | Scenario labels disagree with runbooks/data in 24 of 315 cases: S7 negative-answer topics (10/30: RBK-0034, RBK-0052), S4 accounts with an open carrier-gateway incident (11/25), S14 accounts with an open tracking incident (3/130) | scenario run | reported individually as LABEL_CONFLICT; expectations not edited |
| R-45 | A fooled model can mark everything applicable and turn an abstention into an ANSWER *draft* | adversarial test | non-executable and human-reviewed, but a misleading draft is still a harm path; only a real-model evaluation can say how often it happens |
| R-46 | Injected documents still reach the model prompt (they are on-topic evidence); only the architecture contains them | injection matrix | 0 invariant violations over 32 attack runs incl. detectors OFF; draft text can still be steered (see R-45) |
| R-47 | Hostile tickets were not recognised as out of policy by the stand-in in 10 cases (S2: 4, S3: 6) and ended safely as ANSWER/ESCALATE drafts; recognising intent is the model's job | scenario run | contained (no unsafe action); intent recognition by a real model is **not measured** |
| R-48 | Resume is by polling durable state; no worker/scheduler resumes cases automatically | design | M5/M6 |
| R-49 | The case file (jsonb) holds scrubbed ticket text, evidence excerpts and draft text; retention/erasure policy is undefined | design | document before any real data |
| R-50 | No real local model could be run here (no Ollama/local server); the integration is tested at its HTTP boundary only | environment | stated in every report; `scripts/run_m4_local_model.py` records or refuses honestly |
| R-51 | The static 'no SQL outside copilot/db' test exempts calls whose receiver is named `gateway` (`ControlGateway.execute` is not SQL) | CI | documented in the test; the workflow package is separately checked for no SQL/email/network imports |
