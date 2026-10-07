"""Collects the release evidence from RUNS, not from prose: the full test suite (junit), the four mutation checks, the M3 scenarios, the M4 scenario matrix and injection matrix, the attack catalogue,
the draft-steering result and the retrieval / real-model status from the committed reports. Writes reports/m6/release-evidence.json, stamped with the exact code SHA it ran against.

  python scripts/with_local_pg.py python scripts/collect_release_evidence.py [--only pytest,scenarios,mutation] [--bootstrap]

The historical M3/M4 reports and docs are NOT overwritten: the scenario scripts are given a scratch directory. Takes ~25 minutes with the mutation checks (they deliberately break source files and
restore them; do not edit sources while it runs). If the working tree is dirty the result says so."""
import argparse
import ast
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


GENERATED = [":!README.md", ":!docs/evaluation.md", ":!docs/interview-guide.md", ":!content", ":!reports/m6"]      # outputs of this very process; everything else must be committed before collecting


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


def real_model_evidence() -> dict:
    """The real-model section, DERIVED from the recorded result files (never typed in). `executed` is true only if a committed result file says so. The frozen v1 run (which stopped at case 5), the complete run under
    Amendment A1 and the model-free replay record are summarised from the raw artefacts in reports/m8; every figure the claims quote comes from here. Whether the recorded replies still reproduce every outcome is enforced by
    tests/db/test_real_model_replay.py (part of the pytest section), not re-run here."""
    probe = json.loads((ROOT / "reports/m5/real-model-probe.json").read_text())
    freeze = json.loads((ROOT / "docs/real-model-freeze.json").read_text())
    files = sorted((ROOT / "reports").rglob("real-model-results*.json"))
    runs = {p.name: (p, json.loads(p.read_text())) for p in files}
    out = {"executed": any(r.get("executed") is True for _, r in runs.values()), "probe_usable_runtime": probe["usable_runtime"], "protocol_frozen": freeze["files"],
           "results_files": sorted(p.name for p in (ROOT / "reports").rglob("real-model-results*"))}
    if not out["executed"]:
        return out
    sys.path.insert(0, str(ROOT / "scripts"))
    import analyze_real_model as arm
    (p1, v1), (pa, a1) = [(p, r) for p, r in runs.values() if r["tag"] == "pass1"][0], [(p, r) for p, r in runs.values() if r["tag"] == "A1-pass1"][0]
    an = arm.analyse(a1, pa)
    rp = json.loads((pa.parent / f"replay-{a1['tag']}.json").read_text())
    rows = a1["rows"]
    reasons = {(x["stage"], x["reason"].split("'")[0].strip(" :")): x for x in an["replay"]["rejection_reasons"]}
    plan = rp["plan_rejections"]
    inj = a1["injection"]
    degraded = sorted(r["ticket"] for r in rows if r.get("degraded"))
    raw = pa.with_name(pa.name.replace("real-model-results-", "real-model-raw-calls-").replace(".json", ".jsonl")).read_text().splitlines()
    out.update({
        "amendment_a1_files": json.loads((ROOT / "docs/real-model-amendment-A1.json").read_text())["files"],
        "model": a1["model"], "inference": a1["inference"], "machine": a1["machine"],
        "v1_run": {"file": p1.name, "tag": v1["tag"], "cases_run": len(v1["rows"]), "of": v1["counts"]["of"], "stopped_by": v1["invariant_violation"], "injection_runs": len(v1["injection"])},
        "a1_run": {"file": pa.name, "tag": a1["tag"], "harness_git_sha": a1["harness_git_sha"], "cases": len(rows), "of": a1["counts"]["of"], "outcome_as_expected": sum(r["expected"] == r["actual"] for r in rows),
                   "degraded": degraded, "per_scenario": {k: f"{v['k']}/{v['n']}" for k, v in an["by_scenario"].items()},
                   "invariants_held_in_cases": {k: f"{v['held']}/{v['n']}" for k, v in an["invariants_cases"].items()}, "i2_original_proxy_flags": an["i2_original_proxy_flags"], "i2_refined_violations": an["i2_refined_violations"],
                   "effects_executed": an["effects"], "model_calls": len(raw), "wall_seconds": a1["wall_seconds"]},
        "injection": {"runs": len(inj), "live_harness_i1_false": sum(1 for x in rp["injection_i1_corrected"] if not x["i1_as_measured_by_live_harness"]),
                      "per_run_invariants_held": sum(1 for x in rp["injection_invariants_corrected"] if all(x["invariants"].values())), "max_new_effects_since_phase_start": max(x["new_effects_since_injection_phase_start"] for x in rp["injection_i1_corrected"]),
                      "actions_proposed_that_passed_the_schema": sum(len(x["proposed"]) for x in inj), "actions_rejected_by_the_schema": sum(len(x["rejected"]) for x in inj), "contained_by": an["injection"]["contained_by"], "outcomes": an["injection"]["outcomes"]},
        "failure_classes": {
            "diagnose_first_reply_schema_invalid": {"cases": an["raw_schema_check"]["by_call"]["DIAGNOSE | call 1 | schema-invalid"], "of_cases": len(rows), "replies_incl_injection_runs": reasons[("DIAGNOSE (schema, agent-loop reply)", "missing required")]["rejected_replies"],
                                                    "cases_or_injection_runs": reasons[("DIAGNOSE (schema, agent-loop reply)", "missing required")]["cases"]},
            "invalid_evidence_handles": {"rejected_replies": reasons[("DIAGNOSE (trust check)", "unknown evidence handle")]["rejected_replies"], "cases_or_injection_runs": reasons[("DIAGNOSE (trust check)", "unknown evidence handle")]["cases"],
                                         "cases_degraded": len(degraded), "degraded_reasons": sorted({r["degraded"] for r in rows if r.get("degraded")}), "degraded_tickets": degraded},
            "action_parameter_schema": {"actions_rejected": sum(len(v) for v in plan.values()), "in_cases": sum(len(v) for k, v in plan.items() if not k.startswith("INJ#")), "in_injection_runs": sum(len(v) for k, v in plan.items() if k.startswith("INJ#")),
                                        "codes": sorted({x["code"] for v in plan.values() for x in v}), "action_types": sorted({x["action_type"] for v in plan.values() for x in v})},
            "draft_rejected_after_repair": {"draft_stages_in_cases": an["stages"]["DRAFT"]["cases_where_stage_ran"], "rejected": sum(1 for r in rows if (r.get("draft") or {}).get("error") == "DRAFT_INVALID_AFTER_REPAIR"), "first_reply_accepted": an["stages"]["DRAFT"]["first_reply_accepted"]}},
        "replay": {"reproduced_exactly": rp["reproduced_exactly"], "model_calls_served": rp["model_calls_served"], "model_calls_recorded": rp["model_calls_recorded"], "mismatches": rp["mismatches"], "enforced_by": "tests/db/test_real_model_replay.py"},
    })
    return out


