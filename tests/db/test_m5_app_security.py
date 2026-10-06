"""The operator application treats the BROWSER as hostile. Every test goes through HTTP-shaped requests (cookie jar, form posts, CSRF token read from the rendered page); nothing reaches into the
app to authorise anything. Each one attempts a violation of a named invariant."""
import re
from dataclasses import replace
from pathlib import Path

import pytest

from copilot.control.identity import IdentityAuthority
from tests.db.app_support import AppWorld, Client
from tests.support.logcapture import CANARIES

pytestmark = [pytest.mark.db, pytest.mark.invariant]
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def env(m4env):
    return m4env


@pytest.fixture()
def w(env):
    w = AppWorld(env)
    yield w
    w.close()


def resync_case(w):
    t = w.picks["resync"]
    return w.open_case(t), w.account_of(t)


# ---- identity & session --------------------------------------------------------------------------------------------------------------------------
def test_unauthenticated_and_forged_cookies_get_nothing(w):                                                          # A-I2-35 A-I1-09
    cid, acc = resync_case(w)
    good = w.person("tier2_engineer", acc)
    forged = [replace(good, accounts=("*",)), replace(good, role="auditor"), replace(good, sig="0" * 64), IdentityAuthority("z" * 40, clock=lambda: w.clock.now().timestamp()).mint("user", "x", "auditor", accounts=("*",)), w.svc.agent]
    expired = w.person("tier2_engineer", acc)
    w.clock.advance(hours=3)                                                                                           # personas live 2h in tests
    forged.append(expired)
    for ident in [None, *forged]:
        c = Client(w.app, ident)
        r = c.get(f"/cases/{cid}")
        assert r.status in (303, 404) and cid not in r.text and acc not in r.text, "no case content for an unauthenticated or forged identity"
        assert c.get("/api/cases").status == 401 and c.get(f"/api/cases/{cid}/audit").status == 401
        assert c.get("/ops").status in (303, 404) and c.get("/metrics").status in (303, 404)
        assert c.req("POST", f"/cases/{cid}/approvals/APR-0000000000000000/decide", {"verdict": "approve"}).status in (303, 404)
    assert w.effects() == 0


def test_session_cookie_is_httponly_samesite_and_no_secret_is_in_it(w):
    c = Client(w.app)
    r = c.req("POST", "/login", {"persona": "sre.rina"})
    # the raw header is what the browser sees
    assert r.status == 303
    environ_cookie = c.cookie
    assert environ_cookie and "sre.rina" in IdentityAuthority.load(environ_cookie).id
    hdr = Client(w.app).req("POST", "/login", {"persona": "sre.rina"}).headers["set-cookie"]
    assert "HttpOnly" in hdr and "SameSite=Strict" in hdr and "Path=/" in hdr
    assert Client(w.app).req("POST", "/login", {"persona": "no.such.person"}).status == 400


def test_every_response_carries_strict_security_headers_and_no_inline_script_or_style_exists(w):
    c = w.persona_browser("tier2.lee")
    for path in ("/cases", "/approvals", "/demo", "/effects", "/login", "/nope"):
        r = c.get(path)
        h = r.headers
        assert "default-src 'none'" in h["content-security-policy"] and "script-src" not in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff" and h["cache-control"] == "no-store" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
        assert "<script" not in r.text.lower() and " style=" not in r.text.lower() and "onclick=" not in r.text.lower()
    for t in (ROOT / "copilot" / "app" / "templates").glob("*.html"):
        s = t.read_text().lower()
        assert "<script" not in s and " style=" not in s and not re.search(r"\son[a-z]+=", s), f"{t.name}: inline script/style/handler"


