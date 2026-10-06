"""Deterministic regeneration and designed shape. These tests do NOT measure retrieval or any model: they check the ENVIRONMENT."""
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from copilot import contracts as C
from copilot.data import design
from copilot.data import generator as G

COMMITTED = Path(__file__).resolve().parent.parent / "data" / "meridian-seed-20260101"


@pytest.fixture(scope="module")
def gen(tmp_path_factory):
    def make(seed):
        d = tmp_path_factory.mktemp(f"seed{seed}")
        return d, G.write_dataset(seed, d)
    return make


def test_same_seed_regenerates_byte_identical_files_and_hashes(gen):
    (d1, h1), (d2, h2) = gen(20260101), gen(20260101)
    assert h1 == h2 and len(h1) == 10
    for f in h1:
        assert (d1 / f).read_bytes() == (d2 / f).read_bytes(), f
    assert (d1 / "manifest.json").read_bytes() == (d2 / "manifest.json").read_bytes()


def test_committed_dataset_equals_a_fresh_regeneration(gen):
    """Cross-platform determinism evidence: CI regenerates on Linux with Python 3.11 and 3.12 and must reproduce the files committed from a macOS run."""
    d, h = gen(20260101)
    committed = json.loads((COMMITTED / "manifest.json").read_text())
    assert {f["path"]: f["sha256"] for f in committed["files"]} == h
    for f in h:
        assert (COMMITTED / f).read_bytes() == (d / f).read_bytes(), f


def test_different_seeds_differ_but_keep_shape_and_scenario_categories(gen):
    base_d, base_h = gen(20260101)
    base_cnt = Counter(l["scenario_id"] for l in C.read_jsonl(base_d / "synthetic_labels.jsonl"))
    for seed in (7, 424242):
        d, h = gen(seed)
        assert h != base_h and all(h[f] != base_h[f] for f in ("accounts.jsonl", "tickets.jsonl", "runbooks.jsonl")), "content must change with the seed"
        assert C.validate_dataset(d).ok and design.design_report(d).ok
        assert Counter(l["scenario_id"] for l in C.read_jsonl(d / "synthetic_labels.jsonl")) == base_cnt
        assert [len(C.read_jsonl(d / f)) for f in ("accounts.jsonl", "tickets.jsonl", "contracts.jsonl", "scenarios.jsonl")] == [40, 300, 40, 16]
        assert {a["name"] for a in C.read_jsonl(d / "accounts.jsonl")} != {a["name"] for a in C.read_jsonl(base_d / "accounts.jsonl")}


def test_committed_dataset_validates_against_the_contracts_and_design():
    assert C.validate_dataset(COMMITTED).ok
    assert design.design_report(COMMITTED).ok


def test_manifest_is_fictional_synthetic_and_not_hand_labelled():
    m = json.loads((COMMITTED / "manifest.json").read_text())
    assert (m["fictional"], m["customer"], m["evidence_class"], m["tuning_allowed"]) == (True, "Meridian Freight Systems (fictional)", "synthetic_label", True)
    assert not (COMMITTED / "hand_labels.jsonl").exists()
    assert all(l["provenance"] == "generator" for l in C.read_jsonl(COMMITTED / "synthetic_labels.jsonl"))


def test_designed_difficulty_is_present_not_optimised_away():
    docs = C.read_jsonl(COMMITTED / "runbooks.jsonl")
    tickets = C.read_jsonl(COMMITTED / "tickets.jsonl")
    labels = {l["ticket_id"]: l for l in C.read_jsonl(COMMITTED / "synthetic_labels.jsonl")}
    assert sum(d["status"] == "superseded" for d in docs) == 3 and sum(1 for d in docs if d["supersedes"]) == 3
    stale_but_active = [d for d in docs if d["status"] == "active" and any(o["title"] == d["title"] and o["doc_id"] != d["doc_id"] and o["status"] == "active" and o["effective_from"] > d["effective_from"] for o in docs)]
    assert len(stale_but_active) >= 3, "stale-yet-active conflicting versions must exist"
    assert sum(d["adversarial"] for d in docs) == 4 and sum(d["title"].endswith("(copy)") for d in docs) == 5
    cnt = Counter(l["scenario_id"] for l in labels.values())
    assert cnt["S2"] == 20 and cnt["S3"] == 20 and cnt["S7"] == 30 and cnt["S16"] == 5
    s2 = [t for t in tickets if labels[t["ticket_id"]]["scenario_id"] == "S2"]
    assert len(s2) == 20 and all(any(inj.split("{")[0][:24] in t["body"] for inj in G.INJECTIONS_TICKET) for t in s2), "every S2 ticket carries an injected instruction"
    s3 = [t for t in tickets if labels[t["ticket_id"]]["scenario_id"] == "S3"]
    assert len(s3) == 20 and all(any(a["account_id"] in t["body"] and a["account_id"] != t["account_id"] for a in C.read_jsonl(COMMITTED / "accounts.jsonl")) or "sister company" in t["body"] for t in s3)
    # realistic noise: some tickets contain typos / are terse
    assert any(len(t["body"]) < 40 for t in tickets) and any("\n\n" in t["body"] for t in tickets)
    assert any("resync" in t["body"].lower() for t in tickets) and all("re-sync" in d["body_markdown"] or "resync" not in d["body_markdown"].lower() for d in docs)


def test_labels_are_not_in_ticket_or_runbook_files():
    blob = (COMMITTED / "tickets.jsonl").read_text() + (COMMITTED / "runbooks.jsonl").read_text()
    for needle in ("expected_outcome", "root_cause_id", "scenario_id", "synthetic_label"):
        assert needle not in blob


def test_design_checks_catch_a_broken_dataset(tmp_path):
    d = tmp_path / "ds"
    shutil.copytree(COMMITTED, d)
    accs = C.read_jsonl(d / "accounts.jsonl")
    docs = C.read_jsonl(d / "runbooks.jsonl")
    docs[0]["body_markdown"] += f" See account {accs[0]['account_id']} for an example."
    (d / "runbooks.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in docs))
    m = json.loads((d / "manifest.json").read_text())
    for f in m["files"]:
        f["sha256"] = hashlib.sha256((d / f["path"]).read_bytes()).hexdigest()
    (d / "manifest.json").write_text(json.dumps(m))
    rep = design.design_report(d)
    assert not rep.ok and "tenant identifier" in "\n".join(rep.errors)
    docs[0]["body_markdown"] = docs[0]["body_markdown"].split(" See account")[0] + " Supports SAML single sign-on for sandboxes."
    (d / "runbooks.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in docs))
    assert "accidentally documented" in "\n".join(design.design_report(d).errors)
