"""M2 retrieval benchmark: CLEAN database -> migrate -> bootstrap -> regenerate dataset -> validate -> load chunks -> score all four strategies on the frozen sets.

  python scripts/with_local_pg.py python scripts/run_benchmark.py            # replay mode: embeddings/rerank scores come from the committed caches (no model needed)
  python scripts/with_local_pg.py python scripts/run_benchmark.py --live     # recompute with the real local models (needs `pip install -e ".[models]"`), measure latency/resources, refresh caches

Refuses to score if the frozen files (hand set, rubric, protocol) changed, or if a non-semantic embedder is in use.
Writes reports/m2/results.json and docs/m2-results.md (generated)."""
import argparse
import hashlib
import json
import platform
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_env  # noqa: E402

from copilot import contracts as C  # noqa: E402
from copilot.retrieval import config, embed, evaluate  # noqa: E402
from copilot.retrieval import probes as probes_mod  # noqa: E402
from copilot.retrieval.service import Retriever, ticket_query  # noqa: E402

ROOT = bench_env.ROOT
HAND = ROOT / "data" / "hand-labelled-v1"
DS = ROOT / "data" / "meridian-seed-20260101"


def check_freeze():
    frozen = json.loads((ROOT / "docs" / "eval-freeze.json").read_text())["files"]
    for rel, digest in frozen.items():
        if hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() != digest:
            raise SystemExit(f"REFUSING TO SCORE: {rel} changed after the freeze")
    return frozen


def git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()  # noqa: S603, S607
    except Exception:
        return "unknown"


def score_set(rt, strategies, items, labels, env=None, signer_cases=None):
    out = {s: [] for s in strategies}
    for t in items:
        q = ticket_query(t["subject"], t["body"])
        for s in strategies:
            scope = signer_cases(t) if signer_cases else None
            res = rt.retrieve(q, s, scope=scope, guard=env.guard if scope else None, ticket_id=t["ticket_id"])
            row = evaluate.score_ticket(labels[t["ticket_id"]], rt.ranked(s, q)[:10], res, rt.registry)
            if scope:
                got = {i["incident_id"] for i in res["tenant_evidence"]["open_incidents"]}
                want = set(labels[t["ticket_id"]]["incidents"])
                row["incident_recall"] = (len(want & got) / len(want)) if want else None
                row["incident_extra"] = len(got - want)
                row["similar_ticket_accounts"] = sorted({x["account_id"] for x in res["tenant_evidence"]["similar_tickets"]})
            out[s].append(row)
    return out


