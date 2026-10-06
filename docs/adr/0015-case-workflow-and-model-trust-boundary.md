# ADR-0015: Case state machine, model stages and the model trust boundary (M4)

**Status:** Accepted (M4; builds on ADR-0002, ADR-0006, ADR-0013, ADR-0014)

## Context
M0–M3 built the tenant boundary, retrieval, typed actions, deterministic policy, role-bound approvals, idempotent execution and an audit chain. M4 makes the AI part of the system. The rule: **the model may interpret evidence and propose decisions; it may not redefine scope, policy, permissions, approval requirements, action schemas or workflow transitions.**

## Decision
**State machine** (`copilot/workflow/states.py`, `machine.py`, migration 008). States `NEW → INTAKE → SCOPE → RETRIEVE → VERIFY → DIAGNOSE → PLAN → REVIEW → EXECUTE → DRAFT → {CLOSED | REFUSED | ABSTAINED | ESCALATED | HANDED_OFF}`, plus `FAILED` from any stage and a degraded `DIAGNOSE → DRAFT` edge. Every edge declares source, destination, required inputs, a guard over the durable case file, the outcomes it may carry and an audit event. A transition is **one database transaction** (guard, optimistic-version state update, transition row, audit event): a crash at any point leaves the previous state intact. The same edge list lives in the database (`workflow_edges`) and is enforced by a trigger, so skipped, backwards, repeated, forged or replayed transitions fail even if application code is bypassed. The model has no input to any of this.

**Stages.** The runner (deterministic code) executes the stage the state names. Each stage persists its output in the case file *before* its transition and is idempotent, so resume = read state and continue. The model is called only in DIAGNOSE (through the frozen runtime's `Agent` loop with read-only tools, followed by the runtime's structured-output repair), PLAN and DRAFT (`generate_structured` + bounded retries + budget + tracer). The runtime's free-form planner is not used. A `caution` rule: if the diagnosis disposition is refuse/abstain/clarify the PLAN stage is not even run: *caution is free, permission is earned*.

**Structured model output** (`schemas.py`): `Diagnosis` (hypotheses with supporting/contradicting handles, per-evidence applicability verdicts, missing evidence, uncertainty, disposition, concise rationale), `ProposedActions` (type, params, cited handles, rationale), `DraftReply` (text, cited handles, limitations). `additionalProperties:false`; no field for chain-of-thought, scope, tenant, role, approval, expiry, state, sufficiency or SQL, so a model that supplies one produces *invalid* output (repair, then degraded). Only these structured conclusions are stored.

**Trust boundary** (`trust.py`): the model refers to evidence only by opaque handles that the workflow resolves; unknown handles are invalid. A proposal becomes a typed action only here: the workflow sets case, requester, role, ids and a **deterministic idempotency key** (same case + type + params ⇒ same key across retries/restarts/repeated output); forbidden/unknown types, bad params, cross-tenant params and secrets are dropped and recorded, never executed. Surviving actions go through the unchanged M3 path: schema → tenant binding → fresh policy → approval service → idempotent executor. Drafts are checked (no credentials or e-mail addresses, only citable evidence, conflicting relevant documents must be cited and disclosed) and stored only as internal artifacts.

**Four layers, kept separate.** Retrieval relevance (M2; no similarity gate is used in the workflow) → semantic applicability (model, *advisory*, recorded as such) → deterministic policy sufficiency (M3 facts) → human approval. A fooled applicability judgement can change what a human is shown; it cannot create an approval, an execution or a policy exception.

**Degraded paths, not silent ones.** Retrieval failure, unverifiable Status/Carrier APIs (retries + breaker with half-open), model outage/timeout/invalid output each end in an explicit `DEGRADED` case file (retrieval-only where applicable) with actions disabled; ticketing failure ends in `FAILED` with a reason.

**Providers.** Deterministic first: `RuleCaseModel` (an offline stand-in, not an LLM) and `FaultyModel` (misbehaves on demand). The approved local path is the runtime's `OpenAICompatProvider` (`local_provider`); it is exercised against a local protocol stub and via `scripts/run_m4_local_model.py`, which records model/digest/config/machine or states that nothing was run. CI never needs a model.

## Consequences
- The control plane's authority is unchanged; M3 tests, attacks and policy are untouched (one false-positive fix to e-mail masking, see risks).
- Resume is by polling the durable state; there is no scheduler yet (M5/M6).
- Semantic applicability and refusal of hostile intent depend on the model; the deterministic architecture only bounds the consequences. How well a real model does is **not measured** here.

## Alternatives
The runtime's free-form planner above the workflow (rejected: the workflow, not the model, owns stages); letting the model name the next state (rejected); trusting retrieval scores or regex injection detection as gates (rejected: M2 evidence; every attack is also run with all detectors off).