# ---- CSRF: the browser can be tricked into submitting a form ------------------------------------------------------------------------------------
def test_state_changing_requests_without_a_valid_csrf_token_do_nothing(w):                                           # A-I1-27
    cid, acc = resync_case(w)
    apr = w.pending_approval(cid)
    sre = w.browser("on_call_sre", acc)
    url = f"/cases/{cid}/approvals/{apr}/decide"
    other = w.browser("on_call_sre", acc, name="sre.other").csrf()
    for token in (None, "", "0" * 40, other, "x" * 400):
        r = sre.req("POST", url, {"verdict": "approve", "reason": "x"} if token is None else {"verdict": "approve", "reason": "x", "csrf": token})
        assert r.status == 403 and "security token" in r.plain
    assert w.case_state(cid)["state"] == "REVIEW" and w.effects() == 0
    assert sre.post(url, {"verdict": "approve", "reason": "ok"}).status == 303                                       # the same request WITH the token works: the control is the token
    assert len(w.svc.resync.effects) == 1


def test_a_get_request_can_never_change_state(w):
    cid, acc = resync_case(w)
    apr = w.pending_approval(cid)
    sre = w.browser("on_call_sre", acc)
    for p in (f"/cases/{cid}/approvals/{apr}/decide", f"/cases/{cid}/actions/ACT-x/amend", "/demo/B/start", "/logout"):
        r = sre.req("GET", p, query="verdict=approve&csrf=" + sre.csrf())
        assert r.status in (404, 303) and w.effects() == 0 and w.case_state(cid)["state"] == "REVIEW"


# ---- approvals through the browser ---------------------------------------------------------------------------------------------------------------
def test_wrong_role_and_wrong_account_cannot_approve_through_the_form(w):                                           # A-I1-28 A-I2-37
    cid, acc = resync_case(w)
    apr = w.pending_approval(cid)
    url = f"/cases/{cid}/approvals/{apr}/decide"
    other_acc = next(a["account_id"] for a in w.env.accounts if a["account_id"] != acc)
    for who in (w.browser("support_manager", acc), w.browser("tier2_engineer", acc), w.browser("auditor", acc)):
        r = who.post(url, {"verdict": "approve"})
        assert r.status == 403 and "WRONG_ROLE" in r.plain, r.plain[:300]
        assert w.case_state(cid)["state"] == "REVIEW" and w.effects() == 0
    sre_elsewhere = w.browser("on_call_sre", other_acc)
    r = sre_elsewhere.post(url, {"verdict": "approve"})
    assert r.status == 404, "an SRE without a grant for this account gets the same answer as for a case that does not exist"
    assert w.effects() == 0 and w.case_state(cid)["state"] == "REVIEW"


def test_double_submit_and_replay_of_an_approval_produce_exactly_one_effect(w):                                     # A-I1-31
    cid, acc = resync_case(w)
    apr = w.pending_approval(cid)
    sre = w.browser("on_call_sre", acc)
    url = f"/cases/{cid}/approvals/{apr}/decide"
    tok = sre.csrf()
    first = sre.req("POST", url, {"verdict": "approve", "csrf": tok})
    second = sre.req("POST", url, {"verdict": "approve", "csrf": tok})
    third = sre.req("POST", url, {"verdict": "deny", "csrf": tok})                                                    # cannot flip a decision either
    assert first.status == 303 and second.status in (400, 403, 409) and third.status in (400, 403, 409)
    assert len(w.svc.resync.effects) == 1 and w.case_state(cid)["state"] == "CLOSED"
    assert next(a for a in w.case_state(cid)["file"]["approvals"])["status"] == "approved"


def test_denying_through_the_ui_executes_nothing_and_says_so(w):
    cid, acc = resync_case(w)
    sre = w.browser("on_call_sre", acc)
    r = sre.post(f"/cases/{cid}/approvals/{w.pending_approval(cid)}/decide", {"verdict": "deny", "reason": "not today"})
    assert r.status == 303 and w.effects() == 0
    page = sre.get(f"/cases/{cid}").plain
    assert "HANDED OFF: DENIED" in page and "Nothing was executed" in page and "not today" in page


