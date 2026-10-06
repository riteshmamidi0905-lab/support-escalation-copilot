"""Build (or verify) the committed embedding/rerank caches with the REAL pinned local models. Needs: pip install -e ".[models]" and the model files
(downloaded once from Hugging Face at the pinned revisions; set COPILOT_MODEL_CACHE to choose where).
  python scripts/embed_cache.py build     # computes every vector/score the benchmark needs and writes data/embeddings/*.json
  python scripts/embed_cache.py verify    # recomputes a sample with the real model and checks cosine >= 0.999 against the cache (reproducibility check)
Ticket texts for the held-out set are embedded here only to make the benchmark replayable; embeddings are not used to tune anything."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from copilot import contracts as C  # noqa: E402
from copilot.retrieval import config, embed, probes  # noqa: E402
from copilot.retrieval.chunker import chunk_corpus  # noqa: E402
from copilot.retrieval.service import ticket_query  # noqa: E402

DS, HAND = ROOT / "data" / "meridian-seed-20260101", ROOT / "data" / "hand-labelled-v1"


def texts():
    docs = C.read_jsonl(DS / "runbooks.jsonl")
    chunks = [c["embed_text"] for c in chunk_corpus(docs)]
    tickets = [ticket_query(t["subject"], t["body"]) for t in C.read_jsonl(DS / "tickets.jsonl")] + [ticket_query(t["subject"], t["body"]) for t in C.read_jsonl(HAND / "hand_tickets.jsonl")]
    return chunks, sorted(set(tickets) | set(probes.TERMINOLOGY_PROBES) | set(probes.INJECTION_QUERIES))


def main(mode: str) -> int:
    chunks, queries = texts()
    prefix = embed.QUERY_PREFIX if config.use_query_prefix() else ""
    live = embed.FastEmbedder(query_prefix=prefix)
    if mode == "build":
        e = embed.CachedEmbedder(inner=live, query_prefix=prefix)
        e.embed_docs(chunks)
        for q in queries:
            e.embed_query(q)
        e.save()
        print("embedding cache:", len(e._d["vectors"]), "vectors")
        return 0
    cached = embed.CachedEmbedder(query_prefix=prefix)
    worst = 1.0
    for t, v in zip(chunks, live.embed_docs(chunks), strict=True):
        worst = min(worst, embed.cosine(v, cached.embed_docs([t])[0]))
    for q in queries[:40]:
        worst = min(worst, embed.cosine(live.embed_query(q), cached.embed_query(q)))
    print(json.dumps({"min_cosine_recomputed_vs_cached": round(worst, 6), "model": live.name}))
    return 0 if worst >= 0.999 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "verify"))
