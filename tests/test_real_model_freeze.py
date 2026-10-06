"""The real-model evaluation protocol and case list were frozen before any real model was run. Changing either must fail loudly (a change needs a v2 and a new freeze).
Also pins the honest status: no real-model result exists in the repository."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_protocol_and_case_list_unchanged_since_the_freeze():
    freeze = json.loads((ROOT / "docs/real-model-freeze.json").read_text())
    assert freeze["files"] and freeze["executed"] is False
    for rel, digest in freeze["files"].items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest, f"{rel} changed after the freeze: needs protocol v2 and a new freeze"


def test_case_list_is_derived_from_the_committed_dataset_by_the_stated_rule():
    cases = json.loads((ROOT / "docs/real-model-cases.json").read_text())["cases"]
    labels = [json.loads(x) for x in (ROOT / "data/meridian-seed-20260101/synthetic_labels.jsonl").read_text().splitlines() if x.strip()]
    by = {}
    for r in labels:
        by.setdefault(r["scenario_id"], []).append(r["ticket_id"])
    expected = [{"scenario": s, "ticket_id": t} for s in ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S14", "S15", "S16") for t in by[s][:2]]
    assert cases == expected and len(cases) == 22


def test_no_real_model_result_is_present_and_the_report_says_not_executed():
    local = json.loads((ROOT / "reports/m4/local-model.json").read_text())
    assert local["executed"] is False and "no real-model evaluation was executed" in local["reason"]
    probe = json.loads((ROOT / "reports/m5/real-model-probe.json").read_text())
    assert probe["usable_runtime"] is False and "NOT executed" in probe["decision"]
    assert not list((ROOT / "reports").rglob("real-model-results*")), "a real-model result file appeared without a recorded run"
