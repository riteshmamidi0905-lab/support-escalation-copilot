"""The case state machine: explicit transitions, guards, fail-closed behaviour, durability. The model cannot appear here."""
import psycopg
import pytest

from copilot.workflow.machine import TransitionRefused
from copilot.workflow.states import BY_EDGE, EDGES, STATES, TERMINAL, TRANSITIONS
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


def new_case(w):
    t = w.case_without_sla()
    cid = t.case_id
    w.machine.create(cid, t.account_id, t.ticket["ticket_id"])
    return cid, t


def test_every_transition_declares_source_destination_inputs_guard_outcomes_and_an_audit_event():
    for t in TRANSITIONS:
        assert t.src in STATES and t.dst in STATES and callable(t.guard) and isinstance(t.requires, tuple) and t.event == "state_transition"
        assert t.src not in TERMINAL, "terminal states have no outgoing edges"
        if t.dst in TERMINAL and t.dst != "FAILED":
            assert t.outcomes
    assert len(EDGES) == len(TRANSITIONS) == len(BY_EDGE)


def test_python_and_database_edge_lists_are_identical(w):
    with psycopg.connect(w.env.admin_dsn) as c:
        db = {tuple(r) for r in c.execute("SELECT src, dst FROM copilot.workflow_edges").fetchall()}
    assert db == EDGES


def test_skipped_states_are_refused(w):
    cid, _ = new_case(w)
    w.machine.advance(cid, "NEW", "INTAKE", {"ticket_id": "TCK-0001"})
    for dst in ("RETRIEVE", "DIAGNOSE", "PLAN", "EXECUTE", "DRAFT", "CLOSED", "REFUSED"):
        with pytest.raises(TransitionRefused) as e:
            w.machine.advance(cid, "INTAKE", dst, {"outcome": "ANSWER"})
        assert e.value.code == "ILLEGAL_TRANSITION", dst
    assert w.machine.get(cid)["state"] == "INTAKE"


def test_repeated_backwards_forged_and_replayed_transitions_are_refused(w):                                 # A-I1-22
    cid, _ = new_case(w)
    w.machine.advance(cid, "NEW", "INTAKE", {"ticket_id": "TCK-0001"})
    w.machine.patch_file(cid, "ticket", {"x": 1})
    w.machine.advance(cid, "INTAKE", "SCOPE")
    for src, dst, code in (("INTAKE", "SCOPE", "STALE_STATE"),                    # repeated / replayed
                           ("SCOPE", "INTAKE", "ILLEGAL_TRANSITION"),              # backwards
                           ("CLOSED", "NEW", "ILLEGAL_TRANSITION"),                # forged source
                           ("NEW", "INTAKE", "STALE_STATE")):                      # replay of the very first transition
        with pytest.raises(TransitionRefused) as e:
            w.machine.advance(cid, src, dst, {"ticket_id": "TCK-0001", "outcome": "ANSWER"})
        assert e.value.code == code, (src, dst, e.value.code)
    with pytest.raises(TransitionRefused) as e:
        w.machine.advance(cid, "SCOPE", "TELEPORT")
    assert e.value.code == "UNKNOWN_STATE"
    with pytest.raises(TransitionRefused) as e:
        w.machine.advance("CASE-999999", "NEW", "INTAKE", {"ticket_id": "x"})
    assert e.value.code == "UNKNOWN_CASE"


def test_guards_need_the_stage_output_and_required_inputs(w):
    cid, _ = new_case(w)
    with pytest.raises(TransitionRefused) as e:
        w.machine.advance(cid, "NEW", "INTAKE", {})
    assert e.value.code == "MISSING_INPUTS"
    w.machine.advance(cid, "NEW", "INTAKE", {"ticket_id": "TCK-0001"})
    with pytest.raises(TransitionRefused) as e:
        w.machine.advance(cid, "INTAKE", "SCOPE")
    assert e.value.code == "GUARD_FAILED" and "MISSING_STAGE_OUTPUT" in e.value.detail


