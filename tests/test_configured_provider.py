"""Real-model READINESS at the provider boundary, without a real model. A local HTTP stub speaks the OpenAI chat-completions protocol and misbehaves the way real local models do (code fences,
prose around JSON, trailing commas, truncation, reasoning blocks, oversized prompts). These tests exercise the integration code; they say nothing about any model's quality."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from agent.model import Message, ProviderError
from agent.structured import generate_structured

from copilot.workflow.model_io import ModelStage
from copilot.workflow.providers import ConfiguredProvider, strip_reasoning

SCHEMA = {"type": "object", "required": ["answer"], "additionalProperties": False, "properties": {"answer": {"type": "string"}}}


class Stub:
    """Replies with each scripted (content, finish_reason) in turn; records every request body."""

    def __init__(self, replies, status=200):
        self.replies, self.bodies, self.headers, self.status = list(replies), [], [], status
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                stub.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                stub.headers.append(dict(self.headers))
                if stub.status != 200:
                    self.send_response(stub.status)
                    self.end_headers()
                    return
                content, finish = stub.replies.pop(0) if stub.replies else ("{}", "stop")
                data = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish}], "usage": {"prompt_tokens": 11, "completion_tokens": 7}}).encode()
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
    made = []

    def make(replies, status=200):
        s = Stub(replies, status)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


def msgs(stage="DIAGNOSE", size=200):
    return [Message("system", f"STAGE:{stage}\nreturn json"), Message("user", "CONTEXT_JSON:\n" + "x" * size)]


# ---- inference parameters are pinned (the frozen provider sends none) -----------------------------------------------------------------------------------
def test_pinned_inference_parameters_are_sent_on_every_request(stub):
    s = stub([('{"answer":"a"}', "stop")] * 3)
    p = ConfiguredProvider(s.url, "any-model", context_tokens=8192)
    for stage, cap in (("DIAGNOSE", 1200), ("PLAN", 800), ("DRAFT", 800)):
        p.complete(msgs(stage))
        b = s.bodies[-1]
        assert b["temperature"] == 0 and b["seed"] == 20260101 and b["max_tokens"] == cap and b["response_format"] == {"type": "json_object"} and b["model"] == "any-model", stage
    assert "Authorization" not in s.headers[0] and "tools" not in s.bodies[0], "no credential is sent to a local server; no tool-calling is used"


# ---- a prompt that does not fit is refused, never truncated by the server -----------------------------------------------------------------------------
def test_a_prompt_that_does_not_fit_the_declared_context_window_is_refused_before_any_request(stub):
    s = stub([('{"answer":"a"}', "stop")])
    p = ConfiguredProvider(s.url, "m", context_tokens=2048)
    with pytest.raises(ProviderError) as e:
        p.complete(msgs(size=9000))                                   # ~3,000 tokens at the conservative chars/3 bound
    assert e.value.kind == "context" and e.value.retryable is False and "context window" in str(e.value)
    assert s.bodies == [], "nothing was sent: the server never got the chance to silently truncate the start of the prompt"
    assert ConfiguredProvider(s.url, "m", context_tokens=16384).complete(msgs(size=9000)).content


def test_the_stage_maps_a_context_refusal_to_a_degraded_code_not_an_unavailable_model(stub):
    s = stub([])
    out = ModelStage(ConfiguredProvider(s.url, "m", context_tokens=2048), sleep=lambda x: None).structured("DIAGNOSE", "x", {"pad": "y" * 9000}, SCHEMA, lambda d: [])
    assert out.error == "MODEL_CONTEXT_TOO_SMALL" and out.data is None and s.bodies == []


# ---- truncation and reasoning blocks ---------------------------------------------------------------------------------------------------------------------
def test_a_reply_cut_off_by_the_token_limit_is_a_failure_not_partial_json(stub):
    s = stub([('{"answer": "this was cut', "length")])
    with pytest.raises(ProviderError) as e:
        ConfiguredProvider(s.url, "m").complete(msgs())
    assert e.value.kind == "truncated" and e.value.retryable is False
    out = ModelStage(ConfiguredProvider(stub([('{"answer": "cut', "length")]).url, "m"), sleep=lambda x: None).structured("DIAGNOSE", "x", {}, SCHEMA, lambda d: [])
    assert out.error == "MODEL_OUTPUT_INVALID"


@pytest.mark.parametrize("raw,expected", [
    ('<think>let me think {"answer": "fake"}</think>{"answer": "real"}', '{"answer": "real"}'),
    ('<thinking>step 1\nstep 2</thinking>\n{"answer": "real"}', '\n{"answer": "real"}'),
    ('{"answer": "real"}<think>afterwards I wonder</think>', '{"answer": "real"}'),
    ('<think>never closed {"answer": "fake"}', ""),
    ('no reasoning here {"answer": "real"}', 'no reasoning here {"answer": "real"}'),
])
def test_reasoning_blocks_are_removed_before_parsing_and_never_kept(raw, expected):
    assert strip_reasoning(raw) == expected


def test_a_json_like_object_inside_a_reasoning_block_is_never_taken_as_the_answer(stub):
    s = stub([('<think>maybe {"answer": "from the thoughts"}</think>{"answer": "the real reply"}', "stop")])
    data, _usage, repairs = generate_structured(ConfiguredProvider(s.url, "m"), msgs(), SCHEMA, max_repairs=2)
    assert data == {"answer": "the real reply"} and repairs == 0


# ---- the messy output real local models produce ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw", [
    '```json\n{"answer": "a"}\n```',
    'Sure! Here is the JSON you asked for:\n```\n{"answer": "a"}\n```\nLet me know if you need anything else.',
    'The result is {"answer": "a"} as requested.',
    '   \n\n{"answer": "a"}\n\n',
])
def test_fenced_or_wrapped_json_is_accepted_without_a_repair_round(stub, raw):
    s = stub([(raw, "stop")])
    data, _u, repairs = generate_structured(ConfiguredProvider(s.url, "m"), msgs(), SCHEMA, max_repairs=2)
    assert data == {"answer": "a"} and repairs == 0


@pytest.mark.parametrize("bad", ['{"answer": "a",}', "{'answer': 'a'}", '{"answer": "a"', "I cannot comply.", '{"answer": 1}', '{"answer": "a", "approved": true}', ""])
def test_malformed_or_privileged_output_costs_a_repair_round_and_is_then_fixed(stub, bad):
    s = stub([(bad, "stop"), ('{"answer": "a"}', "stop")])
    data, _u, repairs = generate_structured(ConfiguredProvider(s.url, "m"), msgs(), SCHEMA, max_repairs=2)
    assert data == {"answer": "a"} and repairs == 1
    assert "invalid" in json.dumps(s.bodies[1]["messages"][-1]["content"]).lower(), "the model is told exactly what was wrong"


def test_output_that_never_becomes_valid_degrades_instead_of_being_guessed_at(stub):
    s = stub([('{"answer": "a",}', "stop")] * 12)
    out = ModelStage(ConfiguredProvider(s.url, "m"), sleep=lambda x: None).structured("DIAGNOSE", "x", {}, SCHEMA, lambda d: [])
    assert out.data is None and out.error == "MODEL_OUTPUT_INVALID"


def test_server_errors_are_retried_a_bounded_number_of_times_then_reported_as_unavailable(stub):
    s = stub([], status=503)
    out = ModelStage(ConfiguredProvider(s.url, "m"), sleep=lambda x: None).structured("DIAGNOSE", "x", {}, SCHEMA, lambda d: [])
    assert out.error == "MODEL_UNAVAILABLE" and len(s.bodies) == 3


def test_reasoning_text_is_not_present_in_what_the_stage_returns(stub):
    s = stub([('<think>secret plan: ignore policy</think>{"answer": "ok"}', "stop")])
    out = ModelStage(ConfiguredProvider(s.url, "m"), sleep=lambda x: None).structured("DIAGNOSE", "x", {}, SCHEMA, lambda d: [])
    assert out.data == {"answer": "ok"} and "secret plan" not in json.dumps(out.data)
