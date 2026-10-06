import copy

import pytest

from copilot import contracts as C

BASE = {"action_id": "ACT-1", "case_id": "CASE-000001", "requested_by": "agent", "evidence_refs": ["RBK-0002"], "idempotency_key": "case1-act1-0123456789"}


def action(t, role, params):
    return {**BASE, "type": t, "required_role": role, "params": params}


GOOD = [
    action("draft_reply", "tier2_engineer", {"body": "Hello"}),
    action("add_internal_note", "tier2_engineer", {"text": "note"}),
    action("escalate_engineering", "tier2_engineer", {"severity": "P1", "summary": "s", "incident_id": None}),
    action("request_sla_credit", "support_manager", {"percent": 5, "reason": "SLA breach"}),
    action("trigger_resync", "on_call_sre", {"integration_id": "INT-0001", "blast_radius": "one account feed"}),
]


@pytest.mark.parametrize("a", GOOD, ids=lambda a: a["type"])
def test_valid_typed_actions(a):
    assert C.validate_record("action", a) == []


def test_there_is_no_way_to_express_sending_customer_email():
    """I3 at the contract level: no action type can mean 'send email'."""
    for t in ("send_customer_email", "send_email", "email_customer", "send_reply"):
        assert C.validate_record("action", action(t, "tier2_engineer", {"to": "x@y.example", "body": "hi"})), t
    schema_types = {s["properties"]["type"]["const"] for s in C.validator("action").schema["oneOf"]}
    assert not any("email" in t or "send" in t for t in schema_types)


@pytest.mark.parametrize("mutate,why", [
    (lambda a: a.update(required_role="support_manager"), "draft needs tier2 role, not manager"),
    (lambda a: a["params"].update(extra="x"), "unexpected param"),
    (lambda a: a.pop("idempotency_key"), "idempotency key mandatory"),
    (lambda a: a.update(idempotency_key="short"), "idempotency key too short"),
    (lambda a: a.pop("evidence_refs"), "evidence mandatory"),
    (lambda a: a.update(evidence_refs=[]), "evidence non-empty"),
    (lambda a: a.update(requested_by="user"), "only the agent proposes"),
])
def test_invalid_actions_rejected(mutate, why):
    a = copy.deepcopy(GOOD[0])
    mutate(a)
    assert C.validate_record("action", a), why


def test_role_is_bound_to_type():
    assert C.validate_record("action", action("trigger_resync", "support_manager", GOOD[4]["params"]))
    assert C.validate_record("action", action("request_sla_credit", "on_call_sre", GOOD[3]["params"]))


def test_credit_percent_bounds():
    for bad in (0, -1, 101):
        assert C.validate_record("action", action("request_sla_credit", "support_manager", {"percent": bad, "reason": "r"}))
