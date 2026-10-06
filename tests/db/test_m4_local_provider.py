"""The approved local-model path (Ollama / vLLM / LM Studio via the frozen runtime's OpenAI-compatible provider), exercised end to end against a LOCAL STUB SERVER that speaks the
OpenAI chat-completions protocol. This tests the integration BOUNDARY (HTTP, schema repair, retries, degraded paths). The stub is the deterministic stand-in, NOT a language model: nothing
here says anything about LLM quality, and no real model was run (see docs/m4-results.md)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from agent.model import Message, ToolCall

from copilot.workflow.providers import RuleCaseModel, local_provider, local_server_info
from tests.db.workflow_support import WorkflowWorld

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def env(m4env):
    return m4env


class Stub:
    def __init__(self, mode="ok"):
        self.mode, self.requests, self.model = mode, [], RuleCaseModel()
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append(body)
                if stub.mode == "503":
                    self.send_response(503)
                    self.end_headers()
                    return
                if stub.mode == "garbage":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"<html>not json</html>")
                    return
                msgs = [Message(m["role"], m.get("content") or "", [ToolCall(c["id"], c["function"]["name"], json.loads(c["function"]["arguments"])) for c in m.get("tool_calls", [])]) for m in body["messages"]]
                out = stub.model.complete(msgs).content
                data = json.dumps({"choices": [{"message": {"role": "assistant", "content": out}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
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
def stub():
    s = Stub()
    yield s
    s.close()


def test_full_case_through_the_openai_compatible_boundary(env, stub):
    w = WorkflowWorld(env)
    try:
        r = w.new_runner(local_provider(stub.url, "stub-model")).start(w.routine_ticket())
        f = w.machine.get(r.case_id)["file"]
        assert r.state == "CLOSED" and r.outcome == "ANSWER" and f["case_file"]["draft_reply"]["source"] == "model"
        assert stub.requests and all(q["model"] == "stub-model" for q in stub.requests)
        assert {"DIAGNOSE", "DRAFT"} <= {m["content"].split("STAGE:")[1].split("\n")[0] for q in stub.requests for m in q["messages"] if m["role"] == "system" and "STAGE:" in m["content"]}
        gated = w.new_runner(local_provider(stub.url, "stub-model")).start(w.resync_ticket())
        assert gated.state == "REVIEW" and len(w.resync.effects) == 0
    finally:
        w.close()


@pytest.mark.parametrize("mode,reason", [("503", "MODEL_UNAVAILABLE"), ("garbage", "MODEL_OUTPUT_INVALID")])
def test_server_failures_degrade_the_case_explainably(env, mode, reason):
    s = Stub(mode)
    w = WorkflowWorld(env)
    try:
        r = w.new_runner(local_provider(s.url, "stub-model", timeout=3)).start(w.routine_ticket())
        f = w.machine.get(r.case_id)["file"]
        assert r.state == "HANDED_OFF" and r.outcome == "DEGRADED" and f["degraded"]["reason"] in (reason, "MODEL_UNAVAILABLE")
    finally:
        w.close()
        s.close()


def test_no_local_server_means_no_real_model_run_is_claimed():
    assert local_server_info("http://127.0.0.1:9", timeout=0.2) is None
    with pytest.raises(ValueError):
        local_provider(model="")
