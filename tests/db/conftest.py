import os
from urllib.parse import urlparse

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

DSN = os.environ.get("COPILOT_TEST_DATABASE_URL", "")


def pytest_collection_modifyitems(config, items):
    if DSN:
        return
    skip = pytest.mark.skip(reason="COPILOT_TEST_DATABASE_URL not set (use scripts/with_local_pg.py or docker compose)")
    for it in items:
        if "db" in it.keywords:
            it.add_marker(skip)


@pytest.fixture()
def admin():
    """Superuser/owner connection (autocommit). Each test works in its own throw-away schema-qualified objects, dropped afterwards."""
    with psycopg.connect(DSN, autocommit=True) as c:
        yield c


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
