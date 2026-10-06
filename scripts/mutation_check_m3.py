"""Mutation check for the M3 control plane: break each defence on purpose and confirm the test-suite notices. Restores every file afterwards.
Includes the two 'degenerate' mutations that a lazy implementation would pass security with: blanket DENY (must fail the positive controls) and blanket ALLOW.
Run under scripts/with_local_pg.py."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POL, APR, GW, LED, ACT, FAC, AUD = (f"copilot/control/{n}.py" for n in ("policy", "approvals", "gateway", "ledger", "actions", "facts", "audit"))
M = [
    ("blanket DENY policy (positive controls must fail)", POL, "def decide(action: ValidatedAction, facts: Facts) -> PolicyDecision:\n    p = action.params", "def decide(action: ValidatedAction, facts: Facts) -> PolicyDecision:\n    return _d(Decision.DENY, ['X'], Sufficiency.INSUFFICIENT)\n    p = action.params"),
    ("blanket ALLOW approval: check_for_execution accepts anything", APR, '        if not approval_id:\n            raise ApprovalError("APPROVAL_REQUIRED")', '        if True:\n            return None'),
    ("gated writes skip the approval check", GW, "        if action.tier is Tier.GATED_WRITE:\n            try:", "        if False:\n            try:"),
    ("approver role not checked", APR, "        if approver.role != a.required_role:", "        if False:"),
    ("expiry not checked at execution", APR, '        if a.status == "expired" or self.clock.now() > a.expires_at:', '        if a.status == "expired":'),
    ("action hash not compared (changed-action replay)", APR, "        if not hmac.compare_digest(a.action_hash, h):", "        if False:"),
    ("tenant not checked on the approval", APR, "        if a.account_id != account_id:", "        if False:"),
    ("case not checked on the approval", APR, "        if a.case_id != action.case_id:", "        if False:"),
    ("self-approval allowed", APR, "        if approver.id == a.requester_id:", "        if False:"),
    ("agents may approve", APR, '        if approver.kind != "user":', "        if False:"),
    ("denied approvals still authorise", APR, '        if a.status == "denied":', "        if False:"),
    ("pending approvals still authorise", APR, '        if a.status == "pending":', "        if False and a.status == 'pending':"),
    ("idempotency payload conflict not enforced", LED, "            if (ex[\"action_hash\"], ex[\"case_id\"], ex[\"action_type\"]) != (action_hash, case_id, action_type):", "            if False:"),
    ("gateway peek ignores a reused key with another payload", GW, "if prior is not None and (prior[\"action_hash\"], prior[\"case_id\"], prior[\"action_type\"]) != (h, action.case_id, action.type):", "if False:"),
    ("idempotency ledger bypassed (always claims)", GW, "        state, rec = self.ledger.begin(", "        state, rec = L.CLAIMED, {}\n        _ = self.ledger.begin("),
    ("uncertain outcome retried blindly", GW, '                if kind == "unsupported":\n                    self._ev(', '                if False:\n                    self._ev('),
    ("credit threshold rule removed", POL, 'if c is not None and p["percent"] > c["max_agent_requestable_pct"]:', "if False:"),
    ("SLA breach evidence ignored", POL, "if facts.sla_breach_evidenced is False:", "if False:"),
    ("resync cooldown removed", POL, "if last is not None and facts.now - last < timedelta(hours=CONFIG[\"resync_cooldown_hours\"]):", "if False:"),
    ("open-incident rule removed", POL, "        if blocking:", "        if False:"),
    ("integration ownership not checked", POL, "        if integ is None:", "        if False:"),
    ("conflict escalation removed", POL, "        if conflicting:\n            return _d(Decision.ESCALATE, [\"CONFLICTING_EVIDENCE_NEEDS_HUMAN\"], Sufficiency.CONFLICTING)\n        missing = tuple(m for m, ok in ((\"integration.status\"", "        if False:\n            return _d(Decision.ESCALATE, [\"CONFLICTING_EVIDENCE_NEEDS_HUMAN\"], Sufficiency.CONFLICTING)\n        missing = tuple(m for m, ok in ((\"integration.status\""),
    ("case/scope mismatch not blocked", GW, "        if action.case_id != scope.case_id:", "        if False:"),
    ("forbidden registry emptied", ACT, "    if t in FORBIDDEN:\n        raise ActionRejected(\"FORBIDDEN_ACTION\", t)", "    if False:\n        raise ActionRejected(\"FORBIDDEN_ACTION\", t)"),
    ("secret check in parameters disabled", ACT, "    if _has_secret(raw[\"params\"]):", "    if False:"),
    ("customer-system responses not scrubbed", GW, "res = scrub(system.call(key, payload), 500)", "res = system.call(key, payload)"),
    ("audit chain does not commit to its predecessor", AUD, '            body["prev_hash"] = row[0] if row else None', '            body["prev_hash"] = None'),
    ("audit allows hidden reasoning keys", AUD, "            if bad:\n                raise AuditError", "            if False:\n                raise AuditError"),
    ("identity signature not verified", "copilot/control/identity.py", "        if not hmac.compare_digest(self._sig(ident.kind, ident.id, ident.role, ident.exp, ident.key_id), ident.sig):", "        if False:"),
    ("identity expiry not checked", "copilot/control/identity.py", "        if ident.exp < int(self._clock()):", "        if False:"),
    ("policy re-evaluation at execution skipped", GW, "        d = self._decide(action, scope, knowledge)\n        self._ev(\"policy_decided\", action.case_id, acc, {\"kind\": \"system\", \"id\": \"policy-engine\", \"role\": None}, corr, {\"decision\": d.decision.value, \"reasons\": list(d.reasons), \"sufficiency\": d.sufficiency.value,\n                 \"requires_approval\": d.requires_approval, \"required_role\": d.required_role, \"policy_version\": d.policy_version})\n        if d.decision not in", "        d = decide(action, __import__('copilot.control.policy', fromlist=['Facts']).Facts(action.case_id, acc, 'open', None, {}, (), True, self.clock.now())) if False else self._decide(action, scope, None)\n        d = type(d)(Decision.REQUIRE_APPROVAL if action.tier is Tier.GATED_WRITE else Decision.ALLOW_PROPOSAL, (), d.sufficiency, required_role=action.required_role)\n        if d.decision not in"),
]
survived = []
for name, rel, old, new in M:
    p = ROOT / rel
    src = p.read_text()
    assert old in src, f"mutation target not found for: {name}"
    p.write_text(src.replace(old, new, 1))
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "tests/test_control_units.py", "tests/db/test_m3_flows.py", "tests/db/test_m3_attacks.py"], cwd=ROOT, capture_output=True, text=True)  # noqa: S603
    finally:
        p.write_text(src)
    killed = r.returncode != 0
    print(f"{'KILLED' if killed else 'SURVIVED':9} {name}")
    if not killed:
        survived.append(name)
print(f"\n{len(M) - len(survived)}/{len(M)} mutations killed")
sys.exit(1 if survived else 0)
