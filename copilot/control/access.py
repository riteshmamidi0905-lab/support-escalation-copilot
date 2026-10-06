"""Server-side authorization for READING the control plane (cases, case files, audit). The browser, a URL, a request parameter or a cookie is never an authority: every read verifies a signed
identity, resolves the case's account from the database, and checks the identity's signed account grants. Failure is indistinguishable from 'not found' (no existence oracle).

Closes A-I2-10: audit browsing/querying obeys the same tenant/case boundary as everything else. A global view (search without a case, chain verification) exists only for the `auditor`
role with an all-accounts grant; every other identity can only ever see events of accounts it is granted."""
from __future__ import annotations

import json
from typing import Any

from .audit import AuditLog, VerifyResult
from .identity import Identity, IdentityAuthority, IdentityError


class AccessDenied(Exception):
    """Deliberately uninformative: unknown case, someone else's case and a forged identity all look the same to the caller."""

    def __init__(self, reason: str = "NOT_FOUND_OR_FORBIDDEN"):
        super().__init__("not found or not permitted")
        self.reason = reason


_SQL_CASE_ACCOUNT = "SELECT account_id FROM copilot.case_runs WHERE case_id = %s"
_SQL_OPS = "SELECT seq, ts, kind, request_id, invocation_id, action_id, approval_id, duration_ms, attrs FROM copilot.ops_events WHERE case_id = %s ORDER BY seq LIMIT %s"
_SQL_ART = "SELECT kind, status, body, body_sha256, created_by, created_at FROM copilot.case_artifacts WHERE case_id = %s ORDER BY created_at"
_SQL_LIST = ("SELECT case_id, account_id, ticket_id, state, outcome, disposition, updated_at, created_at FROM copilot.case_runs WHERE (%(all)s::boolean OR account_id = ANY(%(accts)s::text[])) "
             "AND (%(state)s::text IS NULL OR state = %(state)s::text) ORDER BY updated_at DESC LIMIT %(limit)s::int")
_SQL_PENDING = ("SELECT a.approval_id, a.case_id, a.account_id, a.action_type, a.required_role, a.created_at, a.expires_at FROM copilot.approvals a WHERE a.status = 'pending' "
                "AND (%(all)s::boolean OR a.account_id = ANY(%(accts)s::text[])) ORDER BY a.created_at LIMIT 200")
_SQL_SEARCH = ("SELECT seq, body, hash FROM copilot.audit_events WHERE (%(all)s::boolean OR account_id = ANY(%(accts)s::text[])) AND (%(etype)s::text IS NULL OR event_type = %(etype)s::text) "
               "AND (%(case)s::text IS NULL OR case_id = %(case)s::text) AND seq > %(after)s::bigint ORDER BY seq LIMIT %(limit)s::int")


