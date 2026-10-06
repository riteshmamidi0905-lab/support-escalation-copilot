"""Mock identity provider: signed identity claims. (Customer identity/roles are 'mock claims' in the specification; this is the mock.)

An approval is only as good as the identity behind it. Identities are HMAC-signed, expiring claims: `Identity(kind, id, role, exp, sig)`. The signing secret is held by the
identity authority (and the verifying control service); agent/model-facing code never has it, so it cannot mint, alter or upgrade an identity. HMAC is symmetric, so the
verifier could also mint: the control service is therefore trusted code (a deployment with a real IdP would verify asymmetric signatures instead).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

ROLES = ("tier1", "tier2_engineer", "support_manager", "on_call_sre", "finance")
KINDS = ("user", "agent", "system")
_ID = re.compile(r"^[A-Za-z0-9._:@-]{1,64}$")
MIN_SECRET_BYTES = 32


class IdentityError(Exception):
    pass


@dataclass(frozen=True)
class Identity:
    kind: str
    id: str
    role: str | None
    exp: int
    key_id: str
    sig: str

    def message(self) -> bytes:
        return f"id1|{self.kind}|{self.id}|{self.role or ''}|{self.exp}|{self.key_id}".encode()

    def actor(self) -> dict:
        return {"kind": self.kind, "id": self.id, "role": self.role}


class IdentityAuthority:
    def __init__(self, secret: str, key_id: str = "i1", clock: Callable[[], float] = time.time):
        if len(secret.encode()) < MIN_SECRET_BYTES:
            raise IdentityError(f"identity secret must be at least {MIN_SECRET_BYTES} bytes")
        self._secret, self.key_id, self._clock = secret.encode(), key_id, clock

    def _sig(self, kind: str, id_: str, role: str | None, exp: int, key_id: str) -> str:
        return hmac.new(self._secret, f"id1|{kind}|{id_}|{role or ''}|{exp}|{key_id}".encode(), hashlib.sha256).hexdigest()

    def mint(self, kind: str, id_: str, role: str | None = None, ttl_s: int = 900) -> Identity:
        if kind not in KINDS or not _ID.match(id_ or "") or (kind == "user" and role not in ROLES) or (kind != "user" and role is not None) or not 1 <= ttl_s <= 86_400:
            raise IdentityError("malformed identity request")
        exp = int(self._clock()) + ttl_s
        return Identity(kind, id_, role, exp, self.key_id, self._sig(kind, id_, role, exp, self.key_id))

    def verify(self, ident: Identity) -> Identity:
        """Raises IdentityError for anything that is not a valid, unexpired identity minted by this authority."""
        if not isinstance(ident, Identity) or ident.kind not in KINDS or ident.key_id != self.key_id:
            raise IdentityError("unknown identity")
        if not hmac.compare_digest(self._sig(ident.kind, ident.id, ident.role, ident.exp, ident.key_id), ident.sig):
            raise IdentityError("bad identity signature")
        if ident.exp < int(self._clock()):
            raise IdentityError("identity expired")
        return ident
