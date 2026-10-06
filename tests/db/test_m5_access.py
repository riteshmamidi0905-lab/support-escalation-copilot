"""A-I2-10: audit (and case) READING obeys the same tenant/case authorization boundary as everything else. Every read verifies a signed identity, resolves the case's account in the database
and checks signed account grants; denial is indistinguishable from 'not found'."""
import json
from dataclasses import replace

import pytest

from copilot.control.access import AccessDenied
from copilot.control.approvals import ApprovalError
from tests.db.workflow_support import WorkflowWorld

pytestmark = [pytest.mark.db, pytest.mark.invariant]


@pytest.fixture(scope="module")
def env(m4env):
    return m4env


@pytest.fixture()
def w(env):
    w = WorkflowWorld(env)
    yield w
    w.close()


@pytest.fixture()
def two(w):
    ta = w.resync_ticket()
    a_acc = w.ticket(ta)["account_id"]
    tb = next(t for t in w.scenario_tickets("S14") if w.ticket(t)["account_id"] != a_acc)
    ra, rb = w.new_runner().start(ta), w.new_runner().start(tb)
    return {"A": (a_acc, ra), "B": (w.ticket(tb)["account_id"], rb)}


def op(w, name, role, accounts):
    return w.authority.mint("user", name, role, accounts=tuple(accounts))


def test_operator_reads_only_the_audit_of_cases_in_granted_accounts(w, two):                    # A-I2-10
    (a, ra), (_b, rb) = two["A"], two["B"]
    op_a = op(w, "tier2.a", "tier2_engineer", [a])
    ev = w.access.audit_for_case(op_a, ra.case_id)
    assert ev and {e["case_id"] for e in ev} == {ra.case_id} and {e["account_id"] for e in ev} == {a}
    with pytest.raises(AccessDenied) as foreign:
        w.access.audit_for_case(op_a, rb.case_id)
    with pytest.raises(AccessDenied) as missing:
        w.access.audit_for_case(op_a, "CASE-999999")
    assert foreign.value.reason == missing.value.reason == "NOT_FOUND_OR_FORBIDDEN", "someone else's case and a case that does not exist look identical: no existence oracle"
    assert str(foreign.value) == str(missing.value)


def test_search_is_filtered_in_sql_to_granted_accounts_whatever_the_filters(w, two):            # A-I2-10
    (a, _ra), (b, rb) = two["A"], two["B"]
    op_a = op(w, "tier2.a", "tier2_engineer", [a])
    everything = w.access.audit_search(op_a, limit=1000)
    assert everything and {e["account_id"] for e in everything} == {a}
    blob = json.dumps(everything)
    assert b not in blob and rb.case_id not in blob, "nothing about the other tenant appears, not even as a reference"
    for kw in ({"event_type": "policy_decided"}, {"event_type": "action_proposed"}, {"after_seq": 0}, {"limit": 10_000}, {"limit": -5}, {"event_type": "x' OR '1'='1"}, {"event_type": "'; DROP TABLE copilot.audit_events; --"}):
        got = w.access.audit_search(op_a, **kw)
        assert {e["account_id"] for e in got} <= {a}
    with pytest.raises(AccessDenied):
        w.access.audit_search(op_a, case_id=rb.case_id)                                         # a case filter outside the grants is refused, not silently empty
    with pytest.raises(AccessDenied):
        w.access.audit_search(op_a, case_id="CASE-1' OR '1'='1")
    paged, after = [], 0
    while True:
        page = w.access.audit_search(op_a, after_seq=after, limit=7)
        if not page:
            break
        paged += page
        after = page[-1]["seq"]
    assert {e["account_id"] for e in paged} == {a} and len(paged) == len(everything)
    assert not [e for e in everything if e["account_id"] is None], "events without an account are not visible to a granted operator"


def test_global_views_exist_only_for_an_all_accounts_auditor(w, two):
    (a, _ra), (b, _rb) = two["A"], two["B"]
    auditor = op(w, "audit.ria", "auditor", ["*"])
    both = {e["account_id"] for e in w.access.audit_search(auditor, limit=1000)}
    assert {a, b} <= both and w.access.verify_chain(auditor).ok
    for ident in (op(w, "tier2.a", "tier2_engineer", [a]), op(w, "sre.all", "on_call_sre", ["*"]), op(w, "audit.one", "auditor", [a])):
        with pytest.raises(AccessDenied):
            w.access.verify_chain(ident)


def test_forged_expired_tampered_and_non_human_identities_get_nothing(w, two):
    (a, ra), (_b, rb) = two["A"], two["B"]
    good = op(w, "tier2.a", "tier2_engineer", [a])
    widened = replace(good, accounts=("*",))                                                   # grants are signed: a widened grant fails verification
    from copilot.control.identity import IdentityAuthority  # an authority with another key
    foreign = IdentityAuthority("q" * 40, clock=lambda: w.clock.now().timestamp()).mint("user", "x", "auditor", accounts=("*",))
    for bad in (widened, replace(good, role="auditor"), replace(good, sig="0" * 64), foreign, w.agent, None, "tier2.a", {"accounts": ["*"]}):
        with pytest.raises(AccessDenied):
            w.access.audit_for_case(bad, rb.case_id)
        with pytest.raises(AccessDenied):
            w.access.audit_search(bad)
    w.clock.advance(hours=3)
    with pytest.raises(AccessDenied):
        w.access.audit_for_case(good, ra.case_id)


def test_approval_requires_the_account_grant_not_just_the_role(w):                               # entitlement on the decision path
    t = w.resync_ticket()
    acc = w.ticket(t)["account_id"]
    r = w.new_runner().start(t)
    wrong = op(w, "sre.other", "on_call_sre", ["ACC-0001" if acc != "ACC-0001" else "ACC-0002"])
    with pytest.raises(ApprovalError) as e:
        w.approvals.decide(r.waiting_on[0], wrong, "approve", "x")
    assert e.value.code == "ACCOUNT_NOT_GRANTED" and len(w.resync.effects) == 0
    w.approvals.decide(r.waiting_on[0], op(w, "sre.right", "on_call_sre", [acc]), "approve", "ok")
    assert w.new_runner().run(r.case_id).state == "CLOSED" and len(w.resync.effects) == 1


def test_all_audit_and_case_reads_in_the_application_go_through_access():
    """Static: no module outside copilot.control.access/audit/metrics reads copilot.audit_events or case_runs rows for an operator."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "copilot"
    offenders = []
    for p in root.rglob("*.py"):
        rel = p.relative_to(root).as_posix()
        if rel in ("control/access.py", "control/audit.py", "control/metrics.py", "control/ops.py", "workflow/machine.py", "workflow/recovery.py", "db/admin.py", "db/rebuild.py") or rel.startswith("db/"):
            continue
        if re.search(r"FROM copilot\.(audit_events|case_runs)", p.read_text()):
            offenders.append(rel)
    assert offenders == []
