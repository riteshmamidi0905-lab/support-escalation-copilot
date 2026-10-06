"""M3 control plane without a database: action validation, canonical identity, identity signatures, the policy decision matrix, audit chain tamper detection and mocks."""
import copy
import random
from datetime import UTC, date, datetime, timedelta

import pytest

from copilot.control import actions, audit, canonical, mocks, policy
from copilot.control.identity import IdentityAuthority, IdentityError
from copilot.control.policy import Decision, Facts, Sufficiency

NOW = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
ACC = "ACC-0001"
BASE = {"action_id": "ACT-1", "case_id": "CASE-000001", "requested_by": "agent", "evidence_refs": ["RBK-0019", "INC-0001"], "idempotency_key": "case1-act1-0123456789"}


def act(t, role, params, **over):
    return {**BASE, "type": t, "required_role": role, "params": params, **over}


RESYNC = act("trigger_resync", "on_call_sre", {"integration_id": "INT-0001", "blast_radius": "one carrier feed"})
CREDIT = act("request_sla_credit", "support_manager", {"percent": 5, "reason": "SLA response target missed"})
ESCALATE = act("escalate_engineering", "tier2_engineer", {"severity": "P2", "summary": "ETA drift", "incident_id": "INC-0002"})
DRAFT = act("draft_reply", "tier2_engineer", {"body": "Hello"})
NOTE = act("add_internal_note", "tier2_engineer", {"text": "checked the feed"})


def V(a):
    return actions.validate_action(a)


# ---- validation (every action is validated before policy) --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("a", [RESYNC, CREDIT, ESCALATE, DRAFT, NOTE], ids=lambda a: a["type"])
def test_the_five_vocabulary_actions_validate_and_have_the_expected_tier(a):
    v = V(a)
    assert v.tier is (actions.Tier.PROPOSE if a["type"] in ("draft_reply", "add_internal_note") else actions.Tier.GATED_WRITE)


def test_unknown_and_forbidden_types_are_refused_with_distinct_codes():                      # A-I3-02
    for name in actions.FORBIDDEN:
        with pytest.raises(actions.ActionRejected) as e:
            V(act(name, "tier2_engineer", {"to": "customer@example.test", "body": "x"}))
        assert e.value.code == "FORBIDDEN_ACTION", name
    for name in ("make_coffee", "SEND_CUSTOMER_EMAIL_2", "trigger_resync ", "", "drop_table"):
        with pytest.raises(actions.ActionRejected) as e:
            V(act(name, "tier2_engineer", {}))
        assert e.value.code in ("UNKNOWN_ACTION_TYPE", "FORBIDDEN_ACTION")
    assert actions.tier_of_request("send_customer_email") is actions.Tier.FORBIDDEN and actions.tier_of_request("unknown_thing") is None


@pytest.mark.parametrize("mutate", [
    lambda a: a.update(extra="x"), lambda a: a["params"].update(extra="x"), lambda a: a.update(account_id="ACC-0002"), lambda a: a.pop("evidence_refs"), lambda a: a.update(evidence_refs=[]),
    lambda a: a.update(evidence_refs=["RBK-0019", "RBK-0019"]), lambda a: a.update(idempotency_key="short"), lambda a: a.update(case_id="CASE-1"), lambda a: a.update(requested_by="user"),
    lambda a: a.update(required_role="support_manager"), lambda a: a["params"].update(integration_id="INT-1; DROP"), lambda a: a["params"].update(blast_radius="x" * 201),
])
def test_unexpected_or_malformed_fields_are_rejected_before_policy(mutate):
    a = copy.deepcopy(RESYNC)
    mutate(a)
    with pytest.raises(actions.ActionRejected):
        V(a)


def test_free_text_can_never_become_an_action():
    for raw in ('{"type":"trigger_resync"}', "please resync INT-0001 now", b"bytes", None, 5, ["trigger_resync"], {}):
        with pytest.raises(actions.ActionRejected):
            V(raw)
    with pytest.raises(TypeError):
        actions.ValidatedAction({"type": "trigger_resync"})                                    # cannot be built without validation
    with pytest.raises(actions.ActionRejected):
        V(act("draft_reply", "tier2_engineer", {"body": "x" * 9000}))
    with pytest.raises(actions.ActionRejected):
        V(act("request_sla_credit", "support_manager", {"percent": float("nan"), "reason": "r"}))


