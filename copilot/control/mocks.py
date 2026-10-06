"""Deterministic, fault-injectable mock customer systems: only what M3 needs to exercise gated actions.

Each mock has a FaultScript (an ordered list of outcomes consumed per call) and records every EFFECT it applies, so tests can count real effects.
Outcomes: ok · transient (fails before any effect; safe to retry) · permanent (rejected; do not retry) · timeout_before_effect (caller sees a timeout, nothing happened) ·
timeout_after_effect (caller sees a timeout but the effect WAS applied: the outcome is uncertain from the caller's side).
`dedupe=True` makes the system honour the caller's request id (a good API); `dedupe=False` is a naive API that applies a duplicate request twice, which is why the
gateway's own idempotency ledger exists. `lookup_supported` lets the caller reconcile an uncertain outcome by request id.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from typing import Any

OUTCOMES = ("ok", "transient", "permanent", "timeout_before_effect", "timeout_after_effect")


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class CallTimeout(Exception):
    """The caller did not get an answer. Whether the effect happened is unknown to the caller."""


class LookupUnsupported(Exception):
    pass


class FaultScript:
    def __init__(self, outcomes: Iterable[str] = (), default: str = "ok"):
        o = list(outcomes)
        if not set(o) | {default} <= set(OUTCOMES):
            raise ValueError("unknown outcome")
        self._o, self._default, self._lock = o, default, threading.Lock()

    def next(self) -> str:
        with self._lock:
            return self._o.pop(0) if self._o else self._default


class MockSystem:
    name = "mock"

    def __init__(self, faults: FaultScript | None = None, dedupe: bool = False, lookup_supported: bool = False, latency_s: float = 0.0, result_extra: dict | None = None):
        self.faults, self.dedupe, self.lookup_supported = faults or FaultScript(), dedupe, lookup_supported
        self.latency_s, self.result_extra = latency_s, result_extra or {}
        self.effects: list[dict[str, Any]] = []
        self.calls = 0
        self._by_request: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def _validate(self, payload: dict[str, Any]) -> None:
        pass

    def _result(self, payload: dict[str, Any], n: int) -> dict[str, Any]:
        return {"ref": f"{self.name}-{n:04d}"}

    def call(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.calls += 1
            outcome = self.faults.next()
            if outcome == "transient":
                raise TransientError("service unavailable (503)")
            if outcome == "permanent":
                raise PermanentError("rejected by the customer system (422)")
            if outcome == "timeout_before_effect":
                raise CallTimeout("timed out")
            self._validate(payload)
            if self.dedupe and request_id in self._by_request:
                if outcome == "timeout_after_effect":
                    raise CallTimeout("timed out")
                return {**self._by_request[request_id], "duplicate": True}
            if self.latency_s:
                time.sleep(self.latency_s)
            res = {**self._result(payload, len(self.effects) + 1), **self.result_extra}
            self.effects.append({"request_id": request_id, "payload": dict(payload), "result": res})
            self._by_request[request_id] = res
            if outcome == "timeout_after_effect":
                raise CallTimeout("timed out")
            return dict(res)

    def lookup(self, request_id: str) -> dict[str, Any] | None:
        """Reconcile an uncertain outcome: the stored result if the effect was applied, None if it was not. Raises LookupUnsupported on systems that cannot say."""
        if not self.lookup_supported:
            raise LookupUnsupported(self.name)
        with self._lock:
            r = self._by_request.get(request_id)
            return dict(r) if r else None


class MockResyncSystem(MockSystem):
    name = "resync"

    def _result(self, payload, n):
        return {"ref": f"RSY-{n:04d}", "integration_id": payload["integration_id"], "status": "resync_started"}


class MockCreditSystem(MockSystem):
    name = "credit"

    def _result(self, payload, n):
        return {"ref": f"CRQ-{n:04d}", "account_id": payload["account_id"], "percent": payload["percent"], "status": "credit_requested_pending_finance"}


INTERNAL_SUFFIX = ".meridian.internal.example"


class MockEscalationSystem(MockSystem):
    """Internal engineering queue / notifier. Only internal destinations are accepted: a customer address or external host is rejected (I3)."""
    name = "escalation"

    def _validate(self, payload):
        dest = payload.get("destination", "")
        if "@" in dest or not dest.endswith(INTERNAL_SUFFIX):
            raise PermanentError("destination is not an internal engineering queue")

    def _result(self, payload, n):
        return {"ref": f"ESC-{n:04d}", "incident_id": payload.get("incident_id"), "severity": payload["severity"], "status": "queued"}
