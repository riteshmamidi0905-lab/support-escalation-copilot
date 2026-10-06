"""Retrieval for the workflow: ADR-0013's default (vector + governance) with the lexical strategy as the documented provisioning fallback.
Retrieval SCORES are not used as a gate here (M2: similarity is not a sufficiency detector): the retriever runs without abstention thresholds, and relevance is judged on content
(advisory model assessment) while sufficiency is decided by deterministic facts and human approval."""
from __future__ import annotations

from typing import Any

from copilot.retrieval import embed
from copilot.retrieval.service import Retriever, ticket_query

M4_CACHE = embed.CACHE_DIR / "embeddings-m4.json"


class LayeredEmbedder:
    """Replays vectors from several committed caches (the frozen M2 cache first, then the additive M4 cache). Never fabricates a vector."""
    semantic = True

    def __init__(self, paths=None):
        self.layers = [embed.CachedEmbedder(p) for p in (paths or [embed.CACHE_DIR / "embeddings.json", M4_CACHE]) if p.exists()]
        self.name = "BAAI/bge-small-en-v1.5 (layered replay cache)"

    def embed_docs(self, texts):
        return self.layers[0].embed_docs(texts)

    def embed_query(self, text):
        for layer in self.layers:
            try:
                return layer.embed_query(text)
            except embed.CacheMiss:
                continue
        raise embed.CacheMiss("query vector not in any cache")


class RetrievalService:
    def __init__(self, pool, embedder, reranker=None, top_k: int = 5, text_chars: int = 600):
        self.rt = Retriever(pool, embedder, reranker, thresholds={})
        self.top_k, self.text_chars = top_k, text_chars
        self.fail: set[str] = set()                                  # fault injection: {"vector", "lexical"}

    def retrieve(self, subject: str, body: str, scope, guard, ticket_id: str) -> dict[str, Any]:
        q = ticket_query(subject, body)
        errors, res, used = [], None, None
        for strategy in ("vector", "lexical"):
            try:
                if strategy in self.fail:
                    raise RuntimeError("injected retrieval failure")
                res = self.rt.retrieve(q, strategy, scope=scope, guard=guard, ticket_id=ticket_id, top_k=self.top_k, include_context=True)
                used = strategy
                break
            except Exception as e:                                   # noqa: BLE001 - recorded, then fall back
                errors.append(f"{strategy}:{type(e).__name__}")
        if res is None:
            return {"outcome": "FAILED", "strategy": None, "fallback_reason": ";".join(errors), "evidence": [], "excluded": [], "tenant": None, "conflict_sets": []}
        ev = []
        for n, e in enumerate(res["evidence"], 1):
            ev.append({"handle": f"E{n}", "doc_id": e["doc_id"], "version": e["version"], "status": e["status"], "title": e["title"], "section": e["section"], "citation": e["citation"],
                       "text": e["text"][: self.text_chars], "flags": e["flags"], "conflicts_with": e["authority"]["conflicts_with"], "duplicates": e["duplicates"], "rank": e["rank"]})
        t = res["tenant_evidence"] or {}
        return {"outcome": res["outcome"], "strategy": used, "fallback_reason": ";".join(errors) or None, "evidence": ev, "excluded": res["excluded"], "conflict_sets": res["conflict_sets"],
                "tenant": {"open_incidents": t.get("open_incidents", []), "similar_tickets": [x["ticket_id"] for x in t.get("similar_tickets", [])]}}
