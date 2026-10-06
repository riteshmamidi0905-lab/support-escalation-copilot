# Milestones (each verified and reviewed before the next)

| M | Scope | Exit criteria |
|---|---|---|
| M0 | architecture/ADRs, environment, data contracts, threat model, test strategy, CI | **done, approved at `ce931b7`** |
| M1 | seeded synthetic generator; schema, migrations, roles, forced RLS, signed scope, ScopeGuard, query catalogue, trusted intake, loader; adversarial isolation tests; clean rebuild | **done, approved at `f581140`** |
| M2 | retrieval strategies, evidence, evaluation | **this checkpoint**: A lexical / B vector / C hybrid / D rerank compared on a frozen 40-ticket hand-labelled held-out set (single AI reviewer) and a 300-ticket dev set; ADR-0013; 32 of 54 catalogued attacks executable |
| M3 | typed tools, policy engine, approvals, audit, idempotency | **this checkpoint**: deterministic policy, role-bound approvals (R-1 resolved at this boundary), idempotent gateway, hash-chained audit, mocks with fault injection; 61 of 68 attacks executable; four invariants hold on the clean-database run |
| M4 | case workflow, diagnosis/plan/drafts, scenario suite S1–S16 | scenarios reach expected outcomes; failures inspected, not weakened |
| M5 | mocks with fault injection, breakers, observability, security suite | partial-failure behaviour demonstrated |
| M6 | UI, Compose deployment, runbook, demo (routine, approval-gated, adversarial, abstain/escalate) | clean-environment run; real-model evaluation (separate) if executed |
Portfolio stays frozen until a sufficiently verified milestone.
