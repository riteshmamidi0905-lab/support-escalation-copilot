# Milestones (each verified and reviewed before the next)

| M | Scope | Exit criteria |
|---|---|---|
| M0 | architecture/ADRs, environment, data contracts, threat model, test strategy, CI | **this checkpoint** |
| M0.5/M1 | seeded synthetic generator; schema, roles, RLS, ScopeGuard, query catalogue; isolation tests incl. wrong-account attempts | I2 attacks A-I2-01..05 pass against real Postgres; dataset validates |
| M2 | retrieval strategies + evaluation on all four evidence classes (as applicable) | lexical/vector/hybrid compared; hand-labelled set authored blind |
| M3 | typed tools, policy engine, approvals, audit, idempotency | I1/I3/I4 attack tests pass |
| M4 | case workflow, diagnosis/plan/drafts, scenario suite S1–S16 | scenarios reach expected outcomes; failures inspected, not weakened |
| M5 | mocks with fault injection, breakers, observability, security suite | partial-failure behaviour demonstrated |
| M6 | UI, Compose deployment, runbook, demo (routine, approval-gated, adversarial, abstain/escalate) | clean-environment run; real-model evaluation (separate) if executed |
Portfolio stays frozen until a sufficiently verified milestone.
