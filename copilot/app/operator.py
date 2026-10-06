"""Operator actions (approve / deny / amend / review a draft / reconcile an uncertain write). The web layer calls ONLY these, with the identity taken from a verified session; every action
re-authorizes server-side (signed identity, role, account grants, case state), then goes through the unchanged control plane. Nothing here can execute a customer write by itself: execution
still requires an approved, hash-matching approval and the idempotent executor."""
from __future__ import annotations

import json
from typing import Any

from copilot.control import gateway as G
from copilot.control.access import AccessDenied
from copilot.control.actions import ACTIONS, ActionRejected, validate_action
from copilot.control.approvals import ApprovalError
from copilot.control.identity import Identity
from copilot.redact import scrub
from copilot.workflow import trust
from copilot.workflow.machine import TransitionRefused

from .services import Services

EDITABLE = {"request_sla_credit": ("percent", "reason"), "trigger_resync": ("blast_radius",), "escalate_engineering": ("severity", "summary")}
REVIEW_ROLES = ("tier2_engineer",)


class OperatorError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code, self.message = code, message or code


class OperatorActions:
    def __init__(self, svc: Services):
        self.svc = svc

    # ---- helpers ----------------------------------------------------------------------------------------------------------------------------
    def _case(self, ident: Any, case_id: str) -> tuple[Identity, dict[str, Any]]:
        i, _acc = self.svc.access.authorize_case(ident, case_id)
        row = self.svc.machine.get(case_id)
        if row is None:
            raise AccessDenied()
        return i, row

    def _refresh(self, case_id: str) -> None:
        row = self.svc.machine.get(case_id)
        if row and "case_file" in row["file"]:
            scope = self.svc.intake.rescope(case_id)
            self.svc.machine.patch_file(case_id, "case_file", self.svc.runner(case_id)._build_case_file(row, scope))

    def _audit(self, typ: str, row: dict, ident: Identity, corr: dict, payload: dict) -> None:
        self.svc.audit.append(typ, row["case_id"], row["account_id"], ident.actor(), corr, payload)

    # ---- approve / deny ---------------------------------------------------------------------------------------------------------------------
    def decide(self, ident: Any, case_id: str, approval_id: str, verdict: str, reason: str, request_id: str) -> dict[str, Any]:
        i, row = self._case(ident, case_id)
        rec = self.svc.approvals.get(approval_id)
        entry = next((a for a in row["file"].get("approvals", []) if a["approval_id"] == approval_id), None)
        if rec is None or rec.case_id != case_id or entry is None:
            raise OperatorError("NOT_FOUND", "no such approval on this case")
        if entry["status"] == "superseded":
            raise OperatorError("SUPERSEDED", "this approval belongs to an action that was amended; decide the new approval instead")
        plan = next((p for p in row["file"].get("plan", {}).get("actions", []) if p["action_id"] == entry["action_id"]), None)
        if plan and plan.get("amended_by") == i.id:
            raise OperatorError("AMENDER_CANNOT_APPROVE", "the person who amended an action cannot approve it")
        try:
            self.svc.approvals.decide(approval_id, i, verdict, reason, correlation={"request_id": request_id})
        except ApprovalError as e:
            raise OperatorError(e.code, f"refused: {e.code}") from e
        res = self.svc.run_case(case_id, request_id)                                  # resume now; the recovery worker is only the backup
        return {"state": res.state, "outcome": res.outcome, "disposition": res.disposition}

    # ---- amend ------------------------------------------------------------------------------------------------------------------------------
    def amend(self, ident: Any, case_id: str, action_id: str, changes: dict[str, Any], request_id: str) -> dict[str, Any]:
        i, row = self._case(ident, case_id)
        if i.role != "tier2_engineer":
            raise OperatorError("WRONG_ROLE", "only a Tier-2 engineer can amend a proposed action")
        f = row["file"]
        if row["state"] != "REVIEW":
            raise OperatorError("NOT_IN_REVIEW", "an action can only be amended while the case waits in REVIEW")
        entry = next((p for p in f.get("plan", {}).get("actions", []) if p["action_id"] == action_id), None)
        if entry is None or entry["status"] != "awaiting_approval":
            raise OperatorError("NOT_AMENDABLE", "that action is not awaiting approval")
        if any(e["action_id"] == action_id for e in f.get("executions", [])):
            raise OperatorError("ALREADY_EXECUTING", "execution has started")
        editable = EDITABLE.get(entry["type"], ())
        bad = sorted(set(changes) - set(editable))
        if bad or not changes:
            raise OperatorError("FIELD_NOT_EDITABLE", f"editable for {entry['type']}: {', '.join(editable)}")
        old = entry["raw"]["params"]
        new = {**old, **{k: _coerce(k, v) for k, v in changes.items()}}
        if new == old:
            raise OperatorError("NO_CHANGE", "nothing was changed")
        raw = {**entry["raw"], "params": new, "idempotency_key": trust.idempotency_key(case_id, entry["type"], new)}
        raw["action_id"] = "ACT-" + raw["idempotency_key"][5:17]
        try:
            validate_action(raw)
        except ActionRejected as e:
            raise OperatorError(e.code, f"the amended action is invalid ({e.code})") from e
        scope = self.svc.intake.rescope(case_id)
        res = self.svc.gateway.propose(raw, scope, self.svc.agent, run_id="RUN-" + case_id, request_id=request_id, knowledge=f["retrieval"]["outcome"])
        if res.status != G.AWAITING_APPROVAL:                                          # policy re-evaluated the NEW action: nothing changes if it is not approvable
            raise OperatorError("AMENDMENT_NOT_ALLOWED", f"policy: {res.status} ({', '.join(res.reasons)}); the original action and its approval are unchanged")
        cur = self.svc.machine.get(case_id)
        if cur["state"] != "REVIEW":
            raise OperatorError("NOT_IN_REVIEW", "the case moved on while amending")
        old_ap = next((a for a in cur["file"]["approvals"] if a["action_id"] == action_id and a["status"] != "superseded"), None)
        voided = self.svc.approvals.void_pending(old_ap["approval_id"], f"superseded: action amended by {i.id}", i.actor(), {"request_id": request_id}) if old_ap else False
        plan = cur["file"]["plan"]
        for n, e in enumerate(plan["actions"]):
            if e["action_id"] == action_id:
                plan["actions"][n] = {**e, "action_id": raw["action_id"], "raw": raw, "decision": res.decision.as_dict() if res.decision else None, "reasons": list(res.reasons), "approval_id": res.approval_id,
                                      "status": "awaiting_approval", "amended_from": action_id, "amended_by": i.id, "history": (e.get("history") or []) + [{"action_id": action_id, "params": old, "approval_id": old_ap and old_ap["approval_id"], "approval_voided": voided}]}
        approvals = [({**a, "status": "superseded", "superseded_by": res.approval_id, "decision_reason": f"action amended by {i.id}"} if old_ap and a["approval_id"] == old_ap["approval_id"] else a) for a in cur["file"]["approvals"]]
        approvals.append({"approval_id": res.approval_id, "action_id": raw["action_id"], "role": raw["required_role"], "status": "awaiting_approval"})
        self.svc.machine.patch_file(case_id, "plan", plan)
        self.svc.machine.patch_file(case_id, "approvals", approvals)
        self._audit("action_amended", row, i, {"request_id": request_id, "action_id": raw["action_id"], "approval_id": res.approval_id},
                    {"from_action": action_id, "to_action": raw["action_id"], "changed": {k: {"from": old.get(k), "to": new[k]} for k in changes}, "previous_approval_voided": voided,
                     "previous_approval": old_ap and old_ap["approval_id"], "new_approval": res.approval_id})
        self._refresh(case_id)
        return {"new_action_id": raw["action_id"], "new_approval_id": res.approval_id, "previous_approval_voided": voided, "previous_approval": old_ap and old_ap["approval_id"]}

    # ---- draft review -----------------------------------------------------------------------------------------------------------------------
    def review_draft(self, ident: Any, case_id: str, acknowledged: list[str], request_id: str) -> dict[str, Any]:
        i, row = self._case(ident, case_id)
        if i.role not in REVIEW_ROLES:
            raise OperatorError("WRONG_ROLE", "a Tier-2 engineer reviews customer-facing drafts")
        d = row["file"].get("draft_reply")
        if not d or d.get("source") != "model" or not d.get("review"):
            raise OperatorError("NO_DRAFT", "there is no draft to review")
        if d["review"]["status"] == "reviewed":
            raise OperatorError("ALREADY_REVIEWED", "already reviewed")
        need, got = set(d["review"]["flags"]), set(acknowledged)
        if got != need:
            raise OperatorError("ACKNOWLEDGE_EVERY_FLAG", "this draft carries risk flags; acknowledge each one: " + ", ".join(sorted(need - got)) if need - got else "unknown flag acknowledged")
        from copilot.control.clock import SystemClock
        d["review"] = {**d["review"], "status": "reviewed", "by": i.id, "at": (self.svc.clock or SystemClock()).now().isoformat(), "acknowledged": sorted(got)}
        self.svc.machine.patch_file(case_id, "draft_reply", d)
        self._audit("draft_reviewed", row, i, {"request_id": request_id}, {"artifact_id": d["artifact_id"], "level": d["review"]["level"], "acknowledged": sorted(got)})
        self._refresh(case_id)
        return {"reviewed_by": i.id, "level": d["review"]["level"]}

    # ---- reconcile an uncertain write -------------------------------------------------------------------------------------------------------
    def reconcile(self, ident: Any, case_id: str, action_id: str, outcome: str, note: str, request_id: str) -> dict[str, Any]:
        i, row = self._case(ident, case_id)
        f = row["file"]
        ex = next((e for e in f.get("executions", []) if e["action_id"] == action_id), None)
        if ex is None or ex["status"] != G.UNCERTAIN_:
            raise OperatorError("NOT_UNCERTAIN", "only an UNCERTAIN execution can be reconciled")
        entry = next(p for p in f["plan"]["actions"] if p["action_id"] == action_id)
        if i.role != ACTIONS[entry["type"]][1]:
            raise OperatorError("WRONG_ROLE", f"reconciling a {entry['type']} needs the {ACTIONS[entry['type']][1]} role")
        if outcome not in ("applied", "not_applied"):
            raise OperatorError("BAD_OUTCOME", "outcome must be 'applied' or 'not_applied'")
        if not note.strip():
            raise OperatorError("NOTE_REQUIRED", "record what you checked on the customer system")
        scope = self.svc.intake.rescope(case_id)
        acc, key = row["account_id"], entry["raw"]["idempotency_key"]
        approval = next(a for a in f["approvals"] if a["action_id"] == action_id and a["status"] == "approved")
        now = self.svc.clock.now()
        if outcome == "applied":
            self.svc.ledger.succeed(acc, key, now, {"manual_reconciliation": {"by": i.id, "note": scrub(note)[:200]}})
            status, disp = "RECONCILED_APPLIED", "EXECUTED_RECONCILED"
            self._audit("action_executed", row, i, {"request_id": request_id, "action_id": action_id, "idempotency_key": key, "approval_id": approval["approval_id"]}, {"status": "SUCCEEDED_RECONCILED_BY_HUMAN", "note": scrub(note)[:200]})
        else:
            self.svc.ledger.fail_transient(acc, key, now, {"manual_reconciliation": {"by": i.id, "note": scrub(note)[:200], "outcome": "not_applied"}})
            r = self.svc.gateway.execute(entry["raw"], scope, self.svc.agent, approval_id=approval["approval_id"], run_id="RUN-" + case_id, request_id=request_id, knowledge=f["retrieval"]["outcome"])
            status, disp = ("EXECUTED_AFTER_RECONCILIATION" if r.status == G.SUCCEEDED else r.status), ("EXECUTED_RECONCILED" if r.status == G.SUCCEEDED else "OUTCOME_UNCERTAIN")
        execs = [{**e, "status": status, "reconciled_by": i.id, "note": scrub(note)[:200]} if e["action_id"] == action_id else e for e in f["executions"]]
        self.svc.machine.patch_file(case_id, "executions", execs)
        self.svc.machine.patch_file(case_id, "disposition", disp)
        self.svc.machine.set_disposition(case_id, disp)
        self._refresh(case_id)
        return {"status": status, "disposition": disp}

    # ---- misc -------------------------------------------------------------------------------------------------------------------------------
    def wake(self, case_id: str) -> None:
        self.svc.worker("ui").wake(case_id)


def _coerce(k: str, v: Any) -> Any:
    if k == "percent":
        try:
            return float(v)
        except (TypeError, ValueError) as e:
            raise OperatorError("BAD_VALUE", "percent must be a number") from e
    return str(v)[:500]


_ = (json, TransitionRefused)
