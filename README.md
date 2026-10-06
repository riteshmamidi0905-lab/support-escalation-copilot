# support-escalation-copilot

A **forward-deployed-engineering** project: a support-escalation copilot for a **fictional** B2B software vendor, **Meridian Freight Systems**. All customers, accounts, tickets, runbooks, incidents and integrations are synthetic and generated reproducibly. Nothing here is real company data.

It is a case workflow, not a chatbot: given a ticket it assembles tenant-scoped evidence, proposes a diagnosis with citations, drafts a reply and an action plan, and executes only what a human approves — and it is designed to refuse, request approval, escalate or say "insufficient evidence" in specified situations.

**Status: M1 — synthetic customer environment + tenant boundary.** The deterministic Meridian dataset, the PostgreSQL schema with forced RLS and database-verified signed scope, and the adversarial isolation tests exist. There is no retrieval, agent workflow, approval service or UI yet, and **no performance result of any kind**. See [`docs/real-vs-simulated.md`](docs/real-vs-simulated.md).

## Four absolute invariants
1. No gated action without approval. 2. No cross-tenant data exposure. 3. No customer email is ever sent. 4. No secret appears in logs.
Tests *attempt* to violate them ([attack catalogue](docs/threat-model.md)).

## Read in this order
[`docs/spec.md`](docs/spec.md) (approved specification) · [`docs/architecture.md`](docs/architecture.md) · [`docs/adr/`](docs/adr/README.md) · [`docs/threat-model.md`](docs/threat-model.md) · [`docs/tenant-isolation.md`](docs/tenant-isolation.md) · [`docs/data-contracts.md`](docs/data-contracts.md) · [`docs/test-strategy.md`](docs/test-strategy.md) · [`docs/evaluation-methodology.md`](docs/evaluation-methodology.md) · [`docs/risks.md`](docs/risks.md) · [`docs/milestones.md`](docs/milestones.md)

## Develop
```bash
python -m venv .venv && . .venv/bin/activate
make setup          # editable install incl. the pinned agent runtime (ai-agent-from-scratch @ 231b186)
make lint test      # no database needed
make test-db        # ephemeral local PostgreSQL with pgvector (no Docker needed)
make generate       # regenerate the committed dataset (same seed => identical bytes)
make rebuild        # clean database: migrate, bootstrap, generate, validate, load, tenant sweep (needs the COPILOT_* env vars, see .env.example)
# or with Docker:  cp .env.example .env && make db-up
```
The agent runtime is a dependency pinned to a commit and **never modified** here.
