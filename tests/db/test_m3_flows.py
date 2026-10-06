"""M3 legitimate paths (positive controls) through the real gateway, real PostgreSQL, real roles. If the policy were turned into blanket denial these fail."""
import pytest

from copilot.control import gateway as G
from copilot.control.approvals import ApprovalError
from tests.db.control_support import World

pytestmark = pytest.mark.db


@pytest.fixture()
def w(env):
    w = World(env)
    yield w
    w.close()


def test_permitted_internal_draft_is_stored_and_never_sent(w):
    case = w.case_without_sla()
    a = w.action("draft_reply", case, {"body": "Hello, we are looking into the duplicate events."})
    r = w.gateway.execute(a, case.scope, w.agent)
    assert r.status == G.PROPOSAL_STORED and r.decision.decision.value == "ALLOW_PROPOSAL"
    assert r.effect["kind"] == "draft_reply"
    again = w.gateway.execute(a, case.scope, w.agent)                    # retry: same artifact, no duplicate
    assert again.status == G.REPLAYED and again.effect["artifact_id"] == r.effect["artifact_id"]


def test_valid_manager_approval_then_credit_executes_once(w):
    case = w.sla_case("Premier")
    a = w.credit_action(case, 5)
    p = w.gateway.propose(a, case.scope, w.agent)
    assert p.status == G.AWAITING_APPROVAL and p.decision.required_role == "support_manager"
    w.approve(p, "support_manager")
    r = w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id)
    assert r.status == G.SUCCEEDED and len(w.credit.effects) == 1
    r2 = w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id)       # retry
    assert r2.status == G.REPLAYED and len(w.credit.effects) == 1


def test_valid_sre_approval_then_resync_executes(w):
    f = w.feed(affected_by_gateway_incident=False)
    case = w.case_for_account(f["account_id"])
    a = w.resync_action(case, f["integration_id"])
    p = w.gateway.propose(a, case.scope, w.agent)
    assert p.status == G.AWAITING_APPROVAL and p.decision.required_role == "on_call_sre"
    w.approve(p, "on_call_sre")
    r = w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id)
    assert r.status == G.SUCCEEDED and len(w.resync.effects) == 1


def test_gated_write_without_approval_is_refused(w):
    case = w.sla_case("Premier")
    r = w.gateway.execute(w.credit_action(case, 5), case.scope, w.agent)
    assert r.status == G.REFUSED and r.reasons == ("APPROVAL_REQUIRED",) and w.credit.effects == []


# ---- more legitimate paths, failure modes and idempotency ---------------------------------------------------------------------------------------------
from copilot.control import mocks  # noqa: E402
from copilot.control.events import timeline  # noqa: E402


def approved_resync(w, **kw):
    f = w.feed(affected_by_gateway_incident=False)
    case = w.case_for_account(f["account_id"])
    a = w.resync_action(case, f["integration_id"])
    p = w.gateway.propose(a, case.scope, w.agent)
    w.approve(p, "on_call_sre")
    return case, a, p


def test_escalation_with_a_verified_open_incident_is_approved_by_tier2_and_goes_to_an_internal_queue(w):
    inc = next(i for i in w.incidents if i["status"] != "resolved" and i["component"] == "tracking")
    acc = next(a for a in inc["affected_account_ids"] if any(t["account_id"] == a for t in w.env.tickets))
    case = w.case_for_account(acc)
    a = w.action("escalate_engineering", case, {"severity": "P2", "summary": "ETAs drifting after release", "incident_id": inc["incident_id"]})
    p = w.gateway.propose(a, case.scope, w.agent)
    assert p.status == G.AWAITING_APPROVAL and "INCIDENT_LINKED_AND_VERIFIED_OPEN" in p.reasons
    w.approve(p, "tier2_engineer")
    r = w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id)
    assert r.status == G.SUCCEEDED and w.escalation.effects[0]["payload"]["destination"].endswith(mocks.INTERNAL_SUFFIX)


def test_internal_note_is_stored(w):
    case = w.case_without_sla()
    r = w.gateway.execute(w.action("add_internal_note", case, {"text": "Checked feed health; duplicates since Monday."}), case.scope, w.agent)
    assert r.status == G.PROPOSAL_STORED and r.effect["kind"] == "internal_note"


def test_open_incident_turns_a_resync_proposal_into_an_escalation_not_an_approval_request(w):
    f = w.feed(affected_by_gateway_incident=True)
    case = w.case_for_account(f["account_id"])
    p = w.gateway.propose(w.resync_action(case, f["integration_id"]), case.scope, w.agent)
    assert p.status == G.ESCALATED and "OPEN_INCIDENT_BLOCKS_RESYNC" in p.reasons and p.approval_id is None
    assert [e["type"] for e in w.audit.events(case.case_id)][-1] == "escalated"


