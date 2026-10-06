# ADR-0011: Minimal server-rendered operational UI

**Status:** Accepted. Refined by [ADR-0016](0016-operator-experience-observability-recovery.md): the decision stands; the implementation is a stdlib WSGI app with Jinja2 rather than FastAPI.

## Context
The UI exists to show evidence → diagnosis → action → approval → execution → audit, not to be a design project.

## Decision
Server-rendered pages from the same FastAPI app (templates, no JS build chain, small progressive-enhancement scripts if needed) for: case state, evidence & citations, hypotheses, confidence, proposed actions with required role, approval/denial history, execution result, draft reply, internal note, audit timeline.

## Consequences
Fast to build, easy to test, few dependencies. Less interactive than an SPA.

## Alternatives considered
SPA (rejected: scope).
