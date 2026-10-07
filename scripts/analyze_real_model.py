"""Read-only analysis of a recorded real-model run (results JSON + raw-calls JSONL written by run_real_model_eval.py). It computes nothing the run did not record and prints every case.
  python scripts/analyze_real_model.py reports/m8/real-model-results-<label>-<tag>.json
Writes reports/m8/analysis-<tag>.json and reports/m8/ANALYSIS-<tag>.md next to the results."""
from __future__ import annotations

import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def pct(k, n):
    """k/n and the percentage. The frozen protocol (section 4) says no confidence interval is claimed on 22 cases, so none is printed."""
    return f"{k}/{n} ({k / n:.0%})" if n else "n/a (0)"


def q(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    i = min(len(xs) - 1, max(0, math.ceil(p * len(xs)) - 1))
    return xs[i]


def raw_schema_check(res_path: Path, res: dict) -> dict | None:
    """Re-validate every recorded raw reply against the Copilot's own stage schema (offline; needs the repository on sys.path and the raw-calls file next to the results). The trust check (known evidence handles
    etc.) needs the case's database state, which the frozen harness did not keep, so a schema-valid reply that was still retried is reported as 'schema-valid, rejected for a reason the harness did not record'."""
    raw_path = res_path.with_name(res_path.name.replace("real-model-results-", "real-model-raw-calls-").replace(".json", ".jsonl"))
    if not raw_path.exists():
        return None
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from copilot.workflow import schemas as S
    from copilot.workflow.model_io import parse_final
    schemas = {"DIAGNOSE": S.DIAGNOSIS, "PLAN": S.PROPOSED_ACTIONS, "DRAFT": S.DRAFT_REPLY}
    seqs = defaultdict(list)
    for line in raw_path.read_text().splitlines():
        d = json.loads(line)
        seqs[(d["case"], d["stage"])].append(d)
    cat = Counter()
    rows_by_ticket = {r["ticket"]: r for r in res["rows"]}
    first_problems = Counter()
    detail = []
    for (case, stage), calls in seqs.items():
        if case.startswith("INJ:") or stage not in schemas:
            continue
        for i, d in enumerate(calls):
            _, probs = parse_final(d.get("content") or "", schemas[stage])
            retried = i < len(calls) - 1
            if probs:
                kind = "schema-invalid"
            elif retried:
                kind = "schema-valid, then retried (rejected for a reason the harness did not record)"
            else:
                row = rows_by_ticket.get(case) or {}
                final = (row.get("draft") or {}).get("error") if stage == "DRAFT" else row.get("degraded") if stage == "DIAGNOSE" else None
                kind = f"schema-valid, final call, stage rejected it ({final})" if final else "schema-valid, final call, stage accepted it"
            cat[(stage, f"call {i + 1}", kind)] += 1
            if i == 0 and probs:
                first_problems[(stage, probs[0].split(":", 1)[-1].strip()[:80])] += 1
            detail.append({"case": case, "stage": stage, "call": i + 1, "kind": kind, "problems": probs[:3]})
    return {"by_call": {" | ".join(k): v for k, v in sorted(cat.items())}, "first_call_schema_problems": {" | ".join(k): v for k, v in first_problems.most_common()}, "detail": detail}


def model_text(res_path: Path, res: dict):
    """What the model actually said, per case, for a human reading (appendix; not a metric): the disposition in the last DIAGNOSE reply that parsed, and the opening of the last DRAFT reply that parsed."""
    raw_path = res_path.with_name(res_path.name.replace("real-model-results-", "real-model-raw-calls-").replace(".json", ".jsonl"))
    if not raw_path.exists():
        return None
    seqs = defaultdict(list)
    for line in raw_path.read_text().splitlines():
        d = json.loads(line)
        seqs[d["case"]].append(d)
    out = {}
    for r in res["rows"]:
        calls = seqs.get(r["ticket"], [])
        disp, draft, cited = None, None, None
        for c in calls:
            try:
                o = json.loads(c.get("content") or "")
            except (json.JSONDecodeError, TypeError):
                continue
            if c["stage"] == "DIAGNOSE" and isinstance(o, dict) and "disposition" in o and ("statement" in json.dumps(o)[:400] or "hypotheses" in o):
                disp = o.get("disposition")
            if c["stage"] == "DRAFT" and isinstance(o, dict) and "draft" in o:
                draft, cited = o.get("draft"), o.get("cited_evidence")
        out[r["ticket"]] = {"disposition": disp, "draft_opening": (draft or "")[:230], "cited": cited}
    return out


def normalise_problem(p: str) -> str:
    import re
    p = re.sub(r"^\$[^:]*:\s*", "", p)
    p = re.sub(r"'[^']*'", "'…'", p)
    p = re.sub(r"\[[^\]]*\]", "[…]", p)
    return p[:150]


def replay_summary(res_path: Path, tag: str):
    """Model-free replay (scripts/replay_real_model.py): whether the recorded replies reproduce every outcome, the corrected injection invariants, and WHY replies were rejected (which the live harness did not record)."""
    p = res_path.parent / f"replay-{tag}.json"
    if not p.exists():
        return None
    r = json.loads(p.read_text())
    reasons = Counter()
    cases_by_reason = defaultdict(set)
    for key, lists in r["rejections"].items():
        case, stage = key.split(" | ", 1)
        for probs in lists:
            for pr in set(normalise_problem(x) for x in probs):
                reasons[(stage, pr)] += 1
                cases_by_reason[(stage, pr)].add(case)
    return {"reproduced_exactly": r["reproduced_exactly"], "model_calls_served": r["model_calls_served"], "model_calls_recorded": r["model_calls_recorded"], "mismatches": r["mismatches"],
            "injection_invariants_corrected": r.get("injection_invariants_corrected"), "injection_i1_corrected": r.get("injection_i1_corrected"),
            "rejection_reasons": [{"stage": k[0], "reason": k[1], "rejected_replies": v, "cases": len(cases_by_reason[k])} for k, v in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))]}


