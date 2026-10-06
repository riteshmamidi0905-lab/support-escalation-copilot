# Architecture (target) and what exists at M0

Everything below is **designed**; at M0 only the contracts, the database environment spike, redaction, the invariant catalogue and CI exist (see `real-vs-simulated.md`).

```
 Ticketing (mock) ─► Case Service (FastAPI) ──► Agent runtime ──► Tool Gateway ──► Ops Postgres (RLS, read-only app role)
   (system of record)   │ state machine          (pinned,          │ typed tools    ├─► Status API (mock, fault injection)
                        │ approvals queue         unmodified)       │ retries,       ├─► Carrier API (mock, fault injection)
 Approver UI ◄──────────┤                                           │ breaker,       └─► Ticketing/Notifier (mock) [writes gated]
 (minimal server-       ▼                                           │ idempotency
  rendered)        Policy Engine (YAML: tiers, thresholds, roles) ◄─┘
                   Retrieval Service (Postgres FTS + pgvector; lexical | vector | hybrid, independently selectable)
                   Audit log (append-only, hash-chained) · Observability (traces, metrics, structured logs) · Eval harness
```

## Case workflow (state machine)
`INTAKE → SCOPE → RETRIEVE → VERIFY → DIAGNOSE → PLAN → REVIEW → EXECUTE → DRAFT_AND_CLOSE`, with terminal outcomes `ANSWER | REFUSE | APPROVAL | ESCALATE | INSUFFICIENT_EVIDENCE | CLARIFY | DEGRADED`. The agent acts *inside* states; the workflow, not the model, decides what happens next. Every transition is an audit event.

## Modules (planned) and the milestone that builds them
| Module | Responsibility | Milestone |
|---|---|---|
| `copilot.contracts` | schemas + cross-record rules (exists) | M0 |
| `copilot.redact`, `copilot.invariants` | redaction, invariant/attack catalogue (exist) | M0 |
| `copilot.data` | seeded synthetic generator, loaders | M0.5 → M1 |
| `copilot.db` | migrations, roles, RLS, ScopeGuard, query catalogue | M1 |
| `copilot.retrieval` | FTS, vector, hybrid strategies + eval | M2 |
| `copilot.tools`, `copilot.policy`, `copilot.approvals`, `copilot.audit` | typed tools, role-aware policy, approval workflow, audit | M3 |
| `copilot.case` | workflow, diagnosis, plan, drafts | M4 |
| `copilot.mocks`, `copilot.observability` | fault-injectable systems, traces/metrics | M5 |
| `copilot.ui` + Compose + runbook | operational UI, deployment | M6 |

## Boundaries that matter
- **Runtime boundary:** `agent.*` (frozen) is imported, never modified. Gaps are logged in `risks.md` and raised for approval.
- **Package names:** the runtime installs top-level packages `agent` and `service`; this project therefore never uses those names (risk R-4).
- **Data boundary:** tenant tables are reachable only through the RLS-protected app role.
- **Action boundary:** the only write vocabulary is `contracts/action.schema.json`; there is no email action by construction.
