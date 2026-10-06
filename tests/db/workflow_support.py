"""Harness for the M4 workflow tests/scripts: a World (M3 control plane) plus the case machine, retrieval service, mock Status/Carrier/Ticketing APIs and a CaseRunner."""
import itertools
import secrets

import psycopg
from agent.trace import Tracer

from copilot import contracts as C
from copilot.control.access import Access
from copilot.control.metrics import Metrics
from copilot.control.ops import OpsRecorder
from copilot.retrieval import embed
from copilot.workflow import externals as X
from copilot.workflow.machine import CaseMachine
from copilot.workflow.providers import RuleCaseModel
from copilot.workflow.retrieval_service import LayeredEmbedder, RetrievalService
from copilot.workflow.runner import CaseRunner, Deps
from tests.db.control_support import World


class WorkflowWorld(World):
    def __init__(self, env, provider=None, *, status_faults=None, carrier_faults=None, ticketing_faults=None, **kw):
        super().__init__(env, **kw)
        ds = env.dataset
        self.tickets_all = C.read_jsonl(ds / "tickets.jsonl")
        self.status_api = X.StatusAPI(self.integrations, status_faults)
        self.carrier_api = X.CarrierAPI(self.integrations, carrier_faults)
        self.ticketing = X.TicketingAPI(self.tickets_all, ticketing_faults)
        self.ops = OpsRecorder(self.control_pool, self.clock)
        self.client = X.GuardedClient(clock=self.clock, observer=self.ops.record)
        self.access = Access(self.control_pool, self.authority, self.audit)
        self.metrics = Metrics(self.control_pool, self.clock)
        self.provider = provider or RuleCaseModel()
        self.machine = CaseMachine(self.control_pool, self.audit, self.clock)
        self.retrieval = RetrievalService(env.pool, LayeredEmbedder(), embed.Reranker())
        self.runner = self.new_runner()

    def deps(self, provider=None) -> Deps:
        return Deps(app_pool=self.env.pool, guard=self.env.guard, intake=self.env.intake, gateway=self.gateway, approvals=self.approvals, audit=self.audit, machine=self.machine, retrieval=self.retrieval,
                    ticketing=self.ticketing, status_api=self.status_api, carrier_api=self.carrier_api, client=self.client, provider=provider or self.provider, agent_identity=self.agent, clock=self.clock, ops=self.ops,
                    tracer_factory=lambda cid, inv, acc, req: Tracer(run_id=cid, listeners=[self.ops.tracer_listener(cid, acc, "RUN-" + cid, inv, req)]))

    def worker(self, name="worker-1", **kw):
        from copilot.workflow.recovery import RecoveryWorker
        return RecoveryWorker(self.control_pool, lambda cid: self.new_runner(), self.clock, name, self.ops, **kw)

    def new_runner(self, provider=None, crash_points=None) -> CaseRunner:
        return CaseRunner(self.deps(provider), crash_points)

    def labels(self):
        if not hasattr(self, "_labels"):
            self._labels = {r["ticket_id"]: r for r in C.read_jsonl(self.env.dataset / "synthetic_labels.jsonl")}
        return self._labels

    def scenario_tickets(self, sid):
        return [t for t, lab in self.labels().items() if lab["scenario_id"] == sid]

    def open_incident_components(self, account_id):
        return {i["component"] for i in self.incidents if i["status"] != "resolved" and account_id in i["affected_account_ids"]}

    def routine_ticket(self):
        """An S14 ticket whose account has no open tracking/gateway incident (RBK-0027 then says: refresh the route cache, no escalation)."""
        return next(t for t in self.scenario_tickets("S14") if not self.open_incident_components(self.ticket(t)["account_id"]) & {"tracking", "carrier_gateway"})

    def routine_tickets(self, n=None):
        out = [t for t in self.scenario_tickets("S14") if not self.open_incident_components(self.ticket(t)["account_id"]) & {"tracking", "carrier_gateway"}]
        return out[:n] if n else out

    def resync_ticket(self):
        """An S4 ticket on which a re-sync can be proposed: carrier feed in an account without a gateway incident."""
        for t in self.scenario_tickets("S4"):
            tk = self.ticket(t)
            if "carrier_gateway" not in self.open_incident_components(tk["account_id"]):
                return t
        raise LookupError

    def ticket(self, ticket_id):
        return next(t for t in self.env.tickets if t["ticket_id"] == ticket_id)

    def human(self, role, approval_id, verdict="approve", reason="reviewed"):
        return self.approvals.decide(approval_id, self.user(role), verdict, reason)


_TICKET_SEQ = itertools.count(1000)


def next_ticket_id() -> str:
    """Unique per process (a random id collided in CI: 9,000 values and ~30 inserts in one database). Matches the contract pattern TCK-[0-9]{4,6}; never equals the demo rows TCK-9001/9002 (4 digits)."""
    return f"TCK-9{next(_TICKET_SEQ):04d}"


def uniq():
    return secrets.token_hex(3)


def insert_ticket(w, subject, body, account_id=None, severity="P3"):
    """Insert a hostile/custom ticket row (loader role) and register it with the mock ticketing API."""
    account_id = account_id or w.ticket(w.resync_ticket())["account_id"]
    tid = next_ticket_id()
    acc = next(a for a in w.env.accounts if a["account_id"] == account_id)
    contact = acc["contacts"][0]
    row = (tid, account_id, "2026-03-02T08:00:00Z", "carrier_integrations", severity, subject, body, contact["name"], contact["email"], "portal")
    with psycopg.connect(w.env.loader_dsn) as c:
        c.execute("INSERT INTO copilot.tickets VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", row)
    w.ticketing._by[tid] = {"ticket_id": tid, "account_id": account_id, "created_at": row[2], "product_area": row[3], "severity": severity, "subject": subject, "body": body, "history": []}
    return tid