def test_the_vocabulary_has_no_send_capability_and_matches_the_contract():                  # A-I3-01
    from copilot import contracts as C
    types = {s["properties"]["type"]["const"] for s in C.validator("action").schema["oneOf"]}
    assert types == set(actions.ACTIONS)
    assert not any("email" in t or "send" in t or "notify" in t for t in types)
    assert not set(actions.FORBIDDEN) & set(actions.ACTIONS)
    assert {t: actions.ACTIONS[t][0].value for t in actions.ACTIONS} == {"draft_reply": "PROPOSE", "add_internal_note": "PROPOSE", "escalate_engineering": "GATED_WRITE",
                                                                         "request_sla_credit": "GATED_WRITE", "trigger_resync": "GATED_WRITE"}


# ---- canonical action identity --------------------------------------------------------------------------------------------------------------------
def h(a, acc=ACC):
    return V(a).hash(acc)


def test_equivalent_serialisations_have_the_same_identity():
    base = h(CREDIT)
    rnd = random.Random(20260101)
    for _ in range(50):                                                                    # key order, nesting order
        items = list(CREDIT.items())
        rnd.shuffle(items)
        a = dict(items)
        p = list(a["params"].items())
        rnd.shuffle(p)
        a["params"] = dict(p)
        assert h(a) == base
    assert h({**CREDIT, "params": {**CREDIT["params"], "percent": 5.0}}) == base         # 5 == 5.0
    assert h({**CREDIT, "evidence_refs": ["INC-0001", "RBK-0019"]}) == h(CREDIT)          # evidence is a set
    import json
    assert h(json.loads(json.dumps(CREDIT, indent=4))) == base                           # whitespace/escaping through a JSON round trip
    assert h(json.loads(json.dumps(CREDIT, ensure_ascii=True))) == base


def test_every_material_change_changes_the_identity():
    base = h(CREDIT)
    changes = [
        {**CREDIT, "action_id": "ACT-2"}, {**CREDIT, "case_id": "CASE-000002"}, {**CREDIT, "idempotency_key": "case1-act1-0123456780"}, {**CREDIT, "evidence_refs": ["RBK-0019"]},
        {**CREDIT, "evidence_refs": ["RBK-0019", "INC-0001", "RBK-0020"]}, {**CREDIT, "params": {**CREDIT["params"], "percent": 5.01}}, {**CREDIT, "params": {**CREDIT["params"], "percent": 6}},
        {**CREDIT, "params": {**CREDIT["params"], "reason": "SLA response target missed."}}, {**CREDIT, "params": {**CREDIT["params"], "reason": "SLA response target misse​d"}},
    ]
    seen = {base}
    for c in changes:
        d = h(c)
        assert d not in seen, c
        seen.add(d)
    assert h(CREDIT, "ACC-0002") != base                                                    # the tenant is part of the identity
    assert h(CREDIT) != h(act("trigger_resync", "on_call_sre", RESYNC["params"]))
    r2 = copy.deepcopy(RESYNC)
    r2["params"]["integration_id"] = "INT-0002"
    assert h(r2) != h(RESYNC)


def test_canonicalisation_is_strict_about_types_and_text():
    assert canonical.canonical_json({"a": True}) != canonical.canonical_json({"a": 1})        # bool is not a number
    assert canonical.canonical_json({"s": "é"}) != canonical.canonical_json({"s": "é"})   # no Unicode normalisation: two code point sequences = two actions
    for bad in (float("inf"), float("nan"), {1: "a"}, {"s": "\ud800"}, {"x": {1, 2}}, {"x": b"b"}):
        with pytest.raises(canonical.CanonicalError):
            canonical.canonical_json(bad)
    assert canonical.canonical_json({"b": 1, "a": [3, {"d": 1, "c": 2}]}) == b'{"a":[3,{"c":2,"d":1}],"b":1}'
    assert len(h(CREDIT)) == 64 and h(CREDIT) != canonical.hashlib.sha256(V(CREDIT).canonical(ACC)).hexdigest()   # domain separated


