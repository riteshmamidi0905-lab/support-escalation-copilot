"""The operator web application (WSGI, server-rendered, no JavaScript). The browser is NOT a trusted authority:
  * identity comes from a signed session cookie that the server verifies on every request (grants are inside the signature);
  * every read and every action re-authorizes server-side (role, account grants, case state); the UI hides buttons only as a courtesy;
  * state-changing requests are POST + CSRF-token only; there is NO route that executes an action directly (approve/deny only records a decision; execution still needs the idempotent executor);
  * unknown case / someone else's case / forged identity all produce the same 404;
  * strict CSP, no inline script/style, no framing, no caching of sensitive pages.
Sign-in is SIMULATED (pick a persona): there is no real authentication in this project."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

from jinja2 import Environment, FileSystemLoader, select_autoescape

from copilot.control.access import AccessDenied
from copilot.control.identity import Identity, IdentityAuthority, IdentityError

from . import demo as demo_mod
from . import viewmodel as vm
from .operator import OperatorActions, OperatorError
from .services import Services

HERE = Path(__file__).resolve().parent
CASE_ID = re.compile(r"^CASE-[0-9]{6}$")
ID = re.compile(r"^[A-Za-z0-9-]{1,40}$")
REQ_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
MAX_BODY = 32_768
FLASH = {"decided": "Your decision was recorded and the case was resumed.", "amended": "The action was amended. The previous approval is void and a new approval is required.", "reviewed": "The draft was marked reviewed.",
         "reconciled": "The uncertain write was reconciled.", "demo": "A demo case was opened.", "signedout": "You were signed out."}
CSP = "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: list[tuple[str, str]] = field(default_factory=list)
    ctype: str = "text/html; charset=utf-8"

    def wsgi(self, start_response):
        reasons = {200: "OK", 303: "See Other", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 409: "Conflict", 413: "Payload Too Large"}
        start_response(f"{self.status} {reasons.get(self.status, 'Status')}", [("Content-Type", self.ctype), ("Content-Length", str(len(self.body))), *self.headers])
        return [self.body]


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    form: dict[str, list[str]]
    cookies: dict[str, str]
    headers: dict[str, str]

    def f(self, k: str, default: str = "") -> str:
        v = self.form.get(k)
        return v[0] if v else default

    def fl(self, k: str) -> list[str]:
        return self.form.get(k, [])


class OperatorApp:
    def __init__(self, svc: Services, csrf_key: bytes | None = None, secure_cookies: bool = False):
        self.svc, self.ops = svc, OperatorActions(svc)
        self.csrf_key = csrf_key or secrets.token_bytes(32)
        self.secure = secure_cookies
        self.env = Environment(loader=FileSystemLoader(str(HERE / "templates")), autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
        self.env.filters["ts"] = vm.fmt_ts
        self.env.filters["why"] = vm.why

    # ---- WSGI --------------------------------------------------------------------------------------------------------------------------------
    def __call__(self, environ, start_response):
        try:
            req = self._parse(environ)
        except ValueError:
            return Response(413, b"request too large", ctype="text/plain").wsgi(start_response)
        return self.handle(req).wsgi(start_response)

    def _parse(self, environ) -> Request:
        method = environ["REQUEST_METHOD"]
        form: dict[str, list[str]] = {}
        if method == "POST":
            n = int(environ.get("CONTENT_LENGTH") or 0)
            if n > MAX_BODY:
                raise ValueError("too large")
            form = parse_qs(environ["wsgi.input"].read(n).decode("utf-8", "replace"), keep_blank_values=True)
        cookies = {k: m.value for k, m in SimpleCookie(environ.get("HTTP_COOKIE", "")).items()}
        headers = {k[5:].replace("_", "-").lower(): v for k, v in environ.items() if k.startswith("HTTP_")}
        return Request(method, environ.get("PATH_INFO", "/"), parse_qs(environ.get("QUERY_STRING", "")), form, cookies, headers)

    # ---- session / csrf ----------------------------------------------------------------------------------------------------------------------
    def identity(self, req: Request) -> Identity | None:
        tok = req.cookies.get("scec_session")
        if not tok:
            return None
        try:
            ident = self.svc.authority.verify(IdentityAuthority.load(tok))
        except IdentityError:
            return None
        return ident if ident.kind == "user" else None                     # only a signed HUMAN identity is a session; an agent token in a cookie is nothing

    def csrf(self, ident: Identity) -> str:
        return hmac.new(self.csrf_key, ident.sig.encode(), hashlib.sha256).hexdigest()[:40]

    def _csrf_ok(self, req: Request, ident: Identity) -> bool:
        return hmac.compare_digest(req.f("csrf"), self.csrf(ident))

    # ---- helpers -----------------------------------------------------------------------------------------------------------------------------
    def render(self, name: str, req: Request | None, ident: Identity | None, status: int = 200, **ctx: Any) -> Response:
        t = self.env.get_template(name)
        html = t.render(ident=ident, global_view=bool(ident and self.is_global_viewer(ident)), csrf=self.csrf(ident) if ident else "", demo=self.svc.demo, flash=FLASH.get(req.query.get("m", [""])[0]) if req else None, now=self.svc.clock.now(), **ctx)
        return self._secure(Response(status, html.encode()))

    def _secure(self, r: Response) -> Response:
        r.headers += [("Content-Security-Policy", CSP), ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"), ("Cache-Control", "no-store"), ("X-Frame-Options", "DENY"), ("Permissions-Policy", "camera=(), microphone=(), geolocation=()")]
        return r

    def redirect(self, to: str, req: Request | None = None, cookie: str | None = None) -> Response:
        r = Response(303, b"", [("Location", to)] + ([("Set-Cookie", cookie)] if cookie else []), "text/plain")
        return self._secure(r)

    def not_found(self, req: Request, ident: Identity | None) -> Response:
        return self.render("message.html", req, ident, 404, title="Not found", message="That page does not exist, or you are not permitted to see it.")

    def json(self, obj: Any, status: int = 200) -> Response:
        return self._secure(Response(status, json.dumps(obj, indent=1, default=str).encode(), ctype="application/json"))

    def request_id(self, req: Request) -> str:
        rid = req.headers.get("x-request-id", "")
        return rid if REQ_ID.match(rid) else "REQ-" + secrets.token_hex(4)

    # ---- routing -----------------------------------------------------------------------------------------------------------------------------
    def handle(self, req: Request) -> Response:
        ident = self.identity(req)
        p, m = req.path, req.method
        if p == "/healthz":
            return Response(200, b"ok", ctype="text/plain")
        if p == "/static/app.css":
            return Response(200, (HERE / "static" / "app.css").read_bytes(), [("Cache-Control", "public, max-age=300")], "text/css; charset=utf-8")
        if p == "/login" and self.svc.demo:
            return self._login(req, ident)
        if p.startswith("/api/"):
            return self._api(req, ident)
        if ident is None:
            return self.redirect("/login") if self.svc.demo else self.not_found(req, None)
        if m == "POST":
            if not self._csrf_ok(req, ident):
                return self.render("message.html", req, ident, 403, title="Request refused", message="The security token on this form is missing or invalid. Go back, reload the page and try again.")
            return self._post(req, ident)
        if m != "GET":
            return self.render("message.html", req, ident, 405, title="Method not allowed", message="")
        if p == "/":
            return self.redirect("/cases")
        if p == "/cases":
            return self.render("cases.html", req, ident, cases=self.svc.access.list_cases(ident, req.query.get("state", [None])[0] if req.query.get("state", [""])[0] in vm_states() else None))
        if p == "/approvals":
            return self._approvals(req, ident)
        if p == "/ops":
            return self._ops(req, ident)
        if p == "/metrics":
            return self._metrics(req, ident)
        if p == "/effects" and self.svc.demo:
            return self._effects(req, ident)
        if p == "/demo" and self.svc.demo:
            return self.render("demo.html", req, ident, demos=demo_mod.DEMOS)
        mt = re.fullmatch(r"/cases/(CASE-[0-9]{6})(/audit)?", p)
        if mt:
            return self._case(req, ident, mt.group(1), bool(mt.group(2)))
        return self.not_found(req, ident)

    # ---- pages -------------------------------------------------------------------------------------------------------------------------------
    def _login(self, req: Request, ident: Identity | None) -> Response:
        if req.method == "POST":
            pid = req.f("persona")
            if pid not in self.svc.personas:
                return self.render("message.html", req, None, 400, title="Unknown persona", message="Choose one of the simulated personas.")
            i = self.svc.sign_in(pid)
            ck = f"scec_session={IdentityAuthority.dump(i)}; Path=/; HttpOnly; SameSite=Strict; Max-Age=3600" + ("; Secure" if self.secure else "")
            return self.redirect("/cases", cookie=ck)
        return self.render("login.html", req, ident, personas=list(self.svc.personas.values()))

    def _case_view(self, ident: Identity, case_id: str):
        i, _acc = self.svc.access.authorize_case(ident, case_id)
        row = self.svc.machine.get(case_id)
        approvals = [a for a in (self.svc.approvals.get(x["approval_id"]) for x in row["file"].get("approvals", [])) if a]
        return vm.build(row, row["file"], ident=i, approvals=approvals, transitions=self.svc.machine.transitions_detailed(case_id), audit=self.svc.access.audit_for_case(i, case_id, 80),
                        artifacts=self.svc.access.artifacts_for_case(i, case_id), registry=self.svc.retrieval.rt.registry, now=self.svc.clock.now())

    def _case(self, req: Request, ident: Identity, case_id: str, audit_page: bool, error: str | None = None, status: int = 200) -> Response:
        try:
            v = self._case_view(ident, case_id)
        except AccessDenied:
            return self.not_found(req, ident)
        if audit_page:
            events = self.svc.access.audit_for_case(ident, case_id, 500)
            return self.render("audit.html", req, ident, case_id=case_id, events=[{**e, "ts": vm.fmt_ts(e["ts"]), "pretty": vm.pretty(e["payload"])} for e in events], trace=self.svc.access.trace_for_case(ident, case_id), v=v)
        return self.render("case.html", req, ident, status, v=v, case_id=case_id, error=error)

    def _approvals(self, req: Request, ident: Identity) -> Response:
        rows = [{**r, "created": vm.fmt_ts(r["created_at"]), "expires": vm.fmt_ts(r["expires_at"]), "age_s": max(0, int((self.svc.clock.now() - r["created_at"]).total_seconds())),
                 "mine": r["required_role"] == ident.role} for r in self.svc.access.pending_approvals(ident)]
        return self.render("approvals.html", req, ident, rows=rows)

    def _global_only(self, req: Request, ident: Identity) -> Response | None:
        if not self.is_global_viewer(ident):
            return self.not_found(req, ident)                                    # a global view exists only for an auditor with an all-accounts grant
        return None

    @staticmethod
    def is_global_viewer(ident: Identity) -> bool:
        return ident.role == "auditor" and "*" in ident.accounts

    def _ops(self, req: Request, ident: Identity) -> Response:
        deny = self._global_only(req, ident)
        return deny or self.render("ops.html", req, ident, m=self.svc.metrics.snapshot(), breakers={a: self.svc.client.breaker_state(a) for a in ("ticketing_api", "status_api", "carrier_api")})

    def _metrics(self, req: Request, ident: Identity) -> Response:
        deny = self._global_only(req, ident)
        return deny or self._secure(Response(200, self.svc.metrics.prometheus().encode(), ctype="text/plain; version=0.0.4"))

    def _effects(self, req: Request, ident: Identity) -> Response:
        integ = self.svc.status_api._by
        rows = []
        for name, system in (("re-sync system", self.svc.resync), ("credit system", self.svc.credit), ("engineering queue", self.svc.escalation)):
            for e in system.effects:
                acc = e["payload"].get("account_id") or integ.get(e["payload"].get("integration_id"), {}).get("account_id")
                if ident.covers(acc):
                    rows.append({"system": name, "ref": e["result"].get("ref"), "account": acc, "request_id": e["request_id"], "detail": {k: v for k, v in e["payload"].items() if k not in ("account_id",)}})
        return self.render("effects.html", req, ident, rows=rows, calls={"re-sync system": self.svc.resync.calls, "credit system": self.svc.credit.calls, "engineering queue": self.svc.escalation.calls})

    # ---- POST actions ------------------------------------------------------------------------------------------------------------------------
    def _post(self, req: Request, ident: Identity) -> Response:
        p, rid = req.path, self.request_id(req)
        if p == "/logout":
            return self.redirect("/login?m=signedout", cookie="scec_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
        md = re.fullmatch(r"/demo/([A-Z][0-9]?)/start", p)
        if md and self.svc.demo and md.group(1) in demo_mod.BY_ID:
            if ident.role not in ("tier2_engineer", "on_call_sre", "support_manager"):
                return self.not_found(req, ident)
            case_id = self.svc.start_demo(md.group(1), rid)
            if not ident.covers(self.svc.machine.get(case_id)["account_id"]):                     # a demo case the operator is not entitled to is not shown to them
                return self.redirect("/demo?m=demo")
            return self.redirect(f"/cases/{case_id}?m=demo")
        mt = re.fullmatch(r"/cases/(CASE-[0-9]{6})/(approvals/(APR-[0-9a-f]{16})/decide|actions/(ACT-[A-Za-z0-9-]{1,40})/(amend|reconcile)|draft/review)", p)
        if not mt:
            return self.not_found(req, ident)
        case_id = mt.group(1)
        try:
            if mt.group(3):
                verdict = req.f("verdict")
                if verdict not in ("approve", "deny"):
                    raise OperatorError("BAD_VERDICT", "choose approve or deny")
                self.ops.decide(ident, case_id, mt.group(3), verdict, req.f("reason")[:300], rid)
                flash = "decided"
            elif mt.group(4) and mt.group(5) == "amend":
                changes = {k[2:]: v[0] for k, v in req.form.items() if k.startswith("p_") and v and v[0] != ""}
                self.ops.amend(ident, case_id, mt.group(4), {k: v for k, v in changes.items() if k in ("percent", "reason", "blast_radius", "severity", "summary")}, rid)
                flash = "amended"
            elif mt.group(4):
                self.ops.reconcile(ident, case_id, mt.group(4), req.f("outcome"), req.f("note"), rid)
                flash = "reconciled"
            else:
                self.ops.review_draft(ident, case_id, req.fl("ack"), rid)
                flash = "reviewed"
        except AccessDenied:
            return self.not_found(req, ident)
        except OperatorError as e:
            return self._case(req, ident, case_id, False, error=f"{e.code}: {e.message}", status=409 if e.code in ("SUPERSEDED", "NOT_IN_REVIEW", "NOT_AMENDABLE", "ALREADY_EXECUTING") else 403 if e.code in ("WRONG_ROLE", "ACCOUNT_NOT_GRANTED", "SELF_APPROVAL", "AMENDER_CANNOT_APPROVE", "NOT_A_HUMAN_APPROVER", "IDENTITY_INVALID") else 400)
        return self.redirect(f"/cases/{case_id}?m={flash}")

    # ---- JSON API ------------------------------------------------------------------------------------------------------------------------------
    def _api(self, req: Request, ident: Identity | None) -> Response:
        if ident is None:
            return self.json({"error": "authentication required"}, 401)
        if req.method != "GET":
            return self.json({"error": "read-only API: use the web forms for decisions"}, 405)
        p = req.path
        try:
            if p == "/api/cases":
                return self.json([{**c, "updated_at": str(c["updated_at"]), "created_at": str(c["created_at"])} for c in self.svc.access.list_cases(ident)])
            mt = re.fullmatch(r"/api/cases/(CASE-[0-9]{6})(/audit|/trace)?", p)
            if mt:
                cid, sub = mt.group(1), mt.group(2)
                if sub == "/audit":
                    return self.json(self.svc.access.audit_for_case(ident, cid))
                if sub == "/trace":
                    return self.json(self.svc.access.trace_for_case(ident, cid))
                self.svc.access.authorize_case(ident, cid)
                row = self.svc.machine.get(cid)
                return self.json({"case_id": cid, "state": row["state"], "outcome": row["outcome"], "disposition": row["disposition"], "case_file": row["file"].get("case_file")})
            if p == "/api/metrics":
                if not self.is_global_viewer(ident):
                    return self.json({"error": "not found"}, 404)
                return self.json(self.svc.metrics.snapshot())
        except AccessDenied:
            return self.json({"error": "not found"}, 404)
        return self.json({"error": "not found"}, 404)


def vm_states() -> set[str]:
    from copilot.workflow.states import STATES
    return set(STATES)


_ = quote
