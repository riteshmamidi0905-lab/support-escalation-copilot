# Milestones (each verified and reviewed before the next)

| M | Scope | Exit criteria |
|---|---|---|
| M0 | architecture/ADRs, environment, data contracts, threat model, test strategy, CI | **done, approved at `ce931b7`** |
| M1 | seeded synthetic generator; schema, migrations, roles, forced RLS, signed scope, ScopeGuard, query catalogue, trusted intake, loader; adversarial isolation tests; clean rebuild | **done, approved at `f581140`** |
| M2 | retrieval strategies, evidence, evaluation | **this checkpoint**: A lexical / B vector / C hybrid / D rerank compared on a frozen 40-ticket hand-labelled held-out set (single AI reviewer) and a 300-ticket dev set; ADR-0013; 32 of 54 catalogued attacks executable |
| M3 | typed tools, policy engine, approvals, audit, idempotency | **done, approved at `39ea8c0`**: deterministic policy, role-bound approvals (R-1 resolved at this boundary), idempotent gateway, hash-chained audit, mocks with fault injection; 61 of 68 attacks executable; four invariants hold on the clean-database run |
| M4 | case workflow, diagnosis/plan/drafts, scenario suite S1–S16 | **done, approved at `626c90a`**: durable state machine, frozen-runtime integration, structured model stages behind a trust boundary, S1–S16 as 315 end-to-end case executions with a scripted stand-in (not an LLM), injection/fault/recovery suites; four invariants hold; no real-model run |
| M5 | operator experience, observability, real-model readiness | **this checkpoint**: server-rendered operator UI with approval/amend/review/reconcile UX and demo entry points A-F; server-side authorization (signed grants, uniform 404); A-I2-10 and 16 new attacks (92 of 92 catalogued attacks executable); observability from real events; PostgreSQL recovery worker with concurrent-worker tests; draft-grounding checks + adversarial evaluation (residual weakness preserved); real-model readiness (`ConfiguredProvider`, frozen protocol, probe) — **real-model evaluation NOT executed**; browser verification at desktop and 375 px |
| M6 | scope set by the next approval; originally UI + Compose deployment + runbook + demo (UI and the demo entry points were brought forward into M5 by direction) | clean-environment run; real-model evaluation (separate) only if a legitimate local runtime exists. **Not started.** |
Portfolio stays frozen until a sufficiently verified milestone.
