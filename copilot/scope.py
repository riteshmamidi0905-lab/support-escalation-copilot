"""Signed trusted scope (ADR-0012).

A Scope says "this database session may see account X, for case Y, until time T". It is minted by the case service (the only holder of the signing
secret) from a case row that the trusted intake role created from a TICKET ROW — never from model output, ticket text or request parameters.
The database independently verifies the signature (migration 004), so even code that can run SQL as the application role cannot forge or alter a
scope: it does not have the secret and there is no signing function it can call.

Message signed: ``v1|account|case|exp|key_id`` (HMAC-SHA256, hex). Token lifetime is short; a stolen unexpired token is usable for its remaining
lifetime by someone who can already run SQL as the app role — that residual risk is stated in docs/tenant-isolation.md.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from dataclasses import dataclass

_ACC = re.compile(r"^ACC-[0-9]{4,6}$")
_CASE = re.compile(r"^CASE-[0-9]{4,8}$")
DEFAULT_TTL_S = 120
MIN_SECRET_BYTES = 32


class ScopeError(Exception):
    """Raised for every scope failure. Callers must treat any ScopeError as 'no data'."""


@dataclass(frozen=True)
class Scope:
    account_id: str
    case_id: str
    exp: int
    key_id: str
    sig: str

    def message(self) -> bytes:
        return f"v1|{self.account_id}|{self.case_id}|{self.exp}|{self.key_id}".encode()

    def settings(self) -> dict:
        """The five session settings the database reads. Set transaction-locally, never session-wide."""
        return {"app.scope_account": self.account_id, "app.scope_case": self.case_id, "app.scope_exp": str(self.exp), "app.scope_key": self.key_id, "app.scope_sig": self.sig}


class Signer:
    def __init__(self, secret: str, key_id: str = "k1", clock=time.time):
        if len(secret.encode()) < MIN_SECRET_BYTES:
            raise ScopeError(f"scope secret must be at least {MIN_SECRET_BYTES} bytes")
        self._secret, self.key_id, self._clock = secret.encode(), key_id, clock

    def _sig(self, account_id: str, case_id: str, exp: int, key_id: str) -> str:
        return hmac.new(self._secret, f"v1|{account_id}|{case_id}|{exp}|{key_id}".encode(), hashlib.sha256).hexdigest()

    def mint(self, account_id: str, case_id: str, ttl_s: int = DEFAULT_TTL_S) -> Scope:
        if not _ACC.match(account_id or ""):
            raise ScopeError("malformed account id")
        if not _CASE.match(case_id or ""):
            raise ScopeError("malformed case id")
        if not 1 <= ttl_s <= 900:
            raise ScopeError("ttl must be between 1 and 900 seconds")
        exp = int(self._clock()) + ttl_s
        return Scope(account_id, case_id, exp, self.key_id, self._sig(account_id, case_id, exp, self.key_id))

    def verify(self, scope: Scope) -> None:
        """Application-side check (the database repeats it independently). Raises ScopeError; never returns a boolean that could be ignored."""
        if scope.key_id != self.key_id:
            raise ScopeError("unknown key id")
        if not hmac.compare_digest(self._sig(scope.account_id, scope.case_id, scope.exp, scope.key_id), scope.sig):
            raise ScopeError("bad signature")
        if scope.exp < int(self._clock()):
            raise ScopeError("scope expired")


class ScopeGuard:
    """Application-layer defence in depth: refuses any request whose account differs from the verified scope, before SQL is ever built."""

    def __init__(self, signer: Signer):
        self.signer = signer

    def check(self, scope: Scope, requested_account: str | None = None) -> Scope:
        self.signer.verify(scope)
        if requested_account is not None and requested_account != scope.account_id:
            raise ScopeError(f"requested account {requested_account!r} is outside the case scope")
        return scope
