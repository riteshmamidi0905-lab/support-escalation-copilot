# Architecture

This is what exists in the repository, drawn from the code (module names are real). Everything marked *simulated* is a deterministic stand-in for something a real deployment would have; see
[`real-vs-simulated.md`](real-vs-simulated.md). The original design intent is in [`spec.md`](spec.md); where this implementation differs, the table at the end says so.

## 1. System architecture

An operator opens a case; a fixed workflow gathers evidence, asks the model for **structured conclusions only**, and hands every proposed action to a deterministic control plane. Humans approve; the
control plane executes once; everything lands in an audit log and in operational telemetry.

```mermaid
flowchart TB
    OP["Operator browser<br/>simulated sign-in"]:::untrusted
    TXT["Ticket and runbook text<br/>untrusted data"]:::untrusted
    subgraph APP["Operator app: copilot.app"]
        WEB["UI and JSON API<br/>signed session, CSRF, strict CSP"]
        ACC["Server-side authorization<br/>signed grants, uniform 404"]
        WEB --> ACC
    end
    subgraph WF["Case workflow: copilot.workflow"]
        RUN["Case runner and state machine<br/>durable, versioned transitions"]
        REC["Recovery worker<br/>PostgreSQL leases"]
        REC --> RUN
    end
    EVID["Evidence: pgvector + lifecycle governance<br/>facts via fixed queries under signed scope"]
    MODEL["Model provider<br/>RuleCaseModel stand-in, not an LLM"]:::untrusted
    TRUST["Trust boundary<br/>schema, citation and grounding checks"]
    subgraph CP["Control plane: copilot.control"]
        GW["Gateway<br/>validate, approval check, idempotent execute"]
        POL["Policy engine<br/>facts and rules only"]
        APR["Approval service<br/>role, tenant, hash, expiry"]
        LED["Idempotency ledger"]
        GW --> POL
        GW --> APR
        GW --> LED
    end
    CUST["Customer systems<br/>deterministic mocks with fault injection"]:::simulated
    PG[("PostgreSQL 16 + pgvector<br/>tenant tables with forced RLS<br/>control tables, audit log, ops events")]
    OP --> WEB
    ACC --> RUN
    TXT --> RUN
    RUN -->|"read evidence"| EVID
    RUN -->|"stage prompt"| MODEL
    MODEL -->|"untrusted output"| TRUST
    TRUST -->|"validated proposals"| GW
    ACC -->|"approve or deny"| APR
    GW -->|"approved, once"| CUST
    RUN -->|"read-only checks"| CUST
    EVID -.-> PG
    CP -.->|"audit, approvals, ledger"| PG
    RUN -.->|"case file, telemetry"| PG
    classDef untrusted fill:#fde4e4,stroke:#9b1c1c,color:#111
    classDef simulated fill:#fff2d6,stroke:#6b4300,color:#111
    style APP fill:#e3f4e8,stroke:#0d4d22
    style WF fill:#e3f4e8,stroke:#0d4d22
    style CP fill:#e3f4e8,stroke:#0d4d22
```

## 2. Trust boundaries

What each side may do, and the one thing that may cross. Nothing a model or a document says is ever an authority.

```mermaid
flowchart LR
    subgraph UNT["Untrusted: can be hostile"]
        T1["Ticket text"]
        T2["Runbook text<br/>incl. injected documents"]
        T3["Model output<br/>any text, any JSON"]
        T4["Browser requests<br/>cookies, forms, URLs"]
        T5["Customer-system replies"]
    end
    subgraph BND["Deterministic boundary"]
        B1["Redact secrets,<br/>wrap as data"]
        B2["Schema, forbidden names,<br/>citations, grounding"]
        B3["Signed identity, CSRF, role,<br/>account grant, case state"]
        B4["Policy re-evaluation<br/>approval hash check"]
    end
    subgraph TRU["Trusted: decides"]
        C1["Signed tenant scope<br/>forced row-level security"]
        C2["State machine edges<br/>plus a database trigger"]
        C3["Approvals and<br/>idempotency ledger"]
        C4["Audit log<br/>hash chain"]
    end
    T1 --> B1
    T2 --> B1
    B1 -->|"as data"| T3
    T3 --> B2
    T5 --> B2
    T4 --> B3
    B2 --> B4
    B3 --> B4
    B4 --> C3
    C3 --> C4
    C1 -.-> B4
    C2 -.-> B4
    style UNT fill:#fde4e4,stroke:#9b1c1c
    style BND fill:#e2eefc,stroke:#0b3d91
    style TRU fill:#e3f4e8,stroke:#0d4d22
```

