"""The measured benchmark must reproduce from a CLEAN database using only the committed caches (no model, no network): same code => same numbers."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parent.parent.parent


def test_replay_reproduces_the_committed_results(tmp_path):
    out = tmp_path / "rep"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_benchmark.py"), "--out", str(out)], capture_output=True, text=True, cwd=ROOT, timeout=900)  # noqa: S603
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    new = json.loads((out / "results.json").read_text())
    old = json.loads((ROOT / "reports" / "m2" / "results.json").read_text())
    assert new["meta"]["mode"] == "replay-from-committed-caches"
    for key in ("thresholds", "embedding_model", "reranker", "chunks", "documents", "dataset_manifest_sha256", "frozen_hashes"):
        assert new["meta"][key] == old["meta"][key], key
    for part in ("dev", "heldout", "tenant_evidence", "terminology_probes"):
        assert new[part] == old[part], f"{part} differs from the committed live-run results"
