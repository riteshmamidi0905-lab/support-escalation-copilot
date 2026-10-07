"""The recorded real-model replies must replay through the UNCHANGED workflow with NO model and reproduce every committed outcome (database test; no language model is called)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from copilot.db import admin as dbadmin

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parent.parent.parent
M8 = ROOT / "reports" / "m8"
RUNS = {
    "v1 (stopped at case 5 on the pre-registered I2 proxy)": ("real-model-results-qwen3-4b-instruct-2507-q4_k_m-pass1.json", "replay-pass1.json"),
    "A1 (complete run, Amendment A1)": ("real-model-results-qwen3-4b-instruct-2507-q4_k_m-A1-pass1.json", "replay-A1-pass1.json"),
}


@pytest.mark.parametrize("label", list(RUNS))
def test_recorded_replies_replay_without_a_model_and_match_the_committed_replay(env, tmp_path, label):
    results, committed = RUNS[label]
    out = tmp_path / "replay.json"
    try:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "replay_real_model.py"), str(M8 / results), "--out", str(out)], capture_output=True, text=True, cwd=ROOT, timeout=1800)  # noqa: S603
    finally:
        dbadmin.bootstrap(env.admin_dsn, env.passwords, env.scope_secret)       # roles are cluster-wide
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    new, old = json.loads(out.read_text()), json.loads((M8 / committed).read_text())
    assert new["reproduced_exactly"] is True and new["mismatches"] == [] and new["divergence"] is None
    assert new["model_calls_served"] == new["model_calls_recorded"]
    compared = [k for k in ("cases_replayed", "injection_replayed", "model_calls_served", "injection_invariants_corrected", "plan_rejections", "rejections") if k in old]
    assert {"cases_replayed", "model_calls_served", "rejections"} <= set(compared)
    for key in compared:
        assert new[key] == old[key], key                                          # the committed replay is exactly what a fresh replay produces (the v1 file predates the injection keys)
