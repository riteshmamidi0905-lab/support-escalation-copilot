"""Mock identity provider: signed identity claims. (Customer identity/roles are 'mock claims' in the specification; this is the mock.)

An approval is only as good as the identity behind it. Identities are HMAC-signed, expiring claims: `Identity(kind, id, role, exp, key_id, sig, accounts)`. `accounts` is the
set of accounts the holder is entitled to work on (`("*",)` = all; agents and system identities hold none). The signing secret is held by the identity authority (and the verifying
control service); agent/model-facing code never has it, so it cannot mint, alter or upgrade an identity or its grants. HMAC is symmetric, so the verifier could also mint: the control
service is therefore trusted code (a deployment with a real IdP would verify asymmetric signatures instead).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

ROLES = ("tier1", "tier2_engineer", "support_manager", "on_call_sre", "finance", "auditor")
KINDS = ("user", "agent", "system")
_ID = re.compile(r"^[A-Za-z0-9._:@-]{1,64}$")
_ACC = re.compile(r"^ACC-[0-9]{4,6}$")
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
    accounts: tuple[str, ...] = ()

    def actor(self) -> dict:
        return {"kind": self.kind, "id": self.id, "role": self.role}

    def covers(self, account_id: str | None) -> bool:
        """True iff this identity is entitled to work on `account_id`. Fails closed: no account (None) is covered only by an all-accounts grant."""
        if self.kind != "user":
            return False
        if "*" in self.accounts:
            return True
        return account_id is not None and account_id in self.accounts


class IdentityAuthority:
    def __init__(self, secret: str, key_id: str = "i1", clock: Callable[[], float] = time.time):
        if len(secret.encode()) < MIN_SECRET_BYTES:
            raise IdentityError(f"identity secret must be at least {MIN_SECRET_BYTES} bytes")
        self._secret, self.key_id, self._clock = secret.encode(), key_id, clock

    def _sig(self, kind: str, id_: str, role: str | None, exp: int, key_id: str, accounts: tuple[str, ...]) -> str:
        return hmac.new(self._secret, f"id2|{kind}|{id_}|{role or ''}|{exp}|{key_id}|{','.join(accounts)}".encode(), hashlib.sha256).hexdigest()

    def mint(self, kind: str, id_: str, role: str | None = None, ttl_s: int = 900, accounts: tuple[str, ...] | None = None) -> Identity:
        if accounts is None:
            accounts = ("*",) if kind == "user" else ()
        accounts = tuple(sorted(set(accounts)))
        if kind not in KINDS or not _ID.match(id_ or "") or (kind == "user" and role not in ROLES) or (kind != "user" and (role is not None or accounts)) or not 1 <= ttl_s <= 86_400:
            raise IdentityError("malformed identity request")
        if "*" in accounts and accounts != ("*",) or any(a != "*" and not _ACC.match(a) for a in accounts):
            raise IdentityError("malformed account grants")
        if kind == "user" and not accounts:
            raise IdentityError("a user identity needs at least one account grant")
        exp = int(self._clock()) + ttl_s
        return Identity(kind, id_, role, exp, self.key_id, self._sig(kind, id_, role, exp, self.key_id, accounts), accounts)

    def verify(self, ident: Identity) -> Identity:
        """Raises IdentityError for anything that is not a valid, unexpired identity minted by this authority (grants included: they are signed)."""
        if not isinstance(ident, Identity) or ident.kind not in KINDS or ident.key_id != self.key_id or not isinstance(ident.accounts, tuple):
            raise IdentityError("unknown identity")
        if not hmac.compare_digest(self._sig(ident.kind, ident.id, ident.role, ident.exp, ident.key_id, ident.accounts), ident.sig):
            raise IdentityError("bad identity signature")
        if ident.exp < int(self._clock()):
            raise IdentityError("identity expired")
        return ident

    # a serialised form for session cookies (the signature is what makes it trustworthy)
    @staticmethod
    def dump(i: Identity) -> str:
        import base64
        import json
        return base64.urlsafe_b64encode(json.dumps([i.kind, i.id, i.role, i.exp, i.key_id, i.sig, list(i.accounts)]).encode()).decode()

    @staticmethod
    def load(token: str) -> Identity:
        import base64
        import json
        try:
            k, i, r, e, kid, sig, acc = json.loads(base64.urlsafe_b64decode(token.encode()))
            return Identity(k, i, r, int(e), kid, sig, tuple(acc))
        except Exception as ex:                                    # noqa: BLE001
            raise IdentityError("unreadable identity token") from ex
