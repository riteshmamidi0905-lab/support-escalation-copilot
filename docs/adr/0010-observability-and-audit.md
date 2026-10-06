# ADR-0010: Structured logs, traces, metrics and an append-only hash-chained audit log

**Status:** Accepted

## Context
Operators and auditors need to see what the system did and why, without secrets or hidden reasoning.

## Decision
Per-case trace with a span per state/tool; JSON logs with case/run/request ids; metrics for latency, errors, retries, approvals, refusals, abstentions; audit events defined in `contracts/audit_event.schema.json` (hash chain; payloads redacted by `copilot.redact`). Only structured decisions and concise user-facing rationale are recorded; hidden chain-of-thought is never stored or shown.

## Consequences
Full traceability. Cost: write discipline.

## Alternatives considered
Plain logging (rejected).
