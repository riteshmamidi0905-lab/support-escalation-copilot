"""Canonical serialisation and action identity.

Two serialisations of the SAME action (key order, whitespace, escape forms, `5` vs `5.0`) must give the same bytes; any material change (a parameter, the case, the
account, the role, an evidence reference, the idempotency key, the action id) must give different bytes. Rules:
  * objects: keys are strings, sorted by code point; no insignificant whitespace; UTF-8 without \\u escapes for printable characters (JSON parsing already folds escape forms);
  * numbers: integral floats become ints (5.0 -> 5), other floats use Python's shortest round-trip repr; NaN/Infinity are rejected; booleans stay booleans (True is not 1);
  * strings are NOT Unicode-normalised: two different code point sequences are two different actions (normalising would let one approved string execute as another);
  * lone surrogates and non-JSON types are rejected.
The hash is domain-separated (`scec.action.v1`) so an action hash can never be confused with any other hash in the system.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

DOMAIN = b"scec.action.v1\x00"


class CanonicalError(ValueError):
    pass


def _norm(v: Any, depth: int = 0) -> Any:
    if depth > 12:
        raise CanonicalError("nesting too deep")
    if v is None or isinstance(v, (bool, str)):
        if isinstance(v, str):
            try:
                v.encode("utf-8")
            except UnicodeEncodeError as e:
                raise CanonicalError("string contains a lone surrogate") from e
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if not math.isfinite(v):
            raise CanonicalError("NaN/Infinity are not allowed")
        return int(v) if v.is_integer() and abs(v) < 2 ** 53 else v
    if isinstance(v, (list, tuple)):
        return [_norm(x, depth + 1) for x in v]
    if isinstance(v, dict):
        if not all(isinstance(k, str) for k in v):
            raise CanonicalError("object keys must be strings")
        return {k: _norm(x, depth + 1) for k, x in v.items()}
    raise CanonicalError(f"type {type(v).__name__} is not canonicalisable")


def canonical_json(obj: Any) -> bytes:
    return json.dumps(_norm(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def material(action: dict[str, Any], account_id: str) -> dict[str, Any]:
    """Everything that makes an action THIS action. The account is not a field the model supplies; it is bound here from the trusted case."""
    return {"v": 1, "account_id": account_id, "action_id": action["action_id"], "case_id": action["case_id"], "type": action["type"], "required_role": action["required_role"],
            "requested_by": action["requested_by"], "idempotency_key": action["idempotency_key"], "evidence_refs": sorted(action["evidence_refs"]), "params": action["params"]}


def action_canonical(action: dict[str, Any], account_id: str) -> bytes:
    return canonical_json(material(action, account_id))


def action_hash(action: dict[str, Any], account_id: str) -> str:
    return hashlib.sha256(DOMAIN + action_canonical(action, account_id)).hexdigest()


def hash_of_canonical(canonical: bytes) -> str:
    return hashlib.sha256(DOMAIN + canonical).hexdigest()
