"""Gather the trusted facts the policy needs, through the fixed query catalogue under the database-verified signed scope (the model-facing application role).
A fact that cannot be established is recorded as a GAP (or None) so the policy abstains instead of assuming."""
from __future__ import annotations

from datetime import datetime

from copilot.db import queries as Q

from .policy import SLA_MISSED, Facts


def gather(pool, scope, guard, case_id: str, now: datetime, knowledge: str | None = None) -> Facts:
    """Raises ScopeError for a bad scope (the caller must treat that as a refusal, never as 'no facts')."""
    gaps: list[str] = []
    case = Q.run(pool, scope, guard, "get_case", {"case_id": case_id})
    if not case:
        return Facts(case_id, None, None, None, {}, (), None, now, knowledge, ("case",))
    c = case[0]
    contracts = Q.run(pool, scope, guard, "list_contracts", {"account_id": scope.account_id})
    contract = next((x for x in contracts if x["effective_from"] <= now.date() and (x.get("effective_to") is None or x["effective_to"] >= now.date())), None) if contracts else None
    if contract is not None:
        contract = {**contract, **{k: float(contract[k]) for k in ("max_agent_requestable_pct", "max_manager_approvable_pct")}}      # numeric -> float (JSON-safe)
    integrations = {r["integration_id"]: r for r in Q.run(pool, scope, guard, "list_integrations", {"account_id": scope.account_id})}
    incidents = tuple(Q.run(pool, scope, guard, "open_incidents_for_account", {}))
    history = Q.run(pool, scope, guard, "ticket_history", {"ticket_id": c["ticket_id"]})
    # SLA breach evidence = an entry written by the system SLA monitor (an author the customer cannot choose). Customer text claiming a breach is not evidence.
    breach = any(h["author"] == "sla-monitor" and SLA_MISSED.match(h["text"]) for h in history) if history else False
    return Facts(case_id, c["account_id"], c["status"], contract, integrations, incidents, breach, now, knowledge, tuple(gaps))
