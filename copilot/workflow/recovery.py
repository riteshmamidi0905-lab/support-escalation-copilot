"""Durable recovery: find cases that can make progress and advance them, safely, from any number of worker processes. PostgreSQL is the only coordinator.

Mechanism (the smallest that is justified): a lease on the case row, claimed with `FOR UPDATE SKIP LOCKED`. A worker claims a few runnable cases (non-terminal, due, lease free or expired), runs
the SAME idempotent `CaseRunner.run` that a person would call, then clears the lease. The lease exists to avoid wasted collisions; it is NOT what prevents duplicate effects. Safety comes from the
layers below it, which hold even if a lease is ignored, expires mid-run or two workers run the same case: transitions are versioned (a second mover gets STALE_STATE), every stage persists output
before it advances and is idempotent, and every customer write goes through the M3 idempotency ledger (uncertain outcomes are reconciled, never blindly retried).

A case waiting for a human is revisited on a back-off (cheap) and woken immediately when a decision is made. A case that keeps raising is parked after `max_attempts` and surfaced as 'stuck'
in the metrics: it is never force-failed (that could hide a write whose outcome is uncertain)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from copilot.control.clock import SystemClock
from copilot.control.ops import OpsRecorder
from copilot.redact import scrub

from .machine import TransitionRefused
from .states import TERMINAL

log = logging.getLogger("copilot.recovery")
_TERMINAL = sorted(TERMINAL)
_SQL_CLAIM = ("WITH c AS (SELECT case_id FROM copilot.case_runs WHERE NOT (state = ANY(%(terminal)s)) AND (next_attempt_at IS NULL OR next_attempt_at <= %(now)s) "
              "AND (lease_expires_at IS NULL OR lease_expires_at < %(now)s) AND attempts < %(max)s ORDER BY next_attempt_at NULLS FIRST, updated_at LIMIT %(n)s FOR UPDATE SKIP LOCKED) "
              "UPDATE copilot.case_runs r SET lease_owner = %(w)s, lease_expires_at = %(now)s + make_interval(secs => %(lease)s) FROM c WHERE r.case_id = c.case_id RETURNING r.case_id, r.state, r.account_id")
_SQL_DONE = "UPDATE copilot.case_runs SET lease_owner = NULL, lease_expires_at = NULL, next_attempt_at = %(next)s, attempts = %(attempts)s, last_error = %(err)s WHERE case_id = %(cid)s AND lease_owner = %(w)s"
_SQL_WAKE = "UPDATE copilot.case_runs SET next_attempt_at = NULL WHERE case_id = %s AND NOT (state = ANY(%s))"
_SQL_ATTEMPTS = "SELECT attempts FROM copilot.case_runs WHERE case_id = %s"


@dataclass
class TickResult:
    claimed: list[str] = field(default_factory=list)
    advanced: dict[str, str] = field(default_factory=dict)         # case -> state after the run
    errors: dict[str, str] = field(default_factory=dict)
    benign_races: list[str] = field(default_factory=list)


class RecoveryWorker:
    def __init__(self, pool, runner_factory, clock=None, worker_id: str = "worker-1", ops: OpsRecorder | None = None, lease_s: int = 120, batch: int = 5, max_attempts: int = 5,
                 wait_backoff_s: int = 30, error_backoff_s: int = 10):
        self.pool, self.runner_factory, self.clock, self.worker_id, self.ops = pool, runner_factory, clock or SystemClock(), worker_id, ops
        self.lease_s, self.batch, self.max_attempts, self.wait_backoff_s, self.error_backoff_s = lease_s, batch, max_attempts, wait_backoff_s, error_backoff_s
        self.n = 0

    def claim(self) -> list[tuple[str, str, str]]:
        with self.pool.connection() as c, c.transaction():
            rows = c.execute(_SQL_CLAIM, {"terminal": _TERMINAL, "now": self.clock.now(), "max": self.max_attempts, "n": self.batch, "w": self.worker_id, "lease": self.lease_s}).fetchall()
        return [tuple(r) for r in rows]

    def wake(self, case_id: str) -> None:
        """A human decision (or any external event) makes the case runnable now."""
        with self.pool.connection() as c, c.transaction():
            c.execute(_SQL_WAKE, (case_id, _TERMINAL))

    def tick(self) -> TickResult:
        out = TickResult()
        for case_id, state, account in self.claim():
            out.claimed.append(case_id)
            self.n += 1
            req = f"REQ-{self.worker_id}-{self.n}"
            if self.ops:
                self.ops.record("recovery_claim", case_id=case_id, account_id=account, request_id=req, worker=self.worker_id, state=state)
            attempts, nxt, err = None, None, None
            try:
                res = self.runner_factory(case_id).run(case_id, request_id=req)
                out.advanced[case_id] = res.state
                if res.state not in TERMINAL:
                    nxt = self.clock.now() + _delta(self.wait_backoff_s)      # waiting for a human: look again later (or sooner if woken)
                attempts = 0
                if self.ops:
                    self.ops.record("recovery_run", case_id=case_id, account_id=account, request_id=req, worker=self.worker_id, state=res.state, waiting=bool(res.waiting_on))
            except TransitionRefused as e:
                if e.code in ("STALE_STATE", "GUARD_FAILED"):
                    out.benign_races.append(case_id)                          # someone else advanced the case first: nothing is wrong
                    attempts = 0
                else:
                    attempts, err = self._failure(case_id, e), type(e).__name__ + ":" + e.code
            except Exception as e:                                           # noqa: BLE001 - recorded and backed off, never swallowed silently
                attempts, err = self._failure(case_id, e), type(e).__name__
                log.exception("recovery run failed for %s", case_id)
            if err:
                out.errors[case_id] = err
                nxt = self.clock.now() + _delta(self.error_backoff_s * 2 ** min(attempts or 1, 6))
                if self.ops:
                    self.ops.record("recovery_error", case_id=case_id, account_id=account, request_id=req, worker=self.worker_id, error=scrub(err), attempts=attempts)
            self._release(case_id, nxt, attempts, err)
        return out

    def _failure(self, case_id: str, e: Exception) -> int:
        with self.pool.connection() as c:
            a = c.execute(_SQL_ATTEMPTS, (case_id,)).fetchone()[0]
            c.rollback()
        return a + 1

    def _release(self, case_id: str, nxt, attempts: int | None, err: str | None) -> None:
        with self.pool.connection() as c, c.transaction():
            c.execute(_SQL_DONE, {"next": nxt, "attempts": attempts if attempts is not None else 0, "err": scrub(err) if err else None, "cid": case_id, "w": self.worker_id})


def _delta(seconds: float):
    from datetime import timedelta
    return timedelta(seconds=seconds)