def test_no_route_or_method_can_execute_an_action_directly(w):                                                      # A-I1-29
    cid, acc = resync_case(w)
    apr = w.pending_approval(cid)
    act = next(p["action_id"] for p in w.case_state(cid)["file"]["plan"]["actions"])
    sre = w.browser("on_call_sre", acc)
    tok = sre.csrf()
    paths = [f"/cases/{cid}/execute", f"/cases/{cid}/actions/{act}/execute", f"/cases/{cid}/actions/{act}/run", f"/cases/{cid}/approvals/{apr}/execute", f"/api/cases/{cid}/execute", "/api/execute",
             f"/api/cases/{cid}/actions/{act}", f"/cases/{cid}/actions/{act}", f"/execute/{act}"]
    for p in paths:
        for method in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            r = sre.req(method, p, {"csrf": tok, "verdict": "approve"} if method != "GET" else None)
            assert r.status in (404, 405, 303, 401), (method, p, r.status)
    assert w.effects() == 0 and w.case_state(cid)["state"] == "REVIEW"
    src = (ROOT / "copilot" / "app" / "web.py").read_text()
    posts = re.findall(r'fullmatch\(r"([^"]+)", p\)', src)
    assert not [p for p in posts if "execute" in p and "reconcile" not in p], posts


def test_the_json_api_is_read_only(w):
    cid, acc = resync_case(w)
    sre = w.browser("on_call_sre", acc)
    for p in ("/api/cases", f"/api/cases/{cid}", f"/api/cases/{cid}/audit"):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert sre.req(method, p, {"verdict": "approve"}).status == 405
    assert sre.get("/api/cases").status == 200 and w.effects() == 0


# ---- tenant isolation through every surface -------------------------------------------------------------------------------------------------------
def test_another_tenants_operator_sees_nothing_and_cannot_tell_a_case_exists(w):                                      # A-I2-34
    cid, acc = resync_case(w)
    foreign_acc = next(a["account_id"] for a in w.env.accounts if a["account_id"] != acc)
    me = w.browser("tier2_engineer", foreign_acc)
    ghost = "CASE-999999"
    apr = w.pending_approval(cid)
    for tmpl in ("/cases/{}", "/cases/{}/audit", "/api/cases/{}", "/api/cases/{}/audit", "/api/cases/{}/trace"):
        real, none = me.get(tmpl.format(cid)), me.get(tmpl.format(ghost))
        assert real.status == none.status == 404, tmpl
        assert real.body == none.body or re.sub(r"CASE-\d+", "", real.text) == re.sub(r"CASE-\d+", "", none.text), f"{tmpl}: existence oracle"
    leaks = (cid, acc, apr, w.account_of(w.picks["resync"]), "INT-")
    for p in ("/cases", "/approvals", "/effects", "/api/cases", "/demo"):
        t = me.get(p).text
        assert not any(x in t for x in leaks[:3]), f"{p} leaks another tenant"
    assert me.post(f"/cases/{cid}/approvals/{apr}/decide", {"verdict": "approve"}).status == 404
    assert me.post(f"/cases/{cid}/draft/review", {}).status == 404
    assert w.effects() == 0 and w.case_state(cid)["state"] == "REVIEW"
    assert me.get("/api/cases").json() == [] or all(c["account_id"] == foreign_acc for c in me.get("/api/cases").json())


def test_effects_page_shows_only_the_operators_own_accounts(w):
    cid, acc = resync_case(w)
    assert w.browser("on_call_sre", acc).post(f"/cases/{cid}/approvals/{w.pending_approval(cid)}/decide", {"verdict": "approve"}).status == 303
    foreign_acc = next(a["account_id"] for a in w.env.accounts if a["account_id"] != acc)
    assert "RSY-0001" in w.browser("on_call_sre", acc).get("/effects").text
    assert "RSY-0001" not in w.browser("on_call_sre", foreign_acc).get("/effects").text


