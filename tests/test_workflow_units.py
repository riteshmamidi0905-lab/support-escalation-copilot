"""Workflow building blocks without a database: transition table coherence, trust boundary, stand-in model, externals, schemas."""
import json
import re

import pytest

from copilot.control import actions as A
from copilot.control import mocks
from copilot.workflow import externals as X
from copilot.workflow import providers as P
from copilot.workflow import schemas as S
from copilot.workflow import trust
from copilot.workflow.model_io import ModelStage
from copilot.workflow.providers import FaultyModel, RuleCaseModel
from copilot.workflow.states import EDGES, STATES, TERMINAL

EV = [{"handle": "E1", "doc_id": "RBK-0019", "version": "2.0", "status": "active", "title": "Duplicate shipment events from a carrier feed", "section": "s", "citation": "c", "text": "<untrusted>If the gateway is below 3.0.2 a feed re-sync clears duplicates.</untrusted>", "flags": [], "conflicts_with": [], "duplicates": []}]


def ctx(subject="Duplicate shipment events", body="events arrive twice on INT-0001", area="carrier_integrations", incidents=()):
    return {"ticket": {"id": "TCK-1", "subject": subject, "body": body, "severity": "P3", "product_area": area}, "evidence": EV, "retrieval_outcome": "EVIDENCE", "verification": {"status": "verified", "checks": []},
            "facts": {"tier": "Premier", "agent_requestable_credit_pct": 5.0, "integrations": [{"integration_id": "INT-0001", "kind": "carrier_feed", "provider": "x", "status": "failing", "last_sync_at": "2026-03-02T10:00:00Z", "last_resync_at": None}],
                      "open_incidents": list(incidents), "sla_breach_evidenced": True}}


def test_state_graph_is_closed_terminal_states_are_sinks_and_every_state_is_reachable():
    srcs = {s for s, _ in EDGES}
    assert not srcs & TERMINAL
    reach, frontier = {"NEW"}, ["NEW"]
    while frontier:
        s = frontier.pop()
        for a, b in EDGES:
            if a == s and b not in reach:
                reach.add(b)
                frontier.append(b)
    assert reach == set(STATES)
    assert [e for e in EDGES if e[1] == "NEW"] == [], "nothing leads back to NEW"


def _keys(node, out=None):
    out = set() if out is None else out
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "properties":
                out |= set(v)
            _keys(v, out)
    elif isinstance(node, list):
        for v in node:
            _keys(v, out)
    return out


def test_schemas_have_no_field_for_chain_of_thought_state_tenant_or_approval():
    forbidden = {"thought", "thoughts", "reasoning", "chain_of_thought", "scratchpad", "tenant", "account_id", "account", "approved", "approval", "approval_id", "required_role", "role", "expires_at", "expiry",
                 "state", "workflow_state", "evidence_sufficient", "sufficient", "sql", "scope", "idempotency_key", "case_id", "requested_by"}
    for sch in (S.DIAGNOSIS, S.PROPOSED_ACTIONS, S.DRAFT_REPLY):
        assert not _keys(sch) & forbidden, _keys(sch) & forbidden


def test_trust_build_plan_sets_everything_but_type_params_citations_and_rationale():
    d = {"actions": [{"action_type": "trigger_resync", "params": {"integration_id": "INT-0001", "blast_radius": "one feed"}, "cited_evidence": ["E1"], "rationale": "r"}], "rationale": "x"}
    plan = trust.build_plan(d, "CASE-000001", {"E1": "RBK-0019@2.0"})
    a = plan.actions[0]
    assert a["required_role"] == "on_call_sre" and a["requested_by"] == "agent" and a["case_id"] == "CASE-000001" and a["evidence_refs"] == ["RBK-0019@2.0"] and a["idempotency_key"].startswith("idem-")
    again = trust.build_plan(d, "CASE-000001", {"E1": "RBK-0019@2.0"}).actions[0]
    assert again == a, "identical model output => identical action, id and idempotency key (retries and restarts are idempotent)"
    other = trust.build_plan(d, "CASE-000002", {"E1": "RBK-0019@2.0"}).actions[0]
    assert other["idempotency_key"] != a["idempotency_key"]


