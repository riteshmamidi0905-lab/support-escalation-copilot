"""Observability: telemetry is produced by the application's own events and metrics are DERIVED from them. Nothing is hard-coded: counts move only when real events happen."""
import json

import pytest

from copilot.control import mocks
from copilot.workflow import providers as P
from copilot.workflow.providers import FaultyModel, RuleCaseModel
from tests.db.workflow_support import WorkflowWorld
from tests.support.logcapture import CANARIES

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def env(m4env):
    return m4env


def fresh(env, **kw):
    return WorkflowWorld(env, **kw)


def delta(before, after, *path):
    def get(d):
        for p in path:
            d = d.get(p, 0) if isinstance(d, dict) else 0
        return d or 0
    return get(after) - get(before)


def test_a_new_system_reports_zeros_not_decorative_numbers(env):
    w = fresh(env)
    try:
        s = w.metrics.snapshot()
        assert s["totals"] == {"audit_events": 0, "ops_events": 0, "cases": 0} and s["cases_by_state"] == {} and s["policy_outcomes"] == {} and s["executions"] == {} and s["dependencies"] == {}
        assert s["approvals"]["pending"] == 0 and s["model"]["calls"] == 0 and s["uncertain_outcomes"] == 0 and s["circuit_breakers"] == {} and s["stuck_cases"] == 0
    finally:
        w.close()


def test_counts_move_exactly_with_the_events_that_cause_them(env):
    w = fresh(env)
    try:
        b = w.metrics.snapshot()
        r = w.new_runner().start(w.resync_ticket())                       # one case that reaches an approval request
        a1 = w.metrics.snapshot()
        assert delta(b, a1, "totals", "cases") == 1 and delta(b, a1, "cases_by_state", "REVIEW") == 1
        assert delta(b, a1, "policy_outcomes", "REQUIRE_APPROVAL") == 1 and delta(b, a1, "approvals", "pending") == 1
        assert delta(b, a1, "model", "calls") >= 2 and delta(b, a1, "dependencies", "status_api", "calls") == 1 and delta(b, a1, "retrieval", "vector", "runs") == 1
        w.clock.advance(minutes=7)
        assert w.metrics.snapshot()["approvals"]["oldest_pending_age_s"] >= 420, "approval queue age is computed from the real approval record"
        w.human("on_call_sre", r.waiting_on[0])
        w.new_runner().run(r.case_id)
        a2 = w.metrics.snapshot()
        assert delta(a1, a2, "executions", "SUCCEEDED") == 1 and delta(a1, a2, "approvals", "pending") == -1 and delta(a1, a2, "cases_by_state", "CLOSED") == 1 and delta(a1, a2, "cases_by_state", "REVIEW") == -1
        assert a2["approvals"]["avg_decision_latency_s"] >= 420 and a2["state_durations_s"]["REVIEW"]["transitions"] >= 1 and a2["state_durations_s"]["REVIEW"]["max"] >= 420
    finally:
        w.close()


def test_dependency_failures_retries_and_breaker_state_are_recorded(env):
    w = fresh(env, status_faults=mocks.FaultScript(["transient"] * 40))
    try:
        b = w.metrics.snapshot()
        for _ in range(3):
            w.new_runner().start(w.resync_ticket())
        a = w.metrics.snapshot()
        assert delta(b, a, "dependencies", "status_api", "failed") == 3 and delta(b, a, "dependencies", "status_api", "retries") >= 4
        assert any(e["api"] == "status_api" and e["code"] in ("SERVER_ERROR_RETRIES_EXHAUSTED", "BREAKER_OPEN") for e in a["dependency_errors"])
        assert a["circuit_breakers"]["status_api"]["state"] == "open" and w.client.breaker_state("status_api") == "open"
        assert a["dependencies"]["status_api"]["failed"] == 3 + b["dependencies"].get("status_api", {}).get("failed", 0)
        w.status_api.faults = mocks.FaultScript()
        w.clock.advance(minutes=2)
        w.new_runner().start(w.resync_ticket())
        c = w.metrics.snapshot()
        assert c["circuit_breakers"]["status_api"]["state"] == "closed"
        assert {t["state"] for t in c["breaker_transitions"] if t["api"] == "status_api"} >= {"open", "half_open", "closed"}
    finally:
        w.close()


