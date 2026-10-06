"""Durable recovery: PostgreSQL-coordinated workers find resumable cases safely. Duplicate customer effects are prevented by the layers below the lease (versioned transitions, idempotent stages,
the M3 idempotency ledger); the lease only avoids wasted collisions. These tests run several workers concurrently and also IGNORE the lease on purpose."""
import threading

import psycopg
import pytest

from copilot.workflow.machine import CaseMachine
from copilot.workflow.runner import SimulatedCrash
from tests.db.workflow_support import WorkflowWorld

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def env(m4env):
    return m4env


@pytest.fixture()
def w(env):
    w = WorkflowWorld(env)
    with psycopg.connect(env.admin_dsn) as c:                      # park every case left over from earlier tests so each test sees only its own backlog
        c.execute("UPDATE copilot.case_runs SET next_attempt_at = '2100-01-01T00:00:00Z' WHERE state NOT IN ('CLOSED','REFUSED','ABSTAINED','ESCALATED','HANDED_OFF','FAILED')")
    yield w
    w.close()


def stale_cases(w, n_routine=6, n_resync=4):
    """A mixed backlog of cases whose process died at different points."""
    ids = {"routine": [], "approved": [], "waiting": [], "exec_crash": []}
    for t in w.routine_tickets(n_routine):
        r = w.new_runner(crash_points={"retrieve:before_store"})
        with pytest.raises(SimulatedCrash):
            r.start(t)
        ids["routine"].append(r.case_id)
    tix = [t for t in w.scenario_tickets("S4") if "carrier_gateway" not in w.open_incident_components(w.ticket(t)["account_id"])]
    tix = tix[:n_resync]
    assert len(tix) >= 3, "dataset must offer at least three resync-able S4 tickets"
    for i, t in enumerate(tix):
        r = w.new_runner().start(t)
        if r.state != "REVIEW":
            continue
        if i % 3 == 0:                                                   # approved, process died AFTER the customer-side effect but before recording it
            w.human("on_call_sre", r.waiting_on[0])
            c = w.new_runner(crash_points={"execute:after_effect"})
            with pytest.raises(SimulatedCrash):
                c.run(r.case_id)
            ids["exec_crash"].append(r.case_id)
        elif i % 3 == 1:                                                 # approved, nobody has resumed it
            w.human("on_call_sre", r.waiting_on[0])
            ids["approved"].append(r.case_id)
        else:                                                            # still waiting for a human
            ids["waiting"].append(r.case_id)
    return ids


def drain(workers, rounds=12):
    claimed = []
    for _ in range(rounds):
        errs: list[Exception] = []

        def go(wk, errs=errs):
            try:
                claimed.extend(wk.tick().claimed)
            except Exception as e:                                           # noqa: BLE001
                errs.append(e)
        ts = [threading.Thread(target=go, args=(wk,)) for wk in workers]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert not errs, errs
    return claimed


def test_concurrent_workers_complete_a_mixed_backlog_with_exactly_one_effect_per_approved_action(w):                    # A-I1-32
    ids = stale_cases(w)
    approved = len(ids["approved"]) + len(ids["exec_crash"])
    workers = [w.worker(f"worker-{i}", batch=3) for i in range(4)]
    drain(workers)
    for cid in ids["routine"] + ids["approved"] + ids["exec_crash"]:
        assert w.machine.get(cid)["state"] in ("CLOSED", "ABSTAINED", "ESCALATED", "REFUSED", "HANDED_OFF"), (cid, w.machine.get(cid)["state"])
    for cid in ids["approved"] + ids["exec_crash"]:
        assert w.machine.get(cid)["state"] == "CLOSED" and w.machine.get(cid)["disposition"] == "EXECUTED"
    for cid in ids["waiting"]:
        assert w.machine.get(cid)["state"] == "REVIEW", "a case waiting for a human is revisited but never advanced without one"
    assert len(w.resync.effects) == approved, "one customer-side effect per approved action, however many workers and however many crashes"
    keys = [e["request_id"] for e in w.resync.effects]
    assert len(keys) == len(set(keys))
    assert w.audit.verify().ok


