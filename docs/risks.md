# Discovered risks and required changes to the approved specification

| ID | Finding | Evidence | Impact | Proposed handling |
|---|---|---|---|---|
| R-1 (DECIDED) | The frozen runtime's approver hook is a **bool callback** (`Policy.approver(name, args) -> bool`); it carries no role, expiry or action diff | `tests/test_dependency_pin.py::test_runtime_approver_hook_carries_role_and_diff` (strict xfail) | Role-aware approvals (I1) cannot be expressed inside the runtime | **Decided at M0 review: no change to the runtime; richer approvals in this project's own boundary (ADR-0006).** Wrap it: the approver callback looks up an approval record created by our approval service, which checks role/expiry/action hash. Documented here for approval; if a pre-tool hook with context proves necessary it is raised as a proposal first. |
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
