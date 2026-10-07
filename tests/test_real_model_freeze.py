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


def test_real_model_results_only_exist_with_a_complete_run_record():
    """Before M8 no real-model result existed and the reports said so. A result may now exist only if it names the exact weights, carries the freeze hashes of the protocol it ran under, and was a
    recorded run (executed, one pass tagged pass1 or a separately tagged variation pass). The M4/M5 reports from BEFORE any real run stay as they were: they are about the stand-in."""
    local = json.loads((ROOT / "reports/m4/local-model.json").read_text())
    assert local["executed"] is False and "no real-model evaluation was executed" in local["reason"]          # the M4 report is a statement about M4, unchanged
    probe = json.loads((ROOT / "reports/m5/real-model-probe.json").read_text())
    assert probe["usable_runtime"] is False and "NOT executed" in probe["decision"]                              # the M5 probe is a statement about the M5 machine state, unchanged
    freeze = json.loads((ROOT / "docs/real-model-freeze.json").read_text())
    results = list((ROOT / "reports").rglob("real-model-results*"))
    for p in results:
        assert p.parent == ROOT / "reports" / "m8", "real-model results live in reports/m8: " + str(p)
        r = json.loads(p.read_text())
        assert r["executed"] is True and r["freeze"] == freeze["files"], "a result must carry the freeze hashes it ran under"
        assert len(r["model"]["weights_sha256"]) == 64 and r["model"]["runtime"] and r["inference"]["temperature"] == 0 and r["inference"]["seed"] == 20260101
        assert r["tag"] == "pass1" and r["single_pass"] is True or r["single_pass"] is False and r["tag"] != "pass1"
        raw = p.with_name(p.name.replace("real-model-results", "real-model-raw-calls").replace(".json", ".jsonl"))
        assert raw.exists(), "the raw calls behind a result must be kept: " + raw.name
