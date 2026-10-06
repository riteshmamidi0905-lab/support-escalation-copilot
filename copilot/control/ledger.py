"""Idempotency ledger. One row per (account, idempotency key). The row commits to the action hash, so a key can never be reused for a different payload
(same key + different hash => PAYLOAD_CONFLICT, refused). Claiming is a single atomic INSERT ... ON CONFLICT, so concurrent duplicates cannot both proceed."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb

CLAIMED, REPLAY, IN_PROGRESS, PAYLOAD_CONFLICT, FINAL_FAILURE, UNCERTAIN, RETRY_CLAIMED = "CLAIMED", "REPLAY", "IN_PROGRESS", "PAYLOAD_CONFLICT", "FINAL_FAILURE", "UNCERTAIN", "RETRY_CLAIMED"
_COLS = "account_id, idempotency_key, case_id, action_type, action_hash, approval_id, status, attempts, result, created_at, updated_at, lease_expires_at"


_SQL_PEEK = "SELECT " + _COLS + " FROM copilot.idempotency_records WHERE account_id=%s AND idempotency_key=%s"
_SQL_LOCK = _SQL_PEEK + " FOR UPDATE"
_SQL_CLAIM = ("INSERT INTO copilot.idempotency_records (account_id, idempotency_key, case_id, action_type, action_hash, approval_id, status, attempts, created_at, updated_at, lease_expires_at) "
              "VALUES (%s,%s,%s,%s,%s,%s,'in_progress',1,%s,%s,%s) ON CONFLICT (account_id, idempotency_key) DO NOTHING RETURNING " + _COLS)
_SQL_RETRY = ("UPDATE copilot.idempotency_records SET status='in_progress', attempts=attempts+1, updated_at=%s, lease_expires_at=%s, approval_id=COALESCE(%s, approval_id) "
              "WHERE account_id=%s AND idempotency_key=%s RETURNING " + _COLS)


def _rec(r) -> dict[str, Any]:
    return dict(zip(_COLS.split(", "), r, strict=True))


class Ledger:
    def __init__(self, pool):
        self.pool = pool

    def peek(self, account_id: str, key: str) -> dict[str, Any] | None:
        with self.pool.connection() as c:
            r = c.execute(_SQL_PEEK, (account_id, key)).fetchone()
            c.rollback()
        return _rec(r) if r else None

    def begin(self, *, account_id: str, key: str, case_id: str, action_type: str, action_hash: str, approval_id: str | None, now: datetime, lease_s: int) -> tuple[str, dict[str, Any]]:
        lease = now + timedelta(seconds=lease_s)
        with self.pool.connection() as c, c.transaction():
            r = c.execute(_SQL_CLAIM, (account_id, key, case_id, action_type, action_hash, approval_id, now, now, lease)).fetchone()
            if r:
                return CLAIMED, _rec(r)
            ex = _rec(c.execute(_SQL_LOCK, (account_id, key)).fetchone())
            if (ex["action_hash"], ex["case_id"], ex["action_type"]) != (action_hash, case_id, action_type):
                return PAYLOAD_CONFLICT, ex
            st = ex["status"]
            if st == "succeeded":
                return REPLAY, ex
            if st == "failed_permanent":
                return FINAL_FAILURE, ex
            if st == "uncertain":
                return UNCERTAIN, ex
            if st == "in_progress":
                if ex["lease_expires_at"] and ex["lease_expires_at"] > now:
                    return IN_PROGRESS, ex
                c.execute("UPDATE copilot.idempotency_records SET status='uncertain', updated_at=%s WHERE account_id=%s AND idempotency_key=%s", (now, account_id, key))
                ex["status"] = "uncertain"                           # a worker died or stalled mid-call: nobody knows whether the effect happened
                return UNCERTAIN, ex
            r2 = c.execute(_SQL_RETRY, (now, lease, approval_id, account_id, key)).fetchone()
            return RETRY_CLAIMED, _rec(r2)

    def _set(self, account_id: str, key: str, status: str, now: datetime, result: dict[str, Any] | None = None, lease_s: int | None = None) -> None:
        with self.pool.connection() as c, c.transaction():
            c.execute("UPDATE copilot.idempotency_records SET status=%s, updated_at=%s, result=COALESCE(%s, result), lease_expires_at=%s WHERE account_id=%s AND idempotency_key=%s",
                      (status, now, Jsonb(result) if result is not None else None, now + timedelta(seconds=lease_s) if lease_s else None, account_id, key))

    def succeed(self, account_id, key, now, result):
        self._set(account_id, key, "succeeded", now, result)

    def fail_permanent(self, account_id, key, now, result):
        self._set(account_id, key, "failed_permanent", now, result)

    def fail_transient(self, account_id, key, now, result):
        self._set(account_id, key, "failed_transient", now, result)

    def mark_uncertain(self, account_id, key, now, result=None):
        self._set(account_id, key, "uncertain", now, result)

    def resume(self, account_id, key, now, lease_s):
        self._set(account_id, key, "in_progress", now, None, lease_s)
