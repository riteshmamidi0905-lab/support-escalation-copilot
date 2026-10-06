"""The action vocabulary, its tiers, and validation. Every action is validated BEFORE any policy is evaluated.

Tiers (ADR-0006):
  READ        catalogue queries and evidence retrieval: no effect, no approval (these are tools, not actions: they never pass through the gateway's write path)
  PROPOSE     draft_reply, add_internal_note: stored as internal case-file artifacts; no external effect; there is no code path that transmits them
  GATED_WRITE escalate_engineering (Tier-2), request_sla_credit (support manager), trigger_resync (on-call SRE): effect only after a role-bound approval
  FORBIDDEN   named requests that exist ONLY so they can be refused and audited; they have no schema, no executor and no handler
There is NO customer-email action. `send_customer_email` and friends are refused as FORBIDDEN; any other unknown type is refused as UNKNOWN.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from copilot import contracts as C
from copilot.db.queries import NAMES as _CATALOGUE
from copilot.redact import redact

from . import canonical


class Tier(StrEnum):
    READ = "READ"
    PROPOSE = "PROPOSE"
    GATED_WRITE = "GATED_WRITE"
    FORBIDDEN = "FORBIDDEN"


# type -> (tier, the ONLY role that may approve it). Exact role: there is no hierarchy ("a manager can approve a re-sync" is false by construction).
ACTIONS: dict[str, tuple[Tier, str]] = {
    "draft_reply": (Tier.PROPOSE, "tier2_engineer"),
    "add_internal_note": (Tier.PROPOSE, "tier2_engineer"),
    "escalate_engineering": (Tier.GATED_WRITE, "tier2_engineer"),
    "request_sla_credit": (Tier.GATED_WRITE, "support_manager"),
    "trigger_resync": (Tier.GATED_WRITE, "on_call_sre"),
}
READ_TOOLS: frozenset[str] = frozenset({*_CATALOGUE, "retrieve_evidence"})
FORBIDDEN: dict[str, str] = {      # request names -> why. Names only: no schema, no executor, nothing to call.
    "send_customer_email": "I3: the system never sends customer email", "send_email": "I3: the system never sends email", "email_customer": "I3: the system never sends customer email",
    "send_reply": "I3: drafts are never transmitted", "send_message": "I3: no outbound customer messaging", "notify_customer": "I3: no outbound customer messaging",
    "execute_sql": "no model-authored SQL", "db_write": "ops database is read-only for the copilot", "update_ticket": "ticketing is read-only except gated escalation",
    "issue_credit": "credits above policy are never requested by support; flag for finance", "request_credit_above_policy": "credits above policy are never requested by support; flag for finance",
    "approve_action": "an action is approved by a human, never by the proposer", "self_approve": "an action is approved by a human, never by the proposer",
    "read_other_account": "I2: cross-tenant access", "cross_account_read": "I2: cross-tenant access", "set_scope": "scope is minted only by trusted intake",
}
MAX_RAW_BYTES = 16_000


class ActionRejected(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


_TOKEN = object()


@dataclass(frozen=True)
class ValidatedAction:
    """The ONLY object the policy engine, approvals and executors accept. It can only be built by `validate_action`, so model-generated text cannot become
    executable parameters without having passed the schema."""
    data: dict[str, Any]
    _tok: object = None

    def __post_init__(self):
        if self._tok is not _TOKEN:
            raise TypeError("ValidatedAction can only be created by validate_action()")

    @property
    def type(self) -> str: return self.data["type"]
    @property
    def tier(self) -> Tier: return ACTIONS[self.type][0]
    @property
    def required_role(self) -> str: return self.data["required_role"]
    @property
    def case_id(self) -> str: return self.data["case_id"]
    @property
    def action_id(self) -> str: return self.data["action_id"]
    @property
    def params(self) -> dict[str, Any]: return copy.deepcopy(self.data["params"])
    @property
    def idempotency_key(self) -> str: return self.data["idempotency_key"]
    @property
    def evidence_refs(self) -> list[str]: return list(self.data["evidence_refs"])

    def canonical(self, account_id: str) -> bytes:
        return canonical.action_canonical(self.data, account_id)

    def hash(self, account_id: str) -> str:
        return canonical.action_hash(self.data, account_id)


def validate_action(raw: Any) -> ValidatedAction:
    """Reject anything that is not exactly one well-formed action of the vocabulary. Raises ActionRejected(code). Echoes no free text from the input (codes + paths only)."""
    if isinstance(raw, (str, bytes)):
        raise ActionRejected("NOT_AN_OBJECT", "an action must be a JSON object, not text")     # free text can never be an action
    if not isinstance(raw, dict):
        raise ActionRejected("NOT_AN_OBJECT")
    try:
        if len(json.dumps(raw, default=str)) > MAX_RAW_BYTES:
            raise ActionRejected("TOO_LARGE")
    except (TypeError, ValueError) as e:
        raise ActionRejected("NOT_SERIALISABLE") from e
    t = raw.get("type")
    if not isinstance(t, str):
        raise ActionRejected("MISSING_TYPE")
    if t in FORBIDDEN:
        raise ActionRejected("FORBIDDEN_ACTION", t)
    if t not in ACTIONS:
        raise ActionRejected("UNKNOWN_ACTION_TYPE", t[:40])
    errs = C.validate_record("action", raw)
    if errs:
        raise ActionRejected("SCHEMA_INVALID", "; ".join("at " + e.split(": ")[1][:60] for e in errs[:3]))
    if raw["required_role"] != ACTIONS[t][1]:
        raise ActionRejected("ROLE_MISMATCH", f"{t} requires {ACTIONS[t][1]}")
    if _has_secret(raw["params"]):
        raise ActionRejected("SECRET_IN_PARAMS", "a parameter contains a credential-like value; it is never stored, approved or executed")
    try:
        canonical.canonical_json(raw)
    except canonical.CanonicalError as e:
        raise ActionRejected("NOT_CANONICALISABLE", str(e)) from e
    return ValidatedAction(copy.deepcopy(raw), _TOKEN)


def _has_secret(v: Any) -> bool:
    if isinstance(v, str):
        return redact(v) != v
    if isinstance(v, dict):
        return any(_has_secret(x) for x in v.values())
    if isinstance(v, list):
        return any(_has_secret(x) for x in v)
    return False


def tier_of_request(name: str) -> Tier | None:
    if name in FORBIDDEN:
        return Tier.FORBIDDEN
    if name in ACTIONS:
        return ACTIONS[name][0]
    if name in READ_TOOLS:
        return Tier.READ
    return None
