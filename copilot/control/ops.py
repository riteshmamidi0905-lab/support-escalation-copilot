"""Operational telemetry produced by the application itself (never by a dashboard): an append-only event stream with correlation ids, and metrics DERIVED from real tables.

Correlation: HTTP request / run -> case -> model invocation -> action -> approval -> execution -> audit. Every record carries whatever ids it has; attributes are scrubbed
(secrets redacted, e-mail addresses masked) and may never carry hidden reasoning. Telemetry must never break the workflow: a failed write is logged and dropped."""
from __future__ import annotations

import logging
from typing import Any

from psycopg.types.json import Jsonb

from copilot.redact import HIDDEN_REASONING_KEYS, scrub

from .clock import SystemClock

log = logging.getLogger("copilot.ops")
_IDS = ("case_id", "account_id", "run_id", "request_id", "invocation_id", "action_id", "approval_id")
_SQL_INSERT = ("INSERT INTO copilot.ops_events (ts, kind, case_id, account_id, run_id, request_id, invocation_id, action_id, approval_id, duration_ms, attrs) "
               "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")


class OpsRecorder:
    def __init__(self, pool, clock=None):
        self.pool, self.clock = pool, clock or SystemClock()

    def record(self, kind: str, *, duration_ms: float | None = None, **fields: Any) -> None:
        ids = {k: fields.pop(k, None) for k in _IDS}
        if any(k.lower() in HIDDEN_REASONING_KEYS for k in fields):
            raise ValueError("hidden reasoning must not be recorded")
        try:
            with self.pool.connection() as c, c.transaction():
                c.execute(_SQL_INSERT, (self.clock.now(), kind, ids["case_id"], ids["account_id"], ids["run_id"], ids["request_id"], ids["invocation_id"], ids["action_id"], ids["approval_id"], duration_ms,
                                        Jsonb(scrub(fields, 200))))
        except Exception:                                                  # noqa: BLE001 - telemetry never breaks the workflow
            log.exception("ops event dropped: %s", kind)

    def tracer_listener(self, case_id: str, account_id: str | None, run_id: str, invocation_id: str | None = None, request_id: str | None = None):
        """Adapter for the frozen runtime's Tracer: maps runtime events to ops events (model calls, retries, provider failures). Content is never copied, only outcomes and counts."""
        def listener(ev: dict[str, Any]) -> None:
            t = ev.get("type")
            base = {"case_id": case_id, "account_id": account_id, "run_id": run_id, "invocation_id": invocation_id, "request_id": request_id}
            if t == "model_call":
                self.record("model_call", duration_ms=ev.get("duration_ms"), ok=ev.get("ok"), prompt_tokens=ev.get("prompt_tokens"), completion_tokens=ev.get("completion_tokens"), **base)
            elif t == "retry":
                self.record("model_retry", attempt=ev.get("attempt"), error_kind=ev.get("kind"), **base)
            elif t == "failure" and ev.get("kind") in ("provider", "network", "malformed", "structured"):
                self.record("model_failure", error_kind=ev.get("kind"), **base)
            elif t == "model_stage":
                self.record("model_stage", stage=ev.get("stage"), ok=ev.get("ok"), repairs=ev.get("repairs"), error=ev.get("error"), problems=ev.get("problems"), tokens=ev.get("tokens"), **base)
        return listener
