# Tenant isolation design

**Invariant I2:** no data of one account is exposed in a case about another account. Defence in depth, because each layer has a known weakness.

| Layer | Mechanism | Defends against | Known weakness |
|---|---|---|---|
| 1. Case binding | a case is bound to exactly one `account_id` derived **server-side from the ticket**, never from model output or request parameters | model/user choosing an account | a bug in binding code |
| 2. ScopeGuard | every query/tool call is checked against the case's account before execution; mismatches are refused and audited (`isolation_violation_blocked`) | app passing a wrong id (A-I2-02/08) | only as good as its coverage; hence layer 3 |
| 3. PostgreSQL RLS | policies on tenant tables use `current_setting('app.account_id', true)`; **FORCE ROW LEVEL SECURITY**; app connects as a non-owner, `NOSUPERUSER NOBYPASSRLS` role | wrong WHERE clauses, forgotten filters | any code that can run SQL can set its own scope (A-I2-05) |
| 4. Fixed query catalogue | only parameterised, reviewed statements; no model- or user-authored SQL | layer-3 weakness | catalogue review discipline |
| 5. Signed scope (to be decided in M1) | HMAC over (account, case, expiry) verified inside the database with a secret the app role cannot read | an attacker with SQL execution forging scope | key management; extra complexity — adopt only if M1 measurements/tests justify |

## Rules (each enforced by a test)
1. Scope is set with `set_config('app.account_id', $1, **true**)` (transaction-local). Session-level scope leaks across pooled connections (A-I2-03 shows it).
2. No scope set ⇒ **zero rows** (default deny; A-I2-01).
3. Tenant tables are `FORCE`d; owners are not exempt (A-I2-04).
4. Global knowledge (runbooks, release notes, deployments, incidents) is not tenant data; **ticket history, integrations, contracts, accounts, tickets** are. Incident↔account links are tenant-scoped.
5. Retrieval never returns another account's ticket history (A-I2-09, M2); audit queries are scoped too (A-I2-10, M5).
6. Pool hygiene: a connection is never returned to the pool inside a transaction that set scope; tests check reuse.

## Established at M0 (tests/db/test_environment_spike.py)
Default-deny, scoped visibility, transaction-local reset, the session-level leak, owner bypass without FORCE, caller-set scope, and the app role's lack of write/DDL privileges are all demonstrated against real PostgreSQL with a real non-owner login role.
