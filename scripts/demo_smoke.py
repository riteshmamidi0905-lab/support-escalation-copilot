"""Walks the demo scenarios A-F over REAL HTTP against a freshly built synthetic environment and checks what a person would see and what actually happened. It is the executable version of
docs/demo-walkthrough.md: a clean checkout that passes this has a working demo.

  python scripts/with_local_pg.py python scripts/demo_smoke.py        # embedded PostgreSQL (no Docker)
  COPILOT_TEST_DATABASE_URL=postgresql://copilot_admin:...@127.0.0.1:5433/copilot python scripts/demo_smoke.py   # the Compose database

Synthetic data, simulated sign-in, deterministic stand-in model (not an LLM). Exits non-zero on the first failed expectation."""
import http.cookiejar
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from wsgiref.simple_server import make_server

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_demo_server as R  # noqa: E402

from copilot.app.web import OperatorApp  # noqa: E402


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Browser:
    def __init__(self, base: str):
        self.base, self.jar = base, http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), NoRedirect())

    def req(self, method: str, path: str, form: dict | None = None):
        data = urllib.parse.urlencode(form, doseq=True).encode() if form is not None else None
        try:
            request = urllib.request.Request(self.base + path, data=data, method=method)           # noqa: S310 - 127.0.0.1 only
            with self.op.open(request, timeout=60) as r:
                return r.status, r.headers, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read().decode()

    def get(self, path):
        return self.req("GET", path)

    def csrf(self):
        return re.search(r'name="csrf" value="([0-9a-f]+)"', self.get("/cases")[2]).group(1)

    def post(self, path, form=None):
        return self.req("POST", path, {**(form or {}), "csrf": self.csrf()})

    def login(self, persona):
        s, _h, _b = self.req("POST", "/login", {"persona": persona})
        assert s == 303, f"login {persona}: {s}"
        return self


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def main() -> int:
    svc, env = R.build_services()
    srv = make_server("127.0.0.1", 0, OperatorApp(svc), server_class=R.Threaded, handler_class=R.Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    results, ok = [], True

    def check(name: str, cond: bool, detail: str = ""):
        nonlocal ok
        results.append((name, cond, detail))
        ok &= bool(cond)
        print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""), flush=True)

    def case(b: Browser, cid: str) -> dict:
        import json
        return json.loads(b.get(f"/api/cases/{cid}")[2])

    def start(b: Browser, demo: str) -> str:
        s, h, _ = b.post(f"/demo/{demo}/start")
        assert s == 303, f"demo {demo}: {s}"
        return re.search(r"CASE-\d{6}", h["Location"]).group(0)

    def effects(b: Browser) -> int:
        m = re.search(r"(\d+) applied effect", text(b.get("/effects")[2]))
        return int(m.group(1)) if m else -1

    try:
        anon = Browser(base)
        check("health endpoint answers", anon.get("/healthz")[2] == "ok")
        check("unauthenticated visitors are sent to sign-in", anon.get("/cases")[0] == 303)
        lee, rina, omar, sam, ria = (Browser(base).login(p) for p in ("tier2.lee", "sre.rina", "manager.omar", "tier2.sam", "audit.ria"))
        check("a form post without the CSRF token is refused and does nothing", lee.req("POST", "/demo/A/start", {})[0] == 403)

        a = start(lee, "A"); ca = case(lee, a)
        check("A routine: answered with cited evidence, nothing executed", ca["state"] == "CLOSED" and ca["outcome"] == "ANSWER" and effects(lee) == 0, str(ca["state"]))
        check("A: the draft is shown as UNREVIEWED and never sent", "UNREVIEWED DRAFT" in text(lee.get(f"/cases/{a}")[2]))

        b = start(lee, "B")
        check("B re-sync: waits for a human; nothing executed yet", case(lee, b)["state"] == "REVIEW" and effects(lee) == 0)
        check("B: the Tier-2 engineer is not offered an approve button", "Approve exactly this action" not in text(lee.get(f"/cases/{b}")[2]))
        apr = re.search(r"APR-[0-9a-f]{16}", rina.get(f"/cases/{b}")[2]).group(0)
        check("B: the SRE approves with a recorded reason", rina.post(f"/cases/{b}/approvals/{apr}/decide", {"verdict": "approve", "reason": "demo smoke"})[0] == 303)
        cb = case(rina, b)
        check("B: exactly one customer-side effect, case closed", cb["state"] == "CLOSED" and cb["disposition"] == "EXECUTED" and effects(rina) == 1, f"effects={effects(rina)}")

        c = start(lee, "C"); cc = case(lee, c); pc = text(lee.get(f"/cases/{c}")[2])
        check("C injection: forbidden proposal dropped, 100% credit refused, re-sync still waits", cc["state"] == "REVIEW" and "FORBIDDEN_ACTION" in pc and "CREDIT_ABOVE_AGENT_THRESHOLD" in pc)
        apr = re.search(r"APR-[0-9a-f]{16}", rina.get(f"/cases/{c}")[2]).group(0)
        rina.post(f"/cases/{c}/approvals/{apr}/decide", {"verdict": "deny", "reason": "hostile ticket"})
        check("C: after a denial the case is refused and still only one effect exists", case(rina, c)["state"] == "REFUSED" and effects(rina) == 1)

        d1 = start(lee, "D1")
        check("D1 insufficient evidence: abstains", case(lee, d1)["state"] == "ABSTAINED" and case(lee, d1)["outcome"] == "INSUFFICIENT_EVIDENCE")
        d2 = start(lee, "D2")
        check("D2 conflicting evidence: flagged, elevated review", "CONFLICTING EVIDENCE FLAGGED" in text(lee.get(f"/cases/{d2}")[2]) and "ELEVATED REVIEW" in text(lee.get(f"/cases/{d2}")[2]))
        check("D2: marking the draft reviewed without acknowledging each risk is refused by the server", lee.post(f"/cases/{d2}/draft/review", {})[0] == 400)

        e = start(lee, "E")
        apr = re.search(r"APR-[0-9a-f]{16}", rina.get(f"/cases/{e}")[2]).group(0)
        rina.post(f"/cases/{e}/approvals/{apr}/decide", {"verdict": "approve", "reason": "demo smoke"})
        ce = case(rina, e)
        check("E uncertain execution: handed off as UNCERTAIN, NOT retried (the effect exists once)", ce["state"] == "HANDED_OFF" and ce["disposition"] == "OUTCOME_UNCERTAIN" and effects(rina) == 2, f"effects={effects(rina)}")
        act = re.search(r"/actions/(ACT-[A-Za-z0-9-]+)/reconcile", rina.get(f"/cases/{e}")[2]).group(1)
        check("E: an SRE reconciles it by hand", rina.post(f"/cases/{e}/actions/{act}/reconcile", {"outcome": "applied", "note": "demo smoke: effect visible on the customer system"})[0] == 303
              and case(rina, e)["disposition"] == "EXECUTED_RECONCILED" and effects(rina) == 2)

        f = start(lee, "F"); cf = case(lee, f)
        check("F dependency outage: degraded, actions disabled, nothing waiting for approval", cf["state"] == "HANDED_OFF" and cf["outcome"] == "DEGRADED" and "Approve exactly this action" not in text(rina.get(f"/cases/{f}")[2]))

        s_foreign, _h, body_foreign = sam.get(f"/cases/{b}")
        s_ghost, _h, body_ghost = sam.get("/cases/CASE-999999")
        check("isolation: another tenant's operator gets the same 404 for a real and a nonexistent case", s_foreign == s_ghost == 404 and re.sub(r"CASE-\d+", "", body_foreign) == re.sub(r"CASE-\d+", "", body_ghost))
        check("operations dashboard: auditor only", ria.get("/ops")[0] == 200 and lee.get("/ops")[0] == 404 and rina.get("/metrics")[0] == 404)
        m = ria.get("/api/metrics")[2]
        check("dashboard figures come from the events just produced (7 cases opened in this run)", '"cases": 7' in m.replace("\n", "").replace("  ", " ") or '"cases":7' in m.replace(" ", "").replace("\n", ""))
        check("audit trail verifies (hash chain)", svc.audit.verify().ok)
        _ = omar
    finally:
        srv.shutdown()
        srv.server_close()
        svc.control_pool.close()
        R.bench_env.drop(env)
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} demo checks passed" + ("" if not bad else "; FAILED: " + "; ".join(r[0] for r in bad)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
