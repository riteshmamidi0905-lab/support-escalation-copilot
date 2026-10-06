# ADR-0009: pytest, with adversarial invariant tests

**Status:** Accepted

## Context
Fixtures (DB, roles, datasets) and parametrised adversarial cases are central; `unittest` was right for the zero-dependency runtime but is awkward here.

## Decision
pytest + ruff. Tests marked `invariant` *attempt* the violation (see the attack catalogue) and positive controls prove detectors can fail. DB tests run against real PostgreSQL in CI and must not skip there.

## Consequences
Readable adversarial tests. Extra dev dependency.

## Alternatives considered
unittest (rejected).
