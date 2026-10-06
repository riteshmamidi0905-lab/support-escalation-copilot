"""Canary-based secret leak detection for invariant I4.

Technique: plant unmistakable canary secrets (in env, config, tickets, exception messages), run the code path, capture EVERYTHING it logged or
emitted, and fail if any canary substring appears. A scanner that cannot fail proves nothing, so tests/test_secret_canaries.py includes a
deliberately leaky logger as a positive control.
"""
from __future__ import annotations

import io
import json
import logging
from contextlib import contextmanager

CANARIES = {
    "api_key": "sk-CANARYAPIKEY0123456789abcdef",
    "db_password": "CANARY-db-pass-7f3a9c",
    "bearer": "CANARYbearertoken1234567890",
    "env_secret": "CANARY-env-secret-55aa",
    "pem": "CANARYPEMBODYabcdef==",
}
CANARY_DSN = f"postgresql://copilot:{CANARIES['db_password']}@db.example.test:5432/copilot"


class Capture:
    def __init__(self):
        self.stream = io.StringIO()
        self.records: list = []

    @property
    def text(self) -> str:
        return self.stream.getvalue() + "\n".join(json.dumps(r, default=str) for r in self.records)

    def leaked(self, canaries=None) -> list:
        t = self.text
        return sorted(k for k, v in (canaries or CANARIES).items() if v in t)


@contextmanager
def capture_logs(level=logging.DEBUG):
    cap = Capture()
    h = logging.StreamHandler(cap.stream)
    h.setLevel(level)
    root = logging.getLogger()
    old = root.level
    root.addHandler(h)
    root.setLevel(level)
    try:
        yield cap
    finally:
        root.removeHandler(h)
        root.setLevel(old)
