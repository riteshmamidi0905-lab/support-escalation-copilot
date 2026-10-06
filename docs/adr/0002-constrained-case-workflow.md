# ADR-0002: Constrained case workflow, not free-form agent planning

**Status:** Accepted

## Context
A customer deployment needs predictable, auditable behaviour; open-ended planning makes approvals, evaluation and failure analysis harder.

## Decision
The case is a fixed state machine (INTAKE → … → DRAFT_AND_CLOSE). The agent loop runs *within* a state with a narrow tool set and a typed output; transitions are decided by code. The runtime's planner module is not used.

## Consequences
Easier to test, explain and audit; less flexible. New situations need a new state or tool, which is the point.

## Alternatives considered
Free planning with guardrails (rejected for this use case).
