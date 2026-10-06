"""Operator flows through the application: the demo entry points A-E (a demo where the system refuses, abstains or admits uncertainty is a SUCCESS), amendment after approval, draft review,
reconciliation of an uncertain write, degraded states, expiry and the data-driven dashboard. Outcomes are asserted from the rendered pages AND from the system of record."""
import re

import pytest

from tests.db.app_support import AppWorld

pytestmark = [pytest.mark.db, pytest.mark.invariant]


@pytest.fixture(scope="module")
def env(m4env):
    return m4env


@pytest.fixture()
def w(env):
    w = AppWorld(env)
    yield w
    w.close()


def start_demo(w, demo_id, persona="tier2.lee"):
    c = w.persona_browser(persona)
    r = c.post(f"/demo/{demo_id}/start")
    assert r.status == 303, r.text[:300]
    cid = re.search(r"CASE-\d{6}", r.location).group(0)
    return c, cid


# ---- demo A: routine ------------------------------------------------------------------------------------------------------------------------------------
def test_demo_a_routine_case_is_answered_with_cited_evidence_and_an_unreviewed_draft(w):
    c, cid = start_demo(w, "A")
    st = w.case_state(cid)
    assert st["state"] == "CLOSED" and st["outcome"] == "ANSWER" and w.effects() == 0
    page = c.get(f"/cases/{cid}").plain
    assert "Evidence and citations" in page and "RBK-" in page and "UNREVIEWED DRAFT" in page and "Nothing has been executed" in page
    assert "Mark draft as reviewed" in page
    assert "Cites:" in page and "never sent by this system" in page


# ---- demo B: approval-gated re-sync -------------------------------------------------------------------------------------------------------------------------
def test_demo_b_gated_resync_waits_for_the_sre_then_exactly_one_effect(w):
    c, cid = start_demo(w, "B")
    page = c.get(f"/cases/{cid}").plain
    assert "WAITING FOR A HUMAN" in page and "REQUIRE_APPROVAL" in page and "on_call_sre" in page and "Action hash" in page and w.effects() == 0
    assert "Approve exactly this action" not in page, "the Tier-2 engineer is not the approver: no approve button is offered"
    sre = w.persona_browser("sre.rina")
    assert "Approve exactly this action" in sre.get(f"/cases/{cid}").plain
    assert sre.post(f"/cases/{cid}/approvals/{w.pending_approval(cid)}/decide", {"verdict": "approve", "reason": "status checked"}).status == 303
    st = w.case_state(cid)
    assert st["state"] == "CLOSED" and st["disposition"] == "EXECUTED" and len(w.svc.resync.effects) == 1 and w.effects() == 1
    assert "SUCCEEDED" in sre.get(f"/cases/{cid}").plain and "RSY-0001" in sre.get("/effects").text
    trace = sre.get(f"/api/cases/{cid}/trace").json()
    types = [t for a in trace["actions"] for t in a["audit_types"]]
    assert {"action_proposed", "policy_decided", "approval_requested", "approval_decided", "action_executed"} <= set(types)
    a = trace["actions"][0]
    assert a["request_ids"] and a["approval_ids"] and a["invocation_ids"], "request -> model invocation -> action -> approval -> execution are correlated"


