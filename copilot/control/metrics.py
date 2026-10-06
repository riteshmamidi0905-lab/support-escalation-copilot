"""Metrics DERIVED from the application's own tables (case_runs, case_transitions, approvals, idempotency_records, audit_events, ops_events). There is no hard-coded number anywhere: an
empty system reports zeros, and every figure changes only when the underlying events do (tests prove it). Read-only SQL, fixed statements, bound parameters."""
from __future__ import annotations

from typing import Any

from .clock import SystemClock

TERMINAL = ("CLOSED", "REFUSED", "ABSTAINED", "ESCALATED", "HANDED_OFF", "FAILED")

_Q = {
    "cases_by_state": "SELECT state, count(*) FROM copilot.case_runs GROUP BY 1 ORDER BY 1",
    "cases_by_outcome": "SELECT coalesce(outcome, '(none yet)'), count(*) FROM copilot.case_runs GROUP BY 1 ORDER BY 1",
    "state_durations": ("SELECT from_state, count(*), avg(extract(epoch FROM (nxt - cur))), max(extract(epoch FROM (nxt - cur))) FROM ("
                        "SELECT t.to_state AS from_state, t.ts::timestamptz AS cur, lead(t.ts::timestamptz) OVER (PARTITION BY t.case_id ORDER BY t.version) AS nxt FROM copilot.case_transitions t) x "
                        "WHERE nxt IS NOT NULL GROUP BY 1 ORDER BY 1"),
    "open_case_age": ("SELECT r.state, count(*), greatest(max(extract(epoch FROM (%(now)s - r.updated_at))), 0) FROM copilot.case_runs r WHERE r.state NOT IN ('CLOSED','REFUSED','ABSTAINED','ESCALATED','HANDED_OFF','FAILED') GROUP BY 1 ORDER BY 1"),
    "approval_queue": "SELECT count(*), greatest(coalesce(max(extract(epoch FROM (%(now)s - created_at))), 0), 0) FROM copilot.approvals WHERE status = 'pending'",
    "approval_status": "SELECT CASE WHEN status = 'expired' AND starts_with(decision_reason, 'superseded:') THEN 'superseded' ELSE status END, count(*) FROM copilot.approvals GROUP BY 1 ORDER BY 1",
    "approval_latency": "SELECT count(*), greatest(coalesce(avg(extract(epoch FROM (decided_at - created_at))), 0), 0) FROM copilot.approvals WHERE status IN ('approved','denied')",
    "policy": "SELECT body::jsonb->'payload'->>'decision', count(*) FROM copilot.audit_events WHERE event_type = 'policy_decided' GROUP BY 1 ORDER BY 1",
    "audit_types": "SELECT event_type, count(*) FROM copilot.audit_events WHERE event_type IN ('abstained','escalated','action_refused','forbidden_action_attempted','idempotent_replay','idempotency_conflict_blocked','approval_expired','execution_uncertain','draft_rejected','model_output_rejected') GROUP BY 1 ORDER BY 1",
    "executions": "SELECT body::jsonb->'payload'->>'status', count(*) FROM copilot.audit_events WHERE event_type = 'action_executed' GROUP BY 1 ORDER BY 1",
    "ledger": "SELECT status, count(*) FROM copilot.idempotency_records GROUP BY 1 ORDER BY 1",
    "dependencies": ("SELECT attrs->>'api', count(*), count(*) FILTER (WHERE (attrs->>'ok')::boolean IS FALSE), coalesce(sum(greatest((attrs->>'attempts')::int - 1, 0)), 0) FROM copilot.ops_events WHERE kind = 'dependency_call' GROUP BY 1 ORDER BY 1"),
    "dependency_errors": "SELECT attrs->>'api', attrs->>'code', count(*) FROM copilot.ops_events WHERE kind = 'dependency_call' AND (attrs->>'ok')::boolean IS FALSE GROUP BY 1, 2 ORDER BY 1, 2",
    "breaker_last": "SELECT DISTINCT ON (attrs->>'api') attrs->>'api', attrs->>'state', ts FROM copilot.ops_events WHERE kind = 'breaker_state' ORDER BY attrs->>'api', seq DESC",
    "breaker_changes": "SELECT attrs->>'api', attrs->>'state', count(*) FROM copilot.ops_events WHERE kind = 'breaker_state' GROUP BY 1, 2 ORDER BY 1, 2",
    "retrieval": "SELECT coalesce(attrs->>'strategy', 'none'), count(*), count(*) FILTER (WHERE attrs->>'fallback' IS NOT NULL) FROM copilot.ops_events WHERE kind = 'retrieval' GROUP BY 1 ORDER BY 1",
    "model_calls": "SELECT count(*), coalesce(avg(duration_ms), 0), coalesce(sum((attrs->>'prompt_tokens')::int), 0), coalesce(sum((attrs->>'completion_tokens')::int), 0) FROM copilot.ops_events WHERE kind = 'model_call'",
    "model_failures": "SELECT attrs->>'error_kind', count(*) FROM copilot.ops_events WHERE kind = 'model_failure' GROUP BY 1 ORDER BY 1",
    "model_stage_errors": "SELECT attrs->>'stage', attrs->>'error', count(*) FROM copilot.ops_events WHERE kind = 'model_stage' AND attrs->>'error' IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2",
    "model_retries": "SELECT count(*) FROM copilot.ops_events WHERE kind = 'model_retry'",
    "recovery": "SELECT kind, count(*) FROM copilot.ops_events WHERE kind IN ('recovery_claim','recovery_run','recovery_error') GROUP BY 1 ORDER BY 1",
    "stuck": "SELECT count(*) FROM copilot.case_runs WHERE attempts >= %(max)s AND state NOT IN ('CLOSED','REFUSED','ABSTAINED','ESCALATED','HANDED_OFF','FAILED')",
    "events_total": "SELECT (SELECT count(*) FROM copilot.audit_events), (SELECT count(*) FROM copilot.ops_events), (SELECT count(*) FROM copilot.case_runs)",
}