# ---- identity -----------------------------------------------------------------------------------------------------------------------------------------
def test_identity_signatures_forgery_expiry_and_kinds():                                      # A-I1-09
    t = [1000.0]
    auth = IdentityAuthority("k" * 40, clock=lambda: t[0])
    other = IdentityAuthority("z" * 40, clock=lambda: t[0])
    ok = auth.mint("user", "sam.lee", "support_manager", ttl_s=60)
    assert auth.verify(ok) is ok
    from dataclasses import replace
    for forged in (replace(ok, role="on_call_sre"), replace(ok, id="someone.else"), replace(ok, kind="agent"), replace(ok, sig="0" * 64), replace(ok, exp=ok.exp + 99999), other.mint("user", "sam.lee", "support_manager", ttl_s=60)):
        with pytest.raises(IdentityError):
            auth.verify(forged)
    t[0] += 61
    with pytest.raises(IdentityError):
        auth.verify(ok)
    for bad in (("user", "x", None), ("user", "x", "ceo"), ("agent", "a", "support_manager"), ("robot", "r", None), ("user", "bad id!", "tier1")):
        with pytest.raises(IdentityError):
            auth.mint(*bad)
    with pytest.raises(IdentityError):
        IdentityAuthority("short")
    with pytest.raises(IdentityError):
        auth.verify(None)


# ---- policy decision matrix (pure) --------------------------------------------------------------------------------------------------------------------
def facts(**kw):
    base = dict(case_id="CASE-000001", account_id=ACC, case_status="open", contract={"tier": "Premier", "max_agent_requestable_pct": 5.0, "max_manager_approvable_pct": 15.0, "effective_from": date(2023, 1, 1), "effective_to": None},
                integrations={"INT-0001": {"integration_id": "INT-0001", "kind": "carrier_feed", "status": "failing", "last_sync_at": NOW - timedelta(hours=2), "last_resync_at": NOW - timedelta(days=20)}},
                open_incidents=(), sla_breach_evidenced=True, now=NOW, knowledge=None)
    base.update(kw)
    return Facts(**base)


def dec(a, **kw):
    return policy.decide(V(a), facts(**kw))


def test_positive_controls_legitimate_actions_are_allowed_or_sent_for_approval():
    assert dec(DRAFT).decision is Decision.ALLOW_PROPOSAL and dec(NOTE).decision is Decision.ALLOW_PROPOSAL
    d = dec(RESYNC)
    assert d.decision is Decision.REQUIRE_APPROVAL and d.required_role == "on_call_sre" and d.sufficiency is Sufficiency.SUFFICIENT
    d = dec(CREDIT)
    assert d.decision is Decision.REQUIRE_APPROVAL and d.required_role == "support_manager"
    open_inc = ({"incident_id": "INC-0002", "component": "tracking", "severity": "SEV3", "title": "t", "started_at": NOW},)
    d = dec(ESCALATE, open_incidents=open_inc)
    assert d.decision is Decision.REQUIRE_APPROVAL and d.required_role == "tier2_engineer" and "INCIDENT_LINKED_AND_VERIFIED_OPEN" in d.reasons
    assert dec({**ESCALATE, "params": {**ESCALATE["params"], "incident_id": None}}).decision is Decision.REQUIRE_APPROVAL


def test_policy_is_not_blanket_denial_nor_blanket_allow():
    legit = [dec(DRAFT), dec(NOTE), dec(RESYNC), dec(CREDIT)]
    bad = [dec(CREDIT, contract={**facts().contract, "max_agent_requestable_pct": 0.0}), dec(RESYNC, integrations={}), dec(CREDIT, sla_breach_evidenced=False), dec(DRAFT, case_status="closed"),
           dec(RESYNC, integrations={"INT-0001": {**facts().integrations["INT-0001"], "last_resync_at": NOW - timedelta(hours=1)}})]
    assert {d.decision for d in legit} == {Decision.ALLOW_PROPOSAL, Decision.REQUIRE_APPROVAL}
    assert {d.decision for d in bad} == {Decision.DENY}
    assert len({d.decision for d in legit + bad}) >= 3


