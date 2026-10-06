"""Fault injection through COMPLETE cases: every fault ends in an explainable terminal (or waiting) state; no silent partial success."""
import pytest

from copilot.control import mocks
from copilot.workflow import providers as P
from copilot.workflow.providers import FaultyModel, RuleCaseModel
from tests.db.workflow_support import WorkflowWorld

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def env(m4env):
    return m4env


def effects(w):
    return len(w.resync.effects) + len(w.credit.effects) + len(w.escalation.effects)


def start(w, tid=None, provider=None):
    r = w.new_runner(provider).start(tid or w.resync_ticket())
    return r, w.machine.get(r.case_id)["file"]


def approve_and_resume(w, r, role="on_call_sre"):
    w.human(role, r.waiting_on[0])
    r2 = w.new_runner().run(r.case_id)
    return r2, w.machine.get(r.case_id)["file"]


def test_retrieval_falls_back_to_lexical_and_says_so(env):
    w = WorkflowWorld(env)
    try:
        w.retrieval.fail = {"vector"}
        r, f = start(w, w.routine_ticket())
        assert r.state == "CLOSED" and f["retrieval"]["strategy"] == "lexical" and "vector:" in f["retrieval"]["fallback_reason"]
        assert f["case_file"]["retrieval"]["fallback_reason"]
    finally:
        w.close()


def test_total_retrieval_failure_is_an_explicit_degraded_case(env):
    w = WorkflowWorld(env)
    try:
        w.retrieval.fail = {"vector", "lexical"}
        r, f = start(w)
        assert r.state == "HANDED_OFF" and r.outcome == "DEGRADED" and f["degraded"]["reason"] == "RETRIEVAL_UNAVAILABLE" and effects(w) == 0 and f["case_file"]["evidence"] == []
    finally:
        w.close()


@pytest.mark.parametrize("api", ["status", "carrier"])
def test_status_or_carrier_api_failure_gives_a_degraded_unverified_plan_with_actions_disabled(env, api):        # spec S9, A-I1-26
    faults = mocks.FaultScript(["transient"] * 30)
    w = WorkflowWorld(env, **({"status_faults": faults} if api == "status" else {"carrier_faults": faults}))
    try:
        r, f = start(w)
        assert r.state == "HANDED_OFF" and r.outcome == "DEGRADED" and f["verification"]["status"] == "unverified" and f["verification"]["unverified_reasons"]
        assert [a["status"] for a in f["plan"]["actions"]] == ["not_actionable"] and f["approvals"] == [] and effects(w) == 0
        assert "Verification was not possible" in " ".join(f["case_file"]["limitations"])
    finally:
        w.close()


def test_a_flaky_status_api_is_retried_and_the_case_proceeds_verified(env):
    w = WorkflowWorld(env, status_faults=mocks.FaultScript(["transient", "timeout_before_effect", "ok"]))
    try:
        r, f = start(w)
        assert f["verification"]["status"] == "verified" and r.state == "REVIEW"
    finally:
        w.close()


def test_ticketing_failure_fails_the_case_explainably_before_any_model_call(env):
    w = WorkflowWorld(env, ticketing_faults=mocks.FaultScript(["transient"] * 10))
    try:
        r = w.new_runner().start(w.routine_ticket())
        row = w.machine.get(r.case_id)
        assert r.state == "FAILED" and row["file"]["failure"] == {"code": "TICKETING_UNAVAILABLE", "stage": "INTAKE"} and w.provider.calls == [] and effects(w) == 0
    finally:
        w.close()


def test_denial_and_timeout(env):                                                                           # spec S11 / S12
    w = WorkflowWorld(env)
    try:
        r, _ = start(w)
        w.human("on_call_sre", r.waiting_on[0], "deny", "gateway upgrade planned this week")
        r2 = w.new_runner().run(r.case_id)
        f = w.machine.get(r.case_id)["file"]
        assert r2.state == "HANDED_OFF" and r2.outcome == "APPROVAL" and r2.disposition == "DENIED" and effects(w) == 0
        assert f["approvals"][0]["status"] == "denied" and "gateway upgrade" in f["approvals"][0]["decision_reason"] and f["case_file"]["draft_reply"]
        r3, _ = start(w, w.scenario_tickets("S4")[1])
        w.clock.advance(minutes=20)
        r4 = w.new_runner().run(r3.case_id)
        assert r4.state == "HANDED_OFF" and r4.disposition == "EXPIRED" and effects(w) == 0
        from copilot.db import queries as Q
        assert Q.run(w.env.pool, w.env.intake.rescope(r3.case_id), w.env.guard, "get_case", {"case_id": r3.case_id})[0]["status"] == "open"
    finally:
        w.close()


