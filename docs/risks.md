# Discovered risks and required changes to the approved specification

| ID | Finding | Evidence | Impact | Proposed handling |
|---|---|---|---|---|
| R-1 | The frozen runtime's approver hook is a **bool callback** (`Policy.approver(name, args) -> bool`); it carries no role, expiry or action diff | `tests/test_dependency_pin.py::test_runtime_approver_hook_carries_role_and_diff` (strict xfail) | Role-aware approvals (I1) cannot be expressed inside the runtime | **No change to the runtime.** Wrap it: the approver callback looks up an approval record created by our approval service, which checks role/expiry/action hash. Documented here for approval; if a pre-tool hook with context proves necessary it is raised as a proposal first. |
| R-2 | The runtime's tracer/redactor knows only generic secret patterns; a DSN password passes through it | `test_runtime_tracer_gap_for_project_specific_formats_is_known` | I4 | Every log/audit/trace write in this project goes through `copilot.redact`; canary tests guard it. |
| R-3 | PostgreSQL FTS does not match `resync` to `re-sync` | `test_server_version_and_fulltext_search` | Lexical retrieval misses obvious variants | Expected finding for the lexical-vs-vector comparison; add normalisation/synonyms and measure, do not hide. |
| R-4 | The runtime installs top-level packages `agent` and `service` | `pip` layout | Namespace collisions | Never name our modules `agent` or `service`; our package is `copilot`. |
| R-5 | RLS-by-session-setting does not stop code that can run SQL from choosing its own scope | A-I2-05 | I2 | Fixed query catalogue; ScopeGuard; evaluate signed scope in M1 (ADR-0004). |
| R-6 | Local embedded Postgres (16.2 / pgvector 0.6.2) differs from the CI image's pgvector version | environment | subtle index/behaviour differences | CI is the source of truth; record versions in test output; avoid version-specific features. |
| R-7 | Docker is not available on the development machine | environment | the Compose path cannot be run locally | Verified in CI only; stated wherever relevant (as with the earlier project). |
| R-8 | A single hand-labeller | methodology | label bias | stated limitation; second review pass; document disagreements. |

**Spec changes requested at M0:** none required. Two refinements are recorded in ADRs: the case workflow uses the runtime loop but not its planner (ADR-0002), and signed scope may be added to tenant isolation (ADR-0004).
