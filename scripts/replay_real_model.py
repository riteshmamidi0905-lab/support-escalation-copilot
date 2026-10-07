"""Replay a recorded real-model run with NO model.

The raw-calls file written by run_real_model_eval.py holds every reply the model gave, in order. This script feeds those recorded replies, unchanged, to the UNCHANGED workflow (same database, same retrieval,
same trust checks, same state machine) and checks that it reaches the same outcome and state for every case and injection run. Two things follow:
  * REPRODUCIBILITY: the headline numbers can be re-derived from the committed raw replies on any machine without a language model (a model-free check that nothing else in the loop is non-deterministic);
  * DIAGNOSIS: the frozen harness did not keep WHY a reply was rejected (the trust/grounding problems). The replay records them, for every rejected reply, without calling a model again.

  python scripts/with_local_pg.py python scripts/replay_real_model.py reports/m8/real-model-results-<label>-<tag>.json [--out PATH]
Writes reports/m8/replay-<tag>.json (or PATH). It never changes a result; a divergence is reported, not hidden. Exit status 0 only if every outcome was reproduced."""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_env  # noqa: E402

ROOT = bench_env.ROOT
import run_m4_scenarios as SCN  # noqa: E402
import run_real_model_eval as H  # noqa: E402  (run_injection(): the same injection catalogue code the live run used)
from agent.model import ModelResponse, Usage  # noqa: E402

from copilot import contracts as C  # noqa: E402
from copilot.workflow import grounding, trust  # noqa: E402
from copilot.workflow import runner as RUNNER  # noqa: E402
from copilot.workflow.providers import _stage  # noqa: E402
from tests.db.workflow_support import WorkflowWorld  # noqa: E402


def _approved(world, case_id):
    import psycopg
    with psycopg.connect(world.env.control_dsn) as c:
        return c.execute("SELECT count(*) FROM copilot.approvals WHERE case_id = %s AND status = 'approved'", (case_id,)).fetchone()[0]


class Divergence(Exception):
    pass


class ReplayProvider:
    """Serves the recorded replies in their original global order; refuses (loudly) if the workflow asks for a different case or stage than the recording."""

    def __init__(self, recorded):
        self.recorded, self.pos, self.case, self.calls = recorded, 0, None, []

    def complete(self, messages, tools=None):
        if self.pos >= len(self.recorded):
            raise Divergence(f"the workflow asked for a model reply beyond the recording (case {self.case})")
        rec = self.recorded[self.pos]
        stage = _stage(messages)
        if rec["case"] != self.case or rec["stage"] != stage:
            raise Divergence(f"call {self.pos}: workflow asked ({self.case}, {stage}), the recording has ({rec['case']}, {rec['stage']})")
        self.pos += 1
        self.calls.append({k: rec.get(k) for k in ("stage", "seconds", "prompt_tokens", "completion_tokens", "error", "preceded_by_repair")})
        return ModelResponse(content=rec.get("content") or "", usage=Usage(rec.get("prompt_tokens") or 0, rec.get("completion_tokens") or 0))


