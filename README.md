# support-escalation-copilot

A **forward-deployed-engineering** project: a support-escalation copilot for a **fictional** B2B software vendor, **Meridian Freight Systems**. All customers, accounts, tickets, runbooks, incidents and integrations are synthetic and generated reproducibly. Nothing here is real company data.

It is a case workflow, not a chatbot: given a ticket it assembles tenant-scoped evidence, proposes a diagnosis with citations, drafts a reply and an action plan, and executes only what a human approves — and it is designed to refuse, request approval, escalate or say "insufficient evidence" in specified situations.

**Status: M5 — operator experience, observability and real-model readiness.** A server-rendered operator UI (no JavaScript) shows each case end to end — evidence with provenance, diagnosis, contradictions, policy decisions, the exact action an approver is asked to approve, execution results, the audit trail — and every read and action is authorized again on the server (the browser is not trusted). Demo entry points A–F run real cases on synthetic data. Observability is derived from real events; a PostgreSQL recovery worker resumes cases safely; drafts are checked and always need human review. **The model is still a deterministic stand-in, not an LLM, and real-model evaluation has NOT been executed** (no local runtime; protocol frozen). See [`docs/m5-results.md`](docs/m5-results.md).

## Four absolute invariants
1. No gated action without approval. 2. No cross-tenant data exposure. 3. No customer email is ever sent. 4. No secret appears in logs.
Tests *attempt* to violate them ([attack catalogue](docs/threat-model.md)).

## Read in this order
[`docs/spec.md`](docs/spec.md) (approved specification) · [`docs/architecture.md`](docs/architecture.md) · [`docs/adr/`](docs/adr/README.md) · [`docs/threat-model.md`](docs/threat-model.md) · [`docs/tenant-isolation.md`](docs/tenant-isolation.md) · [`docs/data-contracts.md`](docs/data-contracts.md) · [`docs/test-strategy.md`](docs/test-strategy.md) · [`docs/evaluation-methodology.md`](docs/evaluation-methodology.md) · [`docs/risks.md`](docs/risks.md) · [`docs/milestones.md`](docs/milestones.md) · M5: [`docs/m5-results.md`](docs/m5-results.md) · [`docs/m5-demos.md`](docs/m5-demos.md) · [`docs/m5-browser-verification.md`](docs/m5-browser-verification.md) · [`docs/m5-draft-steering.md`](docs/m5-draft-steering.md) · [`docs/m5-real-model-readiness.md`](docs/m5-real-model-readiness.md) · [`docs/real-vs-simulated.md`](docs/real-vs-simulated.md)

## Try the operator UI (synthetic data, simulated sign-in)
```bash
make setup
python scripts/with_local_pg.py python scripts/run_demo_server.py     # then open http://127.0.0.1:8765/login
```
Pick a persona (Lee/Tier-2, Omar/manager, Rina/on-call SRE, Sam/unrelated tenant, Ria/auditor) and open a demo case from *Demo cases* ([`docs/m5-demos.md`](docs/m5-demos.md)).

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