def test_missing_facts_abstain_instead_of_guessing(w):
    case = w.sla_case("Premier")
    w.clock._t = w.clock._t.replace(year=2020)                         # no contract is in effect in 2020
    p = w.gateway.propose(w.credit_action(case, 5), case.scope, w.agent)
    assert p.status == G.ABSTAINED and p.decision.sufficiency.value == "INSUFFICIENT" and "contract.in_effect" in p.decision.missing_facts


def test_transient_failures_are_retried_then_succeed_with_one_effect(w):
    w.resync.faults = mocks.FaultScript(["transient", "transient", "ok"])
    case, a, p = approved_resync(w)
    r = w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id)
    assert r.status == G.SUCCEEDED and r.details["attempts"] == 3 and len(w.resync.effects) == 1 and w.resync.calls == 3


def test_exhausted_transient_failures_can_be_retried_later_with_one_effect(w):
    w.resync.faults = mocks.FaultScript(["transient"] * 3)
    case, a, p = approved_resync(w)
    r = w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id)
    assert r.status == G.FAILED_TRANSIENT and w.resync.effects == []
    r2 = w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id)           # the system recovered
    assert r2.status == G.SUCCEEDED and len(w.resync.effects) == 1


def test_permanent_failure_is_final_and_not_retried(w):
    w.resync.faults = mocks.FaultScript(["permanent"])
    case, a, p = approved_resync(w)
    assert w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id).status == G.FAILED_PERMANENT
    calls = w.resync.calls
    r = w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id)
    assert r.status == G.FAILED_PERMANENT and w.resync.calls == calls and w.resync.effects == []


def test_timeout_before_effect_is_reconciled_then_retried_once(w):
    w.resync.lookup_supported = True
    w.resync.faults = mocks.FaultScript(["timeout_before_effect", "ok"])
    case, a, p = approved_resync(w)
    r = w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id)
    assert r.status == G.SUCCEEDED and len(w.resync.effects) == 1 and w.resync.calls == 2


def test_timeout_after_effect_is_confirmed_by_lookup_without_a_second_effect(w):
    w.resync.lookup_supported = True
    w.resync.faults = mocks.FaultScript(["timeout_after_effect"])
    case, a, p = approved_resync(w)
    r = w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id)
    assert r.status == G.SUCCEEDED and len(w.resync.effects) == 1 and w.resync.calls == 1


def test_uncertain_outcome_on_a_system_that_cannot_confirm_is_never_blindly_retried(w):    # A-I1-17
    w.resync.faults = mocks.FaultScript(["timeout_after_effect"])                               # naive API: no lookup, no dedupe
    case, a, p = approved_resync(w)
    r = w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id)
    assert r.status == G.UNCERTAIN_ and r.reasons == ("OUTCOME_UNKNOWN_NEEDS_HUMAN_RECONCILIATION",) and len(w.resync.effects) == 1
    for _ in range(3):
        again = w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id)
        assert again.status == G.UNCERTAIN_
    assert w.resync.calls == 1 and len(w.resync.effects) == 1, "an unsafe automatic retry would have applied the effect twice"
    types = [e["type"] for e in w.audit.events(case.case_id)]
    assert "execution_uncertain" in types and "escalated" in types


def test_retry_after_reconnect_with_a_fresh_gateway_still_gives_one_effect(w, env):
    """The first process dies mid-call (claim recorded, outcome never written). A brand-new gateway with new connections retries the same approved action."""
    w.resync.lookup_supported = True
    case, a, p = approved_resync(w)
    from copilot.control.actions import validate_action
    v = validate_action(a)
    st, _ = w.ledger.begin(account_id=case.account_id, key=v.idempotency_key, case_id=case.case_id, action_type=v.type, action_hash=v.hash(case.account_id), approval_id=p.approval_id, now=w.clock.now(), lease_s=60)
    assert st == "CLAIMED"
    w.resync.call(v.idempotency_key, {"integration_id": v.params["integration_id"]})        # the effect HAPPENED before the crash
    w2 = World(env)
    try:
        w2.clock._t = w.clock.now()
        w2.resync, w2.gateway.systems["trigger_resync"] = w.resync, w.resync
        w2.authority = w.authority
        w2.gateway.authority = w.authority
        w2.approvals = w.approvals
        w2.gateway.approvals = w.approvals
        busy = w2.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id)
        assert busy.status == G.IN_PROGRESS_                                                  # the lease has not expired: do not run concurrently
        w.clock.advance(seconds=61)
        w2.clock._t = w.clock.now()
        done = w2.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id)
        assert done.status == G.SUCCEEDED and len(w.resync.effects) == 1 and w.resync.calls == 1
    finally:
        w2.close()


def test_duplicate_requests_with_a_naive_customer_api_still_give_one_effect(w):               # A-I1-08
    case, a, p = approved_resync(w)
    for _ in range(5):
        w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id)
    assert len(w.resync.effects) == 1 and w.resync.calls == 1


