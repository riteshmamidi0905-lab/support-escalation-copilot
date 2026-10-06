"""Choose every tunable retrieval parameter on the DEV set ONLY (generator tickets, hand-set ids removed via contracts.tuning_view).
Decides: query prefix (on/off), RRF k, and one abstention threshold per strategy. Writes copilot/retrieval/thresholds.json.
Needs the real models (pip install -e ".[models]"). Run:  python scripts/with_local_pg.py python scripts/tune_dev.py"""
import json
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_env  # noqa: E402

from copilot import contracts as C  # noqa: E402
from copilot.retrieval import config, embed, evaluate  # noqa: E402
from copilot.retrieval.service import Retriever, ticket_query  # noqa: E402

ROOT = bench_env.ROOT
HAND = ROOT / "data" / "hand-labelled-v1"


def run(rt, strat, dev, labels):
    rows, confs = [], []
    for t in dev:
        q = ticket_query(t["subject"], t["body"])
        res = rt.retrieve(q, strat)
        rows.append(evaluate.score_ticket(labels[t["ticket_id"]], rt.ranked(strat, q)[:10], res, rt.registry))
        confs.append(res["top_confidence"] if res["top_confidence"] is not None else float("-inf"))
    return rows, confs


def best_threshold(rows, confs):
    """Youden J = P(abstain | insufficient) - P(abstain | sufficient); ties -> the lowest threshold (least abstention)."""
    pos = [c for r, c in zip(rows, confs, strict=True) if r["sufficiency"] == "insufficient"]
    neg = [c for r, c in zip(rows, confs, strict=True) if r["sufficiency"] == "sufficient"]
    cands = sorted({c for c in confs if c != float("-inf")})
    cands = [(a + b) / 2 for a, b in zip(cands, cands[1:], strict=False)] or [0.0]
    best = max(cands[::-1], key=lambda t: (sum(c < t for c in pos) / max(len(pos), 1)) - (sum(c < t for c in neg) / max(len(neg), 1)))
    j = sum(c < best for c in pos) / len(pos) - sum(c < best for c in neg) / len(neg)
    return best, j


def main():
    tickets = C.tuning_view(C.read_jsonl(bench_env.ROOT / "data" / "meridian-seed-20260101" / "tickets.jsonl"), HAND)
    assert not {t["ticket_id"] for t in tickets} & C.heldout_ticket_ids(HAND)
    syn = {r["ticket_id"]: r for r in C.read_jsonl(bench_env.ROOT / "data" / "meridian-seed-20260101" / "synthetic_labels.jsonl")}
    labels = {t["ticket_id"]: evaluate.labels_from_synthetic(syn[t["ticket_id"]]) for t in tickets}
    tmp = Path(tempfile.mkdtemp()) / "emb.json"
    live = embed.FastEmbedder()
    cache = embed.CachedEmbedder(path=tmp, inner=live)
    env = bench_env.build(cache)
    reranker = embed.Reranker(path=Path(tempfile.mkdtemp()) / "rr.json", live=True)
    out = {"dev_tickets": len(tickets), "dev_sufficient": sum(1 for v in labels.values() if v["sufficiency"] == "sufficient")}
    try:
        res = {}
        for prefix in (False, True):
            cache.query_prefix = live.query_prefix = embed.QUERY_PREFIX if prefix else ""
            rt = Retriever(env.pool, cache, reranker, thresholds={})
            for strat in ("vector", "hybrid"):
                rows, _ = run(rt, strat, tickets, labels)
                res[(prefix, strat)] = evaluate.summarise(rows)
        out["prefix_dev"] = {f"prefix={p}/{s}": {"hit@1": v["hit@1"], "mrr@10": v["mrr@10"]} for (p, s), v in res.items()}
        use_prefix = res[(True, "vector")]["mrr@10"] > res[(False, "vector")]["mrr@10"] + 0.005
        cache.query_prefix = live.query_prefix = embed.QUERY_PREFIX if use_prefix else ""
        ks = {}
        for k in (20, 60, 100):
            rt = Retriever(env.pool, cache, reranker, thresholds={})
            rt.rrf_k = k
            rows, _ = run(rt, "hybrid", tickets, labels)
            ks[k] = evaluate.summarise(rows)["mrr@10"]
        out["rrf_k_dev_mrr"] = ks
        rrf_k = 60 if ks[60] >= max(ks.values()) - 0.005 else max(ks, key=ks.get)
        rt = Retriever(env.pool, cache, reranker, thresholds={})
        rt.rrf_k = rrf_k
        thr, per = {}, {}
        for strat in config.STRATEGIES:
            rows, confs = run(rt, strat, tickets, labels)
            thr[strat], j = best_threshold(rows, confs)
            sufficient_conf = [c for r, c in zip(rows, confs, strict=True) if r["sufficiency"] == "sufficient" and c != float("-inf")]
            insuf_conf = [c for r, c in zip(rows, confs, strict=True) if r["sufficiency"] == "insufficient" and c != float("-inf")]
            per[strat] = {"threshold": round(thr[strat], 5), "youden_j": round(j, 3), "median_conf_sufficient": round(statistics.median(sufficient_conf), 4), "median_conf_insufficient": round(statistics.median(insuf_conf), 4),
                          "dev_summary_without_abstention": evaluate.summarise(rows)}
        out["thresholds"] = per
        tuned = {"query_prefix": use_prefix, "rrf_k": rrf_k, "thresholds": {k: round(v, 5) for k, v in thr.items()}, "chosen_on": "dev (generator tickets only; held-out ids excluded via contracts.tuning_view)"}
        (ROOT / "copilot" / "retrieval" / "thresholds.json").write_text(json.dumps(tuned, indent=1, sort_keys=True) + "\n")
        (ROOT / "docs" / "m2-dev-tuning.json").write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
        print(json.dumps({k: v for k, v in out.items() if k != "thresholds"}, indent=1), json.dumps(tuned, indent=1))
        for s, v in per.items():
            print(s, {k: v[k] for k in ("threshold", "youden_j", "median_conf_sufficient", "median_conf_insufficient")})
    finally:
        bench_env.drop(env)


if __name__ == "__main__":
    main()