def test_retrieval_fallback_model_failures_abstentions_escalations_and_uncertain_outcomes_are_counted(env):
    w = fresh(env, resync_kw={"faults": mocks.FaultScript(["timeout_after_effect"])})
    try:
        b = w.metrics.snapshot()
        w.retrieval.fail = {"vector"}
        w.new_runner().start(w.routine_ticket())
        w.retrieval.fail = set()
        a1 = w.metrics.snapshot()
        assert delta(b, a1, "retrieval", "lexical", "fallbacks") == 1
        w.new_runner(FaultyModel(RuleCaseModel(), {"DIAGNOSE": [P.outage_error()] * 3})).start(w.routine_ticket())
        a2 = w.metrics.snapshot()
        assert any(x["error"] == "MODEL_UNAVAILABLE" for x in a2["model"]["stage_errors"])
        assert sum(a2["model"]["failures_by_kind"].values()) - sum(a1["model"]["failures_by_kind"].values()) >= 1
        s7 = next(t for t in w.scenario_tickets("S7") if "sso" not in w.ticket(t)["subject"].lower())
        w.new_runner().start(s7)
        a3 = w.metrics.snapshot()
        assert delta(a2, a3, "control_events", "abstained") == 0 and delta(a2, a3, "cases_by_outcome", "INSUFFICIENT_EVIDENCE") == 1
        r = w.new_runner().start(w.resync_ticket())
        w.human("on_call_sre", r.waiting_on[0])
        w.new_runner().run(r.case_id)
        a4 = w.metrics.snapshot()
        assert delta(a3, a4, "uncertain_outcomes") == 1 and delta(a3, a4, "control_events", "execution_uncertain") == 1 and delta(a3, a4, "cases_by_state", "HANDED_OFF") >= 1
    finally:
        w.close()


def test_correlation_chain_from_request_to_audit(env):
    w = fresh(env)
    try:
        req = "REQ-" + "ab12cd34"
        runner = w.new_runner()
        runner.request_id = req
        r = runner.start(w.resync_ticket())
        w.human("on_call_sre", r.waiting_on[0])
        w.new_runner().run(r.case_id, request_id=req + "-resume")
        ident = w.authority.mint("user", "tier2.t", "tier2_engineer", accounts=(w.machine.get(r.case_id)["account_id"],))
        tr = w.access.trace_for_case(ident, r.case_id)
        act = tr["actions"][0]
        f = w.machine.get(r.case_id)["file"]
        entry = f["plan"]["actions"][0]
        inv = entry["model"]["invocation_id"]
        assert req in act["request_ids"] and req + "-resume" in act["request_ids"], "the same request id threads propose and execute (HTTP request -> case -> action)"
        assert inv and inv in act["invocation_ids"], "model invocation -> action"
        assert f["approvals"][0]["approval_id"] in act["approval_ids"], "action -> approval"
        assert {"action_proposed", "policy_decided", "approval_requested", "execution_attempted", "action_executed"} <= set(act["audit_types"]), "approval -> execution -> audit"
        ops = [e for e in tr["ops"] if e["invocation_id"] == inv]
        assert ops and {e["kind"] for e in ops} >= {"model_stage"}, "the model invocation's own telemetry carries the same id"
        assert all(e["request_id"] for e in tr["ops"] if e["kind"] in ("model_call", "model_stage", "dependency_call", "retrieval"))
    finally:
        w.close()


def test_telemetry_contains_no_secrets_or_hidden_reasoning(env):
    w = fresh(env)
    try:
        w.ops.record("model_stage", case_id=None, stage="DIAGNOSE", note="key sk-CANARYAPIKEY0123456789abcdef and jo.doe@quarryexpress.example")
        with pytest.raises(ValueError):
            w.ops.record("model_stage", reasoning="step by step")
        import psycopg
        with psycopg.connect(env.control_dsn) as c:
            blob = " ".join(json.dumps(x[0]) for x in c.execute("SELECT attrs FROM copilot.ops_events").fetchall())
            for stmt in ("UPDATE copilot.ops_events SET kind = 'x'", "DELETE FROM copilot.ops_events"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    c.execute(stmt)
                c.rollback()
        assert not [k for k, v in CANARIES.items() if v in blob] and "jo.doe@" not in blob
    finally:
        w.close()


def test_prometheus_text_is_a_view_of_the_same_numbers(env):
    w = fresh(env)
    try:
        s = w.metrics.snapshot()
        txt = w.metrics.prometheus()
        assert f"scec_approvals_pending {s['approvals']['pending']}" in txt and "scec_cases{state=" in txt
        for line in txt.strip().splitlines():
            assert line.startswith("scec_") and line.rsplit(" ", 1)[1].replace(".", "").isdigit()
    finally:
        w.close()
