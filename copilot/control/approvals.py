"""Approval service (resolves R-1 at THIS project's boundary; the frozen runtime is unchanged).

An approval record binds: approval id · case · account · the exact typed action (canonical bytes) and its canonical hash · required role · requester · approver ·
created / expires / decision / decided times · the evidence the approver was shown. Everything fails CLOSED: missing, wrong role, wrong tenant, wrong case, expired, denied,
undecided, changed action, or replay against another action all refuse. Timeout = deny. The proposer (an agent) can never approve: approvers must be signed `user`
identities, different from the requester, holding EXACTLY the required role.
"""
from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from copilot.redact import scrub

from .actions import ValidatedAction
from .audit import AuditLog
from .canonical import hash_of_canonical
from .clock import SystemClock
from .identity import Identity, IdentityAuthority, IdentityError
from .policy import Decision, PolicyDecision

DEFAULT_TTL_S = 900


class ApprovalError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class Approval:
    approval_id: str
    case_id: str
    account_id: str
    action_id: str
    action_type: str
    action_hash: str
    action_canonical: str
    required_role: str
    requester_id: str
    evidence: dict[str, Any]
    created_at: datetime
    expires_at: datetime
    status: str
    approver_id: str | None
    approver_role: str | None
    decision_reason: str | None
    decided_at: datetime | None


_COLS = "approval_id, case_id, account_id, action_id, action_type, action_hash, action_canonical, required_role, requester_id, evidence, created_at, expires_at, status, approver_id, approver_role, decision_reason, decided_at"


_SQL_GET = "SELECT " + _COLS + " FROM copilot.approvals WHERE approval_id = %s"
_SQL_GET_LOCK = _SQL_GET + " FOR UPDATE"
_SQL_OPEN = "SELECT " + _COLS + " FROM copilot.approvals WHERE account_id = %s AND action_hash = %s AND status = 'pending'"


def _row(r) -> Approval:
    return Approval(*r)


