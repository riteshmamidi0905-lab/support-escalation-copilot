# Real-model readiness (M5)

## Status, stated plainly
**Real-model evaluation was NOT executed.** `scripts/probe_local_runtime.py` (→ `reports/m5/real-model-probe.json`) found no local language-model server on this machine (Ollama, LM Studio, vLLM and
llama.cpp ports checked; no inference binaries on PATH; no inference libraries installed; the only model files on disk are the retrieval embedder and cross-encoder used by M2, which are not
generative). The machine has 8.6 GB of RAM. This project does not install runtimes, download model weights or call paid APIs to change that. **Every "model" result in this repository
(`reports/m4/*`, the demos, the UI) comes from `RuleCaseModel`, a deterministic rule-based stand-in, not an LLM, and is never labelled otherwise.**

What exists instead is the *readiness* a real run would depend on, each part tested without a model:

| readiness item | state | evidence |
|---|---|---|
| Provider abstraction (frozen runtime `ModelProvider`; stand-in, scripted-fault wrapper, OpenAI-compatible local path) | unchanged | M4 |
| Inference parameters pinned per request (temperature 0, seed, per-stage `max_tokens`, JSON-object format). The frozen provider sends **none** | `ConfiguredProvider` | `tests/test_configured_provider.py` |
| Prompt larger than the declared context window is **refused** (conservative 3 chars/token bound + 1,024 reply reserve) instead of being silently truncated by the server (which drops the start of the prompt: the instructions and untrusted-data warnings). Surfaces as a degraded case, `MODEL_CONTEXT_TOO_SMALL` | tested incl. a whole case through the real workflow | `tests/db/test_m5_real_model_readiness.py` |
| Truncated reply (`finish_reason=length`) is a failure, not partial JSON | tested | same |
| Reasoning blocks (`<think>…</think>`) removed before parsing; an unterminated block swallows the rest; the content never reaches the case file, audit or telemetry | tested | same |
| Fenced / prose-wrapped JSON accepted; trailing commas, single quotes, unterminated JSON, extra privileged fields cost one repair round with the exact problem stated; never-valid output degrades the case | tested | `tests/test_configured_provider.py` |
| Bounded retries on 5xx/timeouts; non-retryable errors are not retried | tested | same |
| Real-model protocol and case list **frozen before any run** (hash-locked) | `docs/m5-real-model-protocol.md`, `docs/real-model-freeze.json` | `tests/test_real_model_freeze.py` |
| The repository cannot contain a real-model result without a recorded run | pinned | `tests/test_real_model_freeze.py` |

## Measured prompt sizes (real prompts, stand-in answering; token figures are ESTIMATES)
`scripts/measure_prompt_budget.py`: the first three tickets of each scenario S1–S16 (33 cases, 83 model calls) through the real workflow. No real tokenizer was used (chars/4 as the frozen runtime
estimates, chars/3 as a conservative bound).

| stage | calls | chars p50 / p95 / max | est. tokens (chars/3) p50 / p95 / max |
|---|---|---|---|
| DIAGNOSE | 33 | 4,669 / 5,601 / 6,078 | 1,556 / 1,867 / 2,026 |
| PLAN | 24 | 6,039 / 7,249 / 7,631 | 2,013 / 2,416 / 2,543 |
| DRAFT | 26 | 5,238 / 6,059 / 6,106 | 1,746 / 2,019 / 2,035 |

Worst measured prompt ≈ 2,543 tokens (conservative bound). With the 1,024-token reply reserve a **2,048-token** window does not fit, **4,096 fits** (3,567), 8,192 and above fit comfortably.
Many local servers default to 2k–4k: configure the window explicitly (the workflow will refuse rather than truncate). This is a measurement of *our prompts*, not of any model.

## What this does NOT show
Nothing about whether any real model follows the structured-output contract, resists injected text, abstains when it should or writes a safe draft. The deterministic controls (trust boundary, policy,
approvals, idempotency) are what hold the four invariants regardless of the model; whether a real model *helps* is exactly what the frozen protocol would measure. On this 8.6 GB machine a
run would realistically use a 1–3B-class model, whose results would say little about larger models.

## To run it later
Start a local OpenAI-compatible server with a model you can name and pin, set its context window ≥ 4,096 (8,192 recommended), then follow `docs/m5-real-model-protocol.md` exactly; record the model digest and
every parameter; report every case, including failures; do not tune. Any change to the protocol is a v2 with a new freeze and a new, separately labelled result.
