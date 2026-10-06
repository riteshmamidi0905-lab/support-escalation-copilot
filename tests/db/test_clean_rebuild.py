"""Rebuild the entire customer environment from nothing on a CLEAN database, twice, and prove the result is identical."""
import secrets

import psycopg
import pytest

from copilot.db import admin as A
from copilot.db.rebuild import rebuild
from copilot.retrieval.embed import PlumbingEmbedder
from tests.db.conftest import DSN

pytestmark = pytest.mark.db


def envvars():
    return {"app_password": secrets.token_hex(12), "loader_password": secrets.token_hex(12), "intake_password": secrets.token_hex(12), "control_password": secrets.token_hex(12), "scope_secret": secrets.token_hex(32)}


def test_clean_rebuild_runs_migrations_generation_loading_validation_and_isolation_sweep():
    s = rebuild(DSN, "copilot_rebuild_a", 20260101, envvars(), drop_after=False)
    try:
        assert s["migrations"] == [p.name for p in A.migration_files()]
        assert (s["contract_validation"], s["design_validation"]) == ("ok", "ok")
        assert s["loaded"]["tickets"] == 300 and s["loaded"]["accounts"] == 40
        assert s["tenant_sweep"].startswith("40 accounts")
        assert A.audit_roles(A.with_dbname(DSN, "copilot_rebuild_a")) == []
    finally:
        A.drop_database(DSN, "copilot_rebuild_a")


def test_rebuilding_twice_gives_identical_database_contents():
    a = rebuild(DSN, "copilot_rebuild_b1", 20260101, envvars(), drop_after=True)
    b = rebuild(DSN, "copilot_rebuild_b2", 20260101, envvars(), drop_after=True)
    assert a["file_hashes"] == b["file_hashes"]
    assert a["table_digest"] == b["table_digest"] and len(a["table_digest"]) >= 10
    # another seed has a different corpus that the committed real-model cache does not cover: use the labelled NON-SEMANTIC plumbing embedder (this test is about loading, not retrieval quality)
    c = rebuild(DSN, "copilot_rebuild_b3", 7, envvars(), drop_after=True, embedder=PlumbingEmbedder())
    assert c["table_digest"]["tickets"] != a["table_digest"]["tickets"]
    assert c["loaded"]["tickets"] == 300
    with psycopg.connect(DSN, autocommit=True) as conn:
        left = conn.execute("SELECT datname FROM pg_database WHERE datname LIKE 'copilot_rebuild_%'").fetchall()
    assert left == []
