"""Secret redaction for logs, traces and audit payloads (invariant I4: no secret appears in logs).

Builds on the frozen runtime's `agent.security.redact` (API-key / token patterns) and adds the formats THIS project handles:
database URLs with passwords, Bearer tokens, `NAME=value` environment assignments for secret-looking names, and PEM private keys.
Redaction is a last line of defence; the first is never putting secrets into messages at all (see docs/threat-model.md T-S1..S4).
"""
from __future__ import annotations

import re
from typing import Any

from agent.security import redact as _base_redact

REDACTED = "[REDACTED]"
_PATTERNS = [
    re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)(@)"),                               # scheme://user:PASSWORD@host
    re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]{8,})"),                                          # Authorization: Bearer <token>
    re.compile(r"(?i)\b((?:[A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY)[A-Z0-9_]*)\s*[=:]\s*)(\S{4,})"),  # ENV_STYLE=value
    re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)(.*?)(-----END [A-Z ]*PRIVATE KEY-----)", re.S),
]


def redact(text: str) -> str:
    out = _base_redact(text)
    for p in _PATTERNS:
        out = p.sub(lambda m: m.group(1) + REDACTED + (m.group(3) if m.lastindex and m.lastindex >= 3 else ""), out)
    return out


def redact_obj(v: Any) -> Any:
    """Recursively redact every string in a JSON-like structure (used before anything is logged or stored in an audit payload)."""
    if isinstance(v, str):
        return redact(v)
    if isinstance(v, dict):
        return {k: redact_obj(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [redact_obj(x) for x in v]
    return v
