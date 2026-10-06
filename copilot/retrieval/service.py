"""Retrieval service: four independently selectable strategies over the same chunks, then lifecycle/duplicate/conflict/abstention governance.

Everything that touches the database goes through the FIXED query catalogue as the least-privileged application role. Runbooks are global knowledge;
tenant evidence (open incidents, similar tickets of the case's own account) is read only under a database-verified signed scope. Retrieved text is DATA:
nothing here parses it for instructions, and retrieval has no side effect (the application role is read-only).
"""
from __future__ import annotations

import hashlib
import math
from typing import Any

from copilot.db import queries as Q

from . import config
from .chunker import chunk_document
from .embed import Embedder, Reranker
from .governance import Registry, exclusion_reason, instruction_like

NO_EVIDENCE, CONFLICT, EVIDENCE = "NO_SUFFICIENT_EVIDENCE", "CONFLICTING_AUTHORITATIVE_EVIDENCE", "EVIDENCE"


def ticket_query(subject: str, body: str) -> str:
    return f"{subject}\n{body}"[: config.MAX_QUERY_CHARS]


class Retriever:
    def __init__(self, pool, embedder: Embedder, reranker: Reranker | None = None, thresholds: dict | None = None, control_order: str = config.CONTROL_ORDER):
        if control_order not in ("conflict_first", "abstain_first"):
            raise ValueError("control_order must be 'conflict_first' (current) or 'abstain_first' (the published M2 behaviour, kept only to reproduce it)")
        self.control_order = control_order
        self.pool, self.embedder, self.reranker = pool, embedder, reranker
        self.thresholds = config.thresholds() if thresholds is None else thresholds
        rows = Q.run(pool, None, None, "list_runbooks", {})
        self.registry = Registry(rows)
        self.chunks: dict[str, list[dict]] = {}
        for r in rows:
            self.chunks[r["doc_id"]] = [dict(c) for c in Q.run(pool, None, None, "get_doc_chunks", {"doc_id": r["doc_id"]})]
        self.embed_texts = {c["chunk_id"]: c["embed_text"] for r in rows for c in chunk_document(r)}   # same deterministic chunker that filled the table (a test compares them)
        self.rrf_k = config.rrf_k()
        self._qvec_cache: dict[str, list[float]] = {}

    # ---- ranked lists ---------------------------------------------------------------------------------------------------------------
    def _docs_from_chunks(self, rows: list[dict]) -> list[dict]:
        seen, out = set(), []
        for r in rows:
            if r["doc_id"] not in seen:
                seen.add(r["doc_id"])
                out.append({"doc_id": r["doc_id"], "chunk_id": r["chunk_id"], "score": float(r["score"])})
        return out

    def _qvec(self, text: str) -> list[float]:
        if text not in self._qvec_cache:
            self._qvec_cache[text] = self.embedder.embed_query(text)
        return self._qvec_cache[text]

    def ranked(self, strategy: str, query: str) -> list[dict]:
        """Document-level raw ranking (all statuses, no governance). `score` is on the strategy's own scale."""
        if strategy == "lexical":
            return self._docs_from_chunks(Q.run(self.pool, None, None, "search_chunks_lexical", {"q": query, "limit": config.CANDIDATES}))
        if strategy == "vector":
            return self._docs_from_chunks(Q.run(self.pool, None, None, "search_chunks_vector", {"v": self._qvec(query), "limit": config.CANDIDATES}))
        if strategy in ("hybrid", "rerank"):
            lex = self._docs_from_chunks(Q.run(self.pool, None, None, "search_chunks_lexical", {"q": query, "limit": config.CANDIDATES}))
            vec = self._docs_from_chunks(Q.run(self.pool, None, None, "search_chunks_vector", {"v": self._qvec(query), "limit": config.CANDIDATES}))
            k, fused, chunk_of = self.rrf_k, {}, {}
            for lst in (vec, lex):
                for rank, d in enumerate(lst, 1):
                    fused[d["doc_id"]] = fused.get(d["doc_id"], 0.0) + 1.0 / (k + rank)
                    chunk_of.setdefault(d["doc_id"], d["chunk_id"])
            norm = 2.0 / (k + 1)
            hyb = [{"doc_id": i, "chunk_id": chunk_of[i], "score": s / norm} for i, s in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))]
            if strategy == "hybrid":
                return hyb
            return self._rerank(query, hyb)
        raise ValueError(f"unknown strategy {strategy!r}")

    def _rerank(self, query: str, hyb: list[dict]) -> list[dict]:
        if self.reranker is None:
            raise RuntimeError("rerank strategy needs a Reranker")
        head, tail = hyb[: config.RERANK_DEPTH], hyb[config.RERANK_DEPTH:]
        sc = self.reranker.scores(query, [self.embed_texts[d["chunk_id"]] for d in head])
        out = sorted(({**d, "score": float(s)} for d, s in zip(head, sc, strict=True)), key=lambda d: (-d["score"], d["doc_id"]))
        return out + [{**d, "score": float("-inf")} for d in tail]

    # ---- governed result ------------------------------------------------------------------------------------------------------------
    def retrieve(self, query: str, strategy: str, *, scope=None, guard=None, ticket_id: str = "", top_k: int = config.TOP_K) -> dict[str, Any]:
        raw = self.ranked(strategy, query)[:10]
        reg = self.registry
        excluded, active, seen_groups = [], [], set()
        for rank, d in enumerate(raw, 1):
            doc = reg.docs[d["doc_id"]]
            reason = exclusion_reason(doc)
            if reason:
                excluded.append({"doc_id": d["doc_id"], "reason": reason, "rank": rank, "score": _num(d["score"])})
                continue
            gkey = tuple(reg.group(d["doc_id"]))
            if gkey in seen_groups:
                continue
            seen_groups.add(gkey)
            active.append({**d, "rank": rank})
        thr = self.thresholds.get(strategy)
        top_conf = active[0]["score"] if active else None
        base = {"schema_version": "1", "strategy": strategy, "query_sha256": hashlib.sha256(query.encode()).hexdigest()[:16], "threshold": thr, "top_confidence": _num(top_conf), "excluded": excluded}
        tenant = self._tenant_evidence(query, scope, guard, ticket_id)

        def conflict_sets_in(items):
            sets, seen = [], set()
            for a in items[: config.CONFLICT_TOPN]:
                cs = reg.conflict_set(a["doc_id"])
                if cs and tuple(cs) not in seen:
                    seen.add(tuple(cs))
                    sets.append(cs)
            return sets

        def conflict_result(sets):
            members = sorted({m for cs in sets for m in cs})
            items = [self._item(m, next((a for a in active if m in reg.group(a["doc_id"])), None), strategy, sets) for m in members]
            return {**base, "outcome": CONFLICT, "evidence": items, "conflict_sets": sets, "abstain_reason": "active documents disagree; no winner is chosen", "tenant_evidence": tenant}

        low = not active or (thr is not None and top_conf is not None and top_conf < thr)
        if self.control_order == "conflict_first":
            # A known disagreement between ACTIVE documents among the best matches is a fact about the corpus, not a similarity judgement. A low-confidence abstention
            # must never hide it (hiding a conflict is the unsafe error; surfacing one only costs a human look). Matches the frozen protocol: conflict (step 3) before abstention (step 4).
            sets = conflict_sets_in(active)
            if sets:
                return conflict_result(sets)
        if low:
            return {**base, "outcome": NO_EVIDENCE, "evidence": [], "conflict_sets": [], "abstain_reason": "no active evidence above the confidence threshold" if active else "no active document retrieved", "tenant_evidence": tenant}
        top = [a for a in active if thr is None or a["score"] >= thr][:top_k]
        if self.control_order == "abstain_first":
            sets = conflict_sets_in(top)
            if sets:
                return conflict_result(sets)
        return {**base, "outcome": EVIDENCE, "evidence": [self._item(a["doc_id"], a, strategy, []) for a in top], "conflict_sets": [], "abstain_reason": None, "tenant_evidence": tenant}

    def _item(self, doc_id: str, hit: dict | None, strategy: str, conflict_sets: list[list[str]]) -> dict:
        reg = self.registry
        doc = reg.docs[doc_id]
        chunk = next((c for c in self.chunks[doc_id] if hit and c["chunk_id"] == hit["chunk_id"]), self.chunks[doc_id][0])
        if hit and hit["doc_id"] != doc_id:                                    # the retrieved copy is a duplicate of this representative
            chunk = self.chunks[doc_id][0]
        conflicts = sorted({m for cs in conflict_sets if doc_id in cs for m in cs if m != doc_id})
        return {
            "doc_id": doc_id, "chunk_id": chunk["chunk_id"], "title": doc["title"], "section": chunk["section"], "version": doc["version"], "status": doc["status"],
            "effective_from": str(doc["effective_from"]), "owner": doc["owner"], "source_path": doc["source_path"], "supersedes": doc["supersedes"],
            "source_location": {"char_start": chunk["char_start"], "char_end": chunk["char_end"]},
            "strategy": strategy, "rank": hit["rank"] if hit else None, "score": _num(hit["score"]) if hit else None,
            "citation": f"{doc_id}@{doc['version']}#{chunk['chunk_id']}[{chunk['char_start']}:{chunk['char_end']}]",
            "text": chunk["text"], "content_trust": "untrusted_data", "flags": ["instruction_like_text"] if instruction_like(chunk["text"]) else [],
            "duplicates": [d for d in reg.group(doc_id) if d != doc_id],
            "authority": {"lifecycle": doc["status"], "conflicts_with": conflicts},
        }

    def resolve_citation(self, citation: str) -> str:
        """Return the exact source text a citation points at, or raise. Used to prove citations resolve to real evidence."""
        head, _, rest = citation.partition("#")
        doc_id = head.split("@")[0]
        rng = rest[rest.index("[") + 1: rest.index("]")]
        a, b = (int(x) for x in rng.split(":"))
        doc = self.registry.docs[doc_id]
        if head.split("@")[1] != doc["version"]:
            raise ValueError("citation version does not match the document")
        return doc["body_markdown"][a:b]

    def _tenant_evidence(self, query, scope, guard, ticket_id) -> dict | None:
        if scope is None:
            return None
        inc = Q.run(self.pool, scope, guard, "open_incidents_for_account", {})
        sim = Q.run(self.pool, scope, guard, "similar_account_tickets", {"q": query, "exclude": ticket_id or "-", "limit": 5})
        return {"account_id": scope.account_id, "open_incidents": [{k: (str(v) if k == "started_at" else v) for k, v in r.items()} for r in inc],
                "similar_tickets": [{**r, "score": _num(r["score"])} for r in sim]}


def _num(x):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), 5)