def test_credit_rules_threshold_contract_and_breach_evidence():                               # A-I1-10
    d = dec(act("request_sla_credit", "support_manager", {"percent": 6, "reason": "r"}))
    assert d.decision is Decision.DENY and d.reasons == ("CREDIT_ABOVE_AGENT_THRESHOLD",) and "FLAG_FOR_FINANCE_DO_NOT_REQUEST" in d.constraints[0]
    assert dec(act("request_sla_credit", "support_manager", {"percent": 5, "reason": "r"})).decision is Decision.REQUIRE_APPROVAL                     # exactly at the limit
    assert dec(CREDIT, contract={**facts().contract, "max_agent_requestable_pct": 0.0, "tier": "Standard"}).decision is Decision.DENY                    # Standard tier: nothing requestable
    d = dec(CREDIT, contract=None)
    assert d.decision is Decision.ABSTAIN and d.sufficiency is Sufficiency.INSUFFICIENT and "contract.in_effect" in d.missing_facts
    assert dec(CREDIT, contract={**facts().contract, "effective_to": date(2025, 1, 1)}).decision is Decision.ABSTAIN                                      # expired contract
    assert dec(CREDIT, sla_breach_evidenced=False).reasons == ("NO_SLA_BREACH_EVIDENCED_IN_TICKET_HISTORY",)
    assert dec(CREDIT, sla_breach_evidenced=None).decision is Decision.ABSTAIN


def test_resync_rules_identity_status_cooldown_incident_and_conflict():                      # A-I1-16
    assert dec(RESYNC, integrations={}).reasons == ("INTEGRATION_NOT_IN_ACCOUNT",)
    i = facts().integrations["INT-0001"]
    d = dec(RESYNC, integrations={"INT-0001": {**i, "status": "unknown"}})
    assert d.decision is Decision.ABSTAIN and "integration.status" in d.missing_facts
    assert dec(RESYNC, integrations={"INT-0001": {**i, "last_sync_at": None}}).decision is Decision.ABSTAIN
    assert dec(RESYNC, integrations={"INT-0001": {**i, "last_resync_at": NOW - timedelta(hours=47)}}).reasons == ("RESYNC_COOLDOWN_ACTIVE",)
    assert dec(RESYNC, integrations={"INT-0001": {**i, "last_resync_at": NOW - timedelta(hours=49)}}).decision is Decision.REQUIRE_APPROVAL
    inc = ({"incident_id": "INC-0001", "component": "carrier_gateway", "severity": "SEV3", "title": "t", "started_at": NOW},)
    d = dec(RESYNC, open_incidents=inc)
    assert d.decision is Decision.ESCALATE and "OPEN_INCIDENT_BLOCKS_RESYNC" in d.reasons and d.details["incident_ids"] == ["INC-0001"]
    unrelated = ({"incident_id": "INC-0003", "component": "scheduler", "severity": "SEV3", "title": "t", "started_at": NOW},)
    assert dec(RESYNC, open_incidents=unrelated).decision is Decision.REQUIRE_APPROVAL
    assert dec(RESYNC, knowledge="CONFLICTING_AUTHORITATIVE_EVIDENCE").decision is Decision.ESCALATE


def test_known_conflicts_and_incidents_are_not_masked_by_missing_facts():                    # the M2 control-order lesson, applied to the policy
    i = facts().integrations["INT-0001"]
    d = dec(RESYNC, integrations={"INT-0001": {**i, "status": "unknown"}}, knowledge="CONFLICTING_AUTHORITATIVE_EVIDENCE")
    assert d.decision is Decision.ESCALATE, "an unrelated gap must not turn a known conflict into an abstention"
    inc = ({"incident_id": "INC-0001", "component": "carrier_gateway", "severity": "SEV3", "title": "t", "started_at": NOW},)
    assert dec(RESYNC, integrations={"INT-0001": {**i, "last_sync_at": None}}, open_incidents=inc).decision is Decision.ESCALATE


def test_escalation_rules_incident_must_be_open_for_this_account():
    assert dec(ESCALATE).reasons == ("INCIDENT_NOT_OPEN_FOR_ACCOUNT",)                                      # not open for this tenant (resolved, other tenant's, or invented)
    d = dec({**ESCALATE, "params": {**ESCALATE["params"], "incident_id": None}}, open_incidents=({"incident_id": "INC-0009", "component": "x", "severity": "SEV3", "title": "t", "started_at": NOW},))
    assert d.decision is Decision.REQUIRE_APPROVAL and "CONSIDER_LINKING_OPEN_INCIDENT" in d.constraints


def test_structural_denials_come_first():
    for a in (DRAFT, RESYNC, CREDIT, ESCALATE):
        assert dec(a, case_status="closed").reasons == ("CASE_NOT_OPEN",)
        assert dec(a, account_id=None, case_status=None).reasons == ("CASE_NOT_FOUND_IN_SCOPE",)
        assert dec(a, case_id="CASE-000999").reasons == ("CASE_MISMATCH",)


