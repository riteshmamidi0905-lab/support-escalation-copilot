"""Structured control-plane events (what was proposed / what policy decided / was approval required / who approved or denied / what executed / what happened),
correlated by run, request, case, action and approval ids. Never contains hidden reasoning: only decisions, reason codes and short rationales. Everything is scrubbed."""
from __future__ import annotations

import json
import logging
from collections import deque
from typing import Any

from copilot.redact import HIDDEN_REASONING_KEYS, scrub

log = logging.getLogger("copilot.control")


class ControlEvents:
    def __init__(self, clock=None, keep: int = 2000):
        self.clock, self.recent = clock, deque(maxlen=keep)

    def emit(self, kind: str, **fields: Any) -> dict[str, Any]:
        bad = {k for k in fields if k.lower() in HIDDEN_REASONING_KEYS}
        if bad:
            raise ValueError(f"hidden reasoning must not be logged ({sorted(bad)})")
        ev = scrub({"kind": kind, **{k: v for k, v in fields.items() if v is not None}})
        if "action_hash" in ev:
            ev["action_hash"] = str(ev["action_hash"])[:12]
        self.recent.append(ev)
        log.info(json.dumps(ev, sort_keys=True, default=str))
        return ev


def timeline(events: list[dict[str, Any]]) -> list[str]:
    """Human-readable explanation built ONLY from audit events: no reasoning, just what happened, in order."""
    out = []
    for e in events:
        p, who = e["payload"], f"{e['actor']['kind']}:{e['actor']['id']}" + (f" ({e['actor']['role']})" if e["actor"].get("role") else "")
        t = e["type"]
        if t == "action_proposed":
            out.append(f"{who} proposed {p.get('action_type')} [{e['correlation'].get('action_id')}]")
        elif t == "policy_decided":
            out.append(f"policy: {p.get('decision')} ({', '.join(p.get('reasons', []))}); approval required: {p.get('requires_approval')}; evidence: {p.get('sufficiency')}")
        elif t == "approval_requested":
            out.append(f"approval requested from role {p.get('required_role')} until {p.get('expires_at')} [{e['correlation'].get('approval_id')}]")
        elif t == "approval_decided":
            out.append(f"{who} {'approved' if p.get('verdict') == 'approve' else 'denied'} the approval")
        elif t == "approval_expired":
            out.append("approval expired without a decision (treated as denied)")
        elif t == "execution_attempted":
            out.append(f"execution attempt {p.get('attempt')} against {p.get('system')}")
        elif t == "action_executed":
            out.append(f"execution result: {p.get('status')}")
        elif t == "draft_created":
            out.append(f"internal {p.get('kind')} stored (never sent)")
        else:
            out.append(f"{t}: {', '.join(map(str, p.get('reasons', [])))}" if p.get("reasons") else t)
    return out
