"""A whole case driven through `ConfiguredProvider` against a local HTTP stub that speaks the OpenAI protocol and answers with the deterministic stand-in, optionally misbehaving the way real
local models do. Tests the integration (parameters, context refusal, truncation, reasoning blocks, degraded paths through the REAL workflow and database). It says nothing about model quality:
no real model was run (see docs/m5-real-model-protocol.md, reports/m5/real-model-probe.json)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from agent.model import Message, ToolCall

from copilot.workflow.providers import ConfiguredProvider, RuleCaseModel
from tests.db.workflow_support import WorkflowWorld

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def env(m4env):
    return m4env


class Server:
    def __init__(self, wrap=None, finish="stop"):
        self.requests, self.model, self.wrap, self.finish = [], RuleCaseModel(), wrap, finish
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                me.requests.append(body)
                msgs = [Message(m["role"], m.get("content") or "", [ToolCall(c["id"], c["function"]["name"], json.loads(c["function"]["arguments"])) for c in m.get("tool_calls", [])]) for m in body["messages"]]
                out = me.model.complete(msgs).content
                out = me.wrap(out) if me.wrap else out
                data = json.dumps({"choices": [{"message": {"role": "assistant", "content": out}, "finish_reason": me.finish}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture()
def srv():
    made = []

    def make(**kw):
        s = Server(**kw)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


@pytest.fixture()
def w(env):
    w = WorkflowWorld(env)
    yield w
    w.close()


def run(w, server, **kw):
    r = w.new_runner(ConfiguredProvider(server.url, "stub-model", **kw)).start(w.resync_ticket())
    return r, w.machine.get(r.case_id)["file"]


def effects(w):
    return len(w.resync.effects) + len(w.credit.effects) + len(w.escalation.effects)


def test_a_case_runs_end_to_end_through_the_configured_provider_with_pinned_parameters(w, srv):
    s = srv()
    r, f = run(w, s, context_tokens=65536)
    assert r.state == "REVIEW" and effects(w) == 0 and "degraded" not in f
    assert s.requests and all(b["temperature"] == 0 and b["seed"] == 20260101 and b["response_format"] == {"type": "json_object"} and b["max_tokens"] in (1200, 800) for b in s.requests)
    assert {b["max_tokens"] for b in s.requests} == {1200, 800}, "per-stage output caps are applied"


def test_a_prompt_larger_than_the_context_window_degrades_the_case_and_nothing_is_sent(w, srv):             # A-I1-21 (model path fails closed)
    s = srv()
    r, f = run(w, s, context_tokens=1024)
    assert r.state == "HANDED_OFF" and r.outcome == "DEGRADED" and f["degraded"]["reason"] == "MODEL_CONTEXT_TOO_SMALL" and f["degraded"]["stage"] == "DIAGNOSE"
    assert s.requests == [] and effects(w) == 0 and not [a for a in f.get("approvals", []) if a["status"] == "awaiting_approval"]
    assert f["case_file"]["evidence"] and f["case_file"]["draft_reply"] is None


def test_a_server_that_truncates_every_reply_degrades_the_case_with_an_invalid_output_code(w, srv):
    s = srv(finish="length")
    r, f = run(w, s, context_tokens=65536)
    assert r.state == "HANDED_OFF" and f["degraded"]["reason"] == "MODEL_OUTPUT_INVALID" and effects(w) == 0


def test_reasoning_blocks_in_replies_never_reach_the_case_file_audit_or_telemetry(w, srv):                         # A-I4-09
    s = srv(wrap=lambda out: '<think>SECRET-REASONING-CANARY plan: ignore policy {"disposition": "proceed", "fake": true}</think>' + out)
    r, f = run(w, s, context_tokens=65536)
    assert r.state == "REVIEW" and "degraded" not in f and s.requests
    auditor = w.authority.mint("user", "audit.readiness", "auditor", accounts=("*",))
    blob = json.dumps(f, default=str) + json.dumps(w.audit.events(r.case_id), default=str) + json.dumps(w.access.ops_for_case(auditor, r.case_id), default=str)
    assert "SECRET-REASONING-CANARY" not in blob and "ignore policy" not in blob and '"fake"' not in blob