def test_retrieval_can_only_make_policy_more_cautious_never_more_permissive():
    outcomes = {k: dec(RESYNC, knowledge=k).decision for k in ("EVIDENCE", "NO_SUFFICIENT_EVIDENCE", None, "CONFLICTING_AUTHORITATIVE_EVIDENCE")}
    assert outcomes["EVIDENCE"] == outcomes["NO_SUFFICIENT_EVIDENCE"] == outcomes[None] == Decision.REQUIRE_APPROVAL
    assert outcomes["CONFLICTING_AUTHORITATIVE_EVIDENCE"] is Decision.ESCALATE
    # no retrieval value can turn a refusal into an allowance
    for k in ("EVIDENCE", None, "NO_SUFFICIENT_EVIDENCE"):
        assert dec(CREDIT, knowledge=k, contract={**facts().contract, "max_agent_requestable_pct": 0.0}).decision is Decision.DENY


def test_decisions_are_structured_machine_readable_and_state_their_limits():
    d = dec(RESYNC).as_dict()
    assert set(d) >= {"decision", "reasons", "sufficiency", "required_role", "missing_facts", "constraints", "limitations", "details", "policy_version"}
    assert all(r.isupper() for r in d["reasons"]) and any("SEMANTIC_APPLICABILITY_NOT_ASSESSED" in x for x in d["limitations"])


def test_unhandled_registered_type_fails_closed(monkeypatch):                                    # A-I1-18
    monkeypatch.setitem(actions.ACTIONS, "trigger_resync", (actions.Tier.GATED_WRITE, "on_call_sre"))
    v = V(RESYNC)
    object.__setattr__(v, "data", {**v.data, "type": "draft_reply", "params": {"body": "x"}, "required_role": "tier2_engineer"})
    assert policy.decide(v, facts()).decision is Decision.ALLOW_PROPOSAL
    monkeypatch.setitem(actions.ACTIONS, "brand_new", (actions.Tier.GATED_WRITE, "tier2_engineer"))
    object.__setattr__(v, "data", {**v.data, "type": "brand_new"})
    assert policy.decide(v, facts()).reasons == ("UNHANDLED_ACTION_TYPE",) and policy.decide(v, facts()).decision is Decision.DENY


# ---- audit chain (pure verification over rows) ---------------------------------------------------------------------------------------------------------------
def make_rows(n=6):
    rows, prev = [], None
    for i in range(n):
        body = {"event_id": f"EVT-{i}", "case_id": "CASE-000001", "account_id": ACC, "ts": f"2026-03-02T12:00:0{i}.000000Z", "actor": {"kind": "system", "id": "x", "role": None}, "type": "policy_decided",
                "correlation": {}, "payload": {"n": i}, "prev_hash": prev}
        blob = canonical.canonical_json(body).decode()
        hh = audit.event_hash(blob.encode())
        rows.append({"seq": i + 1, "event_id": body["event_id"], "case_id": body["case_id"], "account_id": ACC, "event_type": "policy_decided", "ts": body["ts"], "body": blob, "prev_hash": prev, "hash": hh})
        prev = hh
    return rows


def test_audit_chain_verifies_and_every_kind_of_tampering_is_detected():                     # A-I1-20
    rows = make_rows()
    assert audit.verify_rows(rows).ok
    for name, mutate in {
        "payload edited": lambda r: r[2].update(body=r[2]["body"].replace('"n":2', '"n":99')),
        "hash rewritten to match edited body": lambda r: r[2].update(body=r[2]["body"].replace('"n":2', '"n":99'), hash=audit.event_hash(r[2]["body"].replace('"n":2', '"n":99').encode())),
        "middle event deleted": lambda r: r.pop(2),
        "events swapped": lambda r: r.insert(1, r.pop(3)),
        "event inserted": lambda r: r.insert(3, {**make_rows(1)[0], "seq": 99}),
        "prev_hash column forged": lambda r: r[3].update(prev_hash="0" * 64),
        "column disagrees with body": lambda r: r[4].update(case_id="CASE-000777"),
        "first event removed": lambda r: r.pop(0),
    }.items():
        rr = copy.deepcopy(rows)
        mutate(rr)
        assert not audit.verify_rows(rr).ok, name