class Metrics:
    def __init__(self, pool, clock=None, max_recovery_attempts: int = 5):
        self.pool, self.clock, self.max_attempts = pool, clock or SystemClock(), max_recovery_attempts

    def _all(self, name: str, **params: Any) -> list[tuple]:
        with self.pool.connection() as c:
            r = c.execute(_Q[name], params).fetchall()
            c.rollback()
        return [tuple(x) for x in r]

    def snapshot(self) -> dict[str, Any]:
        now = self.clock.now()
        a, o, c = self._all("events_total")[0]
        q = self._all("approval_queue", now=now)[0]
        lat = self._all("approval_latency")[0]
        mc = self._all("model_calls")[0]
        pol = dict(self._all("policy"))
        return {
            "generated_at": now.isoformat(),
            "totals": {"audit_events": a, "ops_events": o, "cases": c},
            "cases_by_state": dict(self._all("cases_by_state")),
            "cases_by_outcome": dict(self._all("cases_by_outcome")),
            "state_durations_s": {s: {"transitions": n, "avg": round(float(avg), 3), "max": round(float(mx), 3)} for s, n, avg, mx in self._all("state_durations")},
            "open_cases": {s: {"count": n, "oldest_age_s": round(float(age), 1)} for s, n, age in self._all("open_case_age", now=now)},
            "approvals": {"pending": q[0], "oldest_pending_age_s": round(float(q[1]), 1), "by_status": dict(self._all("approval_status")), "decided": lat[0], "avg_decision_latency_s": round(float(lat[1]), 1)},
            "policy_outcomes": pol,
            "control_events": dict(self._all("audit_types")),
            "executions": dict(self._all("executions")),
            "ledger": dict(self._all("ledger")),
            "uncertain_outcomes": dict(self._all("ledger")).get("uncertain", 0),
            "dependencies": {api: {"calls": n, "failed": f, "retries": r} for api, n, f, r in self._all("dependencies")},
            "dependency_errors": [{"api": a, "code": c, "count": n} for a, c, n in self._all("dependency_errors")],
            "circuit_breakers": {api: {"state": st, "since": str(ts)} for api, st, ts in self._all("breaker_last")},
            "breaker_transitions": [{"api": a, "state": s, "count": n} for a, s, n in self._all("breaker_changes")],
            "retrieval": {s: {"runs": n, "fallbacks": f} for s, n, f in self._all("retrieval")},
            "model": {"calls": mc[0], "avg_latency_ms": round(float(mc[1]), 2), "prompt_tokens": mc[2], "completion_tokens": mc[3], "retries": self._all("model_retries")[0][0],
                      "failures_by_kind": dict(self._all("model_failures")), "stage_errors": [{"stage": s, "error": e, "count": n} for s, e, n in self._all("model_stage_errors")]},
            "recovery": dict(self._all("recovery")),
            "stuck_cases": self._all("stuck", max=self.max_attempts)[0][0],
        }

    def prometheus(self) -> str:
        s, out = self.snapshot(), []

        def g(name, val, **labels):
            lab = ",".join(f'{k}="{v}"' for k, v in labels.items())
            out.append(f"{name}{{{lab}}} {val}" if lab else f"{name} {val}")
        for k, v in s["cases_by_state"].items():
            g("scec_cases", v, state=k)
        for k, v in s["policy_outcomes"].items():
            g("scec_policy_decisions_total", v, decision=k)
        for k, v in s["executions"].items():
            g("scec_executions_total", v, status=k)
        g("scec_approvals_pending", s["approvals"]["pending"])
        g("scec_approval_oldest_pending_age_seconds", s["approvals"]["oldest_pending_age_s"])
        g("scec_uncertain_outcomes", s["uncertain_outcomes"])
        g("scec_stuck_cases", s["stuck_cases"])
        for api, d in s["dependencies"].items():
            g("scec_dependency_calls_total", d["calls"], api=api)
            g("scec_dependency_failures_total", d["failed"], api=api)
        for api, d in s["circuit_breakers"].items():
            g("scec_circuit_breaker_open", 1 if d["state"] == "open" else 0, api=api)
        g("scec_model_calls_total", s["model"]["calls"])
        for k, v in s["model"]["failures_by_kind"].items():
            g("scec_model_failures_total", v, kind=k)
        return "\n".join(out) + "\n"
