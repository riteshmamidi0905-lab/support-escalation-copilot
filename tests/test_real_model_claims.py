"""The real-model evidence in the release evidence file is derived from the committed run artefacts (no database, no model), and the public claims built from it keep their qualifications."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rm():
    return _load("collect_release_evidence").real_model_evidence()


@pytest.fixture(scope="module")
def manifest(rm):
    ev = json.loads((ROOT / "reports" / "m6" / "release-evidence.json").read_text())
    ev["real_model"] = rm
    return _load("public_claims").build_manifest(ev)


def test_evidence_represents_the_run_as_executed_with_the_canonical_numbers(rm):
    assert rm["executed"] is True and rm["model"]["name"].startswith("qwen3-4b-instruct-2507")
    a1, v1 = rm["a1_run"], rm["v1_run"]
    assert (a1["outcome_as_expected"], a1["cases"]) == (10, 22) and a1["degraded"] == ["TCK-0002", "TCK-0007", "TCK-0082"] and a1["model_calls"] == 154
    assert a1["invariants_held_in_cases"] == {"I1": "22/22", "I2": "21/22", "I3": "22/22", "I4": "22/22"} and a1["i2_original_proxy_flags"] == ["TCK-0018"] and a1["i2_refined_violations"] == []
    assert (v1["cases_run"], v1["of"]) == (5, 22) and v1["stopped_by"].endswith("['I2'] violated")
    assert rm["injection"]["actions_proposed_that_passed_the_schema"] == 0 and rm["injection"]["actions_rejected_by_the_schema"] == 6
    assert rm["injection"]["runs"] == 12 and rm["injection"]["per_run_invariants_held"] == 12 and rm["injection"]["max_new_effects_since_phase_start"] == 0
    assert rm["injection"]["live_harness_i1_false"] == 12
    assert rm["replay"]["reproduced_exactly"] is True and rm["replay"]["model_calls_served"] == rm["replay"]["model_calls_recorded"] == 154


def test_the_four_failure_classes_are_all_counted(rm):
    fc = rm["failure_classes"]
    assert fc["diagnose_first_reply_schema_invalid"]["cases"] == 22 and fc["diagnose_first_reply_schema_invalid"]["replies_incl_injection_runs"] == 34
    assert fc["invalid_evidence_handles"]["rejected_replies"] == 21 and fc["invalid_evidence_handles"]["cases_degraded"] == 3
    assert fc["action_parameter_schema"]["actions_rejected"] == 13 and fc["action_parameter_schema"]["action_types"] == ["escalate_engineering", "trigger_resync"]
    assert (fc["draft_rejected_after_repair"]["rejected"], fc["draft_rejected_after_repair"]["draft_stages_in_cases"]) == (8, 19)


def test_the_evidence_is_a_function_of_the_committed_files_only(rm):
    assert _load("collect_release_evidence").real_model_evidence() == rm


def test_manifest_claims_carry_the_qualifications_and_keep_the_historical_boundary(manifest):
    claims = {c["id"]: c for c in manifest["claims"] if c["id"].startswith("real-model")}
    assert set(claims) == {"real-model-evaluation-status", "real-model-expected-outcomes", "real-model-controls-held", "real-model-failure-classes", "real-model-protocol-history"}
    for c in claims.values():
        assert c["model"] == "real_llm" and c["evidence_class"] == "real_model_single_run" and c["suitable_for"]["resume"] is False and c["suitable_for"]["portfolio"] is True
    assert "v0.6.0" in claims["real-model-evaluation-status"]["claim"] and "had no real-model run" in claims["real-model-evaluation-status"]["claim"]
    assert "not accuracy" in claims["real-model-expected-outcomes"]["claim"] and "10 of 22" in claims["real-model-expected-outcomes"]["claim"]
    assert "cumulatively" in claims["real-model-controls-held"]["qualification"] and "Amendment A1" in claims["real-model-controls-held"]["claim"]
    assert "Amendment A1" in claims["real-model-protocol-history"]["claim"] and "case 5 of 22" in claims["real-model-protocol-history"]["claim"]


def test_no_claim_turns_the_run_into_a_quality_or_security_statement(manifest):
    text = " ".join((c["claim"] + " " + c["qualification"]).lower() for c in manifest["claims"] if c["id"].startswith("real-model")).replace("not accuracy", "").replace("not a success rate", "").replace("not a quality claim", "")
    for banned in ("production", "safe llm", "injection solved", "secure against", "success rate", "accuracy", "validated"):
        assert banned not in text, banned
