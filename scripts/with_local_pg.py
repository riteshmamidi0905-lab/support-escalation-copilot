"""Run a command with an ephemeral local PostgreSQL (pgserver, includes pgvector) and COPILOT_TEST_DATABASE_URL set.
For laptops without Docker. CI uses a pgvector service container instead.   Usage: python scripts/with_local_pg.py pytest -q"""
import os
import subprocess
import sys
import tempfile

import pgserver

db = pgserver.get_server(tempfile.mkdtemp(prefix="copilot-pg"))
try:
    sys.exit(subprocess.run(sys.argv[1:], env={**os.environ, "COPILOT_TEST_DATABASE_URL": db.get_uri()}).returncode)  # noqa: S603
finally:
    db.cleanup()
