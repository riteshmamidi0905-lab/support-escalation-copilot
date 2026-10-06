# ADR-0016: Operator experience, server-side authorization, observability, recovery and draft safety (M5)

**Status:** Accepted. Refines ADR-0011 (the UI decision stands; the implementation is a small stdlib WSGI app + Jinja2 rather than FastAPI, to add one dependency instead of a framework).

## Context
M3/M4 made the system safe to run; M5 must make it *operable and inspectable* without weakening anything: a person has to see the exact action and its evidence before approving, understand why a case
abstained, degraded or is uncertain, and an operator has to see what the system is doing. The browser is the least trusted component in the design.

## Decisions
1. **Server-rendered, no JavaScript, strict CSP.** `default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'`. There is no inline script/style/handler anywhere (a test scans the
   templates). Hostile ticket/runbook text is auto-escaped (Jinja2) and cannot act as the operator.
2. **The browser is never an authority.** Identity is a signed cookie (HttpOnly, SameSite=Strict) verified on every request; account grants are inside the signature (a widened grant fails verification);
   only *human* identities are sessions. All reads go through one class, `Access`; all actions through `OperatorActions`, which re-check role, account grant and case state and then call the unchanged
   control plane. Unknown case / someone else's case / forged identity all answer with the same 404 (no existence oracle). State changes are POST + a CSRF token bound to the session. **There is no
   route that executes an action**: the only decision a person can record is approve/deny, and execution still needs an approved, hash-matching approval and the idempotent executor.
   Sign-in is **simulated** (pick a persona); there is no real authentication and the project does not pretend otherwise.
3. **Approval UX.** The approver sees the canonical action exactly as it will run, its hash, the policy decision, evidence sufficiency, the evidence shown when the approval was requested, expiry and
   the limitations. Approving or denying records a reason in the audit log. **Amending** an action (Tier-2 only, REVIEW state, fixed editable fields) re-runs policy on the *new* action, voids the old
   approval (shown as VOID, never usable), creates a new approval, and forbids the amender from approving it. A refused amendment leaves the original untouched.
4. **Uncertain writes are reconciled by a human**, never retried: the operator records what they found on the customer system (applied / not applied) with a note; the ledger and audit are updated.
5. **Observability from real events.** The application writes an append-only `ops_events` stream (model calls, retries, provider failures, dependency calls, breaker changes, retrieval fallbacks, recovery
   claims) with correlation ids; `Metrics` derives every figure from the real tables (cases, transitions, approvals, ledger, audit, ops events). An empty system reports zeros; there is no hard-coded
   number. Prometheus text is a view of the same snapshot. Telemetry stores outcomes and counts, never prompts, reasoning or secrets (attributes are scrubbed).
6. **Recovery with the smallest justified mechanism.** A lease on the case row claimed with `FOR UPDATE SKIP LOCKED` avoids wasted collisions between workers; it is *not* what prevents duplicate
   effects. Safety comes from versioned transitions, idempotent stages that persist before they advance, and the M3 idempotency ledger. A waiting case is polled on a back-off and woken by the
   decision; a case that keeps failing is *parked and reported*, never force-failed.
7. **Draft safety is layered because the harm path (a convincing wrong draft) cannot be closed by rules alone.** (a) Deterministic grounding checks before a human sees the draft (numbers,
   identifiers, effects, links, other accounts, internal terms, promises, "resolved" claims, instruction echo, hedging under conflict); (b) the draft is always `UNREVIEWED` and cannot be sent by this
   system; (c) with risk flags (conflict, stale or instruction-like evidence, high uncertainty, unverified state, redacted ticket) review is **elevated**: each flag must be acknowledged item by item
   (enforced on the server); (d) contradictions and applicability are shown next to the draft. The residual weakness is measured and documented (`docs/m5-draft-steering.md`), not hidden.
8. **Real-model readiness without a real model.** The provider abstraction is unchanged. `ConfiguredProvider` pins inference parameters, refuses a prompt that does not fit the declared context
   window, treats a truncated reply as a failure and drops reasoning blocks before parsing. Whether a real model behaves is a separate, frozen, **not yet executed** evaluation
   (`docs/m5-real-model-protocol.md`).

## Consequences
* An operator can inspect and act on a case end to end with no JavaScript; accessibility and mobile layouts are testable with a browser or a parser.
* Everything the UI shows comes from the durable case file and audit log, so it cannot disagree with them.
* The attack surface is explicit and tested: 18 new attack ids (A-I1-27..34, A-I2-34..37, A-I3-08/09, A-I4-11/12) plus A-I2-10 now implemented; 28 mutation checks on the new defences.
* Limits (see `docs/risks.md` R-52..R-64): simulated sign-in, stateless sessions (sign-out only clears the cookie), no rate limiting, pattern-based redaction misses unlabelled secrets, grounding cannot
  catch a misleading draft made of grounded words.

## Alternatives considered
FastAPI + a JS front end (rejected: dependency and scope, no benefit for correctness); trusting a role passed in a header or form field (rejected: the exact mistake A-I1-09 forbids); a job queue or
Kubernetes-style scheduler for recovery (rejected: PostgreSQL leases are enough for the failure modes tested); showing the model's reasoning to approvers (rejected: not stored, and unreliable as explanation).
