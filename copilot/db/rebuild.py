"""Rebuild the whole customer environment from scratch on a CLEAN database:
create database -> migrate -> bootstrap roles/signing key -> generate (seed) -> validate contracts + design -> load -> verify counts and a tenant sweep.
Used by tests, CI and the Compose job.  Environment: COPILOT_ADMIN_DSN, COPILOT_APP_PASSWORD, COPILOT_LOADER_PASSWORD, COPILOT_INTAKE_PASSWORD, COPILOT_SCOPE_SECRET."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import psycopg
from psycopg import sql

from copilot import contracts as C
from copilot.data import design, generator
from copilot.db import admin
from copilot.db.intake import Intake
from copilot.db.load import load_dataset
from copilot.db.session import make_pool, scoped
from copilot.scope import ScopeGuard, Signer


def table_digest(db_admin_dsn: str) -> dict:
    """Content hash per data table (order-independent), as the DATABASE sees it. Equal digests after two rebuilds = deterministic load."""
    out = {}
    with psycopg.connect(db_admin_dsn) as c:
        for t in admin.table_names(db_admin_dsn):
            if t in ("cases",):
                continue
            rows = c.execute(sql.SQL("SELECT md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), '')) FROM {} x").format(sql.Identifier("copilot", t))).fetchone()[0]
            out[t] = rows
    return out


def rebuild(admin_dsn: str, dbname: str, seed: int, env: dict, out_dir: Path | None = None, drop_after: bool = False) -> dict:
    db_admin = admin.create_database(admin_dsn, dbname)
    summary: dict = {"database": dbname, "seed": seed}
    summary["migrations"] = admin.migrate(db_admin)
    admin.bootstrap(db_admin, {"copilot_app": env["app_password"], "copilot_loader": env["loader_password"], "copilot_intake": env["intake_password"]}, env["scope_secret"])
    ds = out_dir or Path(tempfile.mkdtemp(prefix="meridian-"))
    summary["file_hashes"] = generator.write_dataset(seed, ds)
    rep, drep = C.validate_dataset(ds), design.design_report(ds)
    rep.raise_if_failed()
    drep.raise_if_failed()
    summary["contract_validation"], summary["design_validation"] = "ok", "ok"
    summary["loaded"] = load_dataset(admin.role_dsn(admin_dsn, "copilot_loader", env["loader_password"], dbname), ds)
    # sweep: every account, through the real scoped path, must see exactly its own tickets
    signer = Signer(env["scope_secret"])
    guard = ScopeGuard(signer)
    intake = Intake(admin.role_dsn(admin_dsn, "copilot_intake", env["intake_password"], dbname), signer)
    tickets = C.read_jsonl(ds / "tickets.jsonl")
    expected: dict = {}
    for t in tickets:
        expected.setdefault(t["account_id"], set()).add(t["ticket_id"])
    pool = make_pool(admin.role_dsn(admin_dsn, "copilot_app", env["app_password"], dbname))
    try:
        for acc, tids in sorted(expected.items()):
            _, scope = intake.open_case(sorted(tids)[0])
            with scoped(pool, scope, guard) as cur:
                seen = {r[0] for r in cur.execute("SELECT ticket_id FROM copilot.tickets").fetchall()}
            if seen != tids:
                raise AssertionError(f"tenant sweep failed for {acc}")
        with scoped(pool, None) as cur:
            if cur.execute("SELECT count(*) FROM copilot.tickets").fetchone()[0] != 0:
                raise AssertionError("unscoped session saw tenant rows")
    finally:
        pool.close()
    summary["tenant_sweep"] = f"{len(expected)} accounts: each saw exactly its own tickets; unscoped saw none"
    summary["table_digest"] = table_digest(db_admin)
    if drop_after:
        admin.drop_database(admin_dsn, dbname)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dbname", default="copilot_dev")
    ap.add_argument("--seed", type=int, default=generator.DEFAULT_SEED)
    a = ap.parse_args(argv)
    env = {"app_password": os.environ["COPILOT_APP_PASSWORD"], "loader_password": os.environ["COPILOT_LOADER_PASSWORD"], "intake_password": os.environ["COPILOT_INTAKE_PASSWORD"], "scope_secret": os.environ["COPILOT_SCOPE_SECRET"]}
    s = rebuild(os.environ["COPILOT_ADMIN_DSN"], a.dbname, a.seed, env)
    print(json.dumps(s, indent=1, default=str))
    print("REBUILD OK", hashlib.sha256(json.dumps(s["table_digest"], sort_keys=True).encode()).hexdigest()[:16])
    return 0


if __name__ == "__main__":
    sys.exit(main())