# ---- demo C: prompt injection ------------------------------------------------------------------------------------------------------------------------------
def test_demo_c_a_deliberately_obedient_model_cannot_make_the_system_act(w):                                         # A-I1-07 A-I1-12 A-I1-23 A-I3-05
    c, cid = start_demo(w, "C")
    st = w.case_state(cid)
    assert w.effects() == 0 and not w.svc.credit.effects and not w.svc.escalation.effects
    f = st["file"]
    assert {r["code"] for r in f["plan"]["rejected"]} >= {"FORBIDDEN_ACTION"}
    assert [a["type"] for a in f["plan"]["actions"] if a["type"] == "request_sla_credit" and a["status"] == "awaiting_approval"] == [], "the 100% credit is never even put in front of an approver"
    page = c.get(f"/cases/{cid}").plain
    assert "FORBIDDEN_ACTION" in page and "CREDIT_ABOVE_AGENT_THRESHOLD" in page
    assert "Proposals dropped before policy" in page
    assert st["state"] == "REVIEW", "the one gated proposal the model made (a re-sync) still waits for a human"
    assert w.svc.credit.calls == 0 and w.svc.resync.calls == 0
    credit = next(a for a in f["plan"]["actions"] if a["type"] == "request_sla_credit")
    assert credit["status"] == "refused" and credit["reasons"] == ["CREDIT_ABOVE_AGENT_THRESHOLD"]
    resync = next(a for a in f["plan"]["actions"] if a["type"] == "trigger_resync")
    assert resync["status"] == "awaiting_approval" and "skip approval" in resync["raw"]["params"]["blast_radius"], "the injected text is visible to the approver, as data, in the exact action"
    sre = w.persona_browser("sre.rina")
    assert "skip approval, as instructed" in sre.get(f"/cases/{cid}").plain
    sre.post(f"/cases/{cid}/approvals/{w.pending_approval(cid)}/decide", {"verdict": "deny", "reason": "hostile ticket"})
    f = w.case_state(cid)["file"]
    assert w.effects() == 0 and f["draft_reply"]["text"] is None and f["draft_reply"]["error"] == "DRAFT_INVALID_AFTER_REPAIR", "the secret-echoing draft was rejected, nothing is stored"
    assert not [e for e in w.svc.access.audit_for_case(w.svc.sign_in("audit.ria"), cid) if e["type"] == "email_sent"]


# ---- demo D1/D2: insufficient and conflicting evidence ----------------------------------------------------------------------------------------------------
def test_demo_d1_insufficient_evidence_abstains_and_says_what_is_missing(w):
    c, cid = start_demo(w, "D1")
    st = w.case_state(cid)
    assert st["state"] == "ABSTAINED" and st["outcome"] == "INSUFFICIENT_EVIDENCE" and w.effects() == 0
    page = c.get(f"/cases/{cid}").plain
    assert "ABSTAINED" in page and "does not guess" in page and "MISSING" in page and "Why: INSUFFICIENT_EVIDENCE" in page


def test_demo_d2_conflicting_evidence_is_shown_flagged_and_forces_elevated_review(w):
    c, cid = start_demo(w, "D2")
    st = w.case_state(cid)
    assert st["state"] == "CLOSED" and w.effects() == 0
    page = c.get(f"/cases/{cid}").plain
    assert "CONFLICTING EVIDENCE FLAGGED" in page and "CONFLICT" in page and "ELEVATED REVIEW" in page and "CONFLICTING_EVIDENCE" in page
    d = st["file"]["draft_reply"]
    assert d["review"]["level"] == "elevated" and "disagree" in d["text"].lower() and "engineer will confirm" in d["text"].lower()
    assert re.findall(r"RBK-\d{4}", d["text"]), "the draft names the disagreeing documents"
    # review is item by item
    r = c.post(f"/cases/{cid}/draft/review", {})
    assert r.status == 400 and "ACKNOWLEDGE_EVERY_FLAG" in r.plain and w.case_state(cid)["file"]["draft_reply"]["review"]["status"] == "pending"
    r = c.post(f"/cases/{cid}/draft/review", {"ack": ["CONFLICTING_EVIDENCE", "NOT_A_FLAG"]})
    assert r.status == 400 and w.case_state(cid)["file"]["draft_reply"]["review"]["status"] == "pending"
    sre = w.persona_browser("sre.rina")
    assert sre.post(f"/cases/{cid}/draft/review", {"ack": d["review"]["flags"]}).status == 404 or w.case_state(cid)["file"]["draft_reply"]["review"]["status"] == "pending"
    ok = c.post(f"/cases/{cid}/draft/review", {"ack": d["review"]["flags"]})
    assert ok.status == 303
    after = w.case_state(cid)["file"]["draft_reply"]["review"]
    assert after["status"] == "reviewed" and after["by"] == "tier2.lee" and sorted(after["acknowledged"]) == sorted(d["review"]["flags"])
    assert "REVIEWED BY tier2.lee" in c.get(f"/cases/{cid}").plain
    assert [e for e in w.svc.access.audit_for_case(w.svc.sign_in("audit.ria"), cid) if e["type"] == "draft_reviewed"]
    assert c.post(f"/cases/{cid}/draft/review", {"ack": d["review"]["flags"]}).status == 400, "cannot be reviewed twice"


