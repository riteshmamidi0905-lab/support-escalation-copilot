"""Append-only, hash-chained audit log (ADR-0010).

GUARANTEE, stated precisely:
  * the control role can only INSERT and SELECT audit rows; triggers refuse UPDATE/DELETE/TRUNCATE for everyone;
  * every event commits to its predecessor (`hash = sha256(domain || canonical_body)`, body contains `prev_hash`), so modifying, deleting, inserting or reordering a row
    inside the chain is DETECTED by `verify()`;
  * this is TAMPER-EVIDENCE, NOT IMMUTABILITY: a database owner/superuser can disable the triggers and rewrite history, and can remove the LAST events (or the whole log)
    without breaking the chain. Detecting tail truncation needs an anchor (`anchor()`: count + head hash) stored outside this database; `verify(anchor)` then checks it.
Payloads are scrubbed (secrets redacted, e-mail addresses masked, strings truncated) and may never carry hidden reasoning.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from typing import Any

from copilot import contracts as C
from copilot.redact import HIDDEN_REASONING_KEYS, scrub

from .canonical import canonical_json
from .clock import SystemClock

DOMAIN = b"scec.audit.v1\x00"
LOCK_KEY = 7243907
MAX_PAYLOAD_BYTES = 8000


class AuditError(Exception):
    pass


@dataclass
class VerifyResult:
    ok: bool
    count: int
    head_hash: str | None
    errors: list[str] = field(default_factory=list)


def sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    def walk(v):
        if isinstance(v, dict):
            bad = {k for k in v if str(k).lower() in HIDDEN_REASONING_KEYS}
            if bad:
                raise AuditError(f"hidden reasoning must not be recorded (keys: {sorted(bad)})")
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(payload)
    clean = scrub(payload)
    if len(canonical_json(clean)) > MAX_PAYLOAD_BYTES:
        raise AuditError("audit payload too large")
    return clean


def event_hash(body: bytes) -> str:
    return hashlib.sha256(DOMAIN + body).hexdigest()


class AuditLog:
    def __init__(self, pool, clock=None):
        self.pool, self.clock = pool, clock or SystemClock()

    def append(self, event_type: str, case_id: str, account_id: str | None, actor: dict[str, Any], correlation: dict[str, Any], payload: dict[str, Any], conn=None) -> dict[str, Any]:
        """`conn`: join the caller's open transaction (used by workflow transitions so state change and audit event commit or roll back together)."""
        body = {"event_id": "EVT-" + secrets.token_hex(8), "case_id": case_id, "account_id": account_id, "ts": self.clock.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "actor": actor,
                "type": event_type, "correlation": {k: v for k, v in correlation.items() if k in ("run_id", "request_id", "action_id", "action_hash", "approval_id", "idempotency_key", "invocation_id")},
                "payload": sanitize_payload(payload)}
        if conn is not None:
            h = self._insert(conn, body, event_type, case_id, account_id)
        else:
            with self.pool.connection() as c, c.transaction():
                h = self._insert(c, body, event_type, case_id, account_id)
        return {**body, "hash": h}

    def _insert(self, c, body, event_type, case_id, account_id) -> str:
        c.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        row = c.execute("SELECT hash FROM copilot.audit_events ORDER BY seq DESC LIMIT 1").fetchone()
        body["prev_hash"] = row[0] if row else None
        blob = canonical_json(body)
        h = event_hash(blob)
        errs = C.validate_record("audit_event", {**body, "hash": h})
        if errs:
            raise AuditError("event violates the audit contract: " + "; ".join(e[:120] for e in errs[:2]))
        c.execute("INSERT INTO copilot.audit_events (event_id, case_id, account_id, event_type, ts, body, prev_hash, hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                  (body["event_id"], case_id, account_id, event_type, body["ts"], blob.decode(), body["prev_hash"], h))
        return h

    def rows(self) -> list[dict[str, Any]]:
        with self.pool.connection() as c:
            r = c.execute("SELECT seq, event_id, case_id, account_id, event_type, ts, body, prev_hash, hash FROM copilot.audit_events ORDER BY seq").fetchall()
            c.rollback()
        return [dict(zip(("seq", "event_id", "case_id", "account_id", "event_type", "ts", "body", "prev_hash", "hash"), x, strict=True)) for x in r]

    def events(self, case_id: str | None = None) -> list[dict[str, Any]]:
        out = []
        for r in self.rows():
            if case_id is None or r["case_id"] == case_id:
                out.append({**json.loads(r["body"]), "hash": r["hash"], "seq": r["seq"]})
        return out

    def anchor(self) -> dict[str, Any]:
        rows = self.rows()
        return {"count": len(rows), "head_hash": rows[-1]["hash"] if rows else None}

    def verify(self, anchor: dict[str, Any] | None = None) -> VerifyResult:
        return verify_rows(self.rows(), anchor)


def verify_rows(rows: list[dict[str, Any]], anchor: dict[str, Any] | None = None) -> VerifyResult:
    errs: list[str] = []
    prev = None
    for i, r in enumerate(rows):
        blob = r["body"].encode()
        if event_hash(blob) != r["hash"]:
            errs.append(f"row {i} (seq {r['seq']}): hash does not match its body")
        try:
            b = json.loads(r["body"])
        except ValueError:
            errs.append(f"row {i}: body is not JSON")
            prev = r["hash"]
            continue
        if b.get("prev_hash") != prev or r["prev_hash"] != prev:
            errs.append(f"row {i} (seq {r['seq']}): chain broken (prev_hash does not match the preceding event)")
        for col, key in (("event_id", "event_id"), ("case_id", "case_id"), ("account_id", "account_id"), ("event_type", "type"), ("ts", "ts")):
            if r[col] != b.get(key):
                errs.append(f"row {i} (seq {r['seq']}): column {col} disagrees with the hashed body")
        prev = r["hash"]
    if anchor is not None:
        if len(rows) < anchor["count"]:
            errs.append(f"log is shorter than the anchor ({len(rows)} < {anchor['count']}): events were removed")
        elif anchor["count"] and rows[anchor["count"] - 1]["hash"] != anchor["head_hash"]:
            errs.append("the event at the anchored position differs from the anchored head hash")
    return VerifyResult(not errs, len(rows), rows[-1]["hash"] if rows else None, errs)
