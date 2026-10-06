"""Mutation check for the M5 defences (operator application, authorization, amendment, review, recovery, grounding, observability). Breaks each defence on purpose and confirms the suite
notices. Restores every file afterwards. Run under scripts/with_local_pg.py. A surviving mutation is a gap in the tests, not a pass."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IDN = "copilot/control/identity.py"
ACC, WEB, OPR, APR, REC, GRD, MET, RUN, VM = ("copilot/control/access.py", "copilot/app/web.py", "copilot/app/operator.py", "copilot/control/approvals.py", "copilot/workflow/recovery.py",
                                              "copilot/workflow/grounding.py", "copilot/control/metrics.py", "copilot/workflow/runner.py", "copilot/app/viewmodel.py")
M = [
    ("identity: account grants not covered by the signature (a widened grant verifies)", IDN, "|{key_id}|{','.join(accounts)}\".encode()", "|{key_id}|\".encode()"),
    ("identity: covers() grants every account", IDN, "        if \"*\" in self.accounts:\n            return True\n        return account_id is not None and account_id in self.accounts", "        return True"),
    ("access: foreign case readable (grant check removed)", ACC, "        if acc is None or not i.covers(acc):\n            raise AccessDenied()", "        if acc is None:\n            raise AccessDenied()"),
    ("access: audit search not filtered to granted accounts", ACC, "WHERE (%(all)s::boolean OR account_id = ANY(%(accts)s::text[])) AND (%(etype)s", "WHERE (TRUE OR account_id = ANY(%(accts)s::text[])) AND (%(etype)s"),
    ("access: case list not filtered to granted accounts", ACC, "case_runs WHERE (%(all)s::boolean OR", "case_runs WHERE (TRUE OR"),
    ("access: approval queue not filtered to granted accounts", ACC, "AND (%(all)s::boolean OR a.account_id = ANY(%(accts)s::text[])) ORDER BY a.created_at LIMIT 200", "AND (TRUE OR a.account_id = ANY(%(accts)s::text[])) ORDER BY a.created_at LIMIT 200"),
    ("access: agent tokens accepted as operators", ACC, "        if i.kind != \"user\":\n            raise AccessDenied(\"NOT_A_HUMAN_OPERATOR\")", "        if False:\n            raise AccessDenied(\"NOT_A_HUMAN_OPERATOR\")"),
    ("web: CSRF token not checked", WEB, "            if not self._csrf_ok(req, ident):", "            if False:"),
    ("web: CSRF token not bound to the session", WEB, "        return hmac.new(self.csrf_key, ident.sig.encode(), hashlib.sha256).hexdigest()[:40]", "        return hmac.new(self.csrf_key, b\"x\", hashlib.sha256).hexdigest()[:40]"),
    ("web: any identity may open the global dashboard", WEB, "        return ident.role == \"auditor\" and \"*\" in ident.accounts", "        return True"),
    ("web: agent identity accepted in a session cookie", WEB, "        return ident if ident.kind == \"user\" else None", "        return ident"),
    ("web: strict CSP removed", WEB, "r.headers += [(\"Content-Security-Policy\", CSP), ", "r.headers += ["),
    ("web: foreign case answers differently from a missing one (existence oracle)", WEB, "        except AccessDenied:\n            return self.not_found(req, ident)\n        if audit_page:", "        except AccessDenied:\n            return self.render(\"message.html\", req, ident, 403, title=\"Forbidden\", message=\"not yours\")\n        if audit_page:"),
    ("operator: amender may approve their own amendment", OPR, "        if plan and plan.get(\"amended_by\") == i.id:", "        if False:"),
    ("operator: amendment does not void the old approval", OPR, "        voided = self.svc.approvals.void_pending(old_ap[\"approval_id\"], f\"superseded: action amended by {i.id}\", i.actor(), {\"request_id\": request_id}) if old_ap else False", "        voided = False"),
    ("operator: amendment skips policy re-evaluation (new action not proposed)", OPR, "        if res.status != G.AWAITING_APPROVAL:", "        if False:"),
    ("operator: superseded approval can still be decided", OPR, "        if entry[\"status\"] == \"superseded\":", "        if False:"),
    ("operator: amendment allowed for any role", OPR, "        if i.role != \"tier2_engineer\":\n            raise OperatorError(\"WRONG_ROLE\", \"only a Tier-2 engineer can amend a proposed action\")", "        if False:\n            raise OperatorError(\"WRONG_ROLE\", \"\")"),
    ("operator: any field of an action may be amended", OPR, "        bad = sorted(set(changes) - set(editable))", "        bad = []"),
    ("operator: draft marked reviewed without acknowledging every flag", OPR, "        if got != need:", "        if False:"),
    ("operator: reconciliation role not checked", OPR, "        if i.role != ACTIONS[entry[\"type\"]][1]:", "        if False:"),
    ("operator: reconciliation without a recorded note", OPR, "        if not note.strip():", "        if False:"),
    ("approvals: account grant not required to decide", APR, "        if not approver.covers(a.account_id):", "        if False:"),
    ("recovery: leases ignored (claim does not skip locked/leased rows)", REC, "AND (lease_expires_at IS NULL OR lease_expires_at < %(now)s) AND attempts", "AND attempts"),
    ("recovery: parked cases are retried forever", REC, "AND attempts < %(max)s ORDER BY", "ORDER BY"),
    ("grounding: effect claims not verified against executions", GRD, "    for rx, action in _EFFECT:", "    for rx, action in ():"),
    ("grounding: ungrounded numbers allowed", GRD, "    for n in sorted(_numbers(draft) - nums):", "    for n in []:"),
    ("runner: draft review level never elevated", RUN, "\"level\": \"elevated\" if flags else \"standard\"", "\"level\": \"standard\""),
    ("metrics: executions counted from a constant", MET, "\"executions\": \"SELECT body::jsonb->'payload'->>'status', count(*) FROM copilot.audit_events WHERE event_type = 'action_executed' GROUP BY 1 ORDER BY 1\"", "\"executions\": \"SELECT 'SUCCEEDED', 5\""),
    ("viewmodel: a viewer who cannot decide is offered the approve button", VM, "    can = bool(ident and rec.status == \"pending\" and remaining is not None and remaining > 0 and ident.role == rec.required_role", "    can = bool(ident and rec.status == \"pending\" and remaining is not None and remaining > 0"),
]
TESTS = ["tests/db/test_m5_app_security.py", "tests/db/test_m5_app_flows.py", "tests/db/test_m5_access.py", "tests/db/test_m5_recovery.py", "tests/db/test_m5_ops.py", "tests/test_draft_grounding.py"]
missing = [name for name, rel, old, _ in M if old not in (ROOT / rel).read_text()]
assert not missing, f"mutation targets not found: {missing}"
survived = []
for name, rel, old, new in M:
    p = ROOT / rel
    src = p.read_text()
    assert old in src, f"mutation target not found for: {name}"
    p.write_text(src.replace(old, new, 1))
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TESTS], cwd=ROOT, capture_output=True, text=True)  # noqa: S603
    finally:
        p.write_text(src)
    killed = r.returncode != 0
    print(f"{'KILLED' if killed else 'SURVIVED':9} {name}", flush=True)
    if not killed:
        survived.append(name)
print(f"\n{len(M) - len(survived)}/{len(M)} mutations killed")
sys.exit(1 if survived else 0)
