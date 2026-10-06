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

## Decision R-1 (approved after M0): richer approvals live in this project's boundary
The frozen runtime stays unchanged; its boolean approver callback is **not** treated as authorization. Approvals are records in this project's own approval/policy boundary and must bind at minimum: the required **role** and the approving **actor**, an **expiry**, the **exact typed action and its hash**, and the **decision**. A changed action (any param, evidence ref or idempotency key) produces a different hash and therefore requires a **new approval**. The runtime's callback only looks up such a record; it never grants anything by returning `True` on its own. Implementation: M3. Until then the strict expected-failure test `test_runtime_approver_hook_carries_role_and_diff` stays, because it documents the runtime limitation the wrapper exists to work around.
