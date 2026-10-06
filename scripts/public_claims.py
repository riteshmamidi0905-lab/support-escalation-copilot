"""The public claims manifest: the machine-readable contract between this repository and anything that quotes it (README, a future portfolio, a CV).

  python scripts/public_claims.py build     # reports/m6/release-evidence.json  ->  content/public-claims.json  and the generated blocks in README.md / docs/evaluation.md
  python scripts/public_claims.py check     # CI: the committed manifest and blocks equal what `build` would produce (source commits aside); no network, no database

Every claim is derived from a RUN (the release evidence) or from a named artifact that tests enforce; none is typed in as a number. Claims about the workflow carry `model: deterministic_stand_in`;
`real_llm` is impossible without a recorded real-model result file. Résumé use is limited to claims backed by deterministic tests or mutation checks, never to stand-in or development results."""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
EVIDENCE = ROOT / "reports" / "m6" / "release-evidence.json"
MANIFEST = ROOT / "content" / "public-claims.json"
REPO = "https://github.com/riteshmamidi0905-lab/support-escalation-copilot"
BLOCKS = {"README.md": "readme", "docs/evaluation.md": "evaluation", "docs/interview-guide.md": "interview"}


def git_last(paths: list[str]) -> str:
    out = subprocess.run(["git", "log", "-1", "--format=%H", "--", *paths], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()      # noqa: S603, S607
    if not out:
        raise SystemExit(f"no commit touches {paths}: commit the artifacts before building the manifest")
    return out


def claims(ev: dict) -> list[dict]:
    from copilot.control.actions import ACTIONS, FORBIDDEN
    pt, cat, mut = ev["pytest"], ev["attack_catalogue"], ev["mutation_checks"]
    m3s, m4s, inj = ev["m3_scenarios"], ev["m4_scenarios"], ev["m4_injection"]
    ds, ret = ev["draft_steering"], ev["retrieval_heldout_m2"]
    strat = ret["strategies"]
    dev = json.loads((ROOT / "reports/m2/results.json").read_text())["dev"]
    lat = json.loads((ROOT / "reports/m2/results.json").read_text())["latency"]
    gated = sorted(k for k, v in ACTIONS.items() if v[0] == "GATED_WRITE")
    code = ev["code_sha"]
    chain_events = re.search(r"\d+", m4s["audit_chain"]).group(0)
    out: list[dict] = []

    def c(id_, text, kind, category, cls, model, artifacts, qual, readme, portfolio, resume, value=None, commit=None):
        entry = {"id": id_, "claim": text, "kind": kind, "category": category, "evidence_class": cls, "model": model, "source_artifacts": artifacts, "source_commit": commit or git_last(artifacts),
                 "qualification": qual, "suitable_for": {"readme": readme, "portfolio": portfolio, "resume": resume}}
        if value is not None:
            entry["value"] = value
        out.append(entry)

    # ---- scope and honesty (always quoted together with anything else) -------------------------------------------------------------------------------
    c("scope-fictional-synthetic", "The customer, Meridian Freight Systems, is fictional and every account, ticket, runbook, incident and integration is synthetic, generated reproducibly from a seed.", "limitation", "scope",
      "design_documented", "none", ["docs/adr/0001-fictional-customer-and-synthetic-data.md", "data/meridian-seed-20260101/manifest.json"],
      "Synthetic data has designed difficulty, not real-world distribution; no real customer or customer data was used.", True, True, True)
    c("scope-reference-implementation", "This is a reference implementation run locally; it has never been deployed and has no production use or real customers.", "limitation", "scope", "design_documented", "none",
      ["docs/real-vs-simulated.md", "docs/getting-started.md"], "Demo mode, simulated sign-in and mock customer systems are deliberate; see docs/real-vs-simulated.md for the full list and docs/security.md for what a real deployment would need.", True, True, True)
    c("model-is-standin", "Every model result in this repository comes from RuleCaseModel, a deterministic rule-based stand-in (plus scripted misbehaving wrappers), not a language model.", "limitation", "scope", "design_documented",
      "deterministic_stand_in", ["copilot/workflow/providers.py", "docs/real-vs-simulated.md"], "The stand-in's heuristics were developed while looking at the scenario set, so its outcome rates are development results about the orchestration, not model quality.", True, True, True)
    rm = ev["real_model"]
    c("real-model-evaluation-status", "Real-model evaluation was " + ("executed." if rm["executed"] else "not executed: no local language-model runtime was available, and no paid API was used; the method is frozen and hash-locked for a future run."),
      "limitation", "real_model", "not_executed" if not rm["executed"] else "deterministic_tests", "real_llm" if rm["executed"] else "none",
      ["reports/m5/real-model-probe.json", "docs/real-model-freeze.json", "docs/m5-real-model-protocol.md"], "The provider boundary (ConfiguredProvider) is tested against a protocol stub only; nothing is known about any real model's behaviour here.", True, True, True,
      {"executed": rm["executed"]})

    # ---- architecture properties (each enforced by named tests) -------------------------------------------------------------------------------------
    c("model-cannot-authorise", "The model cannot choose the tenant, the approver, the required role, the approval expiry, the workflow state or the action vocabulary: its output is untrusted structured data validated by deterministic code, and a model that claims otherwise is ignored.",
      "capability", "architecture", "deterministic_tests", "deterministic_stand_in", ["copilot/workflow/trust.py", "copilot/control/policy.py", "tests/db/test_m4_workflow.py", "tests/db/test_m4_injection.py"],
      "Shown with the stand-in and with deliberately misbehaving scripted models (obedient to injected text, privileged fields, invented and forbidden actions); behaviour of a real LLM is not measured.", True, True, False)
    c("typed-actions-no-email", f"The only write vocabulary is {len(ACTIONS)} typed actions ({len(gated)} human-gated: {', '.join(gated)}); no action, tool, client or code path can send customer email, and {len(FORBIDDEN)} dangerous request names are rejected by name.",
      "capability", "security", "deterministic_tests", "none", ["copilot/control/actions.py", "contracts/action.schema.json", "tests/test_control_units.py"], "Customer email is excluded by construction (invariant I3); the mock escalation system only accepts internal destinations.", True, True, True,
      {"actions": len(ACTIONS), "gated": gated, "forbidden_names": len(FORBIDDEN)})
    c("approval-bound-to-exact-action", "An approval is bound to the exact canonical action (hash), its role, tenant, case and expiry; policy is re-evaluated from fresh facts at execution; an amended action voids the old approval and needs a new one that its amender cannot give.",
      "capability", "approvals", "deterministic_tests", "none", ["copilot/control/approvals.py", "copilot/control/gateway.py", "tests/db/test_m3_attacks.py", "tests/db/test_m5_app_flows.py"],
      "Approvers are simulated (signed mock identities); there is no real identity provider.", True, True, True)
    c("idempotent-execution-and-reconciliation", "Every customer write carries a deterministic idempotency key recorded in a ledger; a write whose outcome is unknown is never retried blindly, and a human reconciles it.", "capability", "idempotency", "deterministic_tests", "none",
      ["copilot/control/ledger.py", "copilot/control/gateway.py", "tests/db/test_m3_flows.py", "tests/db/test_m4_faults.py"], "Customer systems are deterministic mocks with fault injection; reconciliation trusts the operator's recorded note because the mocks offer no lookup.", True, True, True)
    c("tenant-isolation-rls", "Tenant isolation is enforced in the database: forced row-level security keyed to a signed, short-lived scope, a fixed query catalogue and a least-privilege role for the model-facing code; operator reads are authorised server-side by signed account grants with a uniform 404.",
      "capability", "tenant_isolation", "deterministic_tests", "none", ["copilot/db/migrations/004_scope_and_rls.sql", "copilot/scope.py", "copilot/control/access.py", "tests/db/test_tenant_isolation.py", "tests/db/test_m5_access.py"],
      "Single PostgreSQL instance and a mock identity provider; signing-key custody and rotation are out of scope.", True, True, True)
    c("tamper-evident-audit", "Every proposal, policy decision, approval, execution and refusal is written to an append-only, hash-chained audit log that also scrubs secrets and masks e-mail addresses.", "capability", "security", "deterministic_tests", "none",
      ["copilot/control/audit.py", "tests/db/test_m3_attacks.py"], "Tamper-evidence, not immutability: removing the most recent events is detectable only with an external anchor, which is not deployed.", True, True, True)
    c("recovery-with-leases", "A PostgreSQL lease-based recovery worker resumes cases from durable state; tests with concurrent workers, including workers that ignore the lease, produce exactly one effect per approved action.", "capability", "recovery", "deterministic_tests", "none",
      ["copilot/workflow/recovery.py", "tests/db/test_m5_recovery.py"], "Tested on one database with threads/processes on one host; not tested under real network partitions or multiple hosts.", True, True, False)
    c("observability-from-real-events", "Operational metrics are derived from the application's own events and tables (an empty system reports zeros) and correlated by request, model invocation, action, approval and execution ids; telemetry never stores prompts, reasoning or secrets.",
      "capability", "observability", "deterministic_tests", "none", ["copilot/control/metrics.py", "copilot/control/ops.py", "tests/db/test_m5_ops.py"], "No external metrics backend, tracing system, alerting or retention policy.", True, True, False)
    c("operator-ui-no-javascript", "The operator UI is server-rendered with no JavaScript, a strict Content-Security-Policy and CSRF-protected forms; every read and action is re-authorised on the server because the browser is not trusted.", "capability", "operator_experience",
      "deterministic_tests", "none", ["copilot/app/web.py", "tests/db/test_m5_app_security.py"], "Sign-in is simulated (a persona picker); no session revocation, rate limiting or MFA.", True, True, False)

    # ---- measurements from runs ----------------------------------------------------------------------------------------------------------------------
    c("test-suite", f"{pt['passed']} automated tests pass with {pt['xfailed']} documented expected failure (the frozen runtime's approver hook is a bool callback, wrapped at the control boundary) and {pt['failed']} failures; {pt['skipped']} skipped.",
      "measurement", "testing", "deterministic_tests", "none", ["reports/m6/release-evidence.json"], f"One full run at the evidence commit on one machine; the database tests need PostgreSQL 16 with pgvector and are executed (not skipped) in CI. Python {ev['machine']['python']}.", True, True, True,
      {k: pt[k] for k in ("collected", "passed", "xfailed", "skipped", "failed")}, commit=code)
    c("threat-catalogue", f"{cat['total']} attacks against the four invariants are catalogued, and {cat['executable']} have executable tests that attempt the violation (I1 {cat['by_invariant']['I1']['executable']}, I2 {cat['by_invariant']['I2']['executable']}, I3 {cat['by_invariant']['I3']['executable']}, I4 {cat['by_invariant']['I4']['executable']}).",
      "measurement", "security", "deterministic_tests", "none", ["copilot/invariants.py", "docs/threat-model.md", "reports/m6/release-evidence.json"], "The catalogue is the author's own; tests attempt known attack classes, not an independent penetration test.", True, True, True,
      {"total": cat["total"], "executable": cat["executable"], "by_invariant": {k: v["executable"] for k, v in cat["by_invariant"].items()}}, commit=code)
    mt = {k: f"{v['killed']}/{v['total']}" for k, v in mut.items()}
    c("mutation-checks", f"Deliberately breaking each defence makes the tests fail: M2 retrieval {mt['m2']}, M3 control plane {mt['m3']}, M4 workflow {mt['m4']}, M5 operator surface {mt['m5']} mutations killed.", "measurement", "testing", "mutation_checks", "none",
      ["scripts/mutation_check_m2.py", "scripts/mutation_check_m3.py", "scripts/mutation_check_m4.py", "scripts/mutation_check_m5.py", "reports/m6/release-evidence.json"],
      "Hand-chosen mutations by the author (not a systematic mutation tool); a first M5 run had one survivor, which led to an added test. Mutation checks are manual, not run in CI.", True, True, True, {"killed_of_total": mt}, commit=code)
    c("invariants-held-in-scenario-runs", f"Across {m3s['total']} control-plane scenarios, {m4s['cases']} end-to-end case executions and {inj['attack_runs']} injection attack runs, no invariant was violated and the audit hash chain verified over {chain_events} events.", "measurement", "security",
      "standin_workflow_runs", "deterministic_stand_in", ["reports/m6/release-evidence.json", "docs/m3-scenarios.md", "docs/m4-scenarios.md", "docs/m4-injection.md"],
      "This measures the controls around the model, not the model: the model is the stand-in and, in the injection runs, a deliberately obedient scripted model. Containment is not the same as a correct outcome.", True, True, False,
      {"m3_scenarios": m3s["total"], "m4_cases": m4s["cases"], "injection_runs": inj["attack_runs"], "invariant_violations": inj["invariant_violations"]}, commit=code)
    v, lx, hy, rr = strat["vector"], strat["lexical"], strat["hybrid"], strat["rerank"]
    c("retrieval-vector-beat-hybrid", f"On a frozen {ret['n_tickets']}-ticket hand-labelled held-out set, pgvector search (Hit@1 {v['hit@1']:.2f}, MRR@10 {v['mrr@10']:.2f}) ranked better than lexical full-text (Hit@1 {lx['hit@1']:.2f}, MRR@10 {lx['mrr@10']:.2f}), equal-weight hybrid fusion ({hy['hit@1']:.2f}, {hy['mrr@10']:.2f}) and hybrid plus cross-encoder rerank ({rr['hit@1']:.2f}, {rr['mrr@10']:.2f}); vector was chosen.",
      "measurement", "retrieval", "frozen_heldout_retrieval_eval", "none", ["reports/m2/results.json", "docs/m2-results.md", "docs/adr/0013-retrieval-architecture.md", "docs/eval-freeze.json"],
      f"Labelled by a single AI reviewer in one session, not independent human annotation; n={ret['n_sufficient']} answerable tickets, so differences are descriptive and the 95% intervals overlap; one embedding model and one reranker; 60-document corpus.", True, True, False,
      {"labeller": ret["labeller"], "strategies": {k: {"hit@1": s["hit@1"], "mrr@10": s["mrr@10"]} for k, s in strat.items()}}, commit=git_last(["reports/m2/results.json"]))
    c("synthetic-dev-set-flatters-retrieval", f"The generator-labelled development set flattered every retrieval strategy: vector Hit@1 {dev['vector']['summary']['hit@1']:.2f} on dev vs {v['hit@1']:.2f} held-out, lexical {dev['lexical']['summary']['hit@1']:.2f} vs {lx['hit@1']:.2f}.", "measurement", "retrieval",
      "synthetic_dev_retrieval_eval", "none", ["reports/m2/results.json", "docs/m2-results.md"], "Dev tickets are templated and labelled by the same generator (circular); they were used only to choose parameters; held-out remains single-reviewer and small.", True, True, False,
      {"dev_hit@1": {k: dev[k]["summary"]["hit@1"] for k in dev}, "heldout_hit@1": {k: strat[k]["hit@1"] for k in strat}})
    c("similarity-is-not-sufficiency", f"Retrieval confidence does not detect missing evidence: on unanswerable held-out tickets every strategy returned look-alike pages as evidence {lx['false_confidence_on_insufficient']:.0%}/{v['false_confidence_on_insufficient']:.0%}/{hy['false_confidence_on_insufficient']:.0%}/{rr['false_confidence_on_insufficient']:.0%} of the time (lexical/vector/hybrid/rerank, n=11), so sufficiency is judged from content and policy, with escalation when unsure.",
      "measurement", "retrieval", "frozen_heldout_retrieval_eval", "none", ["reports/m2/results.json", "docs/adr/0013-retrieval-architecture.md"], "n=11 unanswerable tickets, single AI reviewer; an engineering finding that shaped the design, not a benchmark score.", True, True, False,
      {s: strat[s]["false_confidence_on_insufficient"] for s in strat}, commit=git_last(["reports/m2/results.json"]))
    c("reranker-not-justified", f"The cross-encoder reranker was not adopted: no ranking gain over plain vector search (MRR@10 {rr['mrr@10']:.2f} vs {v['mrr@10']:.2f}) for about {lat['rerank_20_passages_ms']['median']:.0f} ms of extra CPU per query and a 347 MB model snapshot.", "measurement", "retrieval", "frozen_heldout_retrieval_eval", "none",
      ["reports/m2/results.json", "docs/adr/0013-retrieval-architecture.md"], "One reranker model, one laptop, 60 chunks; latency says nothing about scale.", True, True, False, {"rerank_ms_median_20_passages": lat["rerank_20_passages_ms"]["median"]}, commit=git_last(["reports/m2/results.json"]))
    pa = m4s["per_scenario"]
    c("workflow-standin-scenario-matches", f"The 16 acceptance scenarios were run as {m4s['cases']} end-to-end case executions with the deterministic stand-in; {m4s['outcome_matches']} reached the scenario's expected outcome, the rest are individually explained (label conflicts, retrieval false conflicts, stand-in intent misses).",
      "measurement", "workflow", "standin_workflow_runs", "deterministic_stand_in", ["reports/m6/release-evidence.json", "docs/m4-scenarios.md"],
      "NOT a model-accuracy figure: the stand-in is rule-based and its heuristics were tuned during development while looking at these scenarios; use it only to show the orchestration and controls run end to end.", True, False, False,
      {"cases": m4s["cases"], "outcome_matches": m4s["outcome_matches"], "per_scenario": pa}, commit=code)
    st, ev_, rs, be = ds["steered"], ds["evasion"], ds["residual"], ds["benign"]
    c("draft-steering-result", f"Deterministic grounding checks on draft replies caught {st['flagged']}/{st['n']} steered drafts they were developed against, but {ev_['flagged']}/{ev_['n']} rephrasings of the same harms and {rs['flagged']}/{rs['n']} misleading drafts built only from grounded words (and flagged {be['flagged']}/{be['n']} benign drafts); the control for what rules cannot see is mandatory, itemised human review.",
      "measurement", "draft_safety", "adversarial_dev_corpus", "none", ["reports/m5/draft-steering.json", "docs/m5-draft-steering.md", "tests/test_draft_grounding.py"],
      "Hand-written corpus by the author of the rules (two rules were corrected after the first run): development results; the residual weakness is preserved on purpose; no human-review study exists.", True, True, False,
      {k: {"n": s["n"], "flagged": s["flagged"]} for k, s in ds.items()}, commit=git_last(["reports/m5/draft-steering.json"]))
    c("browser-accessibility-pass", "A manual browser pass at desktop and 375 px found and fixed seven UI defects (including mobile overflow); scripted checks found one h1 per page, labelled controls, captioned tables, a first-tab skip link, and 0 WCAG AA contrast failures across 263 text elements in light and dark mode.",
      "measurement", "operator_experience", "manual_browser_pass", "none", ["docs/m5-browser-verification.md", "docs/m5/screenshots/04-approval-panel-exact-action.jpg"],
      "One browser engine, one developer-operator, synthetic data; no screen-reader session; the automated tests, not this pass, are authoritative.", True, True, False, commit=git_last(["docs/m5-browser-verification.md"]))

    # ---- limitations that must travel with the claims ------------------------------------------------------------------------------------------------
    c("limit-unlabelled-secrets", "Pattern-based redaction removes labelled secrets (Bearer tokens, NAME=value, PEM blocks, DSN passwords) but not a bare unlabelled token typed into a ticket; that token is stored and shown to operators entitled to that tenant.", "limitation", "security",
      "deterministic_tests", "none", ["docs/risks.md", "tests/db/test_m5_app_security.py"], "Pinned by a test as a documented residual (R-54); entropy heuristics would add false positives and were not attempted.", True, True, False)
    c("limit-misleading-grounded-drafts", "A convincing wrong draft made only of grounded words and numbers cannot be detected by any deterministic rule here; the only control is human review, which is recorded but is not a technical gate on use.", "limitation", "draft_safety", "adversarial_dev_corpus", "none",
      ["docs/m5-draft-steering.md", "docs/risks.md"], "Human review effectiveness was not measured (R-58).", True, True, False)
    c("limit-simulated-authentication", "Sign-in is simulated: a persona picker mints a signed identity; there is no real authentication, session revocation, rate limiting or MFA.", "limitation", "security", "design_documented", "none", ["docs/risks.md", "docs/security.md"],
      "Demo-only by design (R-59); a deployment needs an identity provider.", True, True, True)
    c("limit-reconciliation-and-audit-anchor", "Reconciling an uncertain write as applied trusts the operator's recorded note, and the audit log has no external anchor, so deletion of the most recent events is detectable only with one.", "limitation", "idempotency", "design_documented", "none",
      ["docs/risks.md", "docs/security.md"], "Documented residuals (R-32, R-62).", True, True, False)
    return out


def build_manifest(ev: dict) -> dict:
    cl = claims(ev)
    return {"manifest_version": "1", "repository": REPO,
            "project_status": {"customer": "Meridian Freight Systems is fictional", "data": "all data is synthetic and generated from a seed", "deployment": "reference implementation; never deployed; no production use; no real customers",
                              "model": "the workflow's model is the deterministic stand-in RuleCaseModel, not an LLM", "real_model_evaluation": "executed" if ev["real_model"]["executed"] else "not executed"},
            "evidence": {"file": "reports/m6/release-evidence.json", "code_sha": ev["code_sha"]}, "claims": cl}


LABELS = {"deterministic_tests": "deterministic tests", "mutation_checks": "mutation checks", "frozen_heldout_retrieval_eval": "frozen held-out retrieval eval", "synthetic_dev_retrieval_eval": "synthetic dev retrieval eval",
          "standin_workflow_runs": "stand-in workflow runs", "adversarial_dev_corpus": "adversarial dev corpus", "manual_browser_pass": "manual browser pass", "design_documented": "documented design", "not_executed": "not executed"}
README_ORDER = ["scope-fictional-synthetic", "model-is-standin", "real-model-evaluation-status", "test-suite", "threat-catalogue", "mutation-checks", "invariants-held-in-scenario-runs", "retrieval-vector-beat-hybrid",
                "similarity-is-not-sufficiency", "workflow-standin-scenario-matches", "draft-steering-result"]


def render(manifest: dict, which: str) -> str:
    if which == "interview":                                       # the claims that may be said out loud, each with its limit
        lines = []
        for cl in manifest["claims"]:
            if cl["suitable_for"]["resume"] or cl["suitable_for"]["portfolio"]:
                use = "CV, portfolio" if cl["suitable_for"]["resume"] else "portfolio only"
                lines.append(f"- **{cl['claim']}**  \n  *Limit:* {cl['qualification']} *(use: {use}; evidence: {LABELS[cl['evidence_class']]}; id `{cl['id']}`)*")
        return "\n".join(lines) + f"\n\n*Generated from [`content/public-claims.json`](../content/public-claims.json) at code commit `{manifest['evidence']['code_sha'][:10]}`. Claims marked \"portfolio only\" are not for a CV.*"
    by_id = {c["id"]: c for c in manifest["claims"]}
    chosen = [by_id[i] for i in README_ORDER] if which == "readme" else manifest["claims"]
    assert all(c["suitable_for"]["readme"] for c in chosen) or which != "readme", "a README claim is not marked suitable for the README"
    rows = []
    for cl in chosen:
        tag = {"capability": "design + tests", "measurement": LABELS[cl["evidence_class"]], "limitation": "limitation"}[cl["kind"]]
        rows.append(f"| {cl['claim']} | {tag} | {cl['qualification']} |")
    head = "| Claim | Evidence | Limit |\n|---|---|---|\n"
    return head + "\n".join(rows) + f"\n\n*Generated from [`content/public-claims.json`]({'' if which == 'readme' else '../'}content/public-claims.json) at code commit `{manifest['evidence']['code_sha'][:10]}`. {len(chosen)} of {len(manifest['claims'])} claims shown; the manifest holds source artifacts, commits and per-claim usage.*"


def splice(path: Path, which: str, body: str) -> str:
    text = path.read_text()
    begin, end = f"<!-- claims:{which}:begin -->", f"<!-- claims:{which}:end -->"
    if begin not in text or end not in text:
        raise SystemExit(f"{path.name}: missing {begin} / {end} markers")
    pre, rest = text.split(begin, 1)
    _, post = rest.split(end, 1)
    return f"{pre}{begin}\n{body}\n{end}{post}"


def strip_commits(m: dict) -> dict:
    return {**m, "claims": [{k: v for k, v in c.items() if k != "source_commit"} for c in m["claims"]]}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("build", "check"):
        print(__doc__)
        return 2
    ev = json.loads(EVIDENCE.read_text())
    if argv[0] == "build":
        if ev.get("tree_dirty") or ev.get("tree_dirty_after"):
            raise SystemExit("the evidence was collected on a dirty working tree; commit first, then collect")
        m = build_manifest(ev)
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(m, indent=1, ensure_ascii=False) + "\n")
        for f, which in BLOCKS.items():
            (ROOT / f).write_text(splice(ROOT / f, which, render(m, which)))
        print(f"{len(m['claims'])} claims written for code commit {ev['code_sha'][:10]}")
        return 0
    want, have = build_manifest(ev), json.loads(MANIFEST.read_text())
    bad = []
    if strip_commits(want) != strip_commits(have):
        bad.append("content/public-claims.json differs from what the evidence produces (run: python scripts/public_claims.py build)")
    for f, which in BLOCKS.items():
        if splice(ROOT / f, which, render(have, which)) != (ROOT / f).read_text():
            bad.append(f"{f}: generated block is stale (run: python scripts/public_claims.py build)")
    for b in bad:
        print("DRIFT", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
