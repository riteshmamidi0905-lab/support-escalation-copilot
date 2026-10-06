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


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
HIDDEN_REASONING_KEYS = frozenset({"thought", "thoughts", "reasoning", "chain_of_thought", "cot", "scratchpad", "hidden_reasoning", "internal_monologue",
                                   "reasoning_trace"})


def mask_pii(text: str) -> str:
    """Mask e-mail addresses (the only personal identifier this synthetic system carries in free text). Used for audit payloads and control-plane logs."""
    return _EMAIL.sub("[email]", text)


def scrub(v: Any, max_str: int = 300) -> Any:
    """Secrets redacted + e-mail addresses masked + long strings truncated, recursively. Everything written to the audit log or a control-plane log goes through this."""
    if isinstance(v, str):
        out = mask_pii(redact(v))
        return out if len(out) <= max_str else out[:max_str] + f"...[truncated {len(out) - max_str}]"
    if isinstance(v, dict):
        return {k: scrub(x, max_str) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [scrub(x, max_str) for x in v]
    return v