def analyse(res: dict, res_path: Path | None = None) -> dict:
    rows, inj = res["rows"], res.get("injection") or []
    out = {"tag": res["tag"], "model": res["model"], "n_cases": len(rows), "n_planned": res["counts"]["of"], "stopped_early": res.get("invariant_violation"), "amendment": res.get("amendment")}
    # --- outcome vs expectation ------------------------------------------------------------------------------------------------------------------
    ok = [r for r in rows if r["expected"] == r["actual"]]
    out["outcome_as_expected"] = {"k": len(ok), "n": len(rows)}
    by_scn = defaultdict(lambda: [0, 0])
    for r in rows:
        by_scn[r["scenario"]][1] += 1
        by_scn[r["scenario"]][0] += r["expected"] == r["actual"]
    out["by_scenario"] = {k: {"k": v[0], "n": v[1]} for k, v in sorted(by_scn.items(), key=lambda kv: int(kv[0][1:]))}
    out["confusion"] = {f"{e} -> {a}": n for (e, a), n in sorted(Counter((r["expected"], r["actual"]) for r in rows).items())}
    # --- the correctness flags the harness recorded -----------------------------------------------------------------------------------------------
    out["flags"] = {k: {"k": sum(1 for r in rows if r.get(k)), "n": len(rows)} for k in ("action_ok", "tool_ok", "citation_ok", "approval_ok", "outcome_ok", "cites_expected_runbook")}
    out["failure_reasons"] = dict(Counter(x for r in rows for x in (r.get("failure_reason") or [])))
    out["degraded"] = [{"ticket": r["ticket"], "reason": r["degraded"]} for r in rows if r.get("degraded")]
    out["draft_errors"] = dict(Counter((r.get("draft") or {}).get("error") for r in rows if (r.get("draft") or {}).get("error")))
    out["draft_source"] = dict(Counter((r.get("draft") or {}).get("source") for r in rows))
    out["draft_review"] = dict(Counter((r.get("draft") or {}).get("review") for r in rows))
    out["draft_flags"] = dict(Counter(f if isinstance(f, str) else json.dumps(f) for r in rows for f in ((r.get("draft") or {}).get("flags") or [])))
    out["rejected_proposals"] = dict(Counter(c for r in rows for c in (r.get("rejected") or [])))
    out["cited_evidence"] = {"cases_with_a_draft": sum(1 for r in rows if (r.get("draft") or {}).get("source") == "model"), "cases_with_citations": sum(1 for r in rows if (r.get("draft") or {}).get("cited"))}
    # --- structured validity and retries (per stage) ---------------------------------------------------------------------------------------------
    # The harness's `preceded_by_repair` marker only matches one repair wording and misses the ones the runtime actually uses (the repair prompt is not the last message), so it is NOT used here.
    # Instead: a stage that is accepted on its first reply makes exactly one model call; every additional call in that stage is a retry (schema-invalid reply, or reply rejected by the deterministic
    # trust check). DIAGNOSE's first call is the free-form agent-loop reply; a second call is the structured fallback and a third the semantic repair.
    stages = defaultdict(lambda: {"cases": 0, "first_reply_accepted": 0, "extra_calls": 0, "calls": 0, "errors": 0, "seconds": [], "prompt_tokens": [], "completion_tokens": [], "calls_per_case": Counter()})
    for r in rows:
        per = defaultdict(list)
        for c in r.get("calls") or []:
            per[c["stage"]].append(c)
        for name, cs in per.items():
            s_ = stages[name]
            s_["cases"] += 1
            s_["first_reply_accepted"] += len(cs) == 1
            s_["extra_calls"] += len(cs) - 1
            s_["calls"] += len(cs)
            s_["calls_per_case"][len(cs)] += 1
            for c in cs:
                s_["errors"] += bool(c.get("error"))
                for key in ("seconds", "prompt_tokens", "completion_tokens"):
                    if c.get(key) is not None:
                        s_[key].append(c[key])
    out["stages"] = {}
    for name, s_ in stages.items():
        out["stages"][name] = {"cases_where_stage_ran": s_["cases"], "first_reply_accepted": s_["first_reply_accepted"], "extra_calls": s_["extra_calls"], "calls": s_["calls"], "calls_per_case": dict(sorted(s_["calls_per_case"].items())),
                               "provider_errors": s_["errors"], "seconds_median": q(s_["seconds"], .5), "seconds_p90": q(s_["seconds"], .9), "seconds_max": max(s_["seconds"]) if s_["seconds"] else None,
                               "prompt_tokens_total": sum(s_["prompt_tokens"]), "completion_tokens_total": sum(s_["completion_tokens"]), "completion_tokens_median": q(s_["completion_tokens"], .5)}
    out["cases_needing_a_retry"] = {"k": sum(1 for r in rows if any(n > 1 for n in Counter(c["stage"] for c in (r.get("calls") or [])).values())), "n": len(rows)}
    secs = [r["seconds"] for r in rows if r.get("seconds") is not None]
    out["case_seconds"] = {"median": q(secs, .5), "p90": q(secs, .9), "max": max(secs) if secs else None, "total": round(sum(secs), 1), "mean": round(statistics.mean(secs), 1) if secs else None}
    allc = [c for r in rows for c in (r.get("calls") or [])]
    out["totals"] = {"model_calls": len(allc), "prompt_tokens": sum(c.get("prompt_tokens") or 0 for c in allc), "completion_tokens": sum(c.get("completion_tokens") or 0 for c in allc)}
    out["raw_schema_check"] = raw_schema_check(res_path, res) if res_path else None
    out["model_text"] = model_text(res_path, res) if res_path else None
    out["replay"] = replay_summary(res_path, res["tag"]) if res_path else None
    # --- deterministic invariants (control level; reported apart from model quality) -----------------------------------------------------------------
    inv = defaultdict(lambda: [0, 0])
    for r in rows:
        for k, v in (r.get("invariants") or {}).items():
            inv[k][1] += 1
            inv[k][0] += bool(v)
    out["invariants_cases"] = {k: {"held": v[0], "n": v[1]} for k, v in sorted(inv.items())}
    out["i2_refined_violations"] = [r["ticket"] for r in rows if (r.get("i2_refined") or {}).get("violation")]
    out["i2_original_proxy_flags"] = [r["ticket"] for r in rows if not (r.get("invariants") or {}).get("I2", True)]
    out["approval_interaction"] = [{"ticket": r["ticket"], "scenario": r["scenario"], "expected": r["expected"], "actual": r["actual"], "state": r["state"], "post_approval": r.get("post_approval"), "disposition": r.get("disposition"),
                                    "approval_ok": r.get("approval_ok"), "effects": r.get("effects"), "proposed": r.get("proposed")} for r in rows if r["expected"] == "APPROVAL" or r.get("post_approval") or r.get("proposed")]
    out["effects"] = {"cases_with_effects": sum(1 for r in rows if r.get("effects")), "total_effects": sum(r.get("effects") or 0 for r in rows)}
    # --- injection catalogue -------------------------------------------------------------------------------------------------------------------------
    out["injection"] = {"n": len(inj), "all_invariants_held": sum(1 for r in inj if all(r["invariants"].values())), "refined_i2_violations": [r["attack"] for r in inj if r["i2_refined"]["violation"]],
                        "contained_by": dict(Counter(r["contained_by"] for r in inj)), "outcomes": dict(Counter(r["outcome"] for r in inj)),
                        "proposed_actions": {r["attack"]: r["proposed"] for r in inj if r["proposed"]}}
    return out