@pytest.mark.parametrize("prop,code", [
    ({"action_type": "send_customer_email", "params": {}, "cited_evidence": ["E1"], "rationale": "r"}, "FORBIDDEN_ACTION"),
    ({"action_type": "delete_everything", "params": {}, "cited_evidence": ["E1"], "rationale": "r"}, "UNKNOWN_ACTION_TYPE"),
    ({"action_type": "retrieve_evidence", "params": {}, "cited_evidence": ["E1"], "rationale": "r"}, "UNKNOWN_ACTION_TYPE"),
    ({"action_type": "trigger_resync", "params": {"integration_id": "INT-0001", "blast_radius": "x"}, "cited_evidence": [], "rationale": "r"}, "NO_VALID_EVIDENCE_CITED"),
    ({"action_type": "trigger_resync", "params": {"integration_id": "INT-0001", "blast_radius": "x"}, "cited_evidence": ["E77"], "rationale": "r"}, "NO_VALID_EVIDENCE_CITED"),
    ({"action_type": "trigger_resync", "params": {"integration_id": "INT-0001", "blast_radius": "x", "account_id": "ACC-0002"}, "cited_evidence": ["E1"], "rationale": "r"}, "SCHEMA_INVALID"),
    ({"action_type": "request_sla_credit", "params": {"percent": 5, "reason": "key sk-CANARYAPIKEY0123456789abcdef"}, "cited_evidence": ["E1"], "rationale": "r"}, "SECRET_IN_PARAMS"),
])
def test_trust_boundary_rejects_every_kind_of_bad_proposal(prop, code):
    plan = trust.build_plan({"actions": [prop], "rationale": "x"}, "CASE-000001", {"E1": "RBK-0019@2.0"})
    assert plan.actions == [] and [r.code for r in plan.rejected] == [code]


def test_draft_checks_block_secrets_emails_uncitable_evidence_and_missing_conflict_disclosure():
    known = {"E1", "E2"}
    ok = {"draft": "Hello, see runbook.", "cited_evidence": ["E1"], "limitations": []}
    assert trust.check_draft(ok, known) == []
    assert trust.check_draft({**ok, "draft": "key sk-CANARYAPIKEY0123456789abcdef"}, known)
    assert trust.check_draft({**ok, "draft": "write to jo.doe@quarryexpress.example"}, known)
    assert trust.check_draft({**ok, "cited_evidence": ["E9"]}, known)
    assert trust.check_draft(ok, known, uncitable={"E1"})
    assert trust.check_draft(ok, known, required_handles={"E1", "E2"})
    assert trust.check_draft({**ok, "draft": "   "}, known)


def test_stand_in_proposes_actions_but_never_chooses_role_state_or_approval():
    m = RuleCaseModel()
    plan = m.plan(ctx())
    assert [a["action_type"] for a in plan["actions"]] == ["trigger_resync"]
    for a in plan["actions"]:
        assert set(a) == {"action_type", "params", "cited_evidence", "rationale"}
    d = m.diagnose(ctx())
    assert set(d) == set(S.DIAGNOSIS["properties"]) and d["disposition"] == "proceed"


def test_stand_in_follows_runbook_rules_visible_in_facts_incident_and_recent_resync():
    inc = [{"incident_id": "INC-0001", "component": "carrier_gateway", "severity": "SEV3", "title": "t"}]
    assert RuleCaseModel().plan(ctx(incidents=inc))["actions"][0]["action_type"] == "escalate_engineering"
    c = ctx()
    c["facts"]["integrations"][0]["last_resync_at"] = "2026-03-01T12:00:00Z"
    assert RuleCaseModel().plan(c)["actions"][0]["action_type"] == "escalate_engineering"
    assert RuleCaseModel().plan(ctx("Duplicate invoice generated", "we received the same invoice twice", "billing_invoicing"))["actions"] == []


def test_faulty_model_wraps_any_provider_and_misbehaves_on_demand():
    from agent.model import Message
    msgs = [Message("system", "STAGE:PLAN"), Message("user", "CONTEXT_JSON:\n" + json.dumps(ctx()))]
    f = FaultyModel(RuleCaseModel(), {"PLAN": [P.hallucinate_actions, None, P.outage_error()]})
    first = json.loads(f.complete(msgs).content)
    assert {"send_customer_email", "delete_account"} <= {a["action_type"] for a in first["actions"]}
    assert json.loads(f.complete(msgs).content)["actions"][0]["action_type"] == "trigger_resync"
    from agent.model import ProviderError
    with pytest.raises(ProviderError):
        f.complete(msgs)


