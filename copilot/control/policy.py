"""Deterministic policy engine. Inputs: a validated typed action and trusted FACTS. Output: a structured decision with machine-readable reason codes.

The model has no input to this function other than the validated action, and no way to override its result: ALLOW/REQUIRE_APPROVAL come only from rules over facts.
Retrieval scores are never used. Retrieval can only make the policy MORE cautious (a CONFLICTING knowledge outcome); it can never make anything allowed.

Evaluation order (architecture, not tuning): structural/tenant DENY  >  other hard DENY  >  ESCALATE on known facts (open incident, conflicting evidence)  >
ABSTAIN on missing facts  >  REQUIRE_APPROVAL / ALLOW_PROPOSAL.  Known conflicts and incidents are checked BEFORE missing-fact abstention, so a gap elsewhere can never mask them.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from .actions import ValidatedAction

POLICY_VERSION = "meridian-policy-1"
CONFIG = json.loads((Path(__file__).with_name("policy_config.json")).read_text())
LIMITATIONS = ("SEMANTIC_APPLICABILITY_NOT_ASSESSED: whether the runbook or diagnosis fits this ticket's symptoms needs judgement the policy engine does not have; the approver must assess it",)


class Decision(StrEnum):
    ALLOW_PROPOSAL = "ALLOW_PROPOSAL"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"
    ABSTAIN = "ABSTAIN"
    ESCALATE = "ESCALATE"


class Sufficiency(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    CONFLICTING = "CONFLICTING"


@dataclass(frozen=True)
class Facts:
    """Trusted facts, gathered by code (not by the model) through the signed-scope catalogue. `None` = could not be established."""
    case_id: str
    account_id: str | None
    case_status: str | None
    contract: dict[str, Any] | None
    integrations: dict[str, dict[str, Any]]
    open_incidents: tuple[dict[str, Any], ...]
    sla_breach_evidenced: bool | None
    now: datetime
    knowledge: str | None = None          # retrieval outcome name, if retrieval ran. Only ever used to be more cautious.
    gaps: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    reasons: tuple[str, ...]
    sufficiency: Sufficiency
    required_role: str | None = None
    missing_facts: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    limitations: tuple[str, ...] = LIMITATIONS
    details: dict[str, Any] = field(default_factory=dict)
    policy_version: str = POLICY_VERSION

    @property
    def requires_approval(self) -> bool:
        return self.decision is Decision.REQUIRE_APPROVAL

    def as_dict(self) -> dict[str, Any]:
        return {"decision": self.decision.value, "reasons": list(self.reasons), "sufficiency": self.sufficiency.value, "required_role": self.required_role,
                "missing_facts": list(self.missing_facts), "constraints": list(self.constraints), "limitations": list(self.limitations), "details": self.details, "policy_version": self.policy_version}


def _d(decision, reasons, suff, **kw) -> PolicyDecision:
    return PolicyDecision(decision, tuple(reasons), suff, **kw)


def _in_effect(c: dict[str, Any], today: date) -> bool:
    start, end = c.get("effective_from"), c.get("effective_to")
    return (start is None or start <= today) and (end is None or end >= today)


def decide(action: ValidatedAction, facts: Facts) -> PolicyDecision:
    p = action.params
    # ---- structural / tenant -------------------------------------------------------------------------------------------------------------
    if facts.account_id is None or facts.case_status is None:
        return _d(Decision.DENY, ["CASE_NOT_FOUND_IN_SCOPE"], Sufficiency.INSUFFICIENT)
    if facts.case_id != action.case_id:
        return _d(Decision.DENY, ["CASE_MISMATCH"], Sufficiency.INSUFFICIENT)
    if facts.case_status != "open":
        return _d(Decision.DENY, ["CASE_NOT_OPEN"], Sufficiency.INSUFFICIENT)
    role = action.required_role
    conflicting = facts.knowledge == "CONFLICTING_AUTHORITATIVE_EVIDENCE"
    t = action.type

    if t in ("draft_reply", "add_internal_note"):
        cons = ("SURFACE_CONFLICT_IN_DRAFT",) if conflicting else ()
        return _d(Decision.ALLOW_PROPOSAL, ["PROPOSAL_STORED_INTERNALLY_NO_EXTERNAL_EFFECT"] + (["KNOWLEDGE_CONFLICT_NOTED"] if conflicting else []), Sufficiency.CONFLICTING if conflicting else Sufficiency.SUFFICIENT,
                  required_role=role, constraints=cons + ("NEVER_TRANSMITTED",) if t == "draft_reply" else cons)

    if t == "trigger_resync":
        integ = facts.integrations.get(p["integration_id"])
        if integ is None:
            return _d(Decision.DENY, ["INTEGRATION_NOT_IN_ACCOUNT"], Sufficiency.INSUFFICIENT, details={"integration_id": p["integration_id"]})
        last = integ.get("last_resync_at")
        if last is not None and facts.now - last < timedelta(hours=CONFIG["resync_cooldown_hours"]):
            return _d(Decision.DENY, ["RESYNC_COOLDOWN_ACTIVE"], Sufficiency.SUFFICIENT, details={"last_resync_at": last.isoformat(), "cooldown_hours": CONFIG["resync_cooldown_hours"]})
        blocking = [i for i in facts.open_incidents if integ.get("kind") == "carrier_feed" and i.get("component") in CONFIG["resync_blocking_components"]]
        if blocking:
            return _d(Decision.ESCALATE, ["OPEN_INCIDENT_BLOCKS_RESYNC", "ESCALATE_TO_ENGINEERING"], Sufficiency.SUFFICIENT, details={"incident_ids": sorted(i["incident_id"] for i in blocking)})
        if conflicting:
            return _d(Decision.ESCALATE, ["CONFLICTING_EVIDENCE_NEEDS_HUMAN"], Sufficiency.CONFLICTING)
        missing = tuple(m for m, ok in (("integration.status", integ.get("status") not in (None, "unknown")), ("integration.last_sync_at", integ.get("last_sync_at") is not None)) if not ok) + facts.gaps
        if missing:
            return _d(Decision.ABSTAIN, ["EVIDENCE_INSUFFICIENT"], Sufficiency.INSUFFICIENT, missing_facts=missing)
        return _d(Decision.REQUIRE_APPROVAL, ["APPROVAL_REQUIRED_PRODUCTION_ACTION"], Sufficiency.SUFFICIENT, required_role=role,
                  details={"integration_status": integ["status"], "last_sync_at": integ["last_sync_at"].isoformat(), "last_resync_at": last.isoformat() if last else None, "blast_radius": p["blast_radius"]})

    if t == "request_sla_credit":
        c = facts.contract
        if c is not None and not _in_effect(c, facts.now.date()):
            c = None
        if c is not None and p["percent"] > c["max_agent_requestable_pct"]:
            return _d(Decision.DENY, ["CREDIT_ABOVE_AGENT_THRESHOLD"], Sufficiency.SUFFICIENT, constraints=("FLAG_FOR_FINANCE_DO_NOT_REQUEST",),
                      details={"requested_pct": p["percent"], "max_agent_requestable_pct": c["max_agent_requestable_pct"]})
        if facts.sla_breach_evidenced is False:
            return _d(Decision.DENY, ["NO_SLA_BREACH_EVIDENCED_IN_TICKET_HISTORY"], Sufficiency.SUFFICIENT)
        if conflicting:
            return _d(Decision.ESCALATE, ["CONFLICTING_EVIDENCE_NEEDS_HUMAN"], Sufficiency.CONFLICTING)
        missing = tuple(m for m, ok in (("contract.in_effect", c is not None), ("ticket_history.sla_breach_evidence", facts.sla_breach_evidenced is not None)) if not ok) + facts.gaps
        if missing:
            return _d(Decision.ABSTAIN, ["EVIDENCE_INSUFFICIENT"], Sufficiency.INSUFFICIENT, missing_facts=missing)
        return _d(Decision.REQUIRE_APPROVAL, ["APPROVAL_REQUIRED_CREDIT_WITHIN_POLICY"], Sufficiency.SUFFICIENT, required_role=role,
                  details={"requested_pct": p["percent"], "max_agent_requestable_pct": c["max_agent_requestable_pct"], "tier": c["tier"]})

    if t == "escalate_engineering":
        inc_id = p["incident_id"]
        linked = None
        if inc_id is not None:
            linked = next((i for i in facts.open_incidents if i["incident_id"] == inc_id), None)
            if linked is None:
                return _d(Decision.DENY, ["INCIDENT_NOT_OPEN_FOR_ACCOUNT"], Sufficiency.INSUFFICIENT, details={"incident_id": inc_id})
        cons = ("CONSIDER_LINKING_OPEN_INCIDENT",) if inc_id is None and facts.open_incidents else ()
        if facts.gaps:
            return _d(Decision.ABSTAIN, ["EVIDENCE_INSUFFICIENT"], Sufficiency.INSUFFICIENT, missing_facts=facts.gaps)
        return _d(Decision.REQUIRE_APPROVAL, ["APPROVAL_REQUIRED_ENGINEERING_ESCALATION"] + (["INCIDENT_LINKED_AND_VERIFIED_OPEN"] if linked else []), Sufficiency.CONFLICTING if conflicting else Sufficiency.SUFFICIENT,
                  required_role=role, constraints=cons, details={"incident_id": inc_id, "severity": p["severity"]})

    return _d(Decision.DENY, ["UNHANDLED_ACTION_TYPE"], Sufficiency.INSUFFICIENT)       # fail closed: a registered type without a rule is never allowed


SLA_MISSED = re.compile(r"^SLA (response|resolution) target of \d+ (minutes|hours) was missed\.?$")
