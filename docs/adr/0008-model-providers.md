# ADR-0008: Deterministic and scripted providers first; local model later

**Status:** Accepted

## Context
CI must be reproducible, free and offline; real-model claims need real-model runs.

## Decision
Development and CI use the runtime's scripted/rule providers behind the existing `ModelProvider` interface. A local-model (Ollama, OpenAI-compatible) path is added at a later milestone using the same interface. Real-model numbers are reported only after a real-model evaluation is executed and are stored as a separate evidence class.

## Consequences
No paid API in CI. Deterministic runs cannot reveal LLM-specific failures; the real-model evaluation is where those are explored.

## Alternatives considered
Hosted API in CI (rejected: cost, flakiness).