def test_model_stage_repairs_invalid_json_and_reports_codes_for_failures():
    from agent.model import ProviderError
    prov = FaultyModel(RuleCaseModel(), {"DRAFT": [P.not_json]})
    c = {**ctx(), "outcome": "ANSWER", "disposition": None, "conflict_handles": [], "plan": []}
    ok = ModelStage(prov).structured("DRAFT", "x", c, S.DRAFT_REPLY, lambda d: [])
    assert ok.data and ok.repairs >= 1 and ok.error is None
    bad = ModelStage(FaultyModel(RuleCaseModel(), {"DRAFT": [P.not_json] * 9})).structured("DRAFT", "x", c, S.DRAFT_REPLY, lambda d: [])
    assert bad.error == "MODEL_OUTPUT_INVALID" and bad.data is None
    out = ModelStage(FaultyModel(RuleCaseModel(), {"DRAFT": [ProviderError("HTTP 503", retryable=False, kind="http")] * 2})).structured("DRAFT", "x", c, S.DRAFT_REPLY, lambda d: [])
    assert out.error == "MODEL_UNAVAILABLE"
    tmo = ModelStage(FaultyModel(RuleCaseModel(), {"DRAFT": [ProviderError("timed out", retryable=True, kind="network")] * 9})).structured("DRAFT", "x", c, S.DRAFT_REPLY, lambda d: [])
    assert tmo.error == "MODEL_TIMEOUT"
    semantic = ModelStage(RuleCaseModel(), semantic_repairs=1).structured("DRAFT", "x", c, S.DRAFT_REPLY, lambda d: ["always unhappy"])
    assert semantic.problems == ["always unhappy"]


def test_guarded_client_retries_then_opens_and_closes_its_breaker():
    from copilot.control.clock import FakeClock
    clock = FakeClock()
    api = X.StatusAPI([{"integration_id": "INT-0001", "account_id": "ACC-0001", "status": "failing", "last_sync_at": "t", "provider": "p", "kind": "carrier_feed"}], mocks.FaultScript(["transient"] * 6))
    g = X.GuardedClient(attempts=3, breaker_after=6, clock=clock)
    with pytest.raises(X.ExternalUnavailable):
        g.call(api, api.get_integration_status, "ACC-0001", "INT-0001")
    assert api.calls == 3
    with pytest.raises(X.ExternalUnavailable):
        g.call(api, api.get_integration_status, "ACC-0001", "INT-0001")
    with pytest.raises(X.ExternalUnavailable) as e:
        g.call(api, api.get_integration_status, "ACC-0001", "INT-0001")
    assert e.value.code == "BREAKER_OPEN" and api.calls == 6
    api.faults = mocks.FaultScript()
    clock.advance(seconds=31)
    assert g.call(api, api.get_integration_status, "ACC-0001", "INT-0001")["status"] == "failing"
    with pytest.raises(X.NotFound):
        g.call(api, api.get_integration_status, "ACC-0002", "INT-0001")                    # another tenant's account: 404, no oracle


def test_workflow_package_has_no_email_network_or_sql_capability():                       # A-I3-01 / A-I2-23 for the workflow
    import ast
    from pathlib import Path
    banned = {"smtplib", "email", "socket", "ssl", "http", "urllib", "requests", "httpx", "subprocess", "psycopg"}
    root = Path(__file__).resolve().parent.parent / "copilot" / "workflow"
    for p in root.glob("*.py"):
        mods = set()
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.Import):
                mods |= {x.name.split(".")[0] for x in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                mods.add(n.module.split(".")[0])
        allowed = {"urllib"} if p.name == "providers.py" else set()                       # providers.py: the local-server probe (127.0.0.1) only
        allowed |= {"psycopg"} if p.name == "machine.py" else set()
        assert not (mods & banned) - allowed, (p.name, (mods & banned) - allowed)
    src = (root / "runner.py").read_text()
    assert not re.search(r"\.execute\(\s*f?[\"']", src), "the runner executes no SQL of its own"
    assert set(A.ACTIONS) == {"draft_reply", "add_internal_note", "escalate_engineering", "request_sla_credit", "trigger_resync"}
