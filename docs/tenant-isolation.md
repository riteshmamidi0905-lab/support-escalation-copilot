# Tenant isolation design (implemented in M1)

**Invariant I2:** no data of one account is exposed in a case about another account. Zero cross-tenant exposure is an absolute requirement, not a statistical target. Five layers, because each has a known weakness; each is exercised by attack tests (see `docs/threat-model.md`, A-I2-*).

| # | Layer | Mechanism | Implemented in | Weakness (and which layer covers it) |
|---|---|---|---|---|
| 1 | Trusted case binding | a case is created by the intake role from a **ticket row**; its account is the row's account. Ticket text, model output and request parameters never choose it | `copilot/db/intake.py` | a bug here → layers 2–4 |
| 2 | Signed scope | HMAC over (account, case, expiry, key id), minted only by the case service; verified **inside the database** with a key the app role cannot read; case↔account consistency checked in the database | `copilot/scope.py`, migration 004 | a valid unexpired token is usable by someone who can run SQL (short TTL; layer 5) |
| 3 | Forced RLS | policies on every tenant table keyed on the verified scope; `FORCE ROW LEVEL SECURITY`; app role is non-owner, NOSUPERUSER, NOBYPASSRLS, read-only, no DDL; no scope ⇒ zero rows | migrations 004–005 | superuser/BYPASSRLS roles see everything → role audit |
| 4 | ScopeGuard | refuses any request whose account differs from the verified scope, before SQL | `copilot/scope.py`, `copilot/db/session.py` | coverage → layer 3 |
| 5 | Fixed query catalogue | the only SQL the app role runs: constants with bound parameters; unknown names/raw SQL rejected; no SQL executed outside `copilot/db`, none built by formatting (static test) | `copilot/db/queries.py` | catalogue review discipline |
Plus operational controls: `audit_roles` / `audit_privileges` (exact-match privilege audit, run in tests and available at startup), pool `DISCARD ALL` on return, `prepare_threshold=None`, statement timeout 10 s.

## Schema (migrations 001–005)
Tenant tables (RLS): `accounts, account_contacts, contracts, integrations, tickets, ticket_history, incident_accounts, cases`. Child tables use **composite foreign keys** `(ticket_id, account_id) → tickets(ticket_id, account_id)` so a row cannot reference another account's parent. Global knowledge (no RLS): `incidents` (note: **no affected-accounts column**; `incident_accounts` carries that, tenant-scoped), `deployments`, `release_notes`, `runbook_docs` (with a generated `tsvector` and GIN index; default English FTS, no normalisation). **Not in the database at all:** synthetic labels, scenario ids, the `adversarial` document flag — the runtime must not be able to read the answer key.

## Roles
| Role | Login | Can do |
|---|---|---|
| `copilot_owner` | no | owns objects; FORCE'd like everyone |
| `copilot_app` | yes | SELECT only (RLS-scoped tenant tables + global tables); EXECUTE `scope_account()`; read-only transactions by default; 10 s statement timeout |
| `copilot_intake` | yes | read tickets (to learn a ticket's account), create cases; nothing else — **trusted, never model-facing** |
| `copilot_loader` | yes | INSERT/TRUNCATE the data tables (demo/test loading); reads nothing |
| admin (migrations/bootstrap) | out of band | not an application role |
Passwords and the signing key come from the environment at bootstrap; no secret is in a migration file.

## Residual risks
See ADR-0012: token replay within its short lifetime by someone who already has SQL execution; signing-key custody and rotation; intake role reach. M5 adds audit-query scoping (A-I2-10) and M2 retrieval scoping (A-I2-09).
