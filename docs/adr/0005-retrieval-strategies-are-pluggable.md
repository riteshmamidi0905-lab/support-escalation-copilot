# ADR-0005: Retrieval strategies are independently selectable and evaluated

**Status:** Accepted

## Context
The claim 'hybrid retrieval is better' must be measured, not assumed. M0's spike already showed lexical search missing `resync` vs `re-sync`.

## Decision
`copilot.retrieval` exposes `lexical`, `vector` and `hybrid` (and optional rerank) behind one interface, each runnable alone. Documents carry version/status/effective-date/provenance; retrieval returns *all* competing versions with metadata and a conflict flag instead of silently choosing the top-ranked one. The final configuration is chosen from hit rate/MRR/citation-quality results.

## Consequences
More code now; honest evaluation later.

## Alternatives considered
Hybrid by default (rejected: unproven).
