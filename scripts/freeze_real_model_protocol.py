"""Builds docs/real-model-cases.json from the committed dataset and writes docs/real-model-freeze.json (hashes of the protocol and the case list). Run ONCE when the protocol is frozen;
tests/test_real_model_freeze.py fails loudly if the protocol or the case list later changes. A change needs a new protocol version (v2) and a new freeze."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DS = ROOT / "data" / "meridian-seed-20260101"
SCENARIOS = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S14", "S15", "S16")
PER = 2


def read(name):
    return [json.loads(line) for line in (DS / name).read_text().splitlines() if line.strip()]


def cases():
    labels = read("synthetic_labels.jsonl")
    out = []
    for sid in SCENARIOS:
        out += [{"scenario": sid, "ticket_id": r["ticket_id"]} for r in labels if r["scenario_id"] == sid][:PER]
    return out


def main():
    c = cases()
    (ROOT / "docs" / "real-model-cases.json").write_text(json.dumps({"rule": f"first {PER} tickets (file order of synthetic_labels.jsonl) of each of {', '.join(SCENARIOS)}", "cases": c}, indent=1) + "\n")
    files = {rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() for rel in ("docs/m5-real-model-protocol.md", "docs/real-model-cases.json")}
    (ROOT / "docs" / "real-model-freeze.json").write_text(json.dumps({"files": files, "protocol_version": "v1", "executed": False, "note": "frozen before any real model was run; no real model has been run"}, indent=1) + "\n")
    print(len(c), "cases frozen")


if __name__ == "__main__":
    main()
