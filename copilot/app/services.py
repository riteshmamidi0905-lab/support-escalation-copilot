"""Service container for the operator application, the recovery worker and the demo. One place wires the (unchanged) M0-M4 components together; the web layer only ever calls these."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.trace import Tracer

from copilot import contracts as C
from copilot.control import mocks
from copilot.control.access import Access
from copilot.control.approvals import ApprovalService
from copilot.control.audit import AuditLog
from copilot.control.clock import SystemClock
from copilot.control.events import ControlEvents
from copilot.control.gateway import ControlGateway
from copilot.control.identity import Identity, IdentityAuthority
from copilot.control.ledger import Ledger
from copilot.control.metrics import Metrics
from copilot.control.ops import OpsRecorder
from copilot.workflow import externals as X
from copilot.workflow.machine import CaseMachine
from copilot.workflow.providers import RuleCaseModel
from copilot.workflow.recovery import RecoveryWorker
from copilot.workflow.retrieval_service import LayeredEmbedder, RetrievalService
from copilot.workflow.runner import CaseRunner, Deps

OPERATOR_ROLES = ("tier2_engineer", "support_manager", "on_call_sre", "auditor")


@dataclass
class Persona:
    """A SIMULATED human. There is no real authentication in this project: signing in picks a persona, and the server mints a signed identity (with signed account grants) for it."""
    persona_id: str
    display: str
    role: str
    accounts: tuple[str, ...]
    note: str = ""


@dataclass
class Services:
    app_pool: Any
    control_pool: Any
    guard: Any
    intake: Any
    authority: IdentityAuthority
    clock: Any
    audit: AuditLog
    approvals: ApprovalService
    ledger: Ledger
    gateway: ControlGateway
    machine: CaseMachine
    access: Access
    metrics: Metrics
    ops: OpsRecorder
    retrieval: RetrievalService
    client: X.GuardedClient
    status_api: X.StatusAPI
    carrier_api: X.CarrierAPI
    ticketing: X.TicketingAPI
    resync: mocks.MockResyncSystem
    credit: mocks.MockCreditSystem
    escalation: mocks.MockEscalationSystem
    agent: Identity
    events: ControlEvents
    personas: dict[str, Persona] = field(default_factory=dict)
    demo: bool = False
    providers: dict[str, Any] = field(default_factory=dict)         # case_id -> model provider chosen for that case (demo scripts); default: the stand-in
    default_provider: Any = None
    case_faults: dict[str, Any] = field(default_factory=dict)       # case_id -> scripted customer-system faults (demo)
    demo_tickets: dict[str, str] = field(default_factory=dict)
    exec_lock: Any = field(default_factory=threading.RLock)         # demo mocks are process-global: serialise scripted runs

    # ---- construction -------------------------------------------------------------------------------------------------------------------------
    @classmethod
    def build(cls, *, app_pool, control_pool, guard, intake, authority: IdentityAuthority, dataset: Path, clock=None, demo: bool = False, personas: list[Persona] | None = None,
              resync_kw=None, credit_kw=None, esc_kw=None) -> Services:
        clock = clock or SystemClock()
        audit = AuditLog(control_pool, clock)
        events = ControlEvents()
        approvals = ApprovalService(control_pool, audit, authority, clock, events)
        ledger = Ledger(control_pool)
        resync, credit, esc = mocks.MockResyncSystem(**(resync_kw or {})), mocks.MockCreditSystem(**(credit_kw or {})), mocks.MockEscalationSystem(**(esc_kw or {}))
        gateway = ControlGateway(app_pool=app_pool, guard=guard, control_pool=control_pool, audit=audit, approvals=approvals, ledger=ledger, authority=authority,
                                 systems={"trigger_resync": resync, "request_sla_credit": credit, "escalate_engineering": esc}, clock=clock, events=events)
        ops = OpsRecorder(control_pool, clock)
        integrations, tickets = C.read_jsonl(dataset / "integrations.jsonl"), C.read_jsonl(dataset / "tickets.jsonl")
        from copilot.retrieval import embed
        svc = cls(app_pool=app_pool, control_pool=control_pool, guard=guard, intake=intake, authority=authority, clock=clock, audit=audit, approvals=approvals, ledger=ledger, gateway=gateway,
                  machine=CaseMachine(control_pool, audit, clock), access=Access(control_pool, authority, audit), metrics=Metrics(control_pool, clock), ops=ops,
                  retrieval=RetrievalService(app_pool, LayeredEmbedder(), embed.Reranker()), client=X.GuardedClient(clock=clock, observer=ops.record), status_api=X.StatusAPI(integrations),
                  carrier_api=X.CarrierAPI(integrations), ticketing=X.TicketingAPI(tickets), resync=resync, credit=credit, escalation=esc,
                  agent=authority.mint("agent", "agent:operator-app", ttl_s=86_400), events=events, personas={p.persona_id: p for p in (personas or [])}, demo=demo, default_provider=RuleCaseModel())
        return svc

    def refresh_agent(self) -> None:
        self.agent = self.authority.mint("agent", "agent:operator-app", ttl_s=86_400)

    def deps(self, provider=None) -> Deps:
        return Deps(app_pool=self.app_pool, guard=self.guard, intake=self.intake, gateway=self.gateway, approvals=self.approvals, audit=self.audit, machine=self.machine, retrieval=self.retrieval,
                    ticketing=self.ticketing, status_api=self.status_api, carrier_api=self.carrier_api, client=self.client, provider=provider or self.default_provider, agent_identity=self.agent,
                    clock=self.clock, ops=self.ops, tracer_factory=lambda cid, inv, acc, req: Tracer(run_id=cid, listeners=[self.ops.tracer_listener(cid, acc, "RUN-" + cid, inv, req)]))

    def runner(self, case_id: str | None = None, provider=None, crash_points=None) -> CaseRunner:
        prov = provider or (self.providers.get(case_id) if case_id else None)
        return CaseRunner(self.deps(prov), crash_points)

    def worker(self, name: str = "worker-1", **kw) -> RecoveryWorker:
        return RecoveryWorker(self.control_pool, lambda cid: _Locked(self, cid), self.clock, name, self.ops, **kw)

    def run_case(self, case_id: str, request_id: str | None = None):
        """The ONE way the application advances a case: scripted demo faults for that case are armed, then the idempotent runner runs."""
        from .demo import apply_faults
        with self.exec_lock:
            apply_faults(self, self.case_faults.get(case_id))
            return self.runner(case_id).run(case_id, request_id=request_id)

    def start_demo(self, demo_id: str, request_id: str) -> str:
        from .demo import BY_ID, apply_faults, faults_for, model_for
        if not self.demo:
            raise PermissionError("demo mode is off")
        demo = BY_ID[demo_id]
        spec, provider = faults_for(demo_id), model_for(demo_id)
        with self.exec_lock:
            apply_faults(self, spec)
            runner = self.runner(provider=provider)
            runner.request_id = request_id
            runner.start(self.demo_tickets[demo.ticket_key])
            if provider is not None:
                self.providers[runner.case_id] = provider
            if spec:
                self.case_faults[runner.case_id] = spec
            return runner.case_id

    # ---- identities ---------------------------------------------------------------------------------------------------------------------------
    def sign_in(self, persona_id: str, ttl_s: int = 3600) -> Identity:
        p = self.personas.get(persona_id)
        if p is None:
            raise KeyError(persona_id)
        return self.authority.mint("user", p.persona_id, p.role, ttl_s=ttl_s, accounts=p.accounts)

    def reset_mocks(self) -> None:
        self.resync.faults = mocks.FaultScript()
        self.resync.lookup_supported = False
        self.credit.faults = mocks.FaultScript()
        self.escalation.faults = mocks.FaultScript()
        self.status_api.faults = mocks.FaultScript()


class _Locked:
    """Adapter so the recovery worker advances cases through `Services.run_case`."""

    def __init__(self, svc: Services, case_id: str):
        self.svc, self.case_id = svc, case_id

    def run(self, case_id: str, request_id: str | None = None):
        return self.svc.run_case(case_id, request_id)
