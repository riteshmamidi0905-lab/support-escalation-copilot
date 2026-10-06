"""Secondary track: run the S1-S16 acceptance cases with a REAL LOCAL MODEL through the frozen runtime's OpenAI-compatible provider (Ollama default). Never part of CI.
  python scripts/with_local_pg.py python scripts/run_m4_local_model.py --model <name> [--base-url http://127.0.0.1:11434] [--per-scenario 2]
If no local server answers, nothing is run and the report says so: no paid API is ever used and no result is invented.
Records: exact model name and digest (from /api/tags where available), inference configuration (the provider sends none: server defaults apply), machine/environment, methodology.
Results are model-specific and are NOT comparable with the deterministic stand-in's."""
import argparse
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_env  # noqa: E402

ROOT = bench_env.ROOT
sys.path.insert(0, str(ROOT))
from copilot import contracts as C  # noqa: E402
from copilot.workflow.providers import local_provider, local_server_info  # noqa: E402
from tests.db.workflow_support import WorkflowWorld  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", default="http://127.0.0.1:11434")
    ap.add_argument("--per-scenario", type=int, default=2)
    ap.add_argument("--out", default=str(ROOT / "reports" / "m4" / "local-model.json"))
    a = ap.parse_args()
    info = local_server_info(a.base_url)
    out = {"executed": False, "model": a.model, "base_url": a.base_url, "machine": {"platform": platform.platform(), "machine": platform.machine(), "python": platform.python_version()}}
    if info is None:
        out["reason"] = f"no local model server answered at {a.base_url}; no real-model evaluation was executed"
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(out, indent=1) + "\n")
        print(out["reason"])
        return 0
    digest = next((m.get("digest") for m in info.get("models", []) if a.model in (m.get("name"), m.get("model"))), None)
    out.update(executed=True, digest=digest, inference_config="none sent by the provider; the server's defaults apply (temperature/seed/context not pinned)", methodology=f"first {a.per_scenario} dataset tickets of each of S1-S8, S14-S16; "
               "same workflow, trust boundary, policy and approvals as the deterministic run; humans (the harness) approve with the matching role after the first pass; model-specific results only")
    env = bench_env.build()
    w = WorkflowWorld(env, provider=local_provider(a.base_url.rstrip("/") + "/v1", a.model, timeout=300))
    rows = []
    try:
        specs = {s["scenario_id"]: s for s in C.read_jsonl(env.dataset / "scenarios.jsonl")}
        for sid in ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S14", "S15", "S16"):
            for tid in w.scenario_tickets(sid)[: a.per_scenario]:
                t0 = time.time()
                r = w.new_runner().start(tid)
                f = w.machine.get(r.case_id)["file"]
                rows.append({"scenario": sid, "ticket": tid, "expected": specs[sid]["expected_outcome"], "actual": r.outcome, "state": r.state, "degraded": (f.get("degraded") or {}).get("reason"),
                             "rejected_proposals": [x["code"] for x in (f.get("plan") or {}).get("rejected", [])], "seconds": round(time.time() - t0, 1)})
    finally:
        w.close()
        bench_env.drop(env)
    out["rows"] = rows
    out["counts"] = {"cases": len(rows), "outcome_as_expected": sum(r["expected"] == r["actual"] for r in rows), "degraded": sum(1 for r in rows if r["degraded"])}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out["counts"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
