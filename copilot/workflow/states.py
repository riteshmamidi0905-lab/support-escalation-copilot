"""The case state machine: explicit states and the ONLY legal transitions. Each transition declares its source, destination, required inputs, a guard over the durable
case file, the outcomes it may carry, and the audit event it writes. The same edge list is stored in the database (workflow_edges) and enforced by a trigger.
The model never appears here: it cannot name, set or influence a state."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

STATES = ("NEW", "INTAKE", "SCOPE", "RETRIEVE", "VERIFY", "DIAGNOSE", "PLAN", "REVIEW", "EXECUTE", "DRAFT", "CLOSED", "REFUSED", "ABSTAINED", "ESCALATED", "HANDED_OFF", "FAILED")
TERMINAL = frozenset({"CLOSED", "REFUSED", "ABSTAINED", "ESCALATED", "HANDED_OFF", "FAILED"})
OUTCOMES = ("ANSWER", "APPROVAL", "REFUSE", "ESCALATE", "INSUFFICIENT_EVIDENCE", "CLARIFY", "DEGRADED")


@dataclass(frozen=True)
class Transition:
    src: str
    dst: str
    requires: tuple[str, ...]
    guard: Callable[[dict[str, Any], dict[str, Any]], str | None]      # (case file, inputs) -> None if allowed, else a reason code
    outcomes: tuple[str, ...] | None = None                              # allowed outcomes when entering a terminal state
    event: str = "state_transition"


def _has(*keys):
    def g(f, _i):
        miss = [k for k in keys if not f.get(k)]
        return f"MISSING_STAGE_OUTPUT:{','.join(miss)}" if miss else None
    return g


def _approvals(f):
    return [a for a in f.get("approvals", []) if a.get("status") != "superseded"]


def _plan_needs_review(f, _i):
    return None if any(a.get("status") == "awaiting_approval" for a in f.get("plan", {}).get("actions", [])) else "NO_ACTION_AWAITS_APPROVAL"


def _plan_no_review(f, _i):
    return "ACTIONS_AWAIT_APPROVAL" if any(a.get("status") == "awaiting_approval" for a in f.get("plan", {}).get("actions", [])) else (None if f.get("plan") else "MISSING_STAGE_OUTPUT:plan")


def _review_to_execute(f, _i):
    ap = _approvals(f)
    if any(a["status"] == "awaiting_approval" for a in ap):
        return "APPROVALS_STILL_PENDING"
    return None if any(a["status"] == "approved" for a in ap) else "NO_APPROVED_ACTION"


def _review_to_draft(f, _i):
    ap = _approvals(f)
    if not ap or any(a["status"] in ("awaiting_approval", "approved") for a in ap):
        return "APPROVALS_NOT_ALL_DENIED_OR_EXPIRED"
    return None


def _exec_done(f, _i):
    ex = f.get("executions", [])
    return None if ex and all(e.get("status") not in (None, "PENDING") for e in ex) else "EXECUTION_RESULTS_MISSING"


def _degraded(f, _i):
    return None if f.get("degraded", {}).get("reason") else "NO_DEGRADED_REASON_RECORDED"


def _terminal(*ok_outcomes):
    def g(f, i):
        if not f.get("draft_stage_done"):
            return "DRAFT_STAGE_NOT_COMPLETE"
        return None if i.get("outcome") in ok_outcomes and f.get("outcome") == i.get("outcome") else f"OUTCOME_{i.get('outcome')}_NOT_ALLOWED_HERE"
    return g


def _failed(_f, i):
    return None if isinstance(i.get("failure"), dict) and i["failure"].get("code") else "FAILURE_REASON_REQUIRED"


TRANSITIONS: tuple[Transition, ...] = (
    Transition("NEW", "INTAKE", ("ticket_id",), lambda f, i: None),
    Transition("INTAKE", "SCOPE", (), _has("ticket")),
    Transition("SCOPE", "RETRIEVE", (), _has("scope")),
    Transition("RETRIEVE", "VERIFY", (), _has("retrieval")),
    Transition("VERIFY", "DIAGNOSE", (), _has("verification")),
    Transition("DIAGNOSE", "PLAN", (), _has("diagnosis")),
    Transition("DIAGNOSE", "DRAFT", (), _degraded),
    Transition("PLAN", "REVIEW", (), _plan_needs_review),
    Transition("PLAN", "DRAFT", (), _plan_no_review),
    Transition("REVIEW", "EXECUTE", (), _review_to_execute),
    Transition("REVIEW", "DRAFT", (), _review_to_draft),
    Transition("EXECUTE", "DRAFT", (), _exec_done),
    Transition("DRAFT", "CLOSED", ("outcome",), _terminal("ANSWER", "APPROVAL"), ("ANSWER", "APPROVAL")),
    Transition("DRAFT", "REFUSED", ("outcome",), _terminal("REFUSE"), ("REFUSE",)),
    Transition("DRAFT", "ABSTAINED", ("outcome",), _terminal("INSUFFICIENT_EVIDENCE", "CLARIFY"), ("INSUFFICIENT_EVIDENCE", "CLARIFY")),
    Transition("DRAFT", "ESCALATED", ("outcome",), _terminal("ESCALATE"), ("ESCALATE",)),
    Transition("DRAFT", "HANDED_OFF", ("outcome",), _terminal("APPROVAL", "DEGRADED", "ESCALATE"), ("APPROVAL", "DEGRADED", "ESCALATE")),
    *[Transition(s, "FAILED", ("failure",), _failed, None) for s in ("INTAKE", "SCOPE", "RETRIEVE", "VERIFY", "DIAGNOSE", "PLAN", "REVIEW", "EXECUTE", "DRAFT")],
)
EDGES = {(t.src, t.dst) for t in TRANSITIONS}
BY_EDGE = {(t.src, t.dst): t for t in TRANSITIONS}
