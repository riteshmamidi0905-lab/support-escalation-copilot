"""The M4 acceptance run (S1-S16, clean database, deterministic stand-in) must pass its invariants and reproduce its committed outcomes."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from copilot.db import admin as dbadmin

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parent.parent.parent


def test_m4_scenarios_hold_the_invariants_and_match_the_committed_report(env, tmp_path):
    try:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_m4_scenarios.py"), str(tmp_path)], capture_output=True, text=True, cwd=ROOT, timeout=1500)  # noqa: S603
    finally:
        dbadmin.bootstrap(env.admin_dsn, env.passwords, env.scope_secret)       # roles are cluster-wide
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    new = json.loads((tmp_path / "scenarios.json").read_text())
    old = json.loads((ROOT / "reports" / "m4" / "scenarios.json").read_text())
    assert all(v["pass"] for v in new["global"].values())
    key = lambda rows: [(x["scenario"], x["ticket"], x["actual"], x["state"], x["disposition"], x["outcome_ok"], x["action_ok"], x["citation_ok"], x["approval_ok"]) for x in rows]  # noqa: E731
    assert key(new["rows"]) == key(old["rows"])
    for sid in ("S1", "S6", "S8", "S9", "S10", "S11", "S12", "S13", "S15", "S16"):
        s = new["summary"][sid]
        assert s["outcome_ok"] == s["n"], sid                                      # the scenarios the stand-in is expected to satisfy completely
    assert sum(v["invariant_violations"] for v in new["summary"].values()) == 0