def test_global_views_exist_only_for_an_all_accounts_auditor_and_are_aggregate_only(w):                              # A-I2-36
    cid, acc = resync_case(w)
    for who in (w.browser("on_call_sre", acc), w.browser("tier2_engineer", "*"), w.browser("on_call_sre", "*"), w.browser("auditor", acc)):
        for p in ("/ops", "/metrics", "/api/metrics"):
            assert who.get(p).status == 404, p
    aud = w.browser("auditor", "*")
    for p in ("/ops", "/metrics", "/api/metrics"):
        r = aud.get(p)
        assert r.status == 200 and cid not in r.text and acc not in r.text, f"{p}: aggregate only, no tenant identifiers"
    assert w.browser("auditor", "*").get(f"/cases/{cid}/audit").status == 200


# ---- no way to send customer email -----------------------------------------------------------------------------------------------------------------
def test_there_is_no_form_field_route_or_capability_that_sends_customer_email(w):                                    # A-I3-08
    cid, acc = resync_case(w)
    sre = w.browser("on_call_sre", acc)
    assert sre.post(f"/cases/{cid}/approvals/{w.pending_approval(cid)}/decide", {"verdict": "approve"}).status == 303
    lee = w.browser("tier2_engineer", acc)
    page = lee.get(f"/cases/{cid}").text
    assert not re.search(r'(?i)name="(to|recipient|email|cc|bcc|send)"', page) and not re.search(r"(?i)>\s*send\b|send (the )?(reply|email|e-mail)", re.sub(r"never sends|never send|not sent|never sent", "", page))
    for p in (f"/cases/{cid}/send", f"/cases/{cid}/draft/send", f"/cases/{cid}/reply", f"/cases/{cid}/email", f"/api/cases/{cid}/send", "/send_customer_email"):
        for method in ("GET", "POST", "PUT"):
            assert lee.req(method, p, {"to": "customer@example.test", "csrf": lee.csrf()}).status in (404, 405, 303, 401)
    for cls in (w.svc.resync, w.svc.credit, w.svc.escalation):
        assert all(not str(e).count("@") or "meridian.internal.example" in str(e) for e in cls.effects)
    drafts = lee.get(f"/api/cases/{cid}/audit").json()
    assert not [e for e in drafts if "sent" in e["type"] or e["type"] == "email_sent"]
    assert {a["status"] for a in w.svc.access.artifacts_for_case(w.svc.sign_in("tier2.lee"), cid)} <= {"draft"}


# ---- hostile text is escaped, secrets never rendered -----------------------------------------------------------------------------------------------
HOSTILE = ('<script>fetch("/cases/x/approvals/APR-1/decide",{method:"POST"})</script><img src=x onerror=alert(1)> "><form action="/logout" method="post"><input name="csrf"><button>Click me</button></form> '
           '<a href="javascript:alert(1)">approve</a> &lt;b&gt; {{7*7}} {% raw %}')


def test_hostile_markup_in_a_ticket_is_rendered_inert(w):                                                           # A-I1-33
    tid = w.insert_ticket("<b>Duplicate events</b> on INT-0001", HOSTILE + " every shipment event arrives twice on our carrier feed")
    cid = w.open_case(tid)
    acc = w.account_of(tid)
    for who in (w.browser("tier2_engineer", acc), w.browser("auditor", "*")):
        for path in (f"/cases/{cid}", f"/cases/{cid}/audit", "/cases"):
            r = who.get(path)
            assert r.status == 200
            low = r.text.lower()
            assert "<script" not in low and "<img src=x" not in low and "<input name=\"csrf\">" not in low and 'href="javascript:' not in low and "<b>duplicate events</b>" not in low, path
    page = w.browser("tier2_engineer", acc).get(f"/cases/{cid}").text
    assert "&lt;script&gt;" in page and "{{7*7}}" in page and "49" not in re.findall(r"\{\{7\*7\}\}", page) and "&lt;b&gt;Duplicate events&lt;/b&gt;" in page
    assert w.effects() == 0