class ApprovalService:
    def __init__(self, pool, audit: AuditLog, authority: IdentityAuthority, clock=None, events=None):
        self.pool, self.audit, self.authority, self.clock, self.events = pool, audit, authority, clock or SystemClock(), events

    # ---- request ---------------------------------------------------------------------------------------------------------------------------
    def request(self, action: ValidatedAction, account_id: str, decision: PolicyDecision, requester: Identity, evidence: dict[str, Any], ttl_s: int = DEFAULT_TTL_S,
                correlation: dict[str, Any] | None = None) -> Approval:
        try:
            self.authority.verify(requester)
        except IdentityError as e:
            raise ApprovalError("IDENTITY_INVALID", str(e)) from e
        if requester.kind != "agent":
            raise ApprovalError("REQUESTER_MUST_BE_THE_AGENT")
        if decision.decision is not Decision.REQUIRE_APPROVAL or decision.required_role != action.required_role:
            raise ApprovalError("POLICY_DID_NOT_REQUIRE_APPROVAL")
        now = self.clock.now()
        canon = action.canonical(account_id)
        h = hash_of_canonical(canon)
        aid = "APR-" + secrets.token_hex(8)
        try:
            with self.pool.connection() as c, c.transaction():
                c.execute("INSERT INTO copilot.approvals (approval_id, case_id, account_id, action_id, action_type, action_hash, action_canonical, required_role, requester_kind, requester_id, evidence, created_at, expires_at, status) "
                          "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'agent',%s,%s,%s,%s,'pending')",
                          (aid, action.case_id, account_id, action.action_id, action.type, h, canon.decode(), action.required_role, requester.id, Jsonb(scrub(evidence)), now, now + timedelta(seconds=ttl_s)))
        except psycopg.errors.UniqueViolation:
            with self.pool.connection() as c:
                r = c.execute(_SQL_OPEN, (account_id, h)).fetchone()
                c.rollback()
            if r is None:
                raise
            return _row(r)
        corr = {**(correlation or {}), "action_id": action.action_id, "action_hash": h, "approval_id": aid}
        self.audit.append("approval_requested", action.case_id, account_id, requester.actor(), corr, {"action_type": action.type, "required_role": action.required_role, "expires_at": (now + timedelta(seconds=ttl_s)).isoformat()})
        if self.events:
            self.events.emit("approval_requested", **corr, case_id=action.case_id, required_role=action.required_role)
        return self.get(aid)

    def get(self, approval_id: str) -> Approval | None:
        with self.pool.connection() as c:
            r = c.execute(_SQL_GET, (approval_id,)).fetchone()
            c.rollback()
        return _row(r) if r else None

    # ---- decide ----------------------------------------------------------------------------------------------------------------------------
    def decide(self, approval_id: str, approver: Identity, verdict: str, reason: str = "", correlation: dict[str, Any] | None = None) -> Approval:
        if verdict not in ("approve", "deny"):
            raise ApprovalError("BAD_VERDICT")
        now = self.clock.now()
        a = self.get(approval_id)
        if a is None:
            raise ApprovalError("APPROVAL_NOT_FOUND")
        corr = {**(correlation or {}), "action_id": a.action_id, "action_hash": a.action_hash, "approval_id": a.approval_id}

        def refuse(code: str, detail: str = ""):
            self.audit.append("action_refused", a.case_id, a.account_id, {"kind": "user", "id": getattr(approver, "id", "?"), "role": getattr(approver, "role", None)}, corr,
                              {"stage": "approval_decision", "reasons": [code]})
            if self.events:
                self.events.emit("approval_decision_refused", **corr, case_id=a.case_id, reasons=[code])
            raise ApprovalError(code, detail)

        try:
            self.authority.verify(approver)
        except IdentityError as e:
            refuse("IDENTITY_INVALID", str(e))
        if approver.kind != "user":
            refuse("NOT_A_HUMAN_APPROVER")                      # an agent (the model) can never approve, including its own action
        if approver.id == a.requester_id:
            refuse("SELF_APPROVAL")
        if approver.role != a.required_role:
            refuse("WRONG_ROLE", f"requires {a.required_role}")
        with self.pool.connection() as c, c.transaction():
            r = c.execute(_SQL_GET_LOCK, (approval_id,)).fetchone()
            cur = _row(r)
            if cur.status != "pending":
                pass
            elif now > cur.expires_at:
                c.execute("UPDATE copilot.approvals SET status='expired', decided_at=%s, decision_reason='expired before a decision' WHERE approval_id=%s", (now, approval_id))
                cur = None
            else:
                c.execute("UPDATE copilot.approvals SET status=%s, approver_id=%s, approver_role=%s, decision_reason=%s, decided_at=%s WHERE approval_id=%s",
                          ("approved" if verdict == "approve" else "denied", approver.id, approver.role, scrub(reason)[:300], now, approval_id))
        if cur is None:
            self.audit.append("approval_expired", a.case_id, a.account_id, {"kind": "system", "id": "approval-service", "role": None}, corr, {"reasons": ["EXPIRED_BEFORE_DECISION"]})
            raise ApprovalError("APPROVAL_EXPIRED")
        if cur.status != "pending" and (cur.approver_id is not None or cur.status == "expired"):
            refuse("APPROVAL_ALREADY_FINAL", cur.status)
        done = self.get(approval_id)
        self.audit.append("approval_decided", a.case_id, a.account_id, approver.actor(), corr, {"verdict": verdict, "required_role": a.required_role, "rationale": scrub(reason)[:300]})
        if self.events:
            self.events.emit("approval_decided", **corr, case_id=a.case_id, verdict=verdict, approver=approver.id, role=approver.role)
        return done

    # ---- authorise execution ---------------------------------------------------------------------------------------------------------------
    def check_for_execution(self, approval_id: str | None, action: ValidatedAction, account_id: str) -> Approval:
        """Return the approval iff it authorises EXACTLY this action in this tenant and case right now; otherwise raise ApprovalError (fail closed)."""
        if not approval_id:
            raise ApprovalError("APPROVAL_REQUIRED")
        a = self.get(approval_id)
        if a is None:
            raise ApprovalError("APPROVAL_NOT_FOUND")
        if a.account_id != account_id:
            raise ApprovalError("TENANT_MISMATCH")
        if a.case_id != action.case_id:
            raise ApprovalError("CASE_MISMATCH")
        if a.status == "pending":
            if self.clock.now() > a.expires_at:
                self.expire_due()
                raise ApprovalError("APPROVAL_EXPIRED")
            raise ApprovalError("APPROVAL_PENDING")
        if a.status == "denied":
            raise ApprovalError("APPROVAL_DENIED")
        if a.status == "expired" or self.clock.now() > a.expires_at:
            raise ApprovalError("APPROVAL_EXPIRED")
        h = action.hash(account_id)
        if hash_of_canonical(a.action_canonical.encode()) != a.action_hash:
            raise ApprovalError("APPROVAL_RECORD_CORRUPT")
        if not hmac.compare_digest(a.action_hash, h):
            raise ApprovalError("ACTION_CHANGED", "the action differs from the one that was approved")
        if a.approver_role != action.required_role or a.approver_role != a.required_role:
            raise ApprovalError("ROLE_MISMATCH")
        return a

    def expire_due(self) -> list[str]:
        """Undecided past expiry => expired (= denied). Returns the ids that were expired."""
        now = self.clock.now()
        done = []
        with self.pool.connection() as c, c.transaction():
            rows = c.execute("UPDATE copilot.approvals SET status='expired', decided_at=%s, decision_reason='timed out without a decision' WHERE status='pending' AND expires_at < %s "
                             "RETURNING approval_id, case_id, account_id, action_id, action_hash", (now, now)).fetchall()
        for aid, case_id, acc, act_id, h in rows:
            self.audit.append("approval_expired", case_id, acc, {"kind": "system", "id": "approval-service", "role": None}, {"approval_id": aid, "action_id": act_id, "action_hash": h}, {"reasons": ["TIMED_OUT_TREATED_AS_DENIED"]})
            done.append(aid)
        return done
