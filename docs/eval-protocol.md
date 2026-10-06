# M2 retrieval evaluation protocol (frozen before any strategy was scored)

This file, `data/hand-labelled-v1/*` and the rubric are hashed in `docs/eval-freeze.json` **before** the retrieval code exists. `tests/test_eval_freeze.py` fails if any frozen file changes. If the protocol must change, that is a new protocol version with a new freeze, and the old results stay reported as such.

## Sets
| Set | Source | Evidence class | Used for |
|---|---|---|---|
| **dev** | the 300 generator tickets (`tickets.jsonl` minus any id in the hand set, via `contracts.tuning_view`) with `synthetic_labels.jsonl` | `synthetic_label` | every choice that has a parameter: abstention thresholds, RRF `k`, rerank depth. Nothing else may be tuned. |
| **held-out** | 40 hand-labelled tickets TCK-8001..8040 | `hand_labelled` (single AI reviewer, see RUBRIC.md) | the headline numbers. Run once per configuration; never used to choose anything. |

Reported separately, never blended. A parameter is chosen on dev, written to `copilot/retrieval/config.py`, committed, and only then is the held-out set scored.

## Strategies (independently selectable, same chunks, same queries)
- **A lexical**: PostgreSQL FTS, `english` configuration, query = OR of the lexemes of `plainto_tsquery(subject + body)`, `ts_rank`. No synonyms, no normalisation: the `resync`/`re-sync` miss is intentionally preserved.
- **B vector**: pgvector cosine over `BAAI/bge-small-en-v1.5` (quantised ONNX via fastembed), exact scan.
- **C hybrid**: Reciprocal Rank Fusion of A and B, `k=60` (the published default; sensitivity on dev only).
- **D hybrid + rerank**: C's top-20 chunks re-scored by `cross-encoder/ms-marco-MiniLM-L-6-v2`. The reranker is a *public pretrained model that has never seen this corpus or the hand set* and has no fitted parameters here; its only knob (depth 20) is fixed now. Included because it can be evaluated without contamination.

Query text = `subject + "\n" + body` of the ticket (customer-visible text only). No labels, account data, or product_area are used as retrieval signal.

## Output and governance (identical for all strategies)
Chunks → documents (a document's score is its best chunk) → **raw ranking**. Governance then produces the **governed outcome**:
1. `draft` and `superseded` documents are removed from evidence and listed as `excluded` (with reason) so the exclusion is visible.
2. Active near-duplicates (equal normalised body, different owner/version/path) are collapsed into one evidence item listing all copies.
3. Two or more remaining distinct active documents with the same `product_area` and equal normalised title whose bodies differ in their numeric tokens ⇒ `CONFLICTING_AUTHORITATIVE_EVIDENCE` (both documents returned, no winner chosen).
4. If the top governed document's confidence is below the strategy's threshold ⇒ `NO_SUFFICIENT_EVIDENCE`.
5. Otherwise `EVIDENCE` with the top-K governed items.
Retrieved text is data: its content is never parsed for instructions, and retrieval has no side effect.

## Metrics (all computed at document level; K = 1, 3, 5; MRR over top 10)
- **Hit@K**: ≥1 authoritative doc in the top K of the raw ranking. For near-duplicate sets, any copy counts.
- **MRR@10**: reciprocal rank of the first authoritative document.
- **Recall@K**: fraction of authoritative *equivalence groups* found in the top K.
- **Citation precision** (governed view): fraction of cited evidence items that are authoritative or supporting for that ticket.
- **Obsolete/forbidden rate**: fraction of tickets whose raw top-5 contains a `must_not_retrieve` document; and the same for the governed output (should be 0 for superseded/draft by construction, which is a property of governance, reported as such).
- **Abstention**: on `insufficient` tickets, the share answered `NO_SUFFICIENT_EVIDENCE` (correct); on `sufficient` tickets, the share wrongly abstained. **False confidence** = share of insufficient tickets for which a non-empty `EVIDENCE` outcome was produced.
- **Conflict detection**: on `conflicting` tickets, share ending in `CONFLICTING_AUTHORITATIVE_EVIDENCE`; false conflict rate on all others.
- Hit/MRR/Recall are computed only on tickets with `sufficient` labels; the others are scored by their own metrics above.
- **Slices**: the `primary_slice` of each ticket, plus `extra_tags` for the terminology breakdown. With n = 3..10 per slice the numbers are descriptive; no significance is claimed. Overall figures show a bootstrap 95% interval (2000 resamples, seed 20260101) and the interval is quoted whenever a difference between strategies is discussed.
- **Latency**: query-embedding time and database time measured separately, warm, 5 repetitions, median and p95, on the machine described in the report; **not** a production claim.

## Tenant scope
Retrieval over runbooks is global knowledge. Tenant-specific evidence (open incidents, similar past tickets) is read only through the signed-scope query catalogue; the evaluation measures incident evidence recall per ticket through that path.

## What would invalidate the benchmark
Any retrieval parameter chosen after seeing held-out results; changes to frozen files; labels edited after scoring. Failures found on held-out are *reported and analysed*, and fixes are only made if re-validated on dev, with the held-out result before the fix kept in the report.
