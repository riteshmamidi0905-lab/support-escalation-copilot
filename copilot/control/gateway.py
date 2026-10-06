"""Control gateway: the ONLY path from a proposed action to an effect.

    propose():  validate -> scope/case check -> trusted facts -> policy decision -> (approval request)            no effect
    execute():  validate -> scope/case check -> idempotency peek -> trusted facts -> policy -> approval check -> idempotency claim -> effect -> audit

Deterministic controls decide, never model confidence: the policy engine and the approval service have no input from the model beyond the validated action, and the
gateway re-evaluates policy at execution time instead of trusting an earlier decision. Every refusal, decision and effect is audited and emitted as a structured event.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from copilot.redact import scrub
from copilot.scope import Scope, ScopeError, ScopeGuard

from . import ledger as L
from .actions import ActionRejected, Tier, ValidatedAction, validate_action
from .approvals import ApprovalError, ApprovalService
from .audit import AuditLog
from .clock import SystemClock
from .events import ControlEvents
from .facts import gather
from .identity import Identity, IdentityAuthority, IdentityError
from .mocks import INTERNAL_SUFFIX, CallTimeout, LookupUnsupported, MockSystem, PermanentError, TransientError
from .policy import CONFIG, Decision, PolicyDecision, decide

REFUSED, PROPOSAL_STORED, SUCCEEDED, REPLAYED, IN_PROGRESS_, FAILED_PERMANENT, FAILED_TRANSIENT, UNCERTAIN_, ABSTAINED, ESCALATED, AWAITING_APPROVAL = (
    "REFUSED", "PROPOSAL_STORED", "SUCCEEDED", "REPLAYED", "IN_PROGRESS", "FAILED_PERMANENT", "FAILED_TRANSIENT", "UNCERTAIN", "ABSTAINED", "ESCALATED", "AWAITING_APPROVAL")


@dataclass
class Result:
    status: str
    reasons: tuple[str, ...] = ()
    decision: PolicyDecision | None = None
    approval_id: str | None = None
    effect: dict[str, Any] | None = None
    action_hash: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class ControlGateway:
    def __init__(self, *, app_pool, guard: ScopeGuard, control_pool, audit: AuditLog, approvals: ApprovalService, ledger: L.Ledger, authority: IdentityAuthority,
                 systems: dict[str, MockSystem], clock=None, events: ControlEvents | None = None, sleep=lambda s: None):
        self.app_pool, self.guard, self.control_pool, self.audit, self.approvals, self.ledger = app_pool, guard, control_pool, audit, approvals, ledger
        self.authority, self.systems, self.clock, self.events, self.sleep = authority, systems, clock or SystemClock(), events or ControlEvents(), sleep

    # ---- helpers ---------------------------------------------------------------------------------------------------------------------------
    def _ev(self, typ: str, case_id: str, account_id: str | None, actor: dict, corr: dict, payload: dict) -> None:
        self.audit.append(typ, case_id, account_id, actor, corr, payload)
        self.events.emit(typ, case_id=case_id, **corr, **{k: v for k, v in payload.items() if k in ("reasons", "decision", "status", "attempt", "system")})

    def _refuse_raw(self, raw: Any, code: str, detail: str, scope: Scope | None, requester: Identity | None, corr: dict) -> Result:
        case_id = scope.case_id if scope else "CASE-000000"
        typ = "forbidden_action_attempted" if code == "FORBIDDEN_ACTION" else "action_refused"
        actor = requester.actor() if isinstance(requester, Identity) else {"kind": "agent", "id": "unknown", "role": None}
        self._ev(typ, case_id, scope.account_id if scope else None, actor, corr, {"stage": "validation", "reasons": [code], "action_type": (raw.get("type") if isinstance(raw, dict) and isinstance(raw.get("type"), str) else None) and str(raw.get("type"))[:40]})
        return Result(REFUSED, (code,), details={"detail": detail})

    def _prelude(self, raw: Any, scope: Scope, requester: Identity, corr: dict) -> tuple[ValidatedAction | None, Result | None]:
        try:
            self.authority.verify(requester)
            if requester.kind != "agent":
                raise IdentityError("only the agent proposes actions")
        except IdentityError as e:
            return None, self._refuse_raw(raw, "IDENTITY_INVALID", str(e), scope, requester, corr)
        try:
            action = validate_action(raw)
        except ActionRejected as e:
            return None, self._refuse_raw(raw, e.code, e.detail, scope, requester, corr)
        try:
            self.guard.check(scope)
        except ScopeError:
            return None, self._refuse_raw(raw, "SCOPE_INVALID", "", None, requester, {**corr, "action_id": action.action_id})
        if action.case_id != scope.case_id:                # the model named a case other than the one it is working: a cross-case/tenant attempt
            self._ev("isolation_violation_blocked", scope.case_id, scope.account_id, requester.actor(), {**corr, "action_id": action.action_id}, {"reasons": ["CASE_MISMATCH"], "action_type": action.type})
            return None, Result(REFUSED, ("CASE_MISMATCH",))
        return action, None

    def _decide(self, action: ValidatedAction, scope: Scope, knowledge: str | None) -> PolicyDecision:
        facts = gather(self.app_pool, scope, self.guard, action.case_id, self.clock.now(), knowledge)
        return decide(action, facts)

    # ---- propose ---------------------------------------------------------------------------------------------------------------------------
    def propose(self, raw: Any, scope: Scope, requester: Identity, *, run_id: str | None = None, request_id: str | None = None, knowledge: str | None = None, invocation_id: str | None = None) -> Result:
        corr = {"run_id": run_id, "request_id": request_id, "invocation_id": invocation_id}
        action, bad = self._prelude(raw, scope, requester, corr)
        if bad:
            return bad
        acc, h = scope.account_id, action.hash(scope.account_id)
        corr = {**corr, "action_id": action.action_id, "action_hash": h, "idempotency_key": action.idempotency_key}
        self._ev("action_proposed", action.case_id, acc, requester.actor(), corr, {"action_type": action.type, "tier": action.tier.value, "evidence_refs": action.evidence_refs})
        d = self._decide(action, scope, knowledge)
        self._ev("policy_decided", action.case_id, acc, {"kind": "system", "id": "policy-engine", "role": None}, corr, {"decision": d.decision.value, "reasons": list(d.reasons), "sufficiency": d.sufficiency.value,
                 "requires_approval": d.requires_approval, "required_role": d.required_role, "missing_facts": list(d.missing_facts), "policy_version": d.policy_version})
        return self._after_decision(action, scope, requester, d, corr, h, create_approval=True)

    def _after_decision(self, action, scope, requester, d: PolicyDecision, corr, h, create_approval: bool) -> Result:
        acc = scope.account_id
        sysactor = {"kind": "system", "id": "policy-engine", "role": None}
        if d.decision is Decision.DENY:
            self._ev("action_refused", action.case_id, acc, sysactor, corr, {"stage": "policy", "reasons": list(d.reasons), "constraints": list(d.constraints)})
            return Result(REFUSED, d.reasons, d, action_hash=h)
        if d.decision is Decision.ABSTAIN:
            self._ev("abstained", action.case_id, acc, sysactor, corr, {"reasons": list(d.reasons), "missing_facts": list(d.missing_facts)})
            return Result(ABSTAINED, d.reasons, d, action_hash=h)
        if d.decision is Decision.ESCALATE:
            self._ev("escalated", action.case_id, acc, sysactor, corr, {"reasons": list(d.reasons), "details": d.details})
            return Result(ESCALATED, d.reasons, d, action_hash=h)
        if d.decision is Decision.REQUIRE_APPROVAL and create_approval:
            ap = self.approvals.request(action, acc, d, requester, {"policy": d.as_dict(), "action_type": action.type, "params": action.params, "evidence_refs": action.evidence_refs},
                                        ttl_s=CONFIG["approval_ttl_seconds"], correlation={k: corr.get(k) for k in ("run_id", "request_id")})
            return Result(AWAITING_APPROVAL, d.reasons, d, approval_id=ap.approval_id, action_hash=h)
        return Result("ALLOWED", d.reasons, d, action_hash=h)

    # ---- execute ---------------------------------------------------------------------------------------------------------------------------
    def execute(self, raw: Any, scope: Scope, requester: Identity, *, approval_id: str | None = None, run_id: str | None = None, request_id: str | None = None, knowledge: str | None = None) -> Result:
        corr = {"run_id": run_id, "request_id": request_id}
        action, bad = self._prelude(raw, scope, requester, corr)
        if bad:
            return bad
        acc, h = scope.account_id, action.hash(scope.account_id)
        corr = {**corr, "action_id": action.action_id, "action_hash": h, "idempotency_key": action.idempotency_key, "approval_id": approval_id}
        actor = requester.actor()
        # 1. idempotency identity first: a key can never be reused for a different payload, and an already-completed effect is replayed (not re-authorised, not re-run)
        prior = self.ledger.peek(acc, action.idempotency_key)
        if prior is not None and (prior["action_hash"], prior["case_id"], prior["action_type"]) != (h, action.case_id, action.type):
            self._ev("idempotency_conflict_blocked", action.case_id, acc, actor, corr, {"reasons": ["IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD"]})
            return Result(REFUSED, ("IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD",), action_hash=h)
        if prior is not None and prior["status"] == "succeeded":
            self._ev("idempotent_replay", action.case_id, acc, actor, corr, {"status": "REPLAYED"})
            return Result(REPLAYED, ("IDEMPOTENT_REPLAY",), action_hash=h, effect=prior["result"])
        # 2. policy is evaluated again NOW, from fresh trusted facts
        d = self._decide(action, scope, knowledge)
        self._ev("policy_decided", action.case_id, acc, {"kind": "system", "id": "policy-engine", "role": None}, corr, {"decision": d.decision.value, "reasons": list(d.reasons), "sufficiency": d.sufficiency.value,
                 "requires_approval": d.requires_approval, "required_role": d.required_role, "policy_version": d.policy_version})
        if d.decision not in (Decision.ALLOW_PROPOSAL, Decision.REQUIRE_APPROVAL):
            return self._after_decision(action, scope, requester, d, corr, h, create_approval=False)
        # 3. approval (gated writes only)
        if action.tier is Tier.GATED_WRITE:
            try:
                self.approvals.check_for_execution(approval_id, action, acc)
            except ApprovalError as e:
                self._ev("action_refused", action.case_id, acc, actor, corr, {"stage": "approval", "reasons": [e.code]})
                return Result(REFUSED, (e.code,), d, approval_id=approval_id, action_hash=h)
        elif d.decision is not Decision.ALLOW_PROPOSAL:
            return Result(REFUSED, ("POLICY_TIER_MISMATCH",), d, action_hash=h)
        # 4. claim the effect
        state, rec = self.ledger.begin(account_id=acc, key=action.idempotency_key, case_id=action.case_id, action_type=action.type, action_hash=h, approval_id=approval_id,
                                       now=self.clock.now(), lease_s=CONFIG["lease_seconds"])
        if state == L.PAYLOAD_CONFLICT:
            self._ev("idempotency_conflict_blocked", action.case_id, acc, actor, corr, {"reasons": ["IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD"]})
            return Result(REFUSED, ("IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD",), d, action_hash=h)
        if state == L.REPLAY:
            self._ev("idempotent_replay", action.case_id, acc, actor, corr, {"status": "REPLAYED"})
            return Result(REPLAYED, ("IDEMPOTENT_REPLAY",), d, approval_id=approval_id, action_hash=h, effect=rec["result"])
        if state == L.IN_PROGRESS:
            return Result(IN_PROGRESS_, ("ANOTHER_REQUEST_IS_EXECUTING_THIS_ACTION",), d, action_hash=h)
        if state == L.FINAL_FAILURE:
            return Result(FAILED_PERMANENT, ("PREVIOUSLY_FAILED_PERMANENTLY",), d, action_hash=h, effect=rec["result"])
        if action.tier is Tier.PROPOSE:
            return self._store_artifact(action, scope, requester, d, corr, h, state)
        return self._run_effect(action, scope, requester, d, corr, h, state, approval_id)

    # ---- internal artifacts (PROPOSE tier) -------------------------------------------------------------------------------------------------
    def _store_artifact(self, action, scope, requester, d, corr, h, state) -> Result:
        import hashlib
        acc, now = scope.account_id, self.clock.now()
        body = action.params["body" if action.type == "draft_reply" else "text"]
        kind = "draft_reply" if action.type == "draft_reply" else "internal_note"
        art = "ART-" + hashlib.sha256(f"{acc}|{action.idempotency_key}".encode()).hexdigest()[:12]
        with self.control_pool.connection() as c, c.transaction():
            c.execute("INSERT INTO copilot.case_artifacts (artifact_id, case_id, account_id, kind, body, body_sha256, action_hash, idempotency_key, created_by, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                      "ON CONFLICT (account_id, idempotency_key) DO NOTHING", (art, action.case_id, acc, kind, body, hashlib.sha256(body.encode()).hexdigest(), h, action.idempotency_key, requester.id, now))
        self.ledger.succeed(acc, action.idempotency_key, now, {"artifact_id": art, "kind": kind})
        self._ev("draft_created", action.case_id, acc, requester.actor(), corr, {"kind": kind, "artifact_id": art, "body_sha256": hashlib.sha256(body.encode()).hexdigest(), "length": len(body), "sent": False})
        return Result(PROPOSAL_STORED, d.reasons, d, action_hash=h, effect={"artifact_id": art, "kind": kind})

    # ---- gated effect ----------------------------------------------------------------------------------------------------------------------
    def _payload(self, action: ValidatedAction, account_id: str) -> tuple[str, dict[str, Any]]:
        p = action.params
        if action.type == "trigger_resync":
            return "trigger_resync", {"integration_id": p["integration_id"]}
        if action.type == "request_sla_credit":
            return "request_sla_credit", {"account_id": account_id, "percent": p["percent"], "reason": p["reason"]}
        return "escalate_engineering", {"incident_id": p["incident_id"], "severity": p["severity"], "summary": p["summary"], "destination": "engineering-queue" + INTERNAL_SUFFIX}

    def _run_effect(self, action, scope, requester, d, corr, h, state, approval_id) -> Result:
        acc, key, actor = scope.account_id, action.idempotency_key, requester.actor()
        system_name, payload = self._payload(action, acc)
        system = self.systems[system_name]
        attempts = 0
        if state == L.UNCERTAIN:
            kind, found = self._reconcile(system, key, acc, action, corr, actor)
            if kind == "applied":
                return Result(SUCCEEDED, d.reasons, d, approval_id=approval_id, action_hash=h, effect=found)
            if kind == "unsupported":
                return Result(UNCERTAIN_, ("OUTCOME_UNKNOWN_NEEDS_HUMAN_RECONCILIATION",), d, approval_id=approval_id, action_hash=h)
            self.ledger.resume(acc, key, self.clock.now(), CONFIG["lease_seconds"])
        while attempts < CONFIG["max_execution_attempts"]:
            attempts += 1
            self._ev("execution_attempted", action.case_id, acc, actor, corr, {"attempt": attempts, "system": system_name})
            try:
                res = scrub(system.call(key, payload), 500)          # a customer system's response is untrusted: redacted before it is stored, audited or returned
            except TransientError:
                if attempts < CONFIG["max_execution_attempts"]:
                    self.sleep(min(2 ** attempts * 0.1, 1.0))
                    continue
                self.ledger.fail_transient(acc, key, self.clock.now(), {"error": "TRANSIENT_RETRIES_EXHAUSTED", "attempts": attempts})
                self._ev("action_executed", action.case_id, acc, actor, corr, {"status": "FAILED_TRANSIENT_RETRIES_EXHAUSTED", "attempts": attempts})
                return Result(FAILED_TRANSIENT, ("TRANSIENT_RETRIES_EXHAUSTED",), d, approval_id=approval_id, action_hash=h, details={"attempts": attempts})
            except PermanentError:
                self.ledger.fail_permanent(acc, key, self.clock.now(), {"error": "REJECTED_BY_CUSTOMER_SYSTEM"})
                self._ev("action_executed", action.case_id, acc, actor, corr, {"status": "FAILED_PERMANENT", "attempts": attempts})
                return Result(FAILED_PERMANENT, ("REJECTED_BY_CUSTOMER_SYSTEM",), d, approval_id=approval_id, action_hash=h)
            except CallTimeout:
                self.ledger.mark_uncertain(acc, key, self.clock.now())
                kind, found = self._reconcile(system, key, acc, action, corr, actor)
                if kind == "applied":
                    return Result(SUCCEEDED, d.reasons, d, approval_id=approval_id, action_hash=h, effect=found)
                if kind == "unsupported":
                    self._ev("escalated", action.case_id, acc, {"kind": "system", "id": "gateway", "role": None}, corr, {"reasons": ["EXECUTION_OUTCOME_UNKNOWN"]})
                    return Result(UNCERTAIN_, ("OUTCOME_UNKNOWN_NEEDS_HUMAN_RECONCILIATION",), d, approval_id=approval_id, action_hash=h)
                self.ledger.resume(acc, key, self.clock.now(), CONFIG["lease_seconds"])     # the system says it did NOT apply the request: safe to try again
                continue
            self.ledger.succeed(acc, key, self.clock.now(), res)
            self._ev("action_executed", action.case_id, acc, actor, corr, {"status": "SUCCEEDED", "attempts": attempts, "result": res})
            return Result(SUCCEEDED, d.reasons, d, approval_id=approval_id, action_hash=h, effect=res, details={"attempts": attempts})
        return Result(FAILED_TRANSIENT, ("RETRIES_EXHAUSTED",), d, approval_id=approval_id, action_hash=h)

    def _reconcile(self, system: MockSystem, key, acc, action, corr, actor) -> tuple[str, dict[str, Any] | None]:
        """Ask the customer system whether the request was applied: ('applied', result) | ('not_applied', None) | ('unsupported', None)."""
        try:
            found = system.lookup(key)
        except LookupUnsupported:
            self.ledger.mark_uncertain(acc, key, self.clock.now())
            self._ev("execution_uncertain", action.case_id, acc, actor, corr, {"reasons": ["OUTCOME_UNKNOWN_AND_SYSTEM_CANNOT_CONFIRM"]})
            return "unsupported", None
        if found is not None:
            found = scrub(found, 500)
            self.ledger.succeed(acc, key, self.clock.now(), found)
            self._ev("action_executed", action.case_id, acc, actor, corr, {"status": "SUCCEEDED_CONFIRMED_BY_LOOKUP", "result": found})
            return "applied", found
        return "not_applied", None
