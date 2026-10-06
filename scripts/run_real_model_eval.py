"""Run the FROZEN real-model protocol (docs/m5-real-model-protocol.md) against a local OpenAI-compatible server.

  python scripts/with_local_pg.py python scripts/run_real_model_eval.py --model <name> [--base-url http://127.0.0.1:11434] [--context-tokens 8192]

Refuses to run (and writes NOTHING) unless a local server answers and lists the named model. No result file exists in the repository today because no run has happened. When it does run:
parameters are the frozen ones (temperature 0, seed 20260101, per-stage max_tokens, JSON object format); the case list is docs/real-model-cases.json, one pass, no retries of failing cases, no tuning;
every case is reported, including failures; invariants are checked and a violation stops the run and is reported as the headline. Results are labelled with the model name and digest and are
never comparable with the deterministic stand-in's."""
import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_env  # noqa: E402

ROOT = bench_env.ROOT
from copilot.workflow.providers import ConfiguredProvider, local_server_info  # noqa: E402
from tests.db.workflow_support import WorkflowWorld  # noqa: E402

GATED = ("trigger_resync", "request_sla_credit", "escalate_engineering")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", default="http://127.0.0.1:11434")
    ap.add_argument("--context-tokens", type=int, default=8192)
    ap.add_argument("--out-dir", default=str(ROOT / "reports" / "m5"))
    a = ap.parse_args()
    freeze = json.loads((ROOT / "docs" / "real-model-freeze.json").read_text())
    for rel, digest in freeze["files"].items():
        if hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() != digest:
            print(f"REFUSED: {rel} changed after the freeze; a protocol change needs v2 and a new freeze")
            return 2
    info = local_server_info(a.base_url)
    models = [m.get("name") or m.get("model") for m in (info or {}).get("models", [])]
    if info is None or a.model not in models:
        print(f"REFUSED: no local server at {a.base_url} lists the model {a.model!r}; real-model evaluation was NOT executed and nothing was written")
        return 2
    digest = next((m.get("digest") for m in info["models"] if a.model in (m.get("name"), m.get("model"))), None)
    cases = json.loads((ROOT / "docs" / "real-model-cases.json").read_text())["cases"]
    env = bench_env.build()
    w = WorkflowWorld(env, provider=ConfiguredProvider(a.base_url.rstrip("/") + "/v1", a.model, context_tokens=a.context_tokens, timeout=300))
    rows, violation = [], None
    try:
        from copilot import contracts as C
        specs = {s["scenario_id"]: s for s in C.read_jsonl(env.dataset / "scenarios.jsonl")}
        for c in cases:
            before = len(w.resync.effects) + len(w.credit.effects) + len(w.escalation.effects)
            t0 = time.time()
            r = w.new_runner().start(c["ticket_id"])
            f = w.machine.get(r.case_id)["file"]
            effects = len(w.resync.effects) + len(w.credit.effects) + len(w.escalation.effects) - before
            pending = [x for x in f.get("approvals", []) if x["status"] == "awaiting_approval"]
            rows.append({"scenario": c["scenario"], "ticket": c["ticket_id"], "expected": specs[c["scenario"]]["expected_outcome"], "actual": r.outcome, "state": r.state, "degraded": (f.get("degraded") or {}).get("reason"),
                         "rejected_proposals": [x["code"] for x in (f.get("plan") or {}).get("rejected", [])], "repairs": (f.get("diagnosis") or {}).get("_repairs", 0), "effects_before_any_approval": effects,
                         "pending_approvals": len(pending), "seconds": round(time.time() - t0, 1)})
            if effects:                                                          # I1: an effect with no approval is a violation whatever the model did
                violation = f"{c['ticket_id']}: {effects} customer-side effect(s) before any approval"
                break
    finally:
        w.close()
        bench_env.drop(env)
    out = {"executed": True, "label": f"{a.model}@{digest}, local", "protocol": "docs/m5-real-model-protocol.md (v1, frozen)", "freeze": freeze["files"], "machine": {"platform": platform.platform(), "machine": platform.machine(), "python": platform.python_version()},
           "inference": {"temperature": 0, "seed": 20260101, "max_tokens": ConfiguredProvider.MAX_TOKENS, "context_tokens": a.context_tokens, "response_format": "json_object"},
           "single_pass": True, "invariant_violation": violation, "rows": rows,
           "counts": {"cases": len(rows), "of": len(cases), "outcome_as_expected": sum(r["expected"] == r["actual"] for r in rows), "degraded": sum(1 for r in rows if r["degraded"])},
           "caveat": "model-specific; humans simulated by the harness; not comparable with the deterministic stand-in; no tuning was done"}
    path = Path(a.out_dir) / f"real-model-results-{a.model.replace('/', '_').replace(':', '_')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out["counts"]), "VIOLATION: " + violation if violation else "")
    return 1 if violation else 0


if __name__ == "__main__":
    sys.exit(main())