**Simulated, and therefore not evidence about the real thing:** the model (`RuleCaseModel`), the customer systems (mocks with fault injection), the identity provider (a persona picker) and the human reviewers (the author).

| Question | Answer in the code |
|---|---|
| What can the model control? | The *content* of a diagnosis (hypotheses, which evidence handles it cites, a disposition suggestion), proposed typed actions with parameters, and draft text. All of it is validated, and none of it is trusted. |
| What can it not control? | Tenant, role, approver, expiry, workflow state, the action vocabulary, whether an action is allowed, whether evidence is sufficient, whether anything is sent. A privileged field in its output makes the output invalid. |
| Where is a prompt injection stopped? | Not by detection (a regex flag is advisory). By architecture: the model has no tool that writes, the gateway has no input from model text, approvals need a human, and the draft cannot be sent. See [`security.md`](security.md). |

## 3. Case lifecycle

The edges are the *only* legal transitions; they are stored in the database and enforced by a trigger as well as in the application (A-I1-22). The model never names or sets a state.

```mermaid
stateDiagram-v2
    [*] --> NEW
    NEW --> INTAKE
    INTAKE --> SCOPE: ticket redacted
    SCOPE --> RETRIEVE: signed scope minted by trusted intake
    RETRIEVE --> VERIFY: evidence with provenance
    VERIFY --> DIAGNOSE: live status checked, or marked unverified
    DIAGNOSE --> PLAN: structured diagnosis accepted
    DIAGNOSE --> DRAFT: degraded (model or retrieval failed)
    PLAN --> REVIEW: an action needs approval
    PLAN --> DRAFT: nothing needs approval
    REVIEW --> EXECUTE: a matching approval is approved
    REVIEW --> DRAFT: all denied or expired
    EXECUTE --> DRAFT: results recorded (incl. UNCERTAIN)
    DRAFT --> CLOSED: ANSWER or APPROVAL executed
    DRAFT --> REFUSED: outside policy
    DRAFT --> ABSTAINED: insufficient evidence or clarify
    DRAFT --> ESCALATED: routed to engineering
    DRAFT --> HANDED_OFF: degraded, denied, expired, uncertain
    FAILED : FAILED - reachable from every state INTAKE to DRAFT, reason recorded
    CLOSED --> [*]
    REFUSED --> [*]
    ABSTAINED --> [*]
    ESCALATED --> [*]
    HANDED_OFF --> [*]
    FAILED --> [*]
```

A case waiting in `REVIEW` costs nothing: a recovery worker polls on a back-off and the approval decision wakes it. A restart resumes from the stored state; every stage persists its output before it advances.

## 4. Approval and execution sequence

A proposal is not an effect. Policy is evaluated twice (at proposal and again, from fresh facts, at execution); the approval is bound to the exact action by hash; the write carries an idempotency key; an
unknown outcome is never retried blindly.

```mermaid
sequenceDiagram
    autonumber
    participant M as Model stage (untrusted output)
    participant G as Gateway
    participant P as Policy engine
    participant A as Approval service
    participant H as Human approver (signed identity)
    participant L as Idempotency ledger
    participant C as Customer system (simulated)
    participant X as Audit log
    M->>G: proposed action (raw JSON)
    G->>G: validate: schema, forbidden names, secrets, tenant, case
    G->>P: validated action + facts read under signed scope
    P-->>G: REQUIRE_APPROVAL (reasons, sufficiency, required role)
    G->>A: request approval: canonical action, hash, role, expiry, evidence shown
    G->>X: action_proposed, policy_decided, approval_requested
    H->>A: approve or deny (role, account grant, not requester, not amender)
    A->>X: approval_decided (hash-bound record, final)
    Note over G,P: case resumes, nothing has been executed so far
    G->>G: execute(action, approval_id): validate again
    G->>L: idempotency peek (replay returns the original result)
    G->>P: policy re-evaluated from fresh facts
    G->>A: approval check: status, role, expiry, action hash equals approved hash
    G->>L: claim key (one executor wins)
    G->>C: effect call with the idempotency key
    alt confirmed
        C-->>G: result
        G->>L: succeeded
    else timeout or unknown outcome
        G->>C: lookup by key, only if the system supports it
        G->>L: uncertain (never retried blindly)
        Note over H,L: a human reconciles: applied or not applied, with a recorded note
    end
    G->>X: action_executed or execution_uncertain
```

