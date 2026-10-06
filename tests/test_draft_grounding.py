"""The draft-steering mitigation: benign drafts pass, steered drafts are caught, and the KNOWN weaknesses stay documented (a test fails if they silently change, so the docs cannot drift)."""
from copilot.workflow.grounding import check_grounding
from tests.support.steering import CASES, FACTS, OWN


def flagged(c):
    return bool(check_grounding(c["draft"], evidence_texts=c["evidence"], ticket_text=c["ticket"], own_account=OWN, facts_text=FACTS, executed_types=c["executed"], needs_hedge=c["hedge"]))


def test_benign_drafts_are_not_rejected_and_steered_drafts_are_caught():                    # A-I3-09
    for c in CASES:
        if c["kind"] == "benign":
            assert not flagged(c), c["id"]
        if c["kind"] == "steered":
            assert flagged(c), c["id"]


def test_known_weaknesses_are_documented_not_hidden():                    # A-I3-09
    """If a rule is ever improved so that one of these is caught, update docs/m5-draft-steering.md and this list on purpose."""
    assert [c["id"] for c in CASES if c["kind"] == "residual" and flagged(c)] == []
    assert [c["id"] for c in CASES if c["kind"] == "evasion" and flagged(c)] == []


def test_a_true_effect_claim_is_allowed_only_when_the_action_really_succeeded():
    claim = "We have re-synced INT-0001."
    base = dict(evidence_texts=["re-sync RBK-0019 2.0"], ticket_text="INT-0001 duplicates", own_account=OWN, facts_text=FACTS)
    assert check_grounding(claim, executed_types={"trigger_resync"}, **base) == []
    assert check_grounding(claim, executed_types=set(), **base)
    assert check_grounding("We have credited your account.", executed_types={"trigger_resync"}, **base)


def test_numbers_inside_identifiers_and_versions_are_handled():
    base = dict(evidence_texts=["RBK-0019 2.0 If the gateway is below 3.0.2 re-sync"], ticket_text="", own_account=OWN, facts_text="")
    assert check_grounding("See RBK-0019 v2.0 and gateway 3.0.2.", **base) == []
    assert check_grounding("See RBK-0020 v2.0.", **base)
