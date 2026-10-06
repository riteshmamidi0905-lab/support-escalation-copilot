"""Invariant I4 — attempt to leak secrets and prove the detector can fail."""
import logging

import pytest

from copilot.redact import REDACTED, redact, redact_obj
from tests.support.logcapture import CANARIES, CANARY_DSN, capture_logs

pytestmark = pytest.mark.invariant


def test_positive_control_a_leaky_logger_is_detected():          # A-I4-04
    log = logging.getLogger("leaky")
    with capture_logs() as cap:
        log.error("connecting with %s", CANARY_DSN)               # deliberately wrong
        log.info("key=%s", CANARIES["api_key"])
    assert set(cap.leaked()) >= {"db_password", "api_key"}, "scanner failed to see a planted leak: it cannot be trusted"


def test_redaction_removes_every_canary_format():               # A-I4-01
    blob = (f"dsn={CANARY_DSN} Authorization: Bearer {CANARIES['bearer']} API_KEY={CANARIES['api_key']} "
            f"COPILOT_SECRET={CANARIES['env_secret']} -----BEGIN PRIVATE KEY-----{CANARIES['pem']}-----END PRIVATE KEY-----")
    out = redact(blob)
    for name, v in CANARIES.items():
        assert v not in out, f"{name} survived redaction"
    assert REDACTED in out


def test_exception_messages_with_dsn_are_redacted_before_logging():   # A-I4-03
    log = logging.getLogger("copilot.test")
    with capture_logs() as cap:
        try:
            raise RuntimeError(f"could not connect to {CANARY_DSN}")
        except RuntimeError as e:
            log.error("db failure: %s", redact(str(e)))
    assert cap.leaked() == []
    assert "db.example.test" in cap.text              # useful context kept; only the secret is removed


def test_redact_obj_walks_nested_audit_payloads():
    payload = {"headers": {"Authorization": f"Bearer {CANARIES['bearer']}"}, "args": [{"dsn": CANARY_DSN}], "n": 3, "ok": True}
    out = redact_obj(payload)
    assert out["n"] == 3 and out["ok"] is True
    flat = str(out)
    assert CANARIES["bearer"] not in flat and CANARIES["db_password"] not in flat


def test_runtime_tracer_events_do_not_keep_canaries():          # A-I4-01 through the frozen runtime's tracer
    from agent.trace import Tracer
    t = Tracer("r1")
    t.emit("tool_call", tool="x", args={"token": f"token: {CANARIES['env_secret']}", "key": CANARIES["api_key"]})
    assert CANARIES["api_key"] not in str(t.events), "frozen runtime tracer let an sk- style key through"


def test_runtime_tracer_gap_for_project_specific_formats_is_known():
    """The frozen tracer only knows generic patterns; a DSN password passes through it. This documents WHY copilot.redact exists and
    that every audit/log write in this project must go through it (docs/risks.md R-2)."""
    from agent.trace import Tracer
    t = Tracer("r1")
    t.emit("note", dsn=CANARY_DSN)
    assert CANARIES["db_password"] in str(t.events), "if this starts failing the runtime learned DSN redaction; update R-2"
    assert CANARIES["db_password"] not in str(redact_obj(t.events))
