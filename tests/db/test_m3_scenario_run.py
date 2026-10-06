"""The M3 completion run (clean database, legitimate + adversarial paths, four-invariant check) must reproduce its committed outcomes."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from copilot.db import admin as dbadmin

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parent.parent.parent


def test_m3_scenarios_pass_and_match_the_committed_report(env, tmp_path):
    try:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_m3_scenarios.py"), str(tmp_path)], capture_output=True, text=True, cwd=ROOT, timeout=900)  # noqa: S603
    finally:
        dbadmin.bootstrap(env.admin_dsn, env.passwords, env.scope_secret)       # roles are cluster-wide (see test_m2_benchmark_replay)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    new = json.loads((tmp_path / "scenarios.json").read_text())
    old = json.loads((ROOT / "reports" / "m3" / "scenarios.json").read_text())
    assert all(s["pass"] for s in new["scenarios"]) and all(v["pass"] for v in new["invariants"].values())
    assert [(s["group"], s["scenario"], s["expected"], s["actual"]) for s in new["scenarios"]] == [(s["group"], s["scenario"], s["expected"], s["actual"]) for s in old["scenarios"]]
    assert {k: v["pass"] for k, v in new["invariants"].items()} == {k: v["pass"] for k, v in old["invariants"].items()}
