"""Restart/resume: a case survives the process dying at meaningful boundaries, without duplicating customer effects and without inferring a write failed because the process died."""
import threading

import psycopg
import pytest

from copilot.workflow.machine import CaseMachine, TransitionRefused
from copilot.workflow.runner import SimulatedCrash
from tests.db.workflow_support import WorkflowWorld

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def env(m4env):
    return m4env


@pytest.fixture()
def w(env):
    w = WorkflowWorld(env)
    yield w
    w.close()


def crashing(w, point, tid):
    r = w.new_runner(crash_points={point})
    with pytest.raises(SimulatedCrash):
        r.start(tid)
    return r.case_id


def count(w, case_id, typ):
    return len([e for e in w.audit.events(case_id) if e["type"] == typ])


def test_restart_while_retrieving_resumes_once(w):
    cid = crashing(w, "retrieve:before_store", w.routine_ticket())
    assert w.machine.get(cid)["state"] == "RETRIEVE" and "retrieval" not in w.machine.get(cid)["file"]
    r = w.new_runner().run(cid)
    assert r.state == "CLOSED" and count(w, cid, "evidence_retrieved") == 1
    states = [t[1] for t in w.machine.transitions(cid)]
    assert len(states) == len(set(states)), "no state was entered twice"


def test_restart_while_waiting_for_approval_keeps_the_pending_request_and_executes_once_after_approval(w):
    r = w.new_runner().start(w.resync_ticket())
    assert r.state == "REVIEW"
    pending = r.waiting_on
    fresh = w.new_runner()                                           # a brand-new runner AND state machine object: nothing survives in memory
    fresh.d.machine = CaseMachine(w.control_pool, w.audit, w.clock)
    again = fresh.run(r.case_id)
    assert again.state == "REVIEW" and again.waiting_on == pending and len(w.resync.effects) == 0
    w.human("on_call_sre", pending[0])
    done = fresh.run(r.case_id)
    assert done.state == "CLOSED" and len(w.resync.effects) == 1
    approvals = [e for e in w.audit.events(r.case_id) if e["type"] == "approval_requested"]
    assert len(approvals) == 1, "resuming did not create a second approval request"


def test_restart_while_executing_an_approved_action_does_not_duplicate_the_effect(w):                          # A-I1-25
    r = w.new_runner().start(w.resync_ticket())
    w.human("on_call_sre", r.waiting_on[0])
    crashed = w.new_runner(crash_points={"execute:after_effect"})
    with pytest.raises(SimulatedCrash):
        crashed.run(r.case_id)
    assert len(w.resync.effects) == 1 and w.machine.get(r.case_id)["state"] == "EXECUTE", "the effect happened; the case does not know yet"
    done = w.new_runner().run(r.case_id)
    assert done.state == "CLOSED" and done.disposition == "EXECUTED" and len(w.resync.effects) == 1 and w.resync.calls == 1


def test_restart_while_drafting_reuses_the_same_draft_and_creates_no_duplicates(w):
    tid = w.routine_ticket()
    cid = crashing(w, "draft:after_write", tid)
    assert w.machine.get(cid)["state"] == "DRAFT" and "draft_candidate" in w.machine.get(cid)["file"]
    r = w.new_runner().run(cid)
    assert r.state == "CLOSED"
    with psycopg.connect(w.env.control_dsn) as c:
        kinds = [x[0] for x in c.execute("SELECT kind FROM copilot.case_artifacts WHERE case_id = %s ORDER BY kind", (cid,)).fetchall()]
    assert kinds == ["draft_reply", "internal_note"], kinds


def test_two_runners_resuming_the_same_case_cannot_double_execute(w):
    r = w.new_runner().start(w.resync_ticket())
    w.human("on_call_sre", r.waiting_on[0])
    out, errs, bar = [], [], threading.Barrier(2)

    def go():
        bar.wait()
        try:
            out.append(w.new_runner().run(r.case_id).state)
        except TransitionRefused as e:
            errs.append(e.code)
    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    final = w.new_runner().run(r.case_id)
    assert final.state == "CLOSED" and len(w.resync.effects) == 1 and set(errs) <= {"STALE_STATE", "GUARD_FAILED"}


def test_a_dead_process_is_never_taken_as_proof_that_a_write_failed(w):
    """The worker died AFTER claiming the key and BEFORE recording the outcome. On resume the gateway sees an expired lease => UNCERTAIN => reconciles; it never re-sends blindly."""
    from copilot.control.actions import validate_action
    r = w.new_runner().start(w.resync_ticket())
    f = w.machine.get(r.case_id)["file"]
    raw = f["plan"]["actions"][0]["raw"]
    v = validate_action(raw)
    w.human("on_call_sre", r.waiting_on[0])
    w.resync.lookup_supported = False
    st, _ = w.ledger.begin(account_id=f["scope"]["account_id"], key=v.idempotency_key, case_id=r.case_id, action_type=v.type, action_hash=v.hash(f["scope"]["account_id"]), approval_id=r.waiting_on[0], now=w.clock.now(), lease_s=60)
    assert st == "CLAIMED"
    w.clock.advance(seconds=61)
    done = w.new_runner().run(r.case_id)
    assert done.state == "HANDED_OFF" and done.disposition == "OUTCOME_UNCERTAIN" and w.resync.calls == 0, "no blind re-send"
