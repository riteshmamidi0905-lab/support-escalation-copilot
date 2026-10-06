# ADR-0003: Reuse the agent runtime as a pinned, unmodified dependency

**Status:** Accepted

## Context
`ai-agent-from-scratch` is verified and frozen at SHA 231b186; modifying it silently would invalidate its evidence.

## Decision
Installed from git at the full commit SHA; `tests/test_dependency_pin.py` fails if the installed commit differs. Used: model-provider interface, structured-output repair, the loop with budgets/retries/loop-detection, tracer, eval harness, policy/approver concept. Not used: its HTTP service, file/calculator tools, rule model, planner. Gaps are recorded in `risks.md` and raised for approval; the runtime is never edited here.

## Consequences
Honest provenance. Cost: namespace pollution (`agent`, `service` top-level packages) and a bool-only approver hook (R-1, R-4).

## Alternatives considered
Vendoring a copy (rejected: drift); extending the runtime in place (rejected: frozen).