def latency(env, rt, live_emb, reranker, queries):
    res = {}
    for strat in config.STRATEGIES:
        db_t = []
        for _ in range(5):
            for q in queries:
                rt._qvec_cache.pop(q, None)
                rt._qvec(q)                                                      # embedding replayed from cache here: this timing is DATABASE + governance only
                t0 = time.perf_counter()
                rt.retrieve(q, strat)
                db_t.append((time.perf_counter() - t0) * 1000)
        res[strat] = {"db_and_governance_ms_median": round(statistics.median(db_t), 2), "p95": round(sorted(db_t)[int(0.95 * len(db_t)) - 1], 2), "n": len(db_t)}
    if live_emb:
        emb = []
        for _ in range(5):
            for q in queries:
                t0 = time.perf_counter()
                live_emb.embed_query(q)
                emb.append((time.perf_counter() - t0) * 1000)
        res["query_embedding_ms"] = {"median": round(statistics.median(emb), 2), "p95": round(sorted(emb)[int(0.95 * len(emb)) - 1], 2), "n": len(emb)}
        rr = []
        passages = list(rt.embed_texts.values())[:20]
        for q in queries:
            t0 = time.perf_counter()
            list(reranker._m.rerank(q, passages))
            rr.append((time.perf_counter() - t0) * 1000)
        res["rerank_20_passages_ms"] = {"median": round(statistics.median(rr), 2), "p95": round(sorted(rr)[int(0.95 * len(rr)) - 1], 2), "n": len(rr)}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--out", default="reports/m2")
    ap.add_argument("--control-order", default=config.CONTROL_ORDER, choices=("conflict_first", "abstain_first"), help="abstain_first reproduces the published M2 results")
    a = ap.parse_args()
    frozen = check_freeze()
    tuned = config.load_tuned()
    prefix = embed.QUERY_PREFIX if tuned.get("query_prefix") else ""
    live_emb = embed.FastEmbedder(query_prefix=prefix) if a.live else None
    emb = embed.CachedEmbedder(inner=live_emb, query_prefix=prefix)
    if not emb.semantic:
        raise SystemExit("REFUSING: non-semantic embedder")
    reranker = embed.Reranker(live=a.live)
    t_build = time.perf_counter()
    env = bench_env.build(emb)
    build_s = time.perf_counter() - t_build
    try:
        rt = Retriever(env.pool, emb, reranker, control_order=a.control_order)
        all_t = C.read_jsonl(DS / "tickets.jsonl")
        dev = C.tuning_view(all_t, HAND)
        syn = {r["ticket_id"]: r for r in C.read_jsonl(DS / "synthetic_labels.jsonl")}
        dev_labels = {t["ticket_id"]: evaluate.labels_from_synthetic(syn[t["ticket_id"]]) for t in dev}
        hand_t = C.read_jsonl(HAND / "hand_tickets.jsonl")
        hand_l = {r["ticket_id"]: evaluate.labels_from_hand(r) for r in C.read_jsonl(HAND / "hand_labels.jsonl")}
        accounts = {x["account_id"]: x for x in C.read_jsonl(DS / "accounts.jsonl")}
        assert all(t["account_id"] in accounts for t in hand_t)
        cases = lambda t: env.intake.open_case(t["ticket_id"])[1]  # noqa: E731  (trusted intake mints the scope from the TICKET ROW's account)
        dev_rows = score_set(rt, config.STRATEGIES, dev, dev_labels)
        hand_rows = score_set(rt, config.STRATEGIES, hand_t, hand_l, env, cases)
        res = {"meta": {
            "git_sha": git_sha(), "mode": "live" if a.live else "replay-from-committed-caches", "platform": platform.platform(), "python": platform.python_version(), "cpu": platform.processor() or platform.machine(),
            "postgres": env.pg_version, "pgvector": env.pgvector_version, "embedding_model": emb.name, "embedding_dim": embed.DIM, "reranker": reranker.name, "query_prefix": bool(prefix), "rrf_k": config.rrf_k(), "control_order": a.control_order,
            "thresholds": tuned["thresholds"], "chunks": env.counts["runbook_chunks"], "documents": env.counts["runbook_docs"], "dev_tickets": len(dev), "heldout_tickets": len(hand_t),
            "frozen_hashes": frozen, "dataset_manifest_sha256": hashlib.sha256((DS / "manifest.json").read_bytes()).hexdigest(), "clean_db_build_seconds": round(build_s, 2),
            "labeller": "single AI reviewer (Claude); not independent human annotation"},
            "dev": {s: {"summary": evaluate.summarise(r)} for s, r in dev_rows.items()},
            "heldout": {s: {"summary": evaluate.summarise(r), "by_slice": evaluate.by_slice(r),
                            "hit@5_ci95": evaluate.bootstrap_ci([x for x in r if x["sufficiency"] == "sufficient"], lambda x: float(x["hit@5"])),
                            "hit@1_ci95": evaluate.bootstrap_ci([x for x in r if x["sufficiency"] == "sufficient"], lambda x: float(x["hit@1"])),
                            "mrr_ci95": evaluate.bootstrap_ci([x for x in r if x["sufficiency"] == "sufficient"], lambda x: x["rr"]), "rows": r} for s, r in hand_rows.items()}}
        inc = {s: [x["incident_recall"] for x in r if x["incident_recall"] is not None] for s, r in hand_rows.items()}
        res["tenant_evidence"] = {"incident_recall": evaluate._mean(inc["lexical"]), "n_with_incident_labels": len(inc["lexical"])}
        # tenant check: every similar ticket must belong to the case's own account
        own = {t["ticket_id"]: t["account_id"] for t in hand_t}
        res["tenant_evidence"]["similar_ticket_accounts_other_than_case_account"] = sum(1 for x in hand_rows["lexical"] for acc in x["similar_ticket_accounts"] if acc != own[x["ticket_id"]])
        probes = {}
        for text in probes_mod.TERMINOLOGY_PROBES:
            probes[text] = {st: next((i for i, d in enumerate(rt.ranked(st, text)[:10], 1) if d["doc_id"] in ("RBK-0019", "RBK-0026")), None) for st in config.STRATEGIES}
        res["terminology_probes"] = {"note": "diagnostic only, fixed before running; rank of the re-sync runbook family (RBK-0019 current / RBK-0026 obsolete copy) for a bare query; null = not in top 10", "ranks": probes}
        if a.live:
            res["latency"] = latency(env, rt, live_emb, reranker, [ticket_query(t["subject"], t["body"]) for t in hand_t])
            import psycopg
            with psycopg.connect(env.db_admin_dsn) as c:
                sizes = {t: c.execute("SELECT pg_total_relation_size(%s)", (f"copilot.{t}",)).fetchone()[0] for t in ("runbook_chunks", "runbook_docs")}
            snap = lambda repo, rev: sum(f.stat().st_size for f in Path(snapshot_download(repo, revision=rev, cache_dir=None)).rglob("*") if f.is_file())  # noqa: E731
            from huggingface_hub import snapshot_download
            res["resources"] = {"embedding_model_mb": round(snap(embed.EMBED_REPO, embed.EMBED_REVISION) / 1e6, 1), "reranker_mb": round(snap(embed.RERANK_REPO, embed.RERANK_REVISION) / 1e6, 1),
                                "pg_total_bytes": sizes, "embedding_cache_file_mb": round((embed.CACHE_DIR / "embeddings.json").stat().st_size / 1e6, 2), "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)}
            emb.save()
            reranker.save()
        outdir = ROOT / a.out
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "results.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
        print("wrote", outdir / "results.json")
        for s in config.STRATEGIES:
            h = res["heldout"][s]["summary"]
            print(s, {k: h[k] for k in ("hit@1", "hit@3", "hit@5", "mrr@10", "citation_precision", "false_confidence_on_insufficient", "abstained_on_insufficient", "conflict_detected_on_conflicting")})
    finally:
        bench_env.drop(env)


if __name__ == "__main__":
    main()
