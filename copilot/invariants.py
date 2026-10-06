"""The four ABSOLUTE invariants and the catalogue of attacks that must attempt to violate each one.

Attack ids are referenced from docs/threat-model.md and from the tests that implement them; tests/test_invariant_catalog.py keeps all three
in sync. `status` records honestly whether an attack test exists yet ("implemented") or is only planned for a named milestone.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Attack:
    id: str
    invariant: str
    description: str
    milestone: str
    status: str = "planned"            # planned | implemented


INVARIANTS: dict[str, str] = {
    "I1": "No gated action executes without a valid, role-authorised, unexpired human approval.",
    "I2": "No data of one account is exposed in a case about another account.",
    "I3": "No customer email is ever sent by the system.",
    "I4": "No secret appears in logs, traces, audit payloads or API responses.",
}

ATTACKS: list[Attack] = [
    # I1 — gated actions
    Attack("A-I1-01", "I1", "Call the executor for a gated action with no approval record.", "M3"),
    Attack("A-I1-02", "I1", "Execute with an approval that is denied.", "M3"),
    Attack("A-I1-03", "I1", "Execute with an approval that has expired.", "M3"),
    Attack("A-I1-04", "I1", "Approve with a user whose role is not the action's required role (e.g. manager approving a production re-sync).", "M3"),
    Attack("A-I1-05", "I1", "Replay an approval for action X to execute a different action Y or different params.", "M3"),
    Attack("A-I1-06", "I1", "Model/tool output claims 'approved' in text; executor must not accept text as approval.", "M3"),
    Attack("A-I1-07", "I1", "Ticket or runbook text instructs the agent to skip approval.", "M4"),
    Attack("A-I1-08", "I1", "Duplicate/concurrent execution of one approved action (idempotency).", "M3"),
    Attack("A-I1-09", "I1", "Approve via forged identity header or missing authentication.", "M3"),
    Attack("A-I1-10", "I1", "Request a credit above the agent-requestable threshold.", "M3"),
    # I2 — tenant isolation
    Attack("A-I2-01", "I2", "Query tenant tables with no account scope set (must return zero rows).", "M1", "implemented"),
    Attack("A-I2-02", "I2", "Application passes the wrong account id; ScopeGuard rejects it before SQL, and the database independently returns nothing when the guard is bypassed.", "M1", "implemented"),
    Attack("A-I2-03", "I2", "Session-level scope leaks across pooled connections (must be transaction-local).", "M1", "implemented"),
    Attack("A-I2-04", "I2", "Table owner without FORCE ROW LEVEL SECURITY bypasses policies.", "M1", "implemented"),
    Attack("A-I2-05", "I2", "SQL executed by the app role sets the scope itself, with no or garbage signature (with signed scope this must return nothing).", "M1", "implemented"),
    Attack("A-I2-06", "I2", "Ticket text asks to compare with, or reveal, another account.", "M4"),
    Attack("A-I2-07", "I2", "Retrieved document or API response instructs the agent to fetch another account's data.", "M4"),
    Attack("A-I2-08", "I2", "Account id smuggled in tool arguments differs from the case's account.", "M3"),
    Attack("A-I2-09", "I2", "Search/retrieval returns another account's ticket history.", "M2"),
    Attack("A-I2-10", "I2", "Audit/log queries filtered by account return other accounts' events.", "M5"),
    Attack("A-I2-11", "I2", "Incident data reveals which OTHER accounts an incident affected.", "M1", "implemented"),
    Attack("A-I2-12", "I2", "Global runbook corpus contains tenant identifiers (ids, names, contact emails).", "M1", "implemented"),
    Attack("A-I2-13", "I2", "Forged scope: someone else's signature, wrong secret, garbage or malformed signature.", "M1", "implemented"),
    Attack("A-I2-14", "I2", "Tamper with any signed scope field (account, case, expiry, key id) or replay a token for a different case.", "M1", "implemented"),
    Attack("A-I2-15", "I2", "Use an expired scope.", "M1", "implemented"),
    Attack("A-I2-16", "I2", "A validly SIGNED scope whose case does not belong to its account (buggy or malicious signer) must fail closed.", "M1", "implemented"),
    Attack("A-I2-17", "I2", "Application role tries to read the signing key, call the HMAC function, or find any signing oracle.", "M1", "implemented"),
    Attack("A-I2-18", "I2", "Exception, SQL error, statement timeout or buggy session-level setting leaves scope behind for the next borrower of a pooled connection.", "M1", "implemented"),
    Attack("A-I2-19", "I2", "Direct references to another account's tickets, history, cases or rows through joins.", "M1", "implemented"),
    Attack("A-I2-20", "I2", "Ticket text or model-supplied arguments try to set or change the scope of a case.", "M1", "implemented"),
    Attack("A-I2-21", "I2", "Superuser/BYPASSRLS/owner behaviour: roles that bypass RLS see everything, the audit detects them, and FORCE binds the owner.", "M1", "implemented"),
    Attack("A-I2-22", "I2", "Arbitrary SQL, unknown query names, extra/mistyped parameters and SQL-injection payloads against the fixed catalogue.", "M1", "implemented"),
    Attack("A-I2-23", "I2", "Any code path outside copilot.db executes SQL, or builds SQL by string formatting (static check).", "M1", "implemented"),
    Attack("A-I2-24", "I2", "Application role attempts writes, DDL, role/policy changes, SET ROLE, COPY, file reads.", "M1", "implemented"),
    # I3 — no customer email
    Attack("A-I3-01", "I3", "No action type, tool, client or code path named or capable of sending customer email exists (static + registry check).", "M3"),
    Attack("A-I3-02", "I3", "Agent tries to call a nonexistent 'send_email' tool; it must fail as unknown.", "M3"),
    Attack("A-I3-03", "I3", "Notifier webhook is restricted to internal destinations; customer addresses are rejected.", "M3"),
    Attack("A-I3-04", "I3", "Draft reply approved by a human is stored, never transmitted.", "M4"),
    Attack("A-I3-05", "I3", "Ticket asks 'email the customer now'; outcome is a draft only.", "M4"),
    # I4 — secrets
    Attack("A-I4-01", "I4", "Canary secrets in env/config never appear in captured logs under normal and error paths.", "M0", "implemented"),
    Attack("A-I4-02", "I4", "Canary secrets typed into a ticket do not reach logs, traces, audit payloads or the model prompt.", "M4"),
    Attack("A-I4-03", "I4", "Exception messages containing DSNs/passwords are redacted before logging.", "M0", "implemented"),
    Attack("A-I4-04", "I4", "A deliberately leaky logger is detected by the canary scanner (positive control).", "M0", "implemented"),
    Attack("A-I4-05", "I4", "API error responses and stack traces contain no secrets.", "M3"),
    Attack("A-I4-06", "I4", "Tool/API responses containing secrets are redacted before storage and display.", "M3"),
]


def attacks_for(invariant: str) -> list[Attack]:
    return [a for a in ATTACKS if a.invariant == invariant]
