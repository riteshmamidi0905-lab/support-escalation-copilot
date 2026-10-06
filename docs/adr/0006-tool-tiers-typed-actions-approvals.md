# ADR-0006: Tool tiers, typed actions, role-aware approvals, idempotency

**Status:** Accepted

## Context
Writes must be authorised by humans and be safe to retry.

## Decision
Tools are read / propose / gated-write / forbidden. The only write vocabulary is `contracts/action.schema.json` (five action types, each bound to a required role); there is no email-sending action. Approvals bind to the exact action hash, an approver role, an expiry (timeout = deny), and idempotency key. The runtime's bool approver callback is wrapped by a richer approval service rather than changed (R-1).

## Consequences
Strong guarantees (I1, I3). Cost: more ceremony per action.

## Alternatives considered
Free-text actions (rejected).