def test_tail_truncation_is_invisible_without_an_external_anchor_and_detected_with_one():
    rows = make_rows()
    anchor = {"count": len(rows), "head_hash": rows[-1]["hash"]}
    cut = rows[:4]
    assert audit.verify_rows(cut).ok, "a hash chain alone cannot see removal of the LAST events: this is the stated limit of the guarantee"
    r = audit.verify_rows(cut, anchor)
    assert not r.ok and "shorter than the anchor" in r.errors[0]
    assert audit.verify_rows(rows, anchor).ok
    forged_tail = copy.deepcopy(rows)
    forged_tail[-1]["hash"] = "f" * 64
    assert not audit.verify_rows(forged_tail, anchor).ok


def test_audit_payloads_are_scrubbed_and_never_carry_hidden_reasoning():
    from tests.support.logcapture import CANARIES, CANARY_DSN
    p = audit.sanitize_payload({"reason": f"customer jo.doe@quarryexpress.example says key {CANARIES['api_key']} and {CANARY_DSN}", "nested": {"t": "Authorization: Bearer " + CANARIES["bearer"]}, "long": "x" * 900})
    blob = str(p)
    assert not [k for k, v in CANARIES.items() if v in blob] and "jo.doe@" not in blob and "[email]" in blob and "truncated" in p["long"]
    for k in ("reasoning", "chain_of_thought", "Thought", "scratchpad"):
        with pytest.raises(audit.AuditError):
            audit.sanitize_payload({"ok": 1, "nested": [{k: "I will first..."}]})
    with pytest.raises(audit.AuditError):
        audit.sanitize_payload({"x": ["y" * 200] * 80})


def test_timeline_is_built_from_audit_events_only():
    from copilot.control.events import ControlEvents, timeline
    ev = [{"type": "action_proposed", "actor": {"kind": "agent", "id": "agent:1", "role": None}, "correlation": {"action_id": "ACT-1"}, "payload": {"action_type": "trigger_resync"}},
          {"type": "policy_decided", "actor": {"kind": "system", "id": "policy-engine", "role": None}, "correlation": {}, "payload": {"decision": "REQUIRE_APPROVAL", "reasons": ["APPROVAL_REQUIRED_PRODUCTION_ACTION"], "requires_approval": True, "sufficiency": "SUFFICIENT"}}]
    t = timeline(ev)
    assert "proposed trigger_resync" in t[0] and "approval required: True" in t[1]
    with pytest.raises(ValueError):
        ControlEvents().emit("x", reasoning="hidden")


# ---- mocks -------------------------------------------------------------------------------------------------------------------------------------------
def test_mock_fault_modes_and_effect_accounting():
    m = mocks.MockResyncSystem(mocks.FaultScript(["transient", "permanent", "timeout_before_effect", "timeout_after_effect", "ok"]))
    for exc in (mocks.TransientError, mocks.PermanentError, mocks.CallTimeout):
        with pytest.raises(exc):
            m.call("r1", {"integration_id": "INT-0001"})
    assert m.effects == []
    with pytest.raises(mocks.CallTimeout):
        m.call("r1", {"integration_id": "INT-0001"})
    assert len(m.effects) == 1, "timeout_after_effect: the caller saw a timeout but the effect happened"
    assert m.call("r1", {"integration_id": "INT-0001"})["integration_id"] == "INT-0001" and len(m.effects) == 2, "a naive API applies a duplicate request twice"
    d = mocks.MockResyncSystem(dedupe=True, lookup_supported=True)
    d.call("r1", {"integration_id": "INT-0001"})
    assert d.call("r1", {"integration_id": "INT-0001"})["duplicate"] is True and len(d.effects) == 1 and d.lookup("r1")["ref"] == "RSY-0001" and d.lookup("nope") is None
    with pytest.raises(mocks.LookupUnsupported):
        m.lookup("r1")
    with pytest.raises(ValueError):
        mocks.FaultScript(["explode"])


def test_escalation_mock_accepts_only_internal_destinations():                                # A-I3-03
    m = mocks.MockEscalationSystem()
    ok = {"incident_id": None, "severity": "P2", "summary": "s", "destination": "engineering-queue" + mocks.INTERNAL_SUFFIX}
    assert m.call("r1", ok)["status"] == "queued"
    for dest in ("customer@quarryexpress.example", "ops@gmail.com", "evil.example.com", "engineering-queue" + mocks.INTERNAL_SUFFIX + "@x.test", ""):
        with pytest.raises(mocks.PermanentError):
            m.call("r2", {**ok, "destination": dest})
    assert len(m.effects) == 1