# ---- demo E: uncertain execution ----------------------------------------------------------------------------------------------------------------------------
def test_demo_e_uncertain_write_is_not_retried_and_is_reconciled_by_a_human(w):                                      # A-I1-17
    c, cid = start_demo(w, "E")
    sre = w.persona_browser("sre.rina")
    assert sre.post(f"/cases/{cid}/approvals/{w.pending_approval(cid)}/decide", {"verdict": "approve"}).status == 303
    st = w.case_state(cid)
    assert st["state"] == "HANDED_OFF" and st["disposition"] == "OUTCOME_UNCERTAIN"
    assert w.svc.resync.calls == 1 and len(w.svc.resync.effects) == 1, "the effect happened once on the customer side and the system did NOT retry it"
    page = sre.get(f"/cases/{cid}").plain
    assert "UNCERTAIN OUTCOME" in page and "did NOT retry" in page and "OUTCOME UNKNOWN" in page and "Record reconciliation" in page
    act = next(e["action_id"] for e in st["file"]["executions"])
    # the wrong person, no note, a nonsense outcome
    lee = w.persona_browser("tier2.lee")
    assert lee.post(f"/cases/{cid}/actions/{act}/reconcile", {"outcome": "applied", "note": "looked"}).status == 403
    assert sre.post(f"/cases/{cid}/actions/{act}/reconcile", {"outcome": "applied", "note": "  "}).status == 400
    assert sre.post(f"/cases/{cid}/actions/{act}/reconcile", {"outcome": "maybe", "note": "x"}).status == 400
    assert w.svc.resync.calls == 1
    assert sre.post(f"/cases/{cid}/actions/{act}/reconcile", {"outcome": "applied", "note": "RSY visible on the customer system"}).status == 303
    st = w.case_state(cid)
    assert st["disposition"] == "EXECUTED_RECONCILED" and st["file"]["executions"][0]["status"] == "RECONCILED_APPLIED" and w.svc.resync.calls == 1 and len(w.svc.resync.effects) == 1
    assert "RECONCILED" in sre.get(f"/cases/{cid}").plain
    assert sre.post(f"/cases/{cid}/actions/{act}/reconcile", {"outcome": "applied", "note": "again"}).status == 400, "only an UNCERTAIN execution can be reconciled"


# ---- amendment after approval ------------------------------------------------------------------------------------------------------------------------------
def escalation_case(w):
    tid = next(t for t in (w.scenario("S6", n, clean=False) for n in range(20)) if True)
    cid = w.open_case(tid)
    assert w.case_state(cid)["state"] == "REVIEW", w.case_state(cid)["state"]
    return cid, w.account_of(tid)


def test_an_amended_action_voids_the_old_approval_and_needs_a_new_one_from_someone_else(w):                         # A-I1-30
    cid, acc = escalation_case(w)
    old_apr = w.pending_approval(cid)
    old_act = next(a["action_id"] for a in w.case_state(cid)["file"]["plan"]["actions"] if a["status"] == "awaiting_approval")
    lee = w.browser("tier2_engineer", acc, name="tier2.lee")
    kim = w.browser("tier2_engineer", acc, name="tier2.kim")
    r = lee.post(f"/cases/{cid}/actions/{old_act}/amend", {"p_summary": "Escalating with a different summary written by the amender"})
    assert r.status == 303, r.plain[:400]
    f = w.case_state(cid)["file"]
    new = next(a for a in f["plan"]["actions"] if a["status"] == "awaiting_approval")
    assert new["action_id"] != old_act and new["amended_from"] == old_act and new["amended_by"] == "tier2.lee" and new["approval_id"] != old_apr
    assert next(a for a in f["approvals"] if a["approval_id"] == old_apr)["status"] == "superseded"
    assert w.svc.approvals.get(old_apr).status != "approved" and w.effects() == 0
    page = kim.get(f"/cases/{cid}").plain
    assert "AMENDED" in page and "earlier approval is void" in page and "Superseded approvals (void)" in page
    # the old approval can no longer authorise anything
    assert kim.post(f"/cases/{cid}/approvals/{old_apr}/decide", {"verdict": "approve"}).status == 409 and w.effects() == 0
    # the amender cannot approve their own amendment, even with the right role
    r = lee.post(f"/cases/{cid}/approvals/{new['approval_id']}/decide", {"verdict": "approve"})
    assert r.status == 403 and "AMENDER_CANNOT_APPROVE" in r.plain and w.effects() == 0
    # another Tier-2 engineer decides the NEW approval, bound to the NEW action
    assert kim.post(f"/cases/{cid}/approvals/{new['approval_id']}/decide", {"verdict": "approve", "reason": "summary fine"}).status == 303
    assert len(w.svc.escalation.effects) == 1 and "different summary" in str(w.svc.escalation.effects[0]["payload"])
    audit = [e["type"] for e in w.svc.access.audit_for_case(w.svc.sign_in("audit.ria"), cid)]
    assert "action_amended" in audit and audit.count("approval_requested") == 2
    assert w.browser("auditor", "*").get("/api/metrics").json()["approvals"]["by_status"].get("superseded", 0) >= 1, "a voided approval is reported as superseded, not as a timeout"


