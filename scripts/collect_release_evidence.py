"""Collects the release evidence from RUNS, not from prose: the full test suite (junit), the four mutation checks, the M3 scenarios, the M4 scenario matrix and injection matrix, the attack catalogue,
the draft-steering result and the retrieval / real-model status from the committed reports. Writes reports/m6/release-evidence.json, stamped with the exact code SHA it ran against.

  python scripts/with_local_pg.py python scripts/collect_release_evidence.py [--only pytest,scenarios,mutation] [--bootstrap]

The historical M3/M4 reports and docs are NOT overwritten: the scenario scripts are given a scratch directory. Takes ~25 minutes with the mutation checks (they deliberately break source files and
restore them; do not edit sources while it runs). If the working tree is dirty the result says so."""
import argparse
import json
import platform
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PY = sys.executable


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, **kw)        # noqa: S603


def git(*a):
    return run(["git", *a]).stdout.strip()


GENERATED = [":!README.md", ":!docs/evaluation.md", ":!content", ":!reports/m6"]      # outputs of this very process; everything else must be committed before collecting


def dirty() -> bool:
    return bool(git("status", "--porcelain", "--untracked-files=no", "--", ".", *GENERATED))


def pytest_summary(tmp: Path, bootstrap: bool = False) -> dict:
    junit = tmp / "junit.xml"
    extra = ["--deselect", "tests/test_release_claims.py"] if bootstrap else []            # first run only: those tests need an evidence file to exist
    r = run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}", *extra])
    root = ET.parse(junit).getroot()                                                     # noqa: S314 - the file was just written by our own pytest run
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    cases = list(suite.iter("testcase"))
    xfail = [c for c in cases if any(s.get("type") == "pytest.xfail" for s in c.findall("skipped"))]
    skipped = [c for c in cases if c.find("skipped") is not None and c not in xfail]
    failed = [c for c in cases if c.find("failure") is not None or c.find("error") is not None]
    return {"collected": len(cases), "passed": len(cases) - len(xfail) - len(skipped) - len(failed), "xfailed": len(xfail), "skipped": len(skipped), "failed": len(failed),
            "xfail_reasons": sorted({(c.find("skipped").get("message") or "")[:160] for c in xfail}), "exit_code": r.returncode, "duration_s": round(float(suite.get("time", 0)), 1)}


def mutation(script: str) -> dict:
    r = run([PY, f"scripts/{script}"])
    killed, survived = len(re.findall(r"^KILLED", r.stdout, re.M)), re.findall(r"^SURVIVED\s+(.*)$", r.stdout, re.M)
    return {"total": killed + len(survived), "killed": killed, "survived": survived, "exit_code": r.returncode}


def scenarios(tmp: Path) -> dict:
    out = {}
    d3 = tmp / "m3"
    run([PY, "scripts/run_m3_scenarios.py", str(d3)])
    m3 = json.loads((d3 / "scenarios.json").read_text())
    out["m3_scenarios"] = {"total": len(m3["scenarios"]), "as_expected": sum(1 for s in m3["scenarios"] if s["pass"]), "invariants": {k: v["pass"] for k, v in m3["invariants"].items()}}
    d4 = tmp / "m4"
    run([PY, "scripts/run_m4_scenarios.py", str(d4)])
    m4 = json.loads((d4 / "scenarios.json").read_text())
    rows = m4["rows"]
    out["m4_scenarios"] = {"cases": len(rows), "outcome_matches": sum(1 for r in rows if r["outcome_ok"]), "per_scenario": {k: f"{v['outcome_ok']}/{v['n']}" for k, v in m4["summary"].items()},
                           "invariants": {k: v["pass"] for k, v in m4["global"].items()}, "audit_chain": m4["global"]["audit chain intact"]["detail"], "model": m4["model"]}
    d5 = tmp / "inj"
    r = run([PY, "scripts/run_m4_injection.py", str(d5)])
    inj = json.loads((d5 / "injection.json").read_text())
    out["m4_injection"] = {"attack_runs": len(inj["rows"]), "invariant_violations": inj["invariant_violations"], "exit_code": r.returncode}
    return out


def catalogue() -> dict:
    from copilot.invariants import ATTACKS, INVARIANTS
    by = {i: {"total": sum(a.invariant == i for a in ATTACKS), "executable": sum(a.invariant == i and a.status == "implemented" for a in ATTACKS)} for i in INVARIANTS}
    return {"total": len(ATTACKS), "executable": sum(a.status == "implemented" for a in ATTACKS), "by_invariant": by}


def committed_reports() -> dict:
    ds = json.loads((ROOT / "reports/m5/draft-steering.json").read_text())["summary"]
    m2 = json.loads((ROOT / "reports/m2/results.json").read_text())
    probe = json.loads((ROOT / "reports/m5/real-model-probe.json").read_text())
    freeze = json.loads((ROOT / "docs/real-model-freeze.json").read_text())
    h = {s: m2["heldout"][s]["summary"] for s in ("lexical", "vector", "hybrid", "rerank")}
    return {"draft_steering": {k: {"n": v["n"], "flagged": v["flagged"]} for k, v in ds.items()},
            "retrieval_heldout_m2": {"source": "reports/m2/results.json", "code_sha": m2["meta"]["git_sha"], "labeller": m2["meta"]["labeller"], "n_tickets": h["vector"]["n"], "n_sufficient": h["vector"]["n_sufficient"],
                                     "strategies": {s: {k: h[s][k] for k in ("hit@1", "hit@5", "mrr@10", "governed_success_on_sufficient", "false_confidence_on_insufficient", "conflict_detected_on_conflicting")} for s in h}},
            "real_model": {"executed": False, "probe_usable_runtime": probe["usable_runtime"], "protocol_frozen": freeze["files"], "results_files": sorted(p.name for p in (ROOT / "reports").rglob("real-model-results*"))}}


PARTS = ("pytest", "scenarios", "mutation")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(PARTS), help="comma list of parts to (re)collect, merged into the existing evidence file: " + ", ".join(PARTS))
    ap.add_argument("--bootstrap", action="store_true", help="first run only: exclude tests/test_release_claims.py from the suite (they need this file to exist); then run again with --only pytest")
    ap.add_argument("--out", default=str(ROOT / "reports" / "m6" / "release-evidence.json"))
    a = ap.parse_args()
    parts = set(a.only.split(","))
    out = Path(a.out)
    sha = git("rev-parse", "HEAD")
    ev = json.loads(out.read_text()) if out.exists() and parts != set(PARTS) else {}
    if ev and ev.get("code_sha") != sha:
        raise SystemExit("the existing evidence was collected at another commit: collect every part again (no --only)")
    ev.update({"code_sha": sha, "tree_dirty": dirty(), "generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "machine": {"platform": platform.platform(), "python": platform.python_version()},
               "note": "every number below comes from a run of the scripts named in docs/evaluation.md; the model in every workflow figure is the deterministic stand-in RuleCaseModel, not an LLM"})
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        if "pytest" in parts:
            ev["pytest"] = pytest_summary(tmp, a.bootstrap)
        if "scenarios" in parts:
            ev["attack_catalogue"] = catalogue()
            ev.update(scenarios(tmp))
    if "mutation" in parts:
        ev["mutation_checks"] = {k: mutation(f"mutation_check_{k}.py") for k in ("m2", "m3", "m4", "m5")}
    ev.update(committed_reports())
    ev["tree_dirty_after"] = dirty()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ev, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: ev[k] for k in ("code_sha", "pytest", "attack_catalogue") if k in ev}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
