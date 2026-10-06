# ADR-0013: Retrieval architecture — vector search plus lifecycle governance, with no reliance on similarity for sufficiency

**Status:** Accepted (M2). Evidence: `docs/m2-results.md`, `reports/m2/results.json`, frozen protocol `docs/eval-protocol.md`.

## Context
Meridian's support evidence is a small corpus of runbooks with lifecycle metadata (active / superseded / draft, versions, near-duplicates, two genuinely conflicting active pairs, four injected-instruction documents) plus tenant-scoped incidents and past tickets. The question was which architecture works best *for this evidence*, not whether vector search is fashionable. Four strategies were implemented over the same chunks and compared on a frozen 40-ticket hand-labelled held-out set (single AI reviewer, see RUBRIC.md) with parameters chosen only on 300 generator tickets: A lexical (PostgreSQL FTS, OR of lexemes), B vector (bge-small-en-v1.5, pgvector exact cosine), C hybrid (RRF), D hybrid + cross-encoder rerank.

## Evidence (held-out, 25 answerable tickets; 95% bootstrap intervals overlap — treat as descriptive)
| | A lexical | B vector | C hybrid | D rerank |
|---|---|---|---|---|
| Hit@1 | 0.60 | **0.80** | 0.68 | 0.76 |
| Hit@5 | 0.92 | **0.96** | 0.92 | **0.96** |
| MRR@10 | 0.70 | **0.87** | 0.79 | 0.84 |
| governed success | 44% | **88%** | 68% | 48% |
| wrongly abstained (answerable) | 56% | **12%** | 32% | 52% |
| false confidence (unanswerable, n=11) | 45% | 55% | 45% | 36% |
| conflicts detected (n=4) | 3 | **4** | 3 | **4** |
| extra latency per query (this laptop) | 0.2 ms DB | 0.3 ms DB + 7.5 ms embed | 0.5 ms + 7.5 ms | 0.5 ms + 7.5 ms + **109 ms** rerank |
| extra footprint | none | 67 MB model | 67 MB | 67 MB + 347 MB snapshot |

On the dev set the ordering is the same (vector/rerank > hybrid > lexical) and every number is much higher, i.e. the synthetic set flatters all strategies.

## Decision
1. **Default retrieval = B (vector, pgvector, bge-small-en-v1.5 pinned revision, exact scan)**, followed by the governance layer (lifecycle exclusion, near-duplicate collapse, conflict sets from corpus metadata). Strategies A–D remain independently selectable behind one interface and are re-run by `scripts/run_benchmark.py`.
2. **Not chosen: lexical** — simplest and cheapest, but lowest MRR on both sets and it fails on vocabulary mismatch (bare `resync` misses `re-sync`; "re-sent" vs "retry"; a full paraphrase ticket is missed entirely). It stays the **fallback** if the embedding model cannot be provisioned; with governance it is still safe (never worse on lifecycle/tenant properties), only less often right.
3. **Not chosen: hybrid** — with equal-weight RRF it ranked *below* vector on dev and held-out and added a second query path. Weighted fusion was not tried (it would be more tuning on a synthetic dev set). No evidence justifies the complexity.
4. **Not chosen: reranker** — no ranking gain over B (MRR 0.84 vs 0.87, within noise), +109 ms CPU per query and a 347 MB snapshot. Its better abstention on dev did not transfer.
5. **Evidence sufficiency is NOT decided by similarity confidence.** Retrieval returns `NO_SUFFICIENT_EVIDENCE` only as a *necessary-but-not-sufficient* signal. Dev-tuned thresholds separated answerable from unanswerable almost perfectly on templated tickets (AUC 0.95–1.00) and barely at all on free-language held-out tickets (AUC 0.55–0.66); every strategy returned look-alike filler pages and injected documents as evidence for unanswerable questions (36–55% false confidence). M3/M4 must therefore judge sufficiency from the evidence *content* and the case (and escalate when unsure); a retrieval result with outcome `EVIDENCE` is never proof that the question is answerable.
6. **Governance is not optional.** Without it 45–50% of tickets had a superseded or draft document in the raw top 5; with it the governed output had 0 across all strategies, and conflicting active documents are surfaced as `CONFLICTING_AUTHORITATIVE_EVIDENCE` (both documents, no winner) instead of returning whichever ranks first.
7. **Retrieved text is data.** Structured evidence carries `content_trust: untrusted_data`; the result schema has no field that can carry a directive; retrieval has no write path, no email/network capability and cannot alter scope. The regex flag `instruction_like_text` is advisory annotation only and is not the boundary. Injected documents are *retrievable* (and are returned for on-topic queries); containing them is the job of the runtime policy (M3/M4), which is not yet built.

## Consequences
- A local 67 MB open model is a runtime dependency (CPU, ~7 ms/query; ~460 MB process RSS with both models loaded in the benchmark). CI does not need it: the committed, hash-keyed embedding and rerank caches replay the benchmark, and `scripts/embed_cache.py verify` checks the cache against the real model (min cosine 0.999999 on this machine). Embeddings are never faked: the non-semantic plumbing embedder is labelled and refused by the benchmark.
- Exact (non-indexed) vector search is defensible only at this scale (60 chunks). Above ~100k chunks an ANN index and a re-measurement are needed.
- The abstention thresholds in `thresholds.json` are **uncalibrated for real tickets**. They are kept (chosen on dev, recorded) so the behaviour is explicit, but must not be presented as an evidence-gap detector. Calibrating them needs in-distribution labelled tickets, which do not exist yet.
- Abstain-before-conflict ordering lost a conflict on two strategies (TCK-8018); not changed after seeing held-out results; revisit with a new frozen protocol.

## Limits of this decision (stated)
Single AI reviewer and author-of-everything contamination risk; n=40 (25/11/4); one embedding model and one reranker tried; no hand-tuning of fusion weights; corpus of 60 short documents where each document is one chunk, so chunking and long-document retrieval are untested; latency on one laptop; the generator-based dev set is circular. A different corpus or a real ticket stream could reorder A–D.

## Alternatives considered
BM25 extension / `pg_search` (rejected: another dependency for the strategy that lost); hosted embedding API (rejected: paid, non-reproducible in CI); ANN index (premature); LLM-judged sufficiency inside retrieval (rejected: would move model judgement into the evidence layer and is not measurable without a real-model evaluation).

## Addendum (M3): control-order correction
See ADR-0014 and `docs/m2-conflict-order-rerun.md`: conflict detection now precedes abstention (as the frozen protocol specified). The decision above is unchanged; the published tables in this ADR are the original M2 results (abstain-first) and remain reproducible with `--control-order abstain_first`.