def test_concurrent_duplicate_requests_produce_exactly_one_effect(env):                        # A-I1-15
    import threading
    w = World(env, resync_kw={"latency_s": 0.2})
    try:
        case, a, p = approved_resync(w)
        out, barrier = [], threading.Barrier(8)

        def go():
            barrier.wait()
            out.append(w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id))
        ts = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        sts = sorted(r.status for r in out)
        assert sts.count(G.SUCCEEDED) == 1 and set(sts) <= {G.SUCCEEDED, G.REPLAYED, G.IN_PROGRESS_}, sts
        assert len(w.resync.effects) == 1 and w.resync.calls == 1
        assert w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id).status == G.REPLAYED
    finally:
        w.close()


def test_the_control_plane_explains_what_happened_without_hidden_reasoning(w):
    case, a, p = approved_resync(w)
    w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id, run_id="RUN-42", request_id="REQ-7")
    ev = w.audit.events(case.case_id)
    steps = timeline(ev)
    text = "\n".join(steps)
    assert "proposed trigger_resync" in text and "approval required: True" in text and "approved the approval" in text and "execution result: SUCCEEDED" in text
    assert [e["type"] for e in ev][:3] == ["action_proposed", "policy_decided", "approval_requested"]
    last = [e for e in ev if e["correlation"].get("request_id") == "REQ-7"]
    assert last and all(e["correlation"]["action_hash"] == p.action_hash and e["correlation"]["run_id"] == "RUN-42" for e in last)
    assert all(e["case_id"] == case.case_id and e["account_id"] == case.account_id for e in ev)
    assert w.audit.verify().ok
    assert any(x.get("kind") == "action_executed" and x.get("request_id") == "REQ-7" for x in w.events.recent)


def test_a_pending_approval_that_times_out_is_denied_and_the_case_stays_open(w):               # spec S12
    f = w.feed(affected_by_gateway_incident=False)
    case = w.case_for_account(f["account_id"])
    a = w.resync_action(case, f["integration_id"])
    p = w.gateway.propose(a, case.scope, w.agent)
    w.clock.advance(minutes=16)
    assert p.approval_id in w.approvals.expire_due()
    r = w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id)
    assert r.status == G.REFUSED and r.reasons == ("APPROVAL_EXPIRED",) and w.resync.effects == []
    assert "approval_expired" in [e["type"] for e in w.audit.events(case.case_id)]
    from copilot.db import queries as Q
    assert Q.run(w.env.pool, case.scope, w.env.guard, "get_case", {"case_id": case.case_id})[0]["status"] == "open"


def test_denied_approval_blocks_execution_and_is_audited(w):                                    # spec S11
    f = w.feed(affected_by_gateway_incident=False)
    case = w.case_for_account(f["account_id"])
    a = w.resync_action(case, f["integration_id"])
    p = w.gateway.propose(a, case.scope, w.agent)
    w.approvals.decide(p.approval_id, w.user("on_call_sre"), "deny", "gateway upgrade planned")
    r = w.gateway.execute(a, case.scope, w.agent, approval_id=p.approval_id)
    assert r.status == G.REFUSED and r.reasons == ("APPROVAL_DENIED",) and w.resync.effects == []
    with pytest.raises(ApprovalError):
        w.approvals.decide(p.approval_id, w.user("on_call_sre"), "approve")                      # a decision is final


def test_r1_the_frozen_runtimes_bool_approver_is_wrapped_by_the_approval_record(w):
    """R-1: the runtime is unchanged; its approver hook only reports what the approval RECORD says."""
    from agent.security import Policy

    from copilot.control.actions import validate_action
    from copilot.control.runtime_bridge import approver_for
    f = w.feed(affected_by_gateway_incident=False)
    case = w.case_for_account(f["account_id"])
    a = w.resync_action(case, f["integration_id"])
    v = validate_action(a)
    p = w.gateway.propose(a, case.scope, w.agent)
    pol = Policy(approver=approver_for(w.approvals, p.approval_id, v, case.account_id))
    assert pol.check("trigger_resync", "write", v.params) == (False, "human approver denied 'trigger_resync'")            # pending: no
    w.approve(p, "on_call_sre")
    assert pol.check("trigger_resync", "write", v.params) == (True, "ok")                                                  # approved record: yes
    assert pol.check("trigger_resync", "write", {**v.params, "integration_id": "INT-9999"})[0] is False                   # a changed argument: no
    assert pol.check("add_internal_note", "write", {"text": "x"})[0] is False                                              # another tool: no
    w.clock.advance(minutes=16)
    assert pol.check("trigger_resync", "write", v.params)[0] is False                                                      # expired: no
    assert Policy(approver=None).check("trigger_resync", "write", v.params)[0] is False                                    # the runtime's own default still denies
