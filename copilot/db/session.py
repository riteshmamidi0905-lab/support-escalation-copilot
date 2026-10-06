"""Scoped sessions for the application role.

Rules enforced here (each has a test):
  * a scope is applied with transaction-local set_config(..., true) and the transaction ends when the block ends, so nothing outlives it;
  * on ANY exception the transaction is rolled back (scope included);
  * the pool resets every returned connection with DISCARD ALL, so even buggy session-level settings cannot reach the next borrower;
  * no scope => no rows (the database, not this code, enforces it).
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool

from copilot.scope import Scope, ScopeGuard


def _reset(conn: psycopg.Connection) -> None:
    """Runs whenever a connection goes back to the pool: end any open transaction, then wipe ALL session state (including any session-level setting)."""
    conn.rollback()
    conn.autocommit = True
    try:
        conn.execute("DISCARD ALL")
    finally:
        conn.autocommit = False


def make_pool(dsn: str, min_size: int = 1, max_size: int = 4) -> ConnectionPool:
    return ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=True, reset=_reset, kwargs={"autocommit": False, "prepare_threshold": None})   # DISCARD ALL drops server-side prepared statements, so client auto-prepare must be off


@contextmanager
def scoped(pool_or_conn, scope: Scope | None, guard: ScopeGuard | None = None, requested_account: str | None = None) -> Iterator[psycopg.Cursor]:
    """Yield a cursor inside ONE transaction bound to `scope`. With scope=None the cursor is unscoped (and the database returns no tenant rows)."""
    if scope is not None:
        if guard is None:
            raise ValueError("a ScopeGuard is required when a scope is supplied")
        guard.check(scope, requested_account)
    cm = _noop(pool_or_conn) if isinstance(pool_or_conn, psycopg.Connection) else pool_or_conn.connection()
    with cm as conn:
        if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            raise RuntimeError("scoped() needs an idle connection: an open outer transaction would let scope outlive this block")
        if True:
            with conn.transaction():
                cur = conn.cursor()
                if scope is not None:
                    for k, v in scope.settings().items():
                        cur.execute("SELECT set_config(%s, %s, true)", (k, v))
                yield cur


@contextmanager
def _noop(conn):
    yield conn
