"""The numbers quoted in docs/m8-real-model-results.md are recomputed from the committed run artefacts; no database and no model needed."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
M8 = ROOT / "reports" / "m8"
RESULTS = M8 / "real-model-results-qwen3-4b-instruct-2507-q4_k_m-A1-pass1.json"


def _analyser():
    spec = importlib.util.spec_from_file_location("analyze_real_model", ROOT / "scripts" / "analyze_real_model.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_headline_numbers_match_the_committed_results():
    res = json.loads(RESULTS.read_text())
    a = _analyser().analyse(res, RESULTS)
    assert (a["outcome_as_expected"]["k"], a["outcome_as_expected"]["n"]) == (10, 22)
    assert a["injection"]["n"] == 12 and a["totals"]["model_calls"] + sum(len(r["calls"]) for r in res["injection"]) == 154
    assert a["stages"]["DIAGNOSE"]["first_reply_accepted"] == 0 and a["stages"]["PLAN"]["first_reply_accepted"] == 9
    assert a["rejected_proposals"] == {"SCHEMA_INVALID": 7}
    assert [r["ticket"] for r in res["rows"] if r.get("degraded")] == ["TCK-0002", "TCK-0007", "TCK-0082"]


def test_every_first_diagnose_reply_fails_the_schema_and_the_cause_is_the_missing_field_names():
    res = json.loads(RESULTS.read_text())
    rc = _analyser().analyse(res, RESULTS)["raw_schema_check"]
    assert rc["by_call"]["DIAGNOSE | call 1 | schema-invalid"] == 22
    assert list(rc["first_call_schema_problems"].values()) == [22]


def test_replay_reproduced_the_run_exactly_and_corrected_i1():
    rp = json.loads((M8 / "replay-A1-pass1.json").read_text())
    assert rp["reproduced_exactly"] is True and rp["mismatches"] == [] and rp["model_calls_served"] == rp["model_calls_recorded"] == 154
    assert all(x["invariants"]["I1"] for x in rp["injection_invariants_corrected"]) and len(rp["injection_invariants_corrected"]) == 12
    assert max(x["new_effects_since_injection_phase_start"] for x in rp["injection_i1_corrected"]) == 0
    live = json.loads(RESULTS.read_text())["injection"]
    assert not any(r["invariants"]["I1"] for r in live), "the live reading (kept unchanged in the results file) is the harness artefact the replay corrects"


def test_v1_and_a1_agree_byte_for_byte_on_the_shared_calls():
    a = [json.loads(x) for x in (M8 / "real-model-raw-calls-qwen3-4b-instruct-2507-q4_k_m-pass1.jsonl").read_text().splitlines()]
    b = [json.loads(x) for x in (M8 / "real-model-raw-calls-qwen3-4b-instruct-2507-q4_k_m-A1-pass1.jsonl").read_text().splitlines()]
    assert len(a) == 23 and all((x["case"], x["stage"], x["content"], x["prompt_tokens"], x["completion_tokens"]) == (y["case"], y["stage"], y["content"], y["prompt_tokens"], y["completion_tokens"]) for x, y in zip(a, b, strict=False))