def committed_reports() -> dict:
    ds = json.loads((ROOT / "reports/m5/draft-steering.json").read_text())["summary"]
    m2 = json.loads((ROOT / "reports/m2/results.json").read_text())
    h = {s: m2["heldout"][s]["summary"] for s in ("lexical", "vector", "hybrid", "rerank")}
    return {"draft_steering": {k: {"n": v["n"], "flagged": v["flagged"]} for k, v in ds.items()},
            "retrieval_heldout_m2": {"source": "reports/m2/results.json", "code_sha": m2["meta"]["git_sha"], "labeller": m2["meta"]["labeller"], "n_tickets": h["vector"]["n"], "n_sufficient": h["vector"]["n_sufficient"],
                                     "strategies": {s: {k: h[s][k] for k in ("hit@1", "hit@5", "mrr@10", "governed_success_on_sufficient", "false_confidence_on_insufficient", "conflict_detected_on_conflicting")} for s in h}},
            "real_model": real_model_evidence()}


PARTS = ("pytest", "scenarios", "mutation")


def protected_paths() -> tuple[str, ...]:
    """The code the evidence describes, read from the release test (single definition): a change under these paths makes recorded evidence stale."""
    tree = ast.parse((ROOT / "tests" / "test_release_claims.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "PROTECTED" for t in node.targets):
            return tuple(ast.literal_eval(node.value))
    raise SystemExit("PROTECTED not found in tests/test_release_claims.py")


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
        stale = [f for f in git("diff", "--name-only", ev["code_sha"], sha).splitlines() if f.startswith(protected_paths())]
        if stale:
            raise SystemExit(f"code the evidence describes changed since {ev['code_sha'][:10]} ({stale[:3]}): collect every part again (no --only)")
        sha = ev["code_sha"]                                     # only documentation / tooling changed: the other parts still describe that commit
    ev.update({"code_sha": sha, "tree_dirty": dirty(), "generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "machine": {"platform": platform.platform(), "python": platform.python_version()},
               "note": "every number below comes from a run of the scripts named in docs/evaluation.md; the model in every workflow figure is the deterministic stand-in RuleCaseModel, not an LLM; the real_model section is derived from the recorded real-model run (reports/m8)"})
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
