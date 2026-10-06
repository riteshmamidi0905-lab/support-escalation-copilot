"""Mutation check for the M4 workflow defences. Breaks each defence on purpose and confirms the test-suite notices. Restores every file afterwards. Run under scripts/with_local_pg.py."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAC, TRU, RUN, EXT, PRV, RED = (f"copilot/workflow/{n}.py" for n in ("machine", "trust", "runner", "externals", "providers", "model_io"))
M = [
    ("machine: any edge allowed (no edge check)", MAC, "        if tr is None:\n            raise TransitionRefused(\"ILLEGAL_TRANSITION\"", "        if False:\n            raise TransitionRefused(\"ILLEGAL_TRANSITION\""),
    ("machine: stale/replayed expected state accepted", MAC, "            if row[\"state\"] != expected_state:", "            if False:"),
    ("machine: transition guards skipped", MAC, "            why = tr.guard(row[\"file\"], inputs)", "            why = None"),
    ("machine: audit event outside the transition transaction", MAC, "{\"from\": expected_state, \"to\": dst, \"version\": row[\"version\"] + 1, \"inputs_sha256\": digest, \"outcome\": inputs.get(\"outcome\"),", "{\"from\": expected_state, \"to\": dst, \"version\": row[\"version\"] + 1, \"inputs_sha256\": 'x' * 64, \"outcome\": inputs.get(\"outcome\"),"),
    ("trust: forbidden action types accepted into the plan", TRU, "        if t in A.FORBIDDEN:\n            plan.rejected.append(Rejected(t, \"FORBIDDEN_ACTION\"))\n            continue", "        if False:\n            continue"),
    ("trust: unknown evidence handles accepted", TRU, "    return [f\"unknown evidence handle {h!r}\" for h in handles if h not in known]", "    return []"),
    ("trust: draft secret/e-mail check removed", TRU, "    if redact(d[\"draft\"]) != d[\"draft\"]:", "    if False:"),
    ("trust: uncitable (not-applicable/injected) evidence may be cited", TRU, "    bad = sorted(set(d[\"cited_evidence\"]) & set(uncitable))", "    bad = []"),
    ("trust: idempotency key random (not deterministic)", TRU, "    return \"idem-\" + hashlib.sha256(canonical_json({\"case\": case_id, \"type\": action_type, \"params\": params})).hexdigest()[:32]", "    return \"idem-\" + __import__('secrets').token_hex(16)"),
    ("runner: model caution ignored (refuse/abstain/clarify still plans)", RUN, "        caution = diag[\"disposition\"] in (\"refuse\", \"abstain\", \"clarify\")", "        caution = False"),
    ("runner: unverified plan still submitted for approval", RUN, "        degraded = ver[\"status\"] == \"unverified\"", "        degraded = False"),
    ("runner: approvals treated as approved without the record", RUN, "            st = {\"pending\": \"awaiting_approval\", \"approved\": \"approved\", \"denied\": \"denied\", \"expired\": \"expired\"}[rec.status]", "            st = \"approved\""),
    ("runner: ticket not redacted before the model", RUN, "        r = mask_pii(redact(t))", "        r = t"),
    ("runner: draft candidate not persisted before the write", RUN, "        self.d.machine.patch_file(cid, \"draft_candidate\", out.data)", "        pass"),
    ("runner: conflict members need not be disclosed", RUN, "            required = {e[\"handle\"] for e in f[\"retrieval\"][\"evidence\"] if e[\"conflicts_with\"]", "            required = set() and {e[\"handle\"] for e in f[\"retrieval\"][\"evidence\"] if e[\"conflicts_with\"]"),
    ("runner: retrieval failure not degraded", RUN, "        if f[\"retrieval\"][\"outcome\"] == \"FAILED\":", "        if False:"),
    ("runner: content-level sufficiency ignored (always ANSWER)", RUN, "        return \"ANSWER\" if ok else \"INSUFFICIENT_EVIDENCE\"", "        return \"ANSWER\""),
    ("externals: breaker never opens", EXT, "        if self._consecutive.get(api.name, 0) >= self.breaker_after:\n            t0", "        if False:\n            t0"),
    ("externals: tenant not checked by the Status API", EXT, "        if i is None or i[\"account_id\"] != account_id:\n            raise NotFound(integration_id)\n        return {\"integration_id\": integration_id, \"status\"", "        if i is None:\n            raise NotFound(integration_id)\n        return {\"integration_id\": integration_id, \"status\""),
    ("model_io: schema repair disabled", RED, "data, usage, r = retry_call(lambda m=msgs: generate_structured(self.provider, m, schema, max_repairs=2), attempts=3, sleep=self.sleep)", "data, usage, r = retry_call(lambda m=msgs: generate_structured(self.provider, m, schema, max_repairs=0), attempts=3, sleep=self.sleep)"),
]
survived = []
for name, rel, old, new in M:
    p = ROOT / rel
    src = p.read_text()
    assert old in src, f"mutation target not found for: {name}"
    p.write_text(src.replace(old, new, 1))
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "tests/test_workflow_units.py", "tests/db/test_m4_machine.py", "tests/db/test_m4_workflow.py", "tests/db/test_m4_faults.py",
                            "tests/db/test_m4_recovery.py", "tests/db/test_m4_injection.py"], cwd=ROOT, capture_output=True, text=True)  # noqa: S603
    finally:
        p.write_text(src)
    killed = r.returncode != 0
    print(f"{'KILLED' if killed else 'SURVIVED':9} {name}", flush=True)
    if not killed:
        survived.append(name)
print(f"\n{len(M) - len(survived)}/{len(M)} mutations killed")
sys.exit(1 if survived else 0)
