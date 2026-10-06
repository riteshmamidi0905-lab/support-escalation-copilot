# Architecture (target) and what exists at M0

Target architecture. Implemented so far (M1): contracts, the synthetic generator, the database (schema, roles, forced RLS, signed scope), scoped sessions, the fixed query catalogue, trusted intake, loader, redaction, the invariant catalogue and CI. Everything else is designed (see `real-vs-simulated.md`).

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
| `copilot.data`, `copilot.scope` | seeded synthetic generator + design checks; signed scope | **M1 (done)** |
| `copilot.db` | migrations, roles, RLS, scoped sessions, query catalogue, intake, loader, role/privilege audit, rebuild | **M1 (done)** |
| `copilot.retrieval` | FTS, vector, hybrid strategies + eval | M2 |
| `copilot.tools`, `copilot.policy`, `copilot.approvals`, `copilot.audit` | typed tools, role-aware policy, approval workflow, audit | M3 |
| `copilot.case` | workflow, diagnosis, plan, drafts | M4 |
| `copilot.control.{mocks,ops,metrics,access}` | fault-injectable systems; append-only ops events; metrics derived from real tables; server-side read authorization | M3 / **M5 (done)** |
| `copilot.workflow.{recovery,grounding}` | PostgreSQL lease recovery worker; deterministic draft grounding checks | **M5 (done)** |
| `copilot.app` | operator UI (WSGI + Jinja2, no JavaScript), approval/amend/review/reconcile actions, demo entry points | **M5 (done)** |
| Compose + runbook + deployment | | M6 |

## Boundaries that matter
- **Runtime boundary:** `agent.*` (frozen) is imported, never modified. Gaps are logged in `risks.md` and raised for approval.
- **Package names:** the runtime installs top-level packages `agent` and `service`; this project therefore never uses those names (risk R-4).
- **Data boundary:** tenant tables are reachable only through the RLS-protected app role.
- **Action boundary:** the only write vocabulary is `contracts/action.schema.json`; there is no email action by construction.
