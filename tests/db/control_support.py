"""Shared harness for the M3 control-plane tests: a World wiring gateway, approvals, ledger, audit, identity authority, fault-injectable mocks and a fake clock
onto the session's real PostgreSQL environment."""
import json
import secrets
from dataclasses import dataclass

from copilot import contracts as C
from copilot.control import mocks
from copilot.control.approvals import ApprovalService
from copilot.control.audit import AuditLog
from copilot.control.clock import FakeClock
from copilot.control.events import ControlEvents
from copilot.control.gateway import ControlGateway
from copilot.control.identity import IdentityAuthority
from copilot.control.ledger import Ledger
from copilot.db.session import make_pool


@dataclass
class Case:
    case_id: str
    scope: object
    ticket: dict
    account_id: str


class World:
    def __init__(self, env, *, resync_kw=None, credit_kw=None, esc_kw=None):
        self.env = env
        self.clock = FakeClock()
        self.authority = IdentityAuthority(secrets.token_hex(32), clock=lambda: self.clock.now().timestamp())
        self.control_pool = make_pool(env.control_dsn, 1, 8)
        self.events = ControlEvents()
        self.audit = AuditLog(self.control_pool, self.clock)
        self.approvals = ApprovalService(self.control_pool, self.audit, self.authority, self.clock, self.events)
        self.ledger = Ledger(self.control_pool)
        self.resync = mocks.MockResyncSystem(**(resync_kw or {}))
        self.credit = mocks.MockCreditSystem(**(credit_kw or {}))
        self.escalation = mocks.MockEscalationSystem(**(esc_kw or {}))
        self.gateway = ControlGateway(app_pool=env.pool, guard=env.guard, control_pool=self.control_pool, audit=self.audit, approvals=self.approvals, ledger=self.ledger, authority=self.authority,
                                      systems={"trigger_resync": self.resync, "request_sla_credit": self.credit, "escalate_engineering": self.escalation}, clock=self.clock, events=self.events)
        self.agent = self.authority.mint("agent", "agent:run-test", ttl_s=7200)
        self.n = 0
        ds = env.dataset
        self.integrations = C.read_jsonl(ds / "integrations.jsonl")
        self.incidents = C.read_jsonl(ds / "incidents.jsonl")
        self.contracts = {c["account_id"]: c for c in C.read_jsonl(ds / "contracts.jsonl")}

    def close(self):
        self.control_pool.close()

    # ---- identities -----------------------------------------------------------------------------------------------------------------------
    def user(self, role, name=None):
        return self.authority.mint("user", name or f"{role}.alex", role, ttl_s=7200)

    # ---- cases ----------------------------------------------------------------------------------------------------------------------------
    def case_for_ticket(self, ticket) -> Case:
        cid, scope = self.env.intake.open_case(ticket["ticket_id"])
        return Case(cid, scope, ticket, ticket["account_id"])

    def ticket_where(self, pred):
        return next(t for t in self.env.tickets if pred(t))

    def sla_case(self, tier="Premier") -> Case:
        return self.case_for_ticket(self.ticket_where(lambda t: any(h["author"] == "sla-monitor" for h in t["history"]) and self.contracts[t["account_id"]]["tier"] == tier))

    def case_without_sla(self, tier="Premier") -> Case:
        return self.case_for_ticket(self.ticket_where(lambda t: not t["history"] and self.contracts[t["account_id"]]["tier"] == tier))

    def feed(self, *, affected_by_gateway_incident: bool, recent_resync=False):
        gw = {a for i in self.incidents if i["status"] != "resolved" and i["component"] == "carrier_gateway" for a in i["affected_account_ids"]}
        for i in self.integrations:
            if i["kind"] != "carrier_feed" or (i["account_id"] in gw) != affected_by_gateway_incident or i["status"] in ("unknown",):
                continue
            if not any(t["account_id"] == i["account_id"] for t in self.env.tickets):
                continue
            if i["last_resync_at"] and not recent_resync:
                return i
            if recent_resync and i["last_resync_at"]:
                return i
        raise LookupError("no suitable integration in the dataset")

    def case_for_account(self, account_id) -> Case:
        return self.case_for_ticket(self.ticket_where(lambda t: t["account_id"] == account_id))

    # ---- action builders ------------------------------------------------------------------------------------------------------------------
    def action(self, type_, case: Case, params, **over):
        self.n += 1
        role = {"draft_reply": "tier2_engineer", "add_internal_note": "tier2_engineer", "escalate_engineering": "tier2_engineer", "request_sla_credit": "support_manager", "trigger_resync": "on_call_sre"}[type_]
        a = {"action_id": f"ACT-{secrets.token_hex(4)}", "case_id": case.case_id, "requested_by": "agent", "evidence_refs": ["RBK-0019"], "idempotency_key": f"idem-{secrets.token_hex(10)}",
             "type": type_, "required_role": role, "params": params}
        a.update(over)
        return a

    def resync_action(self, case, integration_id, **over):
        return self.action("trigger_resync", case, {"integration_id": integration_id, "blast_radius": "one carrier feed for one account"}, **over)

    def credit_action(self, case, percent=5, **over):
        return self.action("request_sla_credit", case, {"percent": percent, "reason": "SLA response target missed"}, **over)

    def approve(self, result, role):
        return self.approvals.decide(result.approval_id, self.user(role), "approve", "evidence reviewed")


def j(x):
    return json.dumps(x, default=str)
