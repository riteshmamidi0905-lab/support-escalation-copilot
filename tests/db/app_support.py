"""Harness for the operator-application tests: a Services container on the real PostgreSQL test environment (fake clock, test authority) plus a minimal WSGI client that behaves like a browser
(cookie jar, form posts, CSRF token read from the rendered page). The tests never reach into the app to authorise anything: they go through HTTP-shaped requests only."""
from __future__ import annotations

import io
import json
import re
import secrets
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlencode

import psycopg

from copilot import contracts as C
from copilot.app import demo as D
from copilot.app.services import Services
from copilot.app.web import OperatorApp
from copilot.control.clock import FakeClock
from copilot.control.identity import IdentityAuthority
from copilot.db.session import make_pool
from tests.db.workflow_support import next_ticket_id

_SEEDED: dict[str, dict[str, str]] = {}


@dataclass
class Resp:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    @property
    def plain(self) -> str:
        t = re.sub(r"<(script|style).*?</\1>", "", self.text, flags=re.S)
        return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", t))).strip()

    def json(self):
        return json.loads(self.text)

    @property
    def location(self) -> str:
        return self.headers.get("location", "")

    @property
    def csrf(self) -> str:
        m = re.search(r'name="csrf" value="([0-9a-f]+)"', self.text)
        return m.group(1) if m else ""


class Client:
    def __init__(self, app: OperatorApp, ident=None, authority=None):
        self.app, self.cookie, self._csrf = app, (IdentityAuthority.dump(ident) if ident else None), None

    def req(self, method: str, path: str, form: dict | None = None, query: str = "", headers: dict | None = None) -> Resp:
        body = urlencode(form or {}, doseq=True).encode()
        environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query, "wsgi.input": io.BytesIO(body), "CONTENT_LENGTH": str(len(body)), "HTTP_COOKIE": f"scec_session={self.cookie}" if self.cookie else ""}
        for k, v in (headers or {}).items():
            environ["HTTP_" + k.upper().replace("-", "_")] = v
        cap: dict = {}

        def start_response(status, hdrs):
            cap["status"], cap["headers"] = int(status.split()[0]), {k.lower(): v for k, v in hdrs}
            cap["set_cookie"] = [v for k, v in hdrs if k.lower() == "set-cookie"]
        out = b"".join(self.app(environ, start_response))
        for sc in cap["set_cookie"]:
            m = re.match(r"scec_session=([^;]*)", sc)
            if m:
                self.cookie = m.group(1) or None
        return Resp(cap["status"], cap["headers"], out)

    def get(self, path: str, query: str = "") -> Resp:
        return self.req("GET", path, query=query)

    def post(self, path: str, form: dict | None = None, csrf: str | None = None) -> Resp:
        token = self.csrf() if csrf is None else csrf
        return self.req("POST", path, {**(form or {}), "csrf": token})

    def csrf(self) -> str:
        return self.get("/cases").csrf


class AppWorld:
    def __init__(self, env, *, demo: bool = True, clock: FakeClock | None = None, **build_kw):
        self.env = env
        self.clock = clock or FakeClock()
        self.authority = IdentityAuthority(secrets.token_hex(32), clock=lambda: self.clock.now().timestamp())
        self.control_pool = make_pool(env.control_dsn, 1, 8)
        picks = _SEEDED.get(env.dbname)
        if picks is None:
            picks, rows = D.demo_ticket_rows(env.dataset)
            with psycopg.connect(env.loader_dsn) as c, c.transaction():
                c.cursor().executemany("INSERT INTO copilot.tickets VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
            _SEEDED[env.dbname] = picks
        self.picks = picks
        self.svc = Services.build(app_pool=env.pool, control_pool=self.control_pool, guard=env.guard, intake=env.intake, authority=self.authority, dataset=env.dataset, clock=self.clock, demo=demo,
                                  personas=D.demo_personas(env.dataset, picks), **build_kw)
        self.svc.demo_tickets = picks
        D.register_custom_tickets(self.svc, picks, env.dataset)
        self.app = OperatorApp(self.svc, csrf_key=b"k" * 32)
        self.labels = {r["ticket_id"]: r for r in C.read_jsonl(env.dataset / "synthetic_labels.jsonl")}
        self.tickets = {t["ticket_id"]: t for t in env.tickets}
        self._incidents = [i for i in C.read_jsonl(env.dataset / "incidents.jsonl") if i["status"] != "resolved"]

    def close(self):
        self.control_pool.close()

    # ---- people and browsers --------------------------------------------------------------------------------------------------------------------
    def person(self, role: str, *accounts: str, name: str | None = None):
        return self.authority.mint("user", name or f"{role}.t{secrets.token_hex(2)}", role, ttl_s=7200, accounts=tuple(accounts) or ("*",))

    def browser(self, role: str | None = None, *accounts: str, name: str | None = None, ident=None) -> Client:
        return Client(self.app, ident or (self.person(role, *accounts, name=name) if role else None))

    def persona_browser(self, persona_id: str) -> Client:
        c = Client(self.app)
        r = c.req("POST", "/login", {"persona": persona_id})
        assert r.status == 303, r.text[:200]
        return c

    # ---- cases ----------------------------------------------------------------------------------------------------------------------------------
    def comps(self, account_id: str) -> set[str]:
        return {i["component"] for i in self._incidents if account_id in i["affected_account_ids"]}

    def scenario(self, sid: str, n: int = 0, *, clean: bool = True) -> str:
        pool = [t for t, lab in self.labels.items() if lab["scenario_id"] == sid and (not clean or not self.comps(self.tickets[t]["account_id"]) & {"tracking", "carrier_gateway"})]
        return pool[n]

    def account_of(self, ticket_id: str) -> str:
        return self.tickets[ticket_id]["account_id"] if ticket_id in self.tickets else self.picks["account"]

    def open_case(self, ticket_id: str, provider=None) -> str:
        r = self.svc.runner(provider=provider).start(ticket_id)
        if provider is not None:
            self.svc.providers[r.case_id] = provider
        return r.case_id

    def case_state(self, case_id: str) -> dict:
        row = self.svc.machine.get(case_id)
        return {"state": row["state"], "outcome": row["outcome"], "disposition": row["disposition"], "file": row["file"], "account_id": row["account_id"]}

    def pending_approval(self, case_id: str) -> str:
        return next(a["approval_id"] for a in self.case_state(case_id)["file"]["approvals"] if a["status"] == "awaiting_approval")

    def effects(self) -> int:
        return len(self.svc.resync.effects) + len(self.svc.credit.effects) + len(self.svc.escalation.effects)

    def insert_ticket(self, subject: str, body: str, account_id: str | None = None) -> str:
        acc_id = account_id or self.picks["account"]
        tid = next_ticket_id()
        acc = next(a for a in self.env.accounts if a["account_id"] == acc_id)
        ct = acc["contacts"][0]
        row = (tid, acc_id, "2026-03-02T08:00:00Z", "carrier_integrations", "P3", subject, body, ct["name"], ct["email"], "portal")
        with psycopg.connect(self.env.loader_dsn) as c:
            c.execute("INSERT INTO copilot.tickets VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", row)
        self.svc.ticketing._by[tid] = {"ticket_id": tid, "account_id": acc_id, "created_at": row[2], "product_area": row[3], "severity": "P3", "subject": subject, "body": body, "history": []}
        return tid