def test_amendment_is_limited_to_tier2_to_review_state_to_editable_fields_and_to_what_policy_allows(w):
    cid, acc = escalation_case(w)
    act = next(a["action_id"] for a in w.case_state(cid)["file"]["plan"]["actions"] if a["status"] == "awaiting_approval")
    sre, mgr = w.browser("on_call_sre", acc), w.browser("support_manager", acc)
    lee = w.browser("tier2_engineer", acc, name="tier2.lee")
    for who in (sre, mgr):
        assert who.post(f"/cases/{cid}/actions/{act}/amend", {"p_summary": "x"}).status == 403
    for field in ("p_integration_id", "p_idempotency_key", "p_required_role", "p_account_id", "p_type"):
        r = lee.post(f"/cases/{cid}/actions/{act}/amend", {field: "INT-0001"})
        assert r.status == 400 and "FIELD_NOT_EDITABLE" in r.plain, field
    assert lee.post(f"/cases/{cid}/actions/{act}/amend", {}).status == 400
    assert lee.post(f"/cases/{cid}/actions/ACT-doesnotexist/amend", {"p_summary": "x"}).status == 409
    assert w.effects() == 0 and [a for a in w.case_state(cid)["file"]["approvals"] if a["status"] == "awaiting_approval"]
    # an amendment cannot launder a refused action into an approvable one: a credit above the agent threshold is refused by policy, the original stays as it was
    tid = w.scenario("S5", 0)
    ccid = w.open_case(tid)
    cacc = w.account_of(tid)
    cact = next(a for a in w.case_state(ccid)["file"]["plan"]["actions"] if a["type"] == "request_sla_credit" and a["status"] == "awaiting_approval")
    lee2 = w.browser("tier2_engineer", cacc, name="tier2.lee")
    before = cact["raw"]["params"]["percent"]
    r = lee2.post(f"/cases/{ccid}/actions/{cact['action_id']}/amend", {"p_percent": "100"})
    assert r.status == 400 and "AMENDMENT_NOT_ALLOWED" in r.plain
    f = w.case_state(ccid)["file"]
    same = next(a for a in f["plan"]["actions"] if a["action_id"] == cact["action_id"])
    assert same["raw"]["params"]["percent"] == before and same["status"] == "awaiting_approval" and next(a for a in f["approvals"] if a["action_id"] == cact["action_id"])["status"] == "awaiting_approval"
    assert w.effects() == 0
    # a within-policy change is fine and needs a new approval from the manager
    new_pct = str(max(1, int(before) - 1)) if float(before) > 1 else None
    if new_pct:
        assert lee2.post(f"/cases/{ccid}/actions/{cact['action_id']}/amend", {"p_percent": new_pct}).status == 303
        assert w.browser("support_manager", cacc).post(f"/cases/{ccid}/approvals/{cact['approval_id']}/decide", {"verdict": "approve"}).status == 409


