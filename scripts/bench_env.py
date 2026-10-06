"""Shared helper for the benchmark and tuning scripts: builds a CLEAN database (create, migrate, bootstrap, regenerate dataset, validate, load) and returns an app-role pool."""
import os
import secrets
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from copilot import contracts as C  # noqa: E402
from copilot.data import design, generator  # noqa: E402
from copilot.db import admin as dbadmin  # noqa: E402
from copilot.db.intake import Intake  # noqa: E402
from copilot.db.load import load_dataset  # noqa: E402
from copilot.db.session import make_pool  # noqa: E402


@dataclass
class BenchEnv:
    dbname: str
    admin_dsn: str
    pool: object
    dataset: Path
    counts: dict
    signer: object = None
    guard: object = None
    pg_version: str = ""
    pgvector_version: str = ""
    intake: object = None
    db_admin_dsn: str = ""


def build(embedder=None, seed: int = 20260101) -> BenchEnv:
    dsn = os.environ["COPILOT_TEST_DATABASE_URL"]
    dbname = f"copilot_bench_{secrets.token_hex(4)}"
    pw = {r: secrets.token_hex(12) for r in dbadmin.ROLES}
    db_admin = dbadmin.create_database(dsn, dbname)
    dbadmin.migrate(db_admin)
    secret = secrets.token_hex(32)
    dbadmin.bootstrap(db_admin, pw, secret)
    ds = Path(tempfile.mkdtemp(prefix="meridian-bench-"))
    generator.write_dataset(seed, ds)
    C.validate_dataset(ds).raise_if_failed()
    design.design_report(ds).raise_if_failed()
    C.validate_hand_labels(ROOT / "data" / "hand-labelled-v1", ds).raise_if_failed()
    counts = load_dataset(dbadmin.role_dsn(dsn, "copilot_loader", pw["copilot_loader"], dbname), ds, embedder)
    import psycopg

    from copilot.scope import ScopeGuard, Signer
    with psycopg.connect(db_admin) as c:
        pgv = c.execute("SHOW server_version").fetchone()[0]
        vv = c.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
    signer = Signer(secret)
    # The hand-set tickets are evaluation INPUTS, but a scope can only be minted for a case on a real ticket row (trusted intake), so they are inserted
    # as ordinary tickets by the loader role. Runbook retrieval never sees them; tenant evidence (similar tickets) can.
    hand = ROOT / "data" / "hand-labelled-v1"
    with psycopg.connect(dbadmin.role_dsn(dsn, "copilot_loader", pw["copilot_loader"], dbname)) as c:
        c.cursor().executemany("INSERT INTO copilot.tickets VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", [(t["ticket_id"], t["account_id"], t["created_at"], t["product_area"], t["severity"], t["subject"], t["body"],
                               t["reporter"]["name"], t["reporter"]["email"], t["channel"]) for t in C.read_jsonl(hand / "hand_tickets.jsonl")])
    intake = Intake(dbadmin.role_dsn(dsn, "copilot_intake", pw["copilot_intake"], dbname), signer)
    return BenchEnv(dbname, dsn, make_pool(dbadmin.role_dsn(dsn, "copilot_app", pw["copilot_app"], dbname), 1, 2), ds, counts, signer, ScopeGuard(signer), pgv, vv, intake, db_admin)


def drop(env: BenchEnv):
    env.pool.close()
    dbadmin.drop_database(env.admin_dsn, env.dbname)