def test_workers_that_ignore_the_lease_still_cannot_duplicate_an_effect(w):                    # A-I1-32
    """Defence in depth: the lease is only an optimisation. Run the SAME case from several runners at once with no coordination."""
    ids = stale_cases(w, n_routine=0, n_resync=3)
    cid = (ids["approved"] + ids["exec_crash"])[0]
    out, errs, bar = [], [], threading.Barrier(5)

    def go():
        bar.wait()
        try:
            out.append(w.new_runner().run(cid).state)
        except Exception as e:                                               # noqa: BLE001
            errs.append(type(e).__name__ + ":" + getattr(e, "code", ""))
    ts = [threading.Thread(target=go) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert w.machine.get(cid)["state"] == "CLOSED" or w.new_runner().run(cid).state == "CLOSED"
    per_case = [e for e in w.resync.effects if e["request_id"] == w.machine.get(cid)["file"]["plan"]["actions"][0]["raw"]["idempotency_key"]]
    assert len(per_case) == 1, "this case's re-sync happened exactly once"
    assert set(errs) <= {"TransitionRefused:STALE_STATE", "TransitionRefused:GUARD_FAILED"}, errs


def test_a_claimed_case_is_not_claimed_again_until_its_lease_expires_then_another_worker_takes_over(w):                    # A-I1-32
    t = w.routine_tickets()[0]
    r = w.new_runner(crash_points={"retrieve:before_store"})
    with pytest.raises(SimulatedCrash):
        r.start(t)
    a, b = w.worker("a", lease_s=60), w.worker("b", lease_s=60)
    assert a.claim() and not b.claim(), "while A holds the lease B gets nothing"
    w.clock.advance(seconds=30)
    assert not b.claim()
    w.clock.advance(seconds=31)                                              # A died holding the lease: it expires
    got = b.claim()
    assert [c[0] for c in got] == [r.case_id]
    b.runner_factory(r.case_id).run(r.case_id)
    assert w.machine.get(r.case_id)["state"] == "CLOSED"


def test_a_case_waiting_for_a_human_is_not_hot_looped_and_is_woken_by_the_decision(w):
    r = w.new_runner().start(w.resync_ticket())
    wk = w.worker("w1", wait_backoff_s=300)
    first = wk.tick()
    assert r.case_id in first.claimed and first.advanced[r.case_id] == "REVIEW"
    assert wk.tick().claimed == [], "backed off: not claimed again until due"
    w.human("on_call_sre", r.waiting_on[0])
    wk.wake(r.case_id)                                                       # the UI/approval path wakes it
    done = wk.tick()
    assert done.advanced[r.case_id] == "CLOSED" and len(w.resync.effects) == 1
    assert wk.tick().claimed == [], "terminal cases are never claimed"


def test_approval_expiry_is_noticed_by_recovery_without_any_human_action(w):
    r = w.new_runner().start(w.resync_ticket())
    wk = w.worker("w1", wait_backoff_s=10)
    wk.tick()
    w.clock.advance(minutes=20)
    res = wk.tick()
    assert res.advanced[r.case_id] == "HANDED_OFF" and w.machine.get(r.case_id)["disposition"] == "EXPIRED" and len(w.resync.effects) == 0


def test_a_case_that_keeps_failing_is_parked_and_reported_not_force_failed(w):
    t = w.routine_tickets()[1]
    r = w.new_runner(crash_points={"retrieve:before_store"})
    with pytest.raises(SimulatedCrash):
        r.start(t)
    boom = RuntimeError("provider exploded")

    def broken(cid=None):
        class R:
            def run(self, case_id, request_id=None):
                raise boom
        return R()
    wk = w.worker("w1", max_attempts=3, error_backoff_s=1)
    wk.runner_factory = broken
    for _ in range(6):
        wk.tick()
        w.clock.advance(minutes=30)
    row = w.machine.get(r.case_id)
    assert row["state"] == "RETRIEVE" and row["file"].get("failure") is None, "never force-failed: its true state is unknown"
    w.metrics.max_attempts = 3
    assert w.metrics.snapshot()["stuck_cases"] >= 1
    assert wk.tick().claimed == [], "parked"
    with psycopg.connect(w.env.admin_dsn) as c:
        a, e = c.execute("SELECT attempts, last_error FROM copilot.case_runs WHERE case_id = %s", (r.case_id,)).fetchone()
    assert a >= 3 and e == "RuntimeError"


def test_recovery_can_complete_a_case_that_crashed_between_creation_and_first_transition(w):
    t = w.routine_tickets()[2]
    cid, scope = w.env.intake.open_case(t)
    w.machine.create(cid, scope.account_id, t)                              # crash right after creating the row
    wk = w.worker("w1")
    assert wk.tick().advanced[cid] == "CLOSED"


def test_recovery_events_are_recorded_with_correlation_ids(w):
    r = w.new_runner(crash_points={"retrieve:before_store"})
    with pytest.raises(SimulatedCrash):
        r.start(w.routine_tickets()[3])
    w.worker("w9").tick()
    with psycopg.connect(w.env.control_dsn) as c:
        kinds = [x[0] for x in c.execute("SELECT kind FROM copilot.ops_events WHERE case_id = %s AND request_id LIKE 'REQ-w9-%%' ORDER BY seq", (r.case_id,)).fetchall()]
        spans = c.execute("SELECT count(DISTINCT request_id) FROM copilot.ops_events WHERE case_id = %s AND request_id LIKE 'REQ-w9-%%'", (r.case_id,)).fetchone()[0]
    assert kinds[0] == "recovery_claim" and "recovery_run" in kinds and spans == 1, kinds


_ = CaseMachine