def main(argv):
    res_path = Path(argv[1])
    out_path = Path(argv[argv.index("--out") + 1]) if "--out" in argv else None
    res = json.loads(res_path.read_text())
    raw_path = res_path.with_name(res_path.name.replace("real-model-results-", "real-model-raw-calls-").replace(".json", ".jsonl"))
    recorded = [json.loads(line) for line in raw_path.read_text().splitlines()]
    rejections = defaultdict(list)                                       # (case, stage) -> [problem lists, in order]
    prov = ReplayProvider(recorded)

    def log(stage, fn):
        def wrapped(*a, **kw):
            probs = fn(*a, **kw)
            if probs:
                rejections[(prov.case, stage)].append(list(probs))
            return probs
        return wrapped
    trust.check_diagnosis = log("DIAGNOSE (trust check)", trust.check_diagnosis)
    trust.check_proposals = log("PLAN (trust check)", trust.check_proposals)
    trust.check_draft = log("DRAFT (trust check)", trust.check_draft)
    grounding.check_grounding = log("DRAFT (grounding check)", grounding.check_grounding)
    _parse = RUNNER.parse_final

    def parse_logged(text, schema):
        data, probs = _parse(text, schema)
        if probs:
            rejections[(prov.case, "DIAGNOSE (schema, agent-loop reply)")].append(list(probs))
        return data, probs
    RUNNER.parse_final = parse_logged

    cases = json.loads((ROOT / "docs" / "real-model-cases.json").read_text())["cases"][: len(res["rows"])]
    env = bench_env.build()
    w = WorkflowWorld(env, provider=prov)
    rows, mismatches, divergence, inj_replayed, inj_corrected = [], [], None, [], []
    plan_rejections = {}                                                  # case or attack -> [{"action_type", "code", "detail"}] : why proposed actions were dropped (not recorded by the live harness)
    try:
        specs = {s["scenario_id"]: s for s in C.read_jsonl(env.dataset / "scenarios.jsonl")}
        for c, rec in zip(cases, res["rows"], strict=True):
            prov.case = c["ticket_id"]
            SCN.run_case(w, env, c["scenario"], specs[c["scenario"]], c["ticket_id"], rows)
            row = rows[-1]
            plan_rejections[c["ticket_id"]] = (w.machine.get(row["case_id"])["file"].get("plan") or {}).get("rejected", [])
            if (row["actual"], row["state"]) != (rec["actual"], rec["state"]):
                mismatches.append({"ticket": c["ticket_id"], "recorded": [rec["actual"], rec["state"]], "replay": [row["actual"], row["state"]]})
        if res.get("injection"):
            own = w.ticket(w.resync_ticket())["account_id"]
            # The live harness reused ONE world for the 22 cases and the injection catalogue, and `INJ.contained()` counts side effects cumulatively across the whole world, so a legitimately approved effect from an
            # earlier case (S5 TCK-0015) made I1 read False in every injection run. The replay measures I1 as the number of NEW effects since the start of the injection phase (plus approvals for the case itself).
            base = len(w.resync.effects) + len(w.credit.effects) + len(w.escalation.effects)
            original = H.INJ.contained

            def contained_delta(world, case_id, own_account):
                inv = original(world, case_id, own_account)
                new = len(world.resync.effects) + len(world.credit.effects) + len(world.escalation.effects) - base
                plan_rejections[f"INJ#{case_id}"] = (world.machine.get(case_id)["file"].get("plan") or {}).get("rejected", [])
                inj_corrected.append({"case_id": case_id, "i1_as_measured_by_live_harness": inv["I1"], "new_effects_since_injection_phase_start": new})
                return dict(inv, I1=inv["I1"] or (new == 0 and _approved(world, case_id) == 0))
            H.INJ.contained = contained_delta
            inj_replayed = H.run_injection(w, env, prov, own)
            H.INJ.contained = original
            for a, b in zip(inj_replayed, res["injection"], strict=True):
                if (a["outcome"], a["state"], a["contained_by"]) != (b["outcome"], b["state"], b["contained_by"]):
                    mismatches.append({"injection": a["attack"], "recorded": [b["outcome"], b["state"], b["contained_by"]], "replay": [a["outcome"], a["state"], a["contained_by"]]})
    except Divergence as e:
        divergence = str(e)
    finally:
        w.close()
        bench_env.drop(env)
    out = {"results_file": res_path.name, "cases_replayed": len(rows), "cases_recorded": len(res["rows"]), "injection_replayed": len(inj_replayed) if not divergence else None, "injection_recorded": len(res.get("injection") or []),
           "mismatches": mismatches, "divergence": divergence, "injection_i1_corrected": inj_corrected if not divergence else None, "plan_rejections": {k: v for k, v in plan_rejections.items() if v},
           "injection_invariants_corrected": [{"attack": a["attack"], "invariants": a["invariants"]} for a in inj_replayed] if not divergence else None, "model_calls_served": prov.pos, "model_calls_recorded": len(recorded),
           "reproduced_exactly": not mismatches and divergence is None and prov.pos == len(recorded),
           "rejections": {f"{case} | {stage}": probs for (case, stage), probs in rejections.items()}}
    (out_path or res_path.parent / f"replay-{res['tag']}.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "rejections"}, indent=1))
    return 0 if out["reproduced_exactly"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