def test_the_database_refuses_illegal_edges_even_when_application_code_is_bypassed(w):
    cid, _ = new_case(w)
    with psycopg.connect(w.env.control_dsn) as c:
        for sql in ("UPDATE copilot.case_runs SET state = 'CLOSED', version = version + 1 WHERE case_id = %s",
                    "UPDATE copilot.case_runs SET state = 'INTAKE' WHERE case_id = %s",                       # no version bump
                    "UPDATE copilot.case_runs SET version = 99 WHERE case_id = %s",
                    "UPDATE copilot.case_runs SET account_id = 'ACC-0002' WHERE case_id = %s",
                    "DELETE FROM copilot.case_runs WHERE case_id = %s"):
            with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.InsufficientPrivilege, psycopg.errors.ForeignKeyViolation)):
                c.execute(sql, (cid,))
            c.rollback()
        with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.CheckViolation)):
            c.execute("UPDATE copilot.case_transitions SET to_state = 'CLOSED'")
        c.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("TRUNCATE copilot.case_transitions")
    assert w.machine.get(cid)["state"] == "NEW"


def test_the_model_facing_role_cannot_touch_workflow_tables(w):
    for t in ("case_runs", "case_transitions", "workflow_edges"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), w.env.pool.connection() as c:
            c.execute(f"SELECT 1 FROM copilot.{t}")                    # noqa: S608


def test_a_failure_during_a_transition_leaves_the_previous_state_and_no_audit_event(w):
    cid, _ = new_case(w)
    n = len(w.audit.events(cid))

    def boom(point, **kw):
        if point == "before_audit":
            raise RuntimeError("process died mid-transition")
    w.machine.hook = boom
    with pytest.raises(RuntimeError):
        w.machine.advance(cid, "NEW", "INTAKE", {"ticket_id": "TCK-0001"})
    w.machine.hook = lambda *a, **k: None
    assert w.machine.get(cid)["state"] == "NEW" and w.machine.get(cid)["version"] == 0 and w.machine.transitions(cid) == [] and len(w.audit.events(cid)) == n
    w.machine.advance(cid, "NEW", "INTAKE", {"ticket_id": "TCK-0001"})                 # the retry works
    assert w.machine.get(cid)["state"] == "INTAKE"


def test_every_transition_writes_an_audit_event_in_the_same_transaction(w):
    cid, _ = new_case(w)
    w.machine.advance(cid, "NEW", "INTAKE", {"ticket_id": "TCK-0001"})
    ev = [e for e in w.audit.events(cid) if e["type"] == "state_transition"]
    assert len(ev) == 1 and ev[0]["payload"]["from"] == "NEW" and ev[0]["payload"]["to"] == "INTAKE" and ev[0]["payload"]["version"] == 1
    assert w.machine.transitions(cid) == [("NEW", "INTAKE", 1)]


def test_terminal_states_have_no_exit_and_outcomes_are_checked():
    outs = {t.dst: t.outcomes for t in TRANSITIONS if t.dst in TERMINAL and t.dst != "FAILED"}
    assert "REFUSE" in outs["REFUSED"] and "ANSWER" not in outs["REFUSED"] and "DEGRADED" in outs["HANDED_OFF"] and "DEGRADED" not in outs["CLOSED"]


def test_a_failure_AFTER_the_audit_event_is_written_still_rolls_the_whole_transition_back(w):
    cid, _ = new_case(w)
    n = len(w.audit.events(cid))

    def boom(point, **kw):
        if point == "after_audit":
            raise RuntimeError("process died after writing the audit event, before commit")
    w.machine.hook = boom
    with pytest.raises(RuntimeError):
        w.machine.advance(cid, "NEW", "INTAKE", {"ticket_id": "TCK-0001"})
    w.machine.hook = lambda *a, **k: None
    assert w.machine.get(cid)["state"] == "NEW" and w.machine.transitions(cid) == [] and len(w.audit.events(cid)) == n, "state, transition row and audit event commit or roll back together"
