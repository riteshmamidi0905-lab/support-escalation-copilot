# ADR-0012: Signed trusted scope for tenant context

**Status:** Accepted (M1; resolves risk R-5 and the open decision in ADR-0004)

## Context
RLS keyed on a plain session setting protects against application bugs, but any code able to run SQL as the application role can set that setting to another account (demonstrated at M0, attack A-I2-05). Tenant identity must come from trusted case context, not from model output, ticket text or request parameters.

## Decision
- A **Scope** = `(account_id, case_id, expiry, key_id, HMAC-SHA256 signature)`. It is minted only by the trusted case service (`copilot.scope.Signer`) from a **case row** that the trusted `copilot_intake` role created from a **ticket row**. The only input to intake is a ticket id; the account comes from the database row it names.
- The database verifies the scope itself: `copilot_private.scope_account()` (SECURITY DEFINER, pinned `search_path`) recomputes the HMAC with a key the application role cannot read, checks expiry, and checks that the case exists **and belongs to that account**. RLS policies use only this function's result. No valid scope => NULL => no rows.
- HMAC is implemented in SQL from the built-in `sha256()` (no extension to install or trust) and cross-checked against Python's `hmac` and RFC 4231 vectors.
- The application role may EXECUTE exactly that one function. There is no signing function it can call (so no forging oracle); it cannot read `scope_keys`.
- Scopes are applied transaction-locally, tokens are short-lived (default 120 s, max 900 s), the pool wipes session state on return (`DISCARD ALL`), and queries come only from a fixed catalogue.
- `ScopeGuard` (application layer) verifies the signature and refuses any requested account that differs from the scope before SQL is built; the database check is independent.

## Consequences
- Forging, altering, replaying across cases, expiring, mismatching case/account, or setting scope from SQL all fail closed (each has an attack test).
- **Residual risks, stated:** (1) a *valid, unexpired* token can be used by whoever can already run SQL as the app role, for its remaining lifetime; (2) the signing secret lives with the case service and in `scope_keys` (database owner/admin can read it) — key management and rotation (`key_id` supports it) are deployment concerns; (3) the intake role can read ticket→account mapping for all tickets, so it must never be reachable from model-facing code (enforced by module boundaries and a test; in deployment, by separate credentials/process).
- Cost: an extra function call per statement (evaluated once via a sub-select) and a case lookup.

## Alternatives
Plain session setting (rejected: forgeable by SQL); database-per-tenant (rejected: operational weight); per-tenant DB roles (rejected: connection/role explosion).
