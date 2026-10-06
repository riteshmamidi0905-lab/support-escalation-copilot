"""Measure the REAL prompts the workflow sends to a model, stage by stage, so a real model's context window can be chosen on evidence rather than hope.
Runs the first N tickets of every scenario S1-S16 through the real workflow with the deterministic stand-in behind a recording wrapper (the prompts do not depend on which model answers: they are
built from the case). Token counts are ESTIMATES (characters/4 as the frozen runtime does; characters/3 as a conservative bound for JSON-heavy text): no tokenizer of any real model was used.

  python scripts/with_local_pg.py python scripts/measure_prompt_budget.py [--per-scenario 3]

Writes reports/m5/prompt-budget.json. Why it matters: a local server whose context window is smaller than the prompt silently drops the START of the prompt (the instructions and the
untrusted-data warnings). The workflow therefore refuses to call a model whose prompt would not fit (see ModelStage / ConfiguredProvider) instead of letting the server truncate it."""
import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_env  # noqa: E402

ROOT = bench_env.ROOT
from agent.model import ModelProvider  # noqa: E402

from copilot.workflow.providers import RuleCaseModel, _stage  # noqa: E402
from tests.db.workflow_support import WorkflowWorld  # noqa: E402


class Recorder(ModelProvider):
    name = "recorder over RuleCaseModel (deterministic stand-in; not an LLM)"

    def __init__(self):
        self.inner, self.rows = RuleCaseModel(), []

    def complete(self, messages, tools=None):
        chars = sum(len(m.content) for m in messages)
        user = next((m for m in reversed(messages) if m.role == "user"), None)
        self.rows.append({"stage": _stage(messages), "chars": chars, "messages": len(messages), "context_chars": len(user.content) if user else 0})
        return self.inner.complete(messages, tools)


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-scenario", type=int, default=3)
    a = ap.parse_args()
    env = bench_env.build()
    rec = Recorder()
    w = WorkflowWorld(env, provider=rec)
    cases = 0
    try:
        for n in range(1, 17):
            for tid in w.scenario_tickets(f"S{n}")[: a.per_scenario]:
                w.new_runner().start(tid)
                cases += 1
    finally:
        w.close()
        bench_env.drop(env)
    by = {}
    for r in rec.rows:
        by.setdefault(r["stage"], []).append(r)
    out = {"method": f"first {a.per_scenario} tickets of each scenario S1-S16 ({cases} cases) through the real workflow; prompts recorded as sent; token figures are estimates (chars/4 and chars/3), not a real tokenizer's",
           "model": "none: the deterministic stand-in answered; prompts are independent of the answering model", "calls": len(rec.rows), "stages": {}}
    for st, rows in sorted(by.items()):
        ch = [r["chars"] for r in rows]
        out["stages"][st] = {"calls": len(rows), "chars": {"p50": int(statistics.median(ch)), "p95": pct(ch, 95), "max": max(ch)},
                             "est_tokens_chars_div_4": {"p50": int(statistics.median(ch)) // 4, "p95": pct(ch, 95) // 4, "max": max(ch) // 4},
                             "est_tokens_chars_div_3": {"p50": int(statistics.median(ch)) // 3, "p95": pct(ch, 95) // 3, "max": max(ch) // 3}}
    worst = max((s["est_tokens_chars_div_3"]["max"] for s in out["stages"].values()), default=0)
    out["worst_case_prompt_tokens_estimate"] = worst
    out["fits"] = {str(ctx): worst + 1024 <= ctx for ctx in (2048, 4096, 8192, 16384, 32768)}
    out["note"] = "'fits' leaves 1024 tokens for the reply. A server default of 2048/4096 tokens would silently truncate the prompt for the larger stages; configure the server's context window (or refuse) explicitly."
    (ROOT / "reports" / "m5").mkdir(parents=True, exist_ok=True)
    (ROOT / "reports" / "m5" / "prompt-budget.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"calls": out["calls"], "stages": {k: v["est_tokens_chars_div_3"] for k, v in out["stages"].items()}, "fits": out["fits"]}, indent=1))


if __name__ == "__main__":
    main()
