# ADR-0007: Evaluation evidence classes are never merged into one number

**Status:** Accepted

## Context
Mixing generator-labelled, hand-labelled, deterministic and real-model results produces misleading accuracy.

## Decision
Four evidence classes (synthetic_label, hand_labelled, deterministic_runtime, real_model), each with its own dataset manifest and report section. The ~40-ticket hand-reviewed set is held out: `copilot.contracts.tuning_view` removes it from anything tuning code can see. Baselines are measured before any non-safety target is set; the four safety invariants are absolute from day one.

## Consequences
Reports are longer and honest. No headline accuracy.

## Alternatives considered
One blended score (rejected).
