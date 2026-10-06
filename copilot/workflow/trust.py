"""The model trust boundary. Everything the model returns is UNTRUSTED. This module turns model output into either (a) validated, bounded, deterministic structures or
(b) a recorded rejection. It never lets model text choose a tenant, a role, an approval, an expiry, a state, a SQL statement or an action type outside the vocabulary.

Handles: the model refers to evidence only by opaque handles (E1.., or ids that exist in the trusted facts). Citations are resolved here from the workflow's own evidence list;
a handle that does not exist is invalid output."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from copilot.control import actions as A
from copilot.control.canonical import canonical_json
from copilot.redact import redact

from .schemas import MAX_TEXT

_FACT_ID = re.compile(r"^(INC|INT|CTR|TCK)-[0-9]{4,6}$")
MAX_ACTIONS = 3


@dataclass
class Rejected:
    action_type: str
    code: str
    detail: str = ""


@dataclass
class Plan:
    actions: list[dict[str, Any]] = field(default_factory=list)       # full typed actions (the ONLY thing that may reach the control plane)
    meta: list[dict[str, Any]] = field(default_factory=list)          # per action: model rationale + resolved citations (not executable)
    rejected: list[Rejected] = field(default_factory=list)


def known_handles(evidence: list[dict[str, Any]], fact_ids: set[str]) -> set[str]:
    return {e["handle"] for e in evidence} | set(fact_ids)


def _bad_handles(handles: list[str], known: set[str]) -> list[str]:
    return [f"unknown evidence handle {h!r}" for h in handles if h not in known]


def check_diagnosis(d: dict[str, Any], known: set[str]) -> list[str]:
    p: list[str] = []
    if len(d["hypotheses"]) > 5:
        p.append("at most 5 hypotheses")
    for h in d["hypotheses"]:
        p += _bad_handles(h["supporting"] + h["contradicting"], known)
        if len(h["statement"]) > MAX_TEXT["statement"]:
            p.append("hypothesis statement too long")
    for a in d["applicability"]:
        p += _bad_handles([a["evidence"]], known)
        if len(a["note"]) > MAX_TEXT["note"]:
            p.append("applicability note too long")
    if len(d["rationale"]) > MAX_TEXT["rationale"]:
        p.append("rationale too long")
    if any(len(x) > MAX_TEXT["missing"] for x in d["missing_evidence"]):
        p.append("missing_evidence item too long")
    return p


def check_proposals(d: dict[str, Any], known: set[str]) -> list[str]:
    p: list[str] = []
    if len(d["actions"]) > MAX_ACTIONS:
        p.append(f"at most {MAX_ACTIONS} actions")
    for i, a in enumerate(d["actions"]):
        t = a["action_type"]
        if t in A.FORBIDDEN:
            p.append(f"action {i}: '{t}' is forbidden; it can never be proposed")
        elif t not in A.ACTIONS:
            p.append(f"action {i}: '{t}' is not in the action vocabulary {sorted(A.ACTIONS)}")
        p += [f"action {i}: {x}" for x in _bad_handles(a["cited_evidence"], known)]
        if len(a["rationale"]) > MAX_TEXT["rationale"]:
            p.append(f"action {i}: rationale too long")
    return p


def check_draft(d: dict[str, Any], known: set[str], required_handles: set[str] = frozenset(), uncitable: set[str] = frozenset()) -> list[str]:
    p = _bad_handles(d["cited_evidence"], known)
    bad = sorted(set(d["cited_evidence"]) & set(uncitable))
    if bad:
        p.append(f"cited evidence {bad} was assessed as not applicable, stale, or containing instructions and cannot support a reply")
    if not d["draft"].strip():
        p.append("draft is empty")
    if len(d["draft"]) > MAX_TEXT["draft"]:
        p.append("draft too long")
    if redact(d["draft"]) != d["draft"]:
        p.append("draft contains credential-like text; never repeat secrets")
    if re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}", d["draft"]):
        p.append("draft contains an e-mail address; do not include personal addresses")
    miss = sorted(required_handles - set(d["cited_evidence"]))
    if miss:
        p.append(f"the draft must cite the conflicting evidence {miss} and say that they disagree")
    return p


def idempotency_key(case_id: str, action_type: str, params: dict[str, Any]) -> str:
    """Deterministic: the same case + type + parameters always gives the same key, across retries, restarts and repeated model output."""
    return "idem-" + hashlib.sha256(canonical_json({"case": case_id, "type": action_type, "params": params})).hexdigest()[:32]


def build_plan(d: dict[str, Any], case_id: str, handle_refs: dict[str, str]) -> Plan:
    """Model proposals -> typed actions. The model supplies ONLY type, params, citations and a rationale. Case, requester, role, ids and key are set here."""
    plan, seen = Plan(), set()
    for prop in d["actions"][:MAX_ACTIONS]:
        t, params = prop["action_type"], prop["params"]
        if t in A.FORBIDDEN:
            plan.rejected.append(Rejected(t, "FORBIDDEN_ACTION"))
            continue
        if t not in A.ACTIONS or A.ACTIONS[t][0] is A.Tier.READ:
            plan.rejected.append(Rejected(str(t)[:40], "UNKNOWN_ACTION_TYPE"))
            continue
        refs = sorted({handle_refs[h] for h in prop["cited_evidence"] if h in handle_refs})
        if not refs:
            plan.rejected.append(Rejected(t, "NO_VALID_EVIDENCE_CITED"))
            continue
        key = canonical_json({"t": t, "p": params})
        if key in seen:
            continue                                                  # repeated output: one proposal
        seen.add(key)
        raw = {"action_id": "ACT-" + idempotency_key(case_id, t, params)[5:17], "case_id": case_id, "requested_by": "agent", "evidence_refs": refs[:20], "idempotency_key": idempotency_key(case_id, t, params),
               "type": t, "required_role": A.ACTIONS[t][1], "params": params}
        try:
            A.validate_action(raw)
        except A.ActionRejected as e:
            plan.rejected.append(Rejected(t, e.code, e.detail[:120]))
            continue
        plan.actions.append(raw)
        plan.meta.append({"action_type": t, "rationale": prop["rationale"][:MAX_TEXT["rationale"]], "cited": prop["cited_evidence"], "evidence_refs": refs})
    return plan
