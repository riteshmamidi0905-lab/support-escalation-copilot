# ADR-0004: PostgreSQL with row-level security for tenancy

**Status:** Accepted

## Context
Tenant isolation must hold even when application code has a bug.

## Decision
Tenant tables use RLS keyed on a transaction-local `app.account_id`, FORCE'd, accessed by a non-owner NOSUPERUSER NOBYPASSRLS role. ScopeGuard checks scope in the app first. Only fixed parameterised queries exist. The environment spike (M0) demonstrated the mechanism and its limits; the *signed scope* hardening is decided in M1 on evidence.

## Consequences
Isolation is enforced below the application. Limit: a caller who can execute SQL can set its own scope (T-I2-5), so SQL authorship is closed off and signed scope is evaluated.

## Alternatives considered
Application-layer filtering only (rejected); database-per-tenant (rejected: operational weight for this scope).

## Update (M1)
The signed-scope hardening described above was adopted: see [ADR-0012](0012-signed-trusted-scope.md). RLS remains an independent defence-in-depth layer: policies are FORCE'd and keyed on the verified scope; the application role is read-only, owns nothing and cannot DDL.