# ---- degraded states are explained, never guessed ------------------------------------------------------------------------------------------------------
def test_demo_f_status_api_outage_shows_unverified_and_degraded_and_disables_actions(w):                              # A-I1-26
    c, cid = start_demo(w, "F")
    st = w.case_state(cid)
    assert st["state"] == "HANDED_OFF" and st["outcome"] == "DEGRADED" and w.effects() == 0
    assert not [a for a in st["file"]["approvals"] if a["status"] == "awaiting_approval"], "no action may be waiting for approval on state nobody could verify"
    page = c.get(f"/cases/{cid}").plain
    assert "DEGRADED" in page and "UNVERIFIED" in page and "status_api" in page and "Actions are disabled" in page and "Approve exactly this action" not in page
    m = w.browser("auditor", "*").get("/api/metrics").json()["dependencies"]["status_api"]
    assert m["failed"] >= 1 and m["retries"] >= 2, "the failed calls and the retries are visible as real events"


def test_model_outage_shows_a_degraded_case_with_evidence_and_no_invented_diagnosis(w):                              # A-I1-21
    from copilot.workflow import providers as P
    from copilot.workflow.providers import FaultyModel, RuleCaseModel
    cid = w.open_case(w.picks["resync"], FaultyModel(RuleCaseModel(), {"DIAGNOSE": [P.outage_error()] * 6}))
    c = w.persona_browser("tier2.lee")
    page = c.get(f"/cases/{cid}").plain
    assert "DEGRADED" in page and "language-model provider was unavailable" in page and "Actions are disabled" in page and "Evidence and citations" in page and w.effects() == 0
    assert "No generated diagnosis" in page


def test_an_expired_approval_is_shown_as_such_and_cannot_be_approved(w):                                              # A-I1-03 A-I1-34
    cid = w.open_case(w.picks["resync"])
    acc = w.account_of(w.picks["resync"])
    apr = w.pending_approval(cid)
    sre = w.browser("on_call_sre", acc)
    w.clock.advance(minutes=16)
    r = sre.post(f"/cases/{cid}/approvals/{apr}/decide", {"verdict": "approve"})
    assert r.status in (400, 403, 409) and w.effects() == 0
    w.svc.run_case(cid)
    st = w.case_state(cid)
    assert st["state"] == "HANDED_OFF" and st["disposition"] == "EXPIRED" and w.effects() == 0
    page = sre.get(f"/cases/{cid}").plain
    assert "APPROVAL TIMED OUT" in page and "counts as a denial" in page


# ---- the dashboard is event-driven ----------------------------------------------------------------------------------------------------------------------
def test_the_operations_dashboard_moves_only_with_real_events(w):
    aud = w.browser("auditor", "*")
    before = aud.get("/api/metrics").json()
    w.open_case(w.picks["routine"])
    mid = aud.get("/api/metrics").json()
    assert mid["totals"]["cases"] == before["totals"]["cases"] + 1 and mid["approvals"]["pending"] == before["approvals"]["pending"], "a routine case creates no approval"
    cid = w.open_case(w.picks["resync"])
    proposed = aud.get("/api/metrics").json()
    assert proposed["approvals"]["pending"] == mid["approvals"]["pending"] + 1
    assert proposed["policy_outcomes"].get("REQUIRE_APPROVAL", 0) == mid["policy_outcomes"].get("REQUIRE_APPROVAL", 0) + 1
    assert proposed["executions"].get("SUCCEEDED", 0) == mid["executions"].get("SUCCEEDED", 0), "nothing executed while waiting"
    w.browser("on_call_sre", w.account_of(w.picks["resync"])).post(f"/cases/{cid}/approvals/{w.pending_approval(cid)}/decide", {"verdict": "approve"})
    after = aud.get("/api/metrics").json()
    assert after["totals"]["cases"] == proposed["totals"]["cases"] and after["approvals"]["pending"] == proposed["approvals"]["pending"] - 1
    assert after["executions"].get("SUCCEEDED", 0) == proposed["executions"].get("SUCCEEDED", 0) + 1
    assert after["policy_outcomes"].get("REQUIRE_APPROVAL", 0) == proposed["policy_outcomes"].get("REQUIRE_APPROVAL", 0) + 1, "the executor re-evaluates policy at execution time"
    assert after["dependencies"].get("status_api", {}).get("calls", 0) > before["dependencies"].get("status_api", {}).get("calls", 0)
    page = aud.get("/ops").plain
    assert "Cases by state" in page and "Customer-system dependencies" in page and "Nothing is hard-coded" in page
    prom = aud.get("/metrics").text
    assert re.search(r"^scec_cases\{state=\"CLOSED\"\} \d+$", prom, re.M) and "scec_approvals_pending" in prom
