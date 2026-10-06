# ADR-0001: Fictional customer and synthetic data only

**Status:** Accepted

## Context
The project must be reproducible and must not depend on or expose private company information; it must also be credible as a customer scenario.

## Decision
All customer, account, ticket, runbook, incident and integration data is synthetic and generated from a seed by this repository. The customer is **Meridian Freight Systems (fictional)**; every dataset manifest declares `fictional: true` and that customer string. Contracts reject real-looking email domains (only `.example` / `.example.test`), IPv4 outside RFC 5737 documentation ranges, and non-555 phone numbers.

## Consequences
Reproducible, safe to publish. Cost: synthetic text is cleaner than real tickets, so results do not transfer to a real helpdesk; we say so wherever results are reported.

## Alternatives considered
Anonymised real tickets (rejected: leakage risk, not reproducible).
