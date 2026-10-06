"""Durable case state machine over PostgreSQL. A transition is ONE database transaction: guard check, state update (optimistic version), transition row and audit event
commit together or not at all, so a crash during a transition leaves the previous state intact. The database independently refuses illegal edges."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from copilot.control.audit import AuditLog
from copilot.control.canonical import canonical_json
from copilot.control.clock import SystemClock
from copilot.redact import scrub

from .states import BY_EDGE, STATES, TERMINAL


class TransitionRefused(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


_SQL_GET = "SELECT case_id, account_id, ticket_id, state, version, outcome, disposition, file, created_at, updated_at FROM copilot.case_runs WHERE case_id = %s"
_SQL_LOCK = _SQL_GET + " FOR UPDATE"
_COLS = ("case_id", "account_id", "ticket_id", "state", "version", "outcome", "disposition", "file", "created_at", "updated_at")


class CaseMachine:
    def __init__(self, pool, audit: AuditLog, clock=None, hook=None):
        self.pool, self.audit, self.clock = pool, audit, clock or SystemClock()
        self.hook = hook or (lambda point, **kw: None)                  # fault-injection point (tests): may raise to simulate a crash

    def create(self, case_id: str, account_id: str, ticket_id: str) -> dict[str, Any]:
        now = self.clock.now()
        with self.pool.connection() as c, c.transaction():
            c.execute("INSERT INTO copilot.case_runs (case_id, account_id, ticket_id, state, version, created_at, updated_at) VALUES (%s,%s,%s,'NEW',0,%s,%s) ON CONFLICT (case_id) DO NOTHING",
                      (case_id, account_id, ticket_id, now, now))
        return self.get(case_id)

    def get(self, case_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as c:
            r = c.execute(_SQL_GET, (case_id,)).fetchone()
            c.rollback()
        return dict(zip(_COLS, r, strict=True)) if r else None

    def patch_file(self, case_id: str, key: str, value: Any) -> None:
        """Persist one stage output (top-level key of the case file) durably. Values are scrubbed before storage; idempotent (same key overwritten with the same stage output)."""
        with self.pool.connection() as c, c.transaction():
            c.execute("UPDATE copilot.case_runs SET file = jsonb_set(file, %s, %s::jsonb, true), updated_at = %s WHERE case_id = %s", ([key], Jsonb(_jsonable(value)), self.clock.now(), case_id))

    def advance(self, case_id: str, expected_state: str, dst: str, inputs: dict[str, Any] | None = None, actor: dict[str, Any] | None = None, correlation: dict[str, Any] | None = None) -> dict[str, Any]:
        """Move a case along ONE legal edge. Fails closed: unknown/illegal edge, stale expected state, missing inputs, failed guard. Nothing else can change a case's state."""
        inputs = inputs or {}
        if dst not in STATES or expected_state not in STATES:
            raise TransitionRefused("UNKNOWN_STATE")
        tr = BY_EDGE.get((expected_state, dst))
        if tr is None:
            raise TransitionRefused("ILLEGAL_TRANSITION", f"{expected_state} -> {dst}")
        missing = [k for k in tr.requires if k not in inputs]
        if missing:
            raise TransitionRefused("MISSING_INPUTS", ",".join(missing))
        if tr.outcomes is not None and dst in TERMINAL and inputs.get("outcome") not in tr.outcomes:
            raise TransitionRefused("OUTCOME_NOT_ALLOWED", str(inputs.get("outcome")))
        now = self.clock.now()
        with self.pool.connection() as c, c.transaction():
            r = c.execute(_SQL_LOCK, (case_id,)).fetchone()
            if r is None:
                raise TransitionRefused("UNKNOWN_CASE")
            row = dict(zip(_COLS, r, strict=True))
            if row["state"] != expected_state:
                raise TransitionRefused("STALE_STATE", f"case is in {row['state']}, not {expected_state}")        # replay / repeated / backwards / forged expected state
            why = tr.guard(row["file"], inputs)
            if why:
                raise TransitionRefused("GUARD_FAILED", why)
            digest = hashlib.sha256(canonical_json(scrub({k: v for k, v in inputs.items()}))).hexdigest()
            self.hook("before_update", case_id=case_id, src=expected_state, dst=dst)
            c.execute("UPDATE copilot.case_runs SET state=%s, version=version+1, updated_at=%s, outcome=COALESCE(%s, outcome), disposition=COALESCE(%s, disposition) WHERE case_id=%s",
                      (dst, now, inputs.get("outcome"), inputs.get("disposition"), case_id))
            c.execute("INSERT INTO copilot.case_transitions (case_id, from_state, to_state, version, inputs_sha256, ts) VALUES (%s,%s,%s,%s,%s,%s)",
                      (case_id, expected_state, dst, row["version"] + 1, digest, now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")))
            self.hook("before_audit", case_id=case_id, src=expected_state, dst=dst)
            self.audit.append(tr.event, case_id, row["account_id"], actor or {"kind": "system", "id": "case-workflow", "role": None}, correlation or {},
                              {"from": expected_state, "to": dst, "version": row["version"] + 1, "inputs_sha256": digest, "outcome": inputs.get("outcome"),
                               "reasons": [inputs["failure"]["code"]] if dst == "FAILED" and isinstance(inputs.get("failure"), dict) else []}, conn=c)
            self.hook("after_audit", case_id=case_id, src=expected_state, dst=dst)
        return self.get(case_id)

    def transitions(self, case_id: str) -> list[tuple[str, str, int]]:
        with self.pool.connection() as c:
            r = c.execute("SELECT from_state, to_state, version FROM copilot.case_transitions WHERE case_id = %s ORDER BY version", (case_id,)).fetchall()
            c.rollback()
        return [tuple(x) for x in r]


def _jsonable(v: Any) -> Any:
    return json.loads(json.dumps(scrub(v, 4000), default=_default))


def _default(o):
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if hasattr(o, "as_dict"):
        return o.as_dict()
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    return str(o)


_ = psycopg
