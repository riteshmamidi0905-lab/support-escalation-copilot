import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from copilot import contracts as C
from copilot.data import generator
from copilot.db import admin as dbadmin
from copilot.db.intake import Intake
from copilot.db.load import load_dataset
from copilot.db.session import make_pool
from copilot.scope import ScopeGuard, Signer

DSN = os.environ.get("COPILOT_TEST_DATABASE_URL", "")
SEED = 20260101


def pytest_collection_modifyitems(config, items):
    if DSN:
        return
    skip = pytest.mark.skip(reason="COPILOT_TEST_DATABASE_URL not set (use scripts/with_local_pg.py or docker compose)")
    for it in items:
        if "db" in it.keywords:
            it.add_marker(skip)


@pytest.fixture()
def admin_conn():
    with psycopg.connect(DSN, autocommit=True) as c:
        yield c


# kept for the M0 spike tests
@pytest.fixture()
def admin(admin_conn):
    return admin_conn


def connect_as(role: str, password: str):
    info = conninfo_to_dict(DSN)
    info.update(user=role, password=password)
    info.pop("passfile", None)
    return psycopg.connect(make_conninfo(**info), autocommit=False)


@pytest.fixture()
def has_pgvector(admin):
    try:
        admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        return True
    except psycopg.Error:
        return False


@pytest.fixture()
def dsn_host():
    return urlparse(DSN).hostname


@dataclass
class Env:
    dbname: str
    admin_dsn: str                 # superuser/admin connection to THIS database
    server_admin_dsn: str          # admin connection to the server
    app_dsn: str
    loader_dsn: str
    intake_dsn: str
    passwords: dict
    scope_secret: str
    signer: Signer
    guard: ScopeGuard
    intake: Intake
    dataset: Path
    tickets: list
    accounts: list
    labels: list
    counts: dict
    pool: object
    control_dsn: str = ""


def build_env(prefix: str, seed: int = SEED, scope_secret: str | None = None) -> Env:
    dbname = f"{prefix}_{secrets.token_hex(4)}"
    pw = {"copilot_app": secrets.token_hex(12), "copilot_loader": secrets.token_hex(12), "copilot_intake": secrets.token_hex(12), "copilot_control": secrets.token_hex(12)}
    secret = scope_secret or secrets.token_hex(32)
    db_admin = dbadmin.create_database(DSN, dbname)
    dbadmin.migrate(db_admin)
    dbadmin.bootstrap(db_admin, pw, secret)
    ds = Path(tempfile.mkdtemp(prefix="meridian-test-"))
    generator.write_dataset(seed, ds)
    counts = load_dataset(dbadmin.role_dsn(DSN, "copilot_loader", pw["copilot_loader"], dbname), ds)
    signer = Signer(secret)
    intake = Intake(dbadmin.role_dsn(DSN, "copilot_intake", pw["copilot_intake"], dbname), signer)
    app_dsn = dbadmin.role_dsn(DSN, "copilot_app", pw["copilot_app"], dbname)
    return Env(dbname, db_admin, DSN, app_dsn, dbadmin.role_dsn(DSN, "copilot_loader", pw["copilot_loader"], dbname), dbadmin.role_dsn(DSN, "copilot_intake", pw["copilot_intake"], dbname),
               pw, secret, signer, ScopeGuard(signer), intake, ds, C.read_jsonl(ds / "tickets.jsonl"), C.read_jsonl(ds / "accounts.jsonl"), C.read_jsonl(ds / "synthetic_labels.jsonl"), counts,
               make_pool(app_dsn, min_size=1, max_size=1), dbadmin.role_dsn(DSN, "copilot_control", pw["copilot_control"], dbname))


@pytest.fixture(scope="session")
def env():
    if not DSN:
        pytest.skip("no database")
    e = build_env("copilot_m1")
    yield e
    e.pool.close()
    dbadmin.drop_database(DSN, e.dbname)


@pytest.fixture(scope="module")
def m4env(env):
    """A DEDICATED environment for the M4 workflow tests (they insert hostile tickets, open hundreds of cases and fill the audit log; the shared M1 environment must stay exactly as loaded).
    Database roles are CLUSTER-wide, so building this environment re-bootstraps their passwords; on teardown the shared environment's passwords are put back."""
    shared = env
    e = build_env("copilot_m4")
    yield e
    e.pool.close()
    dbadmin.drop_database(DSN, e.dbname)
    dbadmin.bootstrap(shared.admin_dsn, shared.passwords, shared.scope_secret)
