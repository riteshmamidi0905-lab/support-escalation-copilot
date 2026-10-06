"""Release-integrity checks: the public claims manifest is the contract with anything that quotes this repository, so it must be structurally honest. These are STRUCTURAL checks (schemas, provenance,
evidence freshness, derivation from runs, usage policy); the few phrase checks are negation-aware and limited to the most dangerous overclaims."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from copilot.control.actions import ACTIONS, FORBIDDEN
from copilot.invariants import ATTACKS

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "content" / "public-claims.json"
EVIDENCE = ROOT / "reports" / "m6" / "release-evidence.json"
# Code the evidence describes: the application, its contracts, the dataset, the tests, the dependency pins and the scripts that produce scenario, mutation and isolation results. Changing any of it after the
# evidence run makes the evidence stale. Documentation, the claims/evidence tooling, the demo scripts and CI configuration may change: they do not alter what was measured.
PROTECTED = ("copilot/", "contracts/", "tests/", "data/", "pyproject.toml", "scripts/mutation_check_", "scripts/run_m3_scenarios.py", "scripts/run_m4_", "scripts/with_local_pg.py", "scripts/bench_env.py")
RESUME_OK = {"deterministic_tests", "mutation_checks", "design_documented"}


def git(*a, check=True):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, check=check).stdout.strip()      # noqa: S603, S607


def shallow() -> bool:
    return git("rev-parse", "--is-shallow-repository") == "true"


@pytest.fixture(scope="module")
def manifest():
    assert MANIFEST.exists(), "content/public-claims.json is missing (python scripts/public_claims.py build)"
    return json.loads(MANIFEST.read_text())


@pytest.fixture(scope="module")
def evidence():
    assert EVIDENCE.exists(), "reports/m6/release-evidence.json is missing (python scripts/with_local_pg.py python scripts/collect_release_evidence.py)"
    return json.loads(EVIDENCE.read_text())


def test_manifest_validates_against_its_schema(manifest):
    schema = json.loads((ROOT / "contracts" / "public_claims.schema.json").read_text())
    errs = sorted(Draft202012Validator(schema).iter_errors(manifest), key=lambda e: list(e.path))
    assert not errs, [f"{list(e.path)}: {e.message}" for e in errs[:5]]


def test_every_claim_names_real_artifacts_and_a_commit_in_history(manifest):
    ids = [c["id"] for c in manifest["claims"]]
    assert len(ids) == len(set(ids))
    for c in manifest["claims"]:
        for a in c["source_artifacts"]:
            assert (ROOT / a).exists(), f"{c['id']}: source artifact {a} does not exist"
        if not shallow():
            assert subprocess.run(["git", "merge-base", "--is-ancestor", c["source_commit"], "HEAD"], cwd=ROOT).returncode == 0, f"{c['id']}: source commit is not an ancestor of HEAD"      # noqa: S603, S607


def test_usage_policy_no_stand_in_or_development_result_is_offered_for_a_resume(manifest):
    for c in manifest["claims"]:
        if c["suitable_for"]["resume"]:
            assert c["evidence_class"] in RESUME_OK, f"{c['id']}: {c['evidence_class']} evidence may not be quoted on a CV"
        if c["evidence_class"] == "standin_workflow_runs":
            assert c["model"] == "deterministic_stand_in", c["id"]
        if c["evidence_class"] == "not_executed":
            assert c["kind"] == "limitation", c["id"]
        assert c["qualification"].strip(), c["id"]


def test_the_stand_in_scenario_match_rate_is_never_offered_as_a_portfolio_or_cv_claim(manifest):
    c = next(c for c in manifest["claims"] if c["id"] == "workflow-standin-scenario-matches")
    assert c["suitable_for"] == {"readme": True, "portfolio": False, "resume": False} and "NOT a model-accuracy figure" in c["qualification"]


def test_a_real_llm_claim_is_impossible_without_a_recorded_real_model_result(manifest):
    results = [p for p in (ROOT / "reports").rglob("real-model-results*.json") if json.loads(p.read_text()).get("executed") is True]
    llm_claims = [c["id"] for c in manifest["claims"] if c["model"] == "real_llm"]
    freeze = json.loads((ROOT / "docs" / "real-model-freeze.json").read_text())
    if not results:
        assert not llm_claims and manifest["project_status"]["real_model_evaluation"] == "not executed" and freeze["executed"] is False
    else:
        assert manifest["project_status"]["real_model_evaluation"] == "executed"


def test_status_declarations_are_present_and_the_deployment_is_never_claimed(manifest):
    s = manifest["project_status"]
    assert "fictional" in s["customer"] and "synthetic" in s["data"] and "never deployed" in s["deployment"] and "not an LLM" in s["model"]
    kinds = {c["id"]: c for c in manifest["claims"]}
    for needed in ("scope-fictional-synthetic", "scope-reference-implementation", "model-is-standin", "real-model-evaluation-status", "limit-simulated-authentication"):
        assert needed in kinds and kinds[needed]["kind"] == "limitation", needed


# ---- evidence freshness: the numbers describe THIS code -----------------------------------------------------------------------------------------------------
def test_evidence_was_collected_on_a_clean_tree_and_is_green(evidence):
    assert evidence["tree_dirty"] is False and evidence["tree_dirty_after"] is False, "evidence collected with uncommitted changes"
    pt = evidence["pytest"]
    assert pt["failed"] == 0 and pt["skipped"] == 0 and pt["exit_code"] == 0 and pt["passed"] + pt["xfailed"] == pt["collected"]
    assert all(m["survived"] == [] and m["killed"] == m["total"] for m in evidence["mutation_checks"].values())
    assert evidence["m4_injection"]["invariant_violations"] == 0 and evidence["m3_scenarios"]["as_expected"] == evidence["m3_scenarios"]["total"]
    assert all(evidence["m4_scenarios"]["invariants"].values()) and all(evidence["m3_scenarios"]["invariants"].values())


def test_no_code_changed_since_the_evidence_commit(evidence):
    if shallow():
        pytest.skip("shallow clone: evidence freshness needs full history (CI checks out with fetch-depth: 0)")
    sha = evidence["code_sha"]
    assert subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=ROOT).returncode == 0, "evidence commit is not an ancestor of HEAD"      # noqa: S603, S607
    changed = [f for f in git("diff", "--name-only", sha, "HEAD").splitlines() if f.startswith(PROTECTED)]
    assert not changed, f"code/tests changed after the evidence was collected ({changed[:5]}): collect the evidence again"


def test_catalogue_numbers_in_the_evidence_are_the_current_catalogue(evidence):
    cat = evidence["attack_catalogue"]
    assert cat["total"] == len(ATTACKS) == cat["executable"] == sum(a.status == "implemented" for a in ATTACKS)
    assert evidence["draft_steering"] == {k: {"n": v["n"], "flagged": v["flagged"]} for k, v in json.loads((ROOT / "reports/m5/draft-steering.json").read_text())["summary"].items()}


def test_manifest_and_generated_blocks_are_exactly_what_the_evidence_produces():
    r = subprocess.run([sys.executable, "scripts/public_claims.py", "check"], cwd=ROOT, capture_output=True, text=True)       # noqa: S603
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_readme_diagrams_are_the_architecture_diagrams():
    """The README embeds diagrams from docs/architecture.md; a silent divergence would make the front page describe a different system."""
    fence = re.compile(r"```mermaid\n(.*?)```", re.S)
    arch = {b.strip() for b in fence.findall((ROOT / "docs" / "architecture.md").read_text())}
    readme = [b.strip() for b in fence.findall((ROOT / "README.md").read_text())]
    assert readme and set(readme) <= arch


# ---- structural 'no email capability' ------------------------------------------------------------------------------------------------------------------------
def test_no_action_type_can_send_anything_to_a_customer():
    assert not [a for a in ACTIONS if re.search(r"mail|send|notify|message|sms", a, re.I)], "an action type that could transmit text to a customer exists"
    assert {"send_customer_email", "send_email", "notify_customer"} <= set(FORBIDDEN)
    schema = json.loads((ROOT / "contracts" / "action.schema.json").read_text())
    declared = [b["properties"]["type"] for b in schema["oneOf"]]                            # every branch pins its action type with a const/enum
    names = {v for t in declared for v in ([t["const"]] if "const" in t else t.get("enum", []))}
    assert names and not [n for n in names if re.search(r"mail|send|notify|message|sms", n, re.I)], names


# ---- the few phrase checks: negation-aware, only the most dangerous overclaims ---------------------------------------------------------------------------------------
OVERCLAIMS = [r"production[- ]ready", r"deployed (to|in) production", r"in production use", r"real customers?\b(?![- ]systems?)", r"customer data", r"LLM accuracy", r"prompt injection (is )?solved", r"(100%|fully|completely) secure", r"battle[- ]tested"]
QUALIFIERS = re.compile(r"\?|\b(no|not|never|nor|without|none|fictional|synthetic|simulated|cannot|can't|isn't|aren't|would|before|until|if|unless|what a real|requires?|need(s|ed)?|neither|nothing)\b", re.I)
PUBLIC_FILES = ["README.md", "docs/security.md", "docs/interview-guide.md", "docs/evaluation.md", "docs/getting-started.md", "docs/demo-walkthrough.md", "docs/engineering-lessons.md", "docs/architecture.md", "content/public-claims.json"]


def find_overclaims(text: str) -> list[str]:
    return [sent.strip()[:140] for sent in re.split(r"(?<=[.!?])\s+|\n", text) if any(re.search(pat, sent, re.I) for pat in OVERCLAIMS) and not QUALIFIERS.search(sent)]


def test_the_overclaim_check_fires_on_a_positive_control_and_spares_negations():
    assert find_overclaims("This system is production-ready and battle-tested.")
    assert find_overclaims("We evaluated LLM accuracy on real customers.")
    assert find_overclaims("Prompt injection is solved.")
    assert not find_overclaims("It is not production-ready.") and not find_overclaims("No real customers or customer data were used.")
    assert not find_overclaims("What a real deployment would have to change is listed below.")


def test_dangerous_overclaims_appear_only_in_negated_or_qualified_sentences():
    bad = []
    for f in PUBLIC_FILES:
        p = ROOT / f
        assert p.exists(), f"{f} is missing"
        bad += [f"{f}: {s}" for s in find_overclaims(p.read_text())]
    assert not bad, bad