def table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def render(res: dict, a: dict) -> str:
    rows, inj = res["rows"], res.get("injection") or []
    m = a["model"]
    rp = a.get("replay")
    o = []
    o.append(f"# Real-model evaluation analysis: `{a['tag']}`\n")
    o.append(f"Model `{m['name']}` ({m['quantisation']}, {m['runtime']}), weights sha256 `{m['weights_sha256'][:16]}…`; temperature 0, seed 20260101, context {res['inference']['context_tokens']}; machine {res['machine']['platform']}.  ")
    o.append(f"Harness commit `{res['harness_git_sha'][:7]}`; protocol `{res['protocol']}`" + (f"; amendment **{a['amendment']}**" if a["amendment"] else "") + f"; wall time {res['wall_seconds']} s.  ")
    if a["stopped_early"] and a["n_cases"] == a["n_planned"] and a["injection"]["n"]:
        o.append(f"Cases run: **{a['n_cases']} of {a['n_planned']}**; injection runs: **{a['injection']['n']}**. The run completed; the live harness then printed an invariant violation for the injection phase (`{a['stopped_early'][:90]}…`). "
                 "That reading is a **measurement artefact of the live harness** (see 'Injection catalogue' below), established by the model-free replay; the recorded value is kept in the results file unchanged.\n")
    else:
        o.append(f"Cases run: **{a['n_cases']} of {a['n_planned']}**" + (f"; **stopped early:** {a['stopped_early']}" if a["stopped_early"] else "; the run completed") + "; injection runs: " + str(a["injection"]["n"]) + ".\n")
    o.append("> This is a result about one small quantised model, simulated humans and a synthetic knowledge base. It is not a statement about the product with real users, and not a comparison with any other model. No tuning was done between the freeze and this run.\n")
    o.append("## Headline: outcome against the frozen expectation\n")
    k, n = a["outcome_as_expected"]["k"], a["outcome_as_expected"]["n"]
    o.append(f"Outcome as expected: **{pct(k, n)}** (no interval: the frozen protocol claims none on 22 cases).\n")
    o.append(table(["Scenario", "as expected"], [[s, pct(v["k"], v["n"])] for s, v in a["by_scenario"].items()]) + "\n")
    o.append("Expected → actual (every pair that occurred):\n")
    o.append(table(["expected → actual", "cases"], [[kx, v] for kx, v in a["confusion"].items()]) + "\n")
    o.append("## Every case\n")
    o.append(table(["Scenario", "Ticket", "Expected", "Actual", "State", "Calls", "Draft", "Seconds", "I1-I4", "Reason(s)"],
                   [[r["scenario"], r["ticket"], r["expected"], r["actual"], r["state"], len(r.get("calls") or []),
                     (r.get("draft") or {}).get("error") or (r.get("draft") or {}).get("source"), r.get("seconds"), "".join("✓" if v else "✗" for v in (r.get("invariants") or {}).values()), ", ".join(r.get("failure_reason") or [])] for r in rows]) + "\n")
    o.append("## Task correctness flags recorded by the frozen harness\n")
    o.append(table(["flag", "true"], [[kx, pct(v["k"], v["n"])] for kx, v in a["flags"].items()]) + "\n")
    o.append(f"Failure reasons: {a['failure_reasons'] or 'none'}. Degraded runs: {a['degraded'] or 'none'}. Draft errors: {a['draft_errors'] or 'none'}. Draft source: {a['draft_source']}. Draft review level: {a['draft_review']}. Draft grounding flags raised by the deterministic checks: {a['draft_flags'] or 'none'}. Rejected proposals by code: {a['rejected_proposals'] or 'none'}. Cases with a model-written draft: {a['cited_evidence']['cases_with_a_draft']}; of which cite evidence: {a['cited_evidence']['cases_with_citations']}.\n")
    o.append("## Structured output validity and retries\n")
    o.append(f"Cases in which at least one stage needed more than one model call: **{pct(a['cases_needing_a_retry']['k'], a['cases_needing_a_retry']['n'])}**. A stage accepted on its first reply makes one call; extra calls are schema-invalid or trust-rejected replies being retried (DIAGNOSE: call 1 is the free-form agent-loop reply, call 2 the structured fallback, call 3 the semantic repair).\n")
    o.append(table(["stage", "cases", "first reply accepted", "extra calls", "calls per case", "provider errors", "median s", "p90 s", "max s", "median completion tok", "prompt tok", "completion tok"],
                   [[s, v["cases_where_stage_ran"], pct(v["first_reply_accepted"], v["cases_where_stage_ran"]), v["extra_calls"], v["calls_per_case"], v["provider_errors"], v["seconds_median"], v["seconds_p90"], v["seconds_max"],
                     v["completion_tokens_median"], v["prompt_tokens_total"], v["completion_tokens_total"]] for s, v in a["stages"].items()]) + "\n")
    rc = a.get("raw_schema_check")
    if rc:
        o.append("Recorded replies re-validated offline against the Copilot's own stage schemas:\n")
        o.append(table(["stage", "call", "classification", "replies"], [[*k.split(" | "), v] for k, v in rc["by_call"].items()]) + "\n")
        o.append("First-call schema problems (first problem per reply): " + (", ".join(f"{k} ×{v}" for k, v in rc["first_call_schema_problems"].items()) or "none") + ".\n")
    o.append(f"Per case: median {a['case_seconds']['median']} s, p90 {a['case_seconds']['p90']} s, max {a['case_seconds']['max']} s; total {a['case_seconds']['total']} s over {a['n_cases']} cases. Model calls {a['totals']['model_calls']}, prompt tokens {a['totals']['prompt_tokens']}, completion tokens {a['totals']['completion_tokens']}. Cost: $0 (local).\n")
    if a["approval_interaction"]:
        o.append("## Approval interaction (cases where an action was expected or proposed)\n")
        o.append(table(["Scenario", "Ticket", "Expected", "Actual", "State", "Proposed", "After the simulated approval", "Effects", "approval_ok"],
                       [[x["scenario"], x["ticket"], x["expected"], x["actual"], x["state"], ", ".join(f"{p['type']}:{p['status']}" for p in (x["proposed"] or [])) or "-", x["post_approval"] or "-", x["effects"], x["approval_ok"]] for x in a["approval_interaction"]]) + "\n")
        o.append("The approver is simulated by the harness (a script, not a person). `effects` counts side effects that actually executed.\n")
    o.append("## Deterministic controls (kept apart from model quality)\n")
    o.append(table(["invariant", "held in"], [[kx, pct(v["held"], v["n"])] for kx, v in a["invariants_cases"].items()]) + "\n")
    o.append(f"Effects executed without approval: see I3/I4 above. Cases with any effect: {a['effects']['cases_with_effects']}; total effects {a['effects']['total_effects']}.  ")
    o.append(f"Original I2 proxy flags: {a['i2_original_proxy_flags'] or 'none'}; refined (A1) I2 violations: {a['i2_refined_violations'] or 'none'}.\n")
    if rp:
        o.append("## Model-free replay: reproducibility and why replies were rejected\n")
        o.append(f"The recorded raw replies were fed, in order, to the unchanged workflow with no model (`scripts/replay_real_model.py`): **{'every case and injection run reproduced exactly' if rp['reproduced_exactly'] else 'DID NOT reproduce: ' + str(rp['mismatches'])}** "
                 f"({rp['model_calls_served']} of {rp['model_calls_recorded']} recorded replies served; no mismatch in outcome, state or containment layer). The live harness did not record why a reply was rejected; the replay does:\n")
        o.append(table(["stage", "rejection reason (specifics elided)", "rejected replies", "distinct cases or injection runs"], [[x["stage"], x["reason"].replace("|", "/"), x["rejected_replies"], x["cases"]] for x in rp["rejection_reasons"]]) + "\n")
    mt = a.get("model_text")
    if mt:
        o.append("## What the model said (appendix for reading; not a metric)\n")
        o.append("The disposition is the model's own diagnosis field in its last parsed DIAGNOSE reply; the draft is the opening of its last parsed DRAFT reply. A mismatch between the diagnosis disposition and the draft's leading word is itself informative. "
                 "Any judgement of whether the customer-visible text is acceptable is a reading by the AI author, **not** part of the frozen protocol, and no judge model was used.\n")
        o.append(table(["Ticket", "Expected", "Actual", "Model diagnosis disposition", "Draft opening", "Cited"], [[r["ticket"], r["expected"], r["actual"], mt[r["ticket"]]["disposition"], (mt[r["ticket"]]["draft_opening"] or "-").replace("|", "/").replace("\n", " "), mt[r["ticket"]]["cited"]] for r in rows]) + "\n")
    o.append("## Injection catalogue and hostile inputs (detectors on, real model)\n")
    if inj:
        corr = {x["attack"]: x["invariants"] for x in (rp or {}).get("injection_invariants_corrected") or []}
        o.append(table(["attack", "outcome", "contained by", "invariants as recorded live", "invariants, I1 measured per run (replay)", "refined I2 violation", "actions proposed", "rejected proposals", "seconds"],
                       [[r["attack"], r["outcome"], r["contained_by"], "".join("✓" if v else "✗" for v in r["invariants"].values()), ("".join("✓" if v else "✗" for v in corr[r["attack"]].values()) if r["attack"] in corr else "n/a"),
                         r["i2_refined"]["violation"], ", ".join(r["proposed"]) or "none", ", ".join(r["rejected"]) or "none", r["seconds"]] for r in inj]) + "\n")
        o.append(f"As recorded live, all four invariants held in {a['injection']['all_invariants_held']}/{a['injection']['n']} injection runs: **I1 read false in all 12**. Cause (a defect in my harness, found after the run): the live harness used one world for the 22 cases and the 12 injection runs, and the I1 check counts side effects "
                 "cumulatively across the world, so the one legitimately approved effect from case S5 TCK-0015 (CLOSED/EXECUTED after the simulated approval) was counted in every later run. "
                 f"Measured per run (new effects since the injection phase began, plus approvals for the case itself), I1 holds in **{sum(1 for v in corr.values() if all(v.values()))}/{len(corr)}**, with no new side effect and no proposed action in any injection run. Containment layers: {a['injection']['contained_by']}.\n")
    else:
        o.append("Not run (the run stopped before the catalogue).\n")
    return "\n".join(o)


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    p = Path(argv[1])
    res = json.loads(p.read_text())
    a = analyse(res, p)
    (p.parent / f"analysis-{res['tag']}.json").write_text(json.dumps(a, indent=1) + "\n")
    (p.parent / f"ANALYSIS-{res['tag']}.md").write_text(render(res, a))
    print(f"wrote analysis-{res['tag']}.json and ANALYSIS-{res['tag']}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
