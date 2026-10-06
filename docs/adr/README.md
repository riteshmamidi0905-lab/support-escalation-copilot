# Architecture decision records

- [0001-fictional-customer-and-synthetic-data](0001-fictional-customer-and-synthetic-data.md) — Fictional customer and synthetic data only
- [0002-constrained-case-workflow](0002-constrained-case-workflow.md) — Constrained case workflow, not free-form agent planning
- [0003-runtime-as-pinned-dependency](0003-runtime-as-pinned-dependency.md) — Reuse the agent runtime as a pinned, unmodified dependency
- [0004-postgres-rls-tenancy](0004-postgres-rls-tenancy.md) — PostgreSQL with row-level security for tenancy
- [0005-retrieval-strategies-are-pluggable](0005-retrieval-strategies-are-pluggable.md) — Retrieval strategies are independently selectable and evaluated
- [0006-tool-tiers-typed-actions-approvals](0006-tool-tiers-typed-actions-approvals.md) — Tool tiers, typed actions, role-aware approvals, idempotency
- [0007-evidence-classes-never-merged](0007-evidence-classes-never-merged.md) — Evaluation evidence classes are never merged into one number
- [0008-model-providers](0008-model-providers.md) — Deterministic and scripted providers first; local model later
- [0009-testing-approach](0009-testing-approach.md) — pytest, with adversarial invariant tests
- [0010-observability-and-audit](0010-observability-and-audit.md) — Structured logs, traces, metrics and an append-only hash-chained audit log
- [0011-minimal-server-rendered-ui](0011-minimal-server-rendered-ui.md) — Minimal server-rendered operational UI
- [0012-signed-trusted-scope](0012-signed-trusted-scope.md) — Signed trusted scope for tenant context
- [0013-retrieval-architecture](0013-retrieval-architecture.md) — Vector search plus lifecycle governance; similarity is not a sufficiency detector
- [0014-policy-approvals-idempotency-audit](0014-policy-approvals-idempotency-audit.md) — Deterministic policy, role-bound approvals, idempotent execution, tamper-evident audit (M3); ADR-0013 control-order addendum
- [0015-case-workflow-and-model-trust-boundary](0015-case-workflow-and-model-trust-boundary.md) — Case state machine, model stages and the model trust boundary (M4)

Format: context · decision · consequences · alternatives. An ADR changes only by a new ADR that supersedes it.
