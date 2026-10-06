# Evaluation methodology

## Four evidence classes (never combined into one number)
| Class | Source of truth | Used for | Weakness to state |
|---|---|---|---|
| `synthetic_label` | the generator's own answer key | regression, development, large-n retrieval/scenario checks | circular: the generator knows its own answer |
| `hand_labelled` | ~40 tickets authored and labelled by a person, held out | the headline retrieval/citation measurements | small n; single reviewer |
| `deterministic_runtime` | scripted/rule providers | policy, approvals, refusals, fault handling, isolation | measures the runtime, not an LLM |
| `real_model` | a local or hosted model | LLM-specific behaviour | non-deterministic; recorded, never CI-gated; **no claim until executed** |

## Absolute invariants (pass/fail from day one)
I1 no gated action without approval · I2 no cross-tenant exposure · I3 no customer email · I4 no secret in logs. Every other threshold is set **after** a baseline.

## Retrieval comparison (M2) — executed; protocol in `docs/eval-protocol.md`, results in `docs/m2-results.md`
Strategies: lexical (FTS) · vector (pgvector) · hybrid · hybrid+rerank only if justified. Measured: hit@k, MRR, version-resolution accuracy, empty-result rate, and **citation quality** (do the cited chunks support the claim). Reported per evidence class and per strategy, with the same queries and corpus. The default is chosen from these results.

## Business proxies
Time to first useful triage, acceptance/edit rate, escalation precision, approval turnaround are *defined* but stay proxies until a replay or controlled comparison exists. No improvement percentage is stated before then.

## Reproducibility
Seeded generation; manifests with SHA-256; pinned dependencies (runtime by commit SHA); every report records dataset ids/hashes, code SHA and provider.