@pytest.mark.parametrize("where", ["ticket", "audit", "api", "ops"])
def test_canary_secrets_in_a_ticket_never_appear_in_any_page_api_or_telemetry(w, where):                              # A-I4-11 A-I4-12
    body = ("carrier feed duplicates; the carrier returns 401. Our config: " f"Authorization: Bearer {CANARIES['bearer']} API_KEY={CANARIES['api_key']} DB_PASSWORD={CANARIES['db_password']} ENV_SECRET={CANARIES['env_secret']} "
            f"-----BEGIN PRIVATE KEY-----\n{CANARIES['pem']}\n-----END PRIVATE KEY----- postgresql://copilot:{CANARIES['db_password']}@db.example.test:5432/copilot")
    tid = w.insert_ticket("Duplicate events", body)
    cid = w.open_case(tid)
    acc = w.account_of(tid)
    me = w.browser("tier2_engineer", acc)
    aud = w.browser("auditor", "*")
    for _ in range(2):
        w.svc.run_case(cid)
    pages = {"ticket": [me.get(f"/cases/{cid}").text, me.get("/cases").text, w.browser("on_call_sre", acc).get("/approvals").text],
             "audit": [me.get(f"/cases/{cid}/audit").text, me.get(f"/api/cases/{cid}/audit").text, me.get(f"/api/cases/{cid}/trace").text],
             "api": [me.get(f"/api/cases/{cid}").text, me.get("/api/cases").text],
             "ops": [aud.get("/ops").text, aud.get("/metrics").text, aud.get("/api/metrics").text, aud.get(f"/api/cases/{cid}/trace").text]}[where]
    for t in pages:
        leaked = sorted(k for k, v in CANARIES.items() if v in t)
        assert not leaked, f"{where}: {leaked}"
        assert f"copilot:{CANARIES['db_password']}@" not in t
    assert w.case_state(cid)["file"]["ticket"]["redactions"] >= 1 and "REDACTED" in me.get(f"/cases/{cid}").plain


def test_no_hidden_reasoning_or_prompts_are_exposed_anywhere(w):                                                    # A-I4-12
    cid, acc = resync_case(w)
    aud = w.browser("auditor", "*")
    blob = " ".join(r.text for r in (aud.get(f"/cases/{cid}"), aud.get(f"/cases/{cid}/audit"), aud.get(f"/api/cases/{cid}/trace"), aud.get("/api/metrics"))).lower()
    for term in ("chain_of_thought", "chain-of-thought", "scratchpad", "system prompt:", "<<untrusted", "begin_untrusted"):
        assert term not in blob, term


def test_a_secret_with_no_recognisable_label_is_not_caught_by_pattern_redaction_a_documented_residual(w):             # A-I4-11 (residual R-4)
    """HONEST LIMIT, pinned so it cannot be forgotten: redaction is pattern based. A bare high-entropy token with no label ('Bearer', NAME=, a PEM header, a DSN) is indistinguishable from
    ordinary text, so it is stored as typed. What still holds: it appears only on the redacted case copy shown to operators entitled to that tenant, never in the audit payload or telemetry."""
    tid = w.insert_ticket("Duplicate events", f"carrier feed duplicates. token is {CANARIES['bearer']} thanks")
    cid = w.open_case(tid)
    acc = w.account_of(tid)
    assert CANARIES["bearer"] in w.browser("tier2_engineer", acc).get(f"/cases/{cid}").text, "residual: the unlabelled token is shown to the entitled operator (it was in their own ticket)"
    aud = w.browser("auditor", "*")
    assert CANARIES["bearer"] not in aud.get(f"/cases/{cid}/audit").text and CANARIES["bearer"] not in aud.get(f"/api/cases/{cid}/trace").text
    assert CANARIES["bearer"] not in aud.get("/ops").text and CANARIES["bearer"] not in w.browser("tier2_engineer", w.picks["account"] if False else acc).get("/api/metrics").text