class Access:
    def __init__(self, pool, authority: IdentityAuthority, audit: AuditLog | None = None):
        self.pool, self.authority, self.audit = pool, authority, audit

    def verified(self, ident: Any) -> Identity:
        try:
            i = self.authority.verify(ident)
        except IdentityError as e:
            raise AccessDenied("IDENTITY_INVALID") from e
        if i.kind != "user":
            raise AccessDenied("NOT_A_HUMAN_OPERATOR")
        return i

    def case_account(self, case_id: str) -> str | None:
        with self.pool.connection() as c:
            r = c.execute(_SQL_CASE_ACCOUNT, (case_id,)).fetchone()
            c.rollback()
        return r[0] if r else None

    def authorize_case(self, ident: Any, case_id: str) -> tuple[Identity, str]:
        """Returns (verified identity, account id) or raises AccessDenied. Used by the case file, approval, amend, review AND audit paths."""
        i = self.verified(ident)
        acc = self.case_account(case_id) if isinstance(case_id, str) and len(case_id) <= 20 else None
        if acc is None or not i.covers(acc):
            raise AccessDenied()
        return i, acc

    def granted_accounts(self, i: Identity) -> list[str] | None:
        """None = all accounts (an all-accounts grant)."""
        return None if "*" in i.accounts else list(i.accounts)

    # ---- audit ---------------------------------------------------------------------------------------------------------------------------
    def audit_for_case(self, ident: Any, case_id: str, limit: int = 500) -> list[dict[str, Any]]:
        i, _acc = self.authorize_case(ident, case_id)
        return self._events(i, case=case_id, limit=limit)

    def audit_search(self, ident: Any, *, event_type: str | None = None, case_id: str | None = None, after_seq: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        """Search across the audit log. Always filtered to the caller's granted accounts IN SQL; a case filter outside the grants simply returns nothing."""
        i = self.verified(ident)
        if case_id is not None:
            self.authorize_case(i, case_id)
        return self._events(i, case=case_id, etype=event_type, after=after_seq, limit=limit)

    def _events(self, i: Identity, case: str | None = None, etype: str | None = None, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        accts = self.granted_accounts(i)
        params = {"all": accts is None, "accts": accts or [], "etype": etype, "case": case, "after": int(after), "limit": max(1, min(int(limit), 1000))}
        with self.pool.connection() as c:
            rows = c.execute(_SQL_SEARCH, params).fetchall()
            c.rollback()
        return [{**json.loads(b), "hash": h, "seq": sq} for sq, b, h in rows]

    def ops_for_case(self, ident: Any, case_id: str, limit: int = 500) -> list[dict[str, Any]]:
        """Operational telemetry of one case (model calls, dependency calls, retrieval fallbacks, recovery claims...), under the same case authorization as the audit trail."""
        self.authorize_case(ident, case_id)
        with self.pool.connection() as c:
            rows = c.execute(_SQL_OPS, (case_id, max(1, min(int(limit), 1000)))).fetchall()
            c.rollback()
        return [{"seq": r[0], "ts": r[1].isoformat(), "kind": r[2], "request_id": r[3], "invocation_id": r[4], "action_id": r[5], "approval_id": r[6], "duration_ms": r[7], "attrs": r[8]} for r in rows]

    def trace_for_case(self, ident: Any, case_id: str) -> dict[str, Any]:
        """request -> case -> model invocation -> action -> approval -> execution -> audit, joined by the correlation ids both streams carry."""
        audit, ops = self.audit_for_case(ident, case_id), self.ops_for_case(ident, case_id)
        chain: dict[str, dict[str, Any]] = {}
        for e in audit:
            k = e["correlation"].get("action_id")
            if k:
                row = chain.setdefault(k, {"action_id": k, "request_ids": set(), "invocation_ids": set(), "approval_ids": set(), "audit_types": []})
                row["request_ids"].add(e["correlation"].get("request_id"))
                row["invocation_ids"].add(e["correlation"].get("invocation_id"))
                row["approval_ids"].add(e["correlation"].get("approval_id"))
                row["audit_types"].append(e["type"])
        for row in chain.values():
            for k in ("request_ids", "invocation_ids", "approval_ids"):
                row[k] = sorted(x for x in row[k] if x)
        return {"case_id": case_id, "audit": audit, "ops": ops, "actions": list(chain.values())}

    def artifacts_for_case(self, ident: Any, case_id: str) -> list[dict[str, Any]]:
        """Internal drafts/notes of a case (never transmitted: the table has no 'sent' state)."""
        self.authorize_case(ident, case_id)
        with self.pool.connection() as c:
            rows = c.execute(_SQL_ART, (case_id,)).fetchall()
            c.rollback()
        return [{"kind": k, "status": st, "body": b, "sha256": h, "created_by": by, "created_at": at} for k, st, b, h, by, at in rows]

    def list_cases(self, ident: Any, state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        i = self.verified(ident)
        accts = self.granted_accounts(i)
        with self.pool.connection() as c:
            rows = c.execute(_SQL_LIST, {"all": accts is None, "accts": accts or [], "state": state, "limit": max(1, min(int(limit), 500))}).fetchall()
            c.rollback()
        return [dict(zip(("case_id", "account_id", "ticket_id", "state", "outcome", "disposition", "updated_at", "created_at"), r, strict=True)) for r in rows]

    def pending_approvals(self, ident: Any) -> list[dict[str, Any]]:
        """Pending approvals in the caller's granted accounts (the UI further narrows to the caller's role; the SERVER decides again on every decision)."""
        i = self.verified(ident)
        accts = self.granted_accounts(i)
        with self.pool.connection() as c:
            rows = c.execute(_SQL_PENDING, {"all": accts is None, "accts": accts or []}).fetchall()
            c.rollback()
        return [dict(zip(("approval_id", "case_id", "account_id", "action_type", "required_role", "created_at", "expires_at"), r, strict=True)) for r in rows]

    def verify_chain(self, ident: Any) -> VerifyResult:
        """The chain is global, so verifying it is a global act: only an auditor with an all-accounts grant may do it."""
        i = self.verified(ident)
        if i.role != "auditor" or "*" not in i.accounts or self.audit is None:
            raise AccessDenied("AUDITOR_ONLY")
        return self.audit.verify()
