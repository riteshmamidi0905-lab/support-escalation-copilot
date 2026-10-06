"""The measured benchmark must reproduce from a CLEAN database using only the committed caches (no model, no network): same code => same numbers."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from copilot.db import admin as dbadmin

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parent.parent.parent


def same(a, b, path="", tol=2e-3):
    """Structural equality; floats (scores/confidences) may differ in the last digits across pgvector/CPU builds, everything else (ranks, outcomes, ids) must be identical."""
    if isinstance(a, float) or isinstance(b, float):
        assert a is not None and b is not None and abs(a - b) <= tol, f"{path}: {a} vs {b}"
    elif isinstance(a, dict):
        assert a.keys() == b.keys(), f"{path}: keys differ"
        for k in a:
            same(a[k], b[k], f"{path}/{k}", tol)
    elif isinstance(a, list):
        assert len(a) == len(b), f"{path}: length"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            same(x, y, f"{path}[{i}]", tol)
    else:
        assert a == b, f"{path}: {a!r} vs {b!r}"


def test_replay_reproduces_the_committed_results(tmp_path, env):
    out = tmp_path / "rep"
    try:
        r = _run(out)
    finally:
        # Database roles are CLUSTER-wide: the benchmark's own bootstrap gives copilot_* new passwords, which would lock the shared test environment out on a
        # password-authenticating server (CI). Put the shared environment's passwords back.
        dbadmin.bootstrap(env.admin_dsn, env.passwords, env.scope_secret)
    _check(r, out)


def _run(out):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "run_benchmark.py"), "--out", str(out)], capture_output=True, text=True, cwd=ROOT, timeout=900)  # noqa: S603


def _check(r, out):
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    new = json.loads((out / "results.json").read_text())
    old = json.loads((ROOT / "reports" / "m2" / "results.json").read_text())
    assert new["meta"]["mode"] == "replay-from-committed-caches"
    for key in ("thresholds", "embedding_model", "reranker", "chunks", "documents", "dataset_manifest_sha256", "frozen_hashes"):
        assert new["meta"][key] == old["meta"][key], key
    for part in ("dev", "heldout", "tenant_evidence", "terminology_probes"):
        same(new[part], old[part], part)