@pytest.mark.parametrize("rules", [{"PLAN": [P.outage_error()] * 3}, {"PLAN": [P.not_json] * 8}])
def test_plan_stage_model_failure_is_degraded_not_silent(env, rules):
    w = WorkflowWorld(env)
    try:
        r, f = start(w, provider=FaultyModel(RuleCaseModel(), rules))
        assert r.state == "HANDED_OFF" and r.outcome == "DEGRADED" and f["plan"]["model_error"].startswith("MODEL_") and effects(w) == 0
    finally:
        w.close()


def test_draft_stage_model_failure_leaves_a_case_file_without_a_draft_not_a_crash(env):
    w = WorkflowWorld(env)
    try:
        r, f = start(w, w.routine_ticket(), FaultyModel(RuleCaseModel(), {"DRAFT": [P.outage_error()] * 3}))
        assert r.state == "CLOSED" and f["draft_reply"]["source"] == "none" and f["draft_reply"]["error"] == "MODEL_UNAVAILABLE"
    finally:
        w.close()


# ---- customer write faults (M3 reconciliation inside a complete case) ------------------------------------------------------------------------------------
@pytest.mark.parametrize("script,kw,state,disp,n_effects", [
    (["timeout_before_effect", "ok"], {"lookup_supported": True}, "CLOSED", "EXECUTED", 1),
    (["timeout_before_effect"], {}, "HANDED_OFF", "OUTCOME_UNCERTAIN", 0),                 # the effect did NOT happen, but a naive API cannot tell us: never inferred
    (["timeout_after_effect"], {"lookup_supported": True}, "CLOSED", "EXECUTED", 1),
    (["timeout_after_effect"], {}, "HANDED_OFF", "OUTCOME_UNCERTAIN", 1),                  # it DID happen; we must not retry
    (["permanent"], {}, "HANDED_OFF", "EXECUTION_FAILED", 0),
    (["transient"] * 3, {}, "HANDED_OFF", "EXECUTION_FAILED", 0),
    (["transient", "transient", "ok"], {}, "CLOSED", "EXECUTED", 1),
])
def test_customer_write_faults_end_in_an_explainable_state_with_the_right_number_of_effects(env, script, kw, state, disp, n_effects):
    w = WorkflowWorld(env, resync_kw={"faults": mocks.FaultScript(script), **kw})
    try:
        r, _ = start(w)
        r2, f = approve_and_resume(w, r)
        assert (r2.state, r2.disposition, len(w.resync.effects)) == (state, disp, n_effects), (r2.state, r2.disposition, len(w.resync.effects))
        assert f["executions"] and all(e["status"] != "PENDING" for e in f["executions"]), "no silent partial success"
        assert w.resync.calls <= 3 and (disp != "OUTCOME_UNCERTAIN" or w.resync.calls == 1), "an uncertain outcome is never retried"
        assert f["case_file"]["executions"] == f["executions"]
    finally:
        w.close()


def test_circuit_breaker_fails_fast_then_closes_after_the_cooldown(env):
    w = WorkflowWorld(env, status_faults=mocks.FaultScript(["transient"] * 60))
    try:
        r1, f1 = start(w)
        assert f1["verification"]["status"] == "unverified"
        start(w)                                                                                       # 6 consecutive failures: the breaker opens
        before = w.status_api.calls
        r2, f2 = start(w)
        assert any("BREAKER_OPEN" in x for x in f2["verification"]["unverified_reasons"]) and w.status_api.calls == before, "fail fast: the open breaker made no call"
        w.status_api.faults = mocks.FaultScript()
        w.clock.advance(minutes=1)
        r3, f3 = start(w)
        assert f3["verification"]["status"] == "verified" and r3.state == "REVIEW"
    finally:
        w.close()
