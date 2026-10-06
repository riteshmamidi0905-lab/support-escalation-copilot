"""Additive embedding cache for the M4 workflow: query vectors for every dataset ticket AFTER redaction (the workflow embeds the redacted text), the hand-set tickets and the hostile
test tickets. Computed with the real pinned local model and stored in data/embeddings/embeddings-m4.json; the frozen M2 cache is never modified. Needs `pip install -e ".[models]"`."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from copilot import contracts as C  # noqa: E402
from copilot.retrieval import embed  # noqa: E402
from copilot.retrieval.service import ticket_query  # noqa: E402
from copilot.workflow.retrieval_service import M4_CACHE  # noqa: E402
from copilot.workflow.runner import redact_ticket  # noqa: E402
from tests.support.attacks import EXTRA_QUERY_TEXTS  # noqa: E402

DS = ROOT / "data" / "meridian-seed-20260101"


def main():
    qs = set(EXTRA_QUERY_TEXTS)
    for t in C.read_jsonl(DS / "tickets.jsonl"):
        s, b, _ = redact_ticket(t["subject"], t["body"])
        qs.add(ticket_query(s, b))
    qs.add(ticket_query("Operations notes routing tips", "Do you have operations notes with routing tips for carrier integrations and dock scheduling? We also see duplicate events on our carrier feed."))
    base = embed.CachedEmbedder(embed.CACHE_DIR / "embeddings.json")
    live = embed.FastEmbedder()
    m4 = embed.CachedEmbedder(M4_CACHE, inner=live)
    todo = 0
    for q in sorted(qs):
        try:
            base.embed_query(q)
        except embed.CacheMiss:
            m4.embed_query(q)
            todo += 1
    m4.save()
    print(json.dumps({"queries": len(qs), "added_to_m4_cache": todo, "m4_cache_vectors": len(m4._d["vectors"])}))


if __name__ == "__main__":
    main()