## 5. Modules

| Module | Responsibility |
|---|---|
| `copilot.contracts`, `contracts/*.schema.json` | JSON Schemas for data, actions, audit events, case files, the public claims manifest; cross-record rules |
| `copilot.data` | seeded synthetic generator (40 accounts, 300 tickets, 60 runbooks, 119 integrations, 12 incidents) and design checks |
| `copilot.scope`, `copilot.db` | signed tenant scope; migrations, roles, forced RLS, scoped sessions, fixed query catalogue, trusted intake, loader |
| `copilot.retrieval` | chunking, FTS / vector / hybrid / rerank strategies, lifecycle and conflict governance, evaluation harness |
| `copilot.control` | actions, policy, approvals, identity, idempotency ledger, audit, gateway, mocks, access, ops events, metrics |
| `copilot.workflow` | state machine, runner, model I/O (structured output, repair, retries), trust boundary, grounding, recovery, providers |
| `copilot.app` | operator UI, operator actions (approve / amend / review / reconcile), demo entry points |
| `agent.*` (external, pinned) | the frozen agent runtime: provider abstraction, structured-output repair, retries, budgets, tracer. Imported, never modified |

## 6. Boundaries that matter
- **Runtime boundary:** `agent.*` is a dependency pinned to a commit and never modified here; gaps are logged in [`risks.md`](risks.md) (R-1).
- **Data boundary:** tenant tables are reachable only through the RLS-protected application role under a signed scope; the control tables are not visible to it at all.
- **Action boundary:** the only write vocabulary is five typed actions (two propose-only, three human-gated); there is no email action by construction.
- **SQL boundary:** a static test fails if SQL is executed outside `copilot/db` (plus a short allow-list of control/workflow modules that use fixed, parameterised statements).

## 7. Where the implementation differs from the original specification
| Original design ([`spec.md`](spec.md)) | Implemented | Why |
|---|---|---|
| About 400 accounts and a support org of ~25 | 40 synthetic accounts, 300 tickets, 60 runbooks, 119 integrations, 12 incidents (the spec called its counts "design choices, not results") | enough to carry designed difficulty (stale and conflicting runbooks, injected documents, cross-tenant probes) while staying reproducible on a laptop |
| Case service and mock APIs as FastAPI services | stdlib WSGI + Jinja2 for the UI; mocks are in-process Python objects with fault injection | one dependency instead of a framework; the contract and the faults are what matter (ADR-0016) |
| Policy as YAML | policy rules in Python over facts, plus a small JSON config (`policy_config.json`); credit thresholds come from the account's contract | rules need typed facts and are unit-tested; thresholds are customer data |
| Hybrid retrieval as the hypothesised default | vector retrieval + governance; hybrid and rerank kept as selectable, measured alternatives | measured on a frozen held-out set (ADR-0013) |
| Notifier (Slack-like) mock | a mock engineering-escalation system that accepts only internal destinations | the system must never message customers (I3) |
| "Actions disabled until acknowledged" for low-confidence plans | unverified or degraded state disables actions; risky drafts need itemised acknowledgement; evidence sufficiency is judged from content and policy, not similarity | similarity confidence was shown not to detect missing evidence (M2) |
| Reproducible on a laptop with Docker | Compose provides PostgreSQL + pgvector; an embedded PostgreSQL (`pgserver`) serves laptops without Docker; the application runs in a virtualenv, it is not containerised | restraint: no deployment infrastructure was needed to demonstrate the design |
