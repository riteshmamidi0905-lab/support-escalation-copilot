"""Embeddings and reranking: reproducible, local, open-weights, no hosted API.

Model: BAAI/bge-small-en-v1.5 (384-d, MIT) via fastembed's quantised ONNX build, pinned to a Hugging Face revision. Reranker: Xenova/ms-marco-MiniLM-L-6-v2.
Resources (measured on the reference machine in the M2 report): ~35 MB + ~23 MB of weights, CPU only.

Reproducibility: every vector used by the benchmark is cached in `data/embeddings/` keyed by sha256(kind + text) with the model id, revision and prefix
recorded. The benchmark replays from the cache, and `scripts/verify_embeddings.py` recomputes with the real model and checks cosine >= 0.999 against the cache.
CI without the model replays the cache and says so; it does not recompute.

`PlumbingEmbedder` is a hashed bag-of-words used ONLY to exercise code paths when no model is available. It has no semantic quality and is never
used to report retrieval quality (the evaluation refuses it).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Protocol

DIM = 384
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_REPO = "Qdrant/bge-small-en-v1.5-onnx-Q"
EMBED_REVISION = "aa8f8b060edb00e03bfdd08813a2949946c8ba55"
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
RERANK_REPO = "Xenova/ms-marco-MiniLM-L-6-v2"
RERANK_REVISION = "a09144355adeed5f58c8ed011d209bf8ee5a1fec"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "      # bge's documented retrieval instruction; whether to use it was decided on dev (config.py)
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "embeddings"


class CacheMiss(KeyError):
    pass


def _h(kind: str, text: str) -> str:
    return hashlib.sha256(f"{kind}\x00{text}".encode()).hexdigest()


def normalise(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class Embedder(Protocol):
    name: str
    semantic: bool

    def embed_docs(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class PlumbingEmbedder:
    name = "plumbing-hash-bow (NOT SEMANTIC; infrastructure tests only)"
    semantic = False

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * DIM
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.sha256(tok.encode()).hexdigest(), 16) % DIM] += 1.0
        return normalise(v)

    def embed_docs(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


class FastEmbedder:
    """The real model. Weights come from the pinned snapshot; a different revision is refused."""
    semantic = True

    def __init__(self, query_prefix: str = "", cache_dir: str | None = None):
        from fastembed import TextEmbedding
        from huggingface_hub import snapshot_download
        cache = cache_dir or os.environ.get("COPILOT_MODEL_CACHE")
        path = snapshot_download(EMBED_REPO, revision=EMBED_REVISION, cache_dir=cache)
        assert EMBED_REVISION in path, "unpinned model snapshot"
        self._m = TextEmbedding(model_name=EMBED_MODEL, specific_model_path=path)
        self.query_prefix = query_prefix
        self.name = f"{EMBED_MODEL} ({EMBED_REPO}@{EMBED_REVISION[:8]})"

    def embed_docs(self, texts):
        return [normalise([float(x) for x in v]) for v in self._m.embed(texts)]

    def embed_query(self, text):
        return normalise([float(x) for x in next(iter(self._m.embed([self.query_prefix + text])))])


class CachedEmbedder:
    """Replays vectors from the committed cache; optionally falls through to a real model and records what it computed."""
    semantic = True

    def __init__(self, path: Path | None = None, inner: Embedder | None = None, query_prefix: str = ""):
        self.path = path or (CACHE_DIR / "embeddings.json")
        self.inner = inner
        self.query_prefix = query_prefix
        self._d = json.loads(self.path.read_text()) if self.path.exists() else {"model": EMBED_MODEL, "repo": EMBED_REPO, "revision": EMBED_REVISION, "dim": DIM, "vectors": {}}
        if self._d["revision"] != EMBED_REVISION or self._d["dim"] != DIM:
            raise ValueError("embedding cache was produced by a different model revision")
        self.name = f"{EMBED_MODEL}@{EMBED_REVISION[:8]} (cached)"
        self.dirty = False

    def _get(self, kind: str, texts: list[str], compute) -> list[list[float]]:
        out, missing = [], []
        for t in texts:
            k = _h(kind, t)
            if k in self._d["vectors"]:
                out.append(self._d["vectors"][k])
            else:
                out.append(None)
                missing.append(t)
        if missing:
            if self.inner is None:
                raise CacheMiss(f"{len(missing)} {kind} vector(s) not in the embedding cache and no model available")
            for t, v in zip(missing, compute(missing), strict=True):
                self._d["vectors"][_h(kind, t)] = [round(x, 6) for x in v]
            self.dirty = True
            return self._get(kind, texts, compute)
        return out

    def embed_docs(self, texts):
        return self._get("doc", texts, lambda ts: self.inner.embed_docs(ts))

    def embed_query(self, text):
        return self._get(f"query|{self.query_prefix}", [text], lambda ts: [self.inner.embed_query(t) for t in ts])[0]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._d, sort_keys=True, separators=(",", ":")) + "\n")
        self.dirty = False


class Reranker:
    """Cross-encoder scorer with a replay cache keyed by (query, passage) hashes."""

    def __init__(self, path: Path | None = None, live: bool = False, cache_dir: str | None = None):
        self.path = path or (CACHE_DIR / "rerank.json")
        self._d = json.loads(self.path.read_text()) if self.path.exists() else {"model": RERANK_MODEL, "repo": RERANK_REPO, "revision": RERANK_REVISION, "scores": {}}
        if self._d["revision"] != RERANK_REVISION:
            raise ValueError("rerank cache was produced by a different model revision")
        self._m = None
        self.dirty = False
        if live:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            from huggingface_hub import snapshot_download
            path_ = snapshot_download(RERANK_REPO, revision=RERANK_REVISION, cache_dir=cache_dir or os.environ.get("COPILOT_MODEL_CACHE"))
            assert RERANK_REVISION in path_
            self._m = TextCrossEncoder(model_name=RERANK_MODEL, specific_model_path=path_)
        self.name = f"{RERANK_MODEL}@{RERANK_REVISION[:8]}"

    def scores(self, query: str, passages: list[str]) -> list[float]:
        keys = [_h("rerank", query) + ":" + _h("p", p) for p in passages]
        miss = [i for i, k in enumerate(keys) if k not in self._d["scores"]]
        if miss:
            if self._m is None:
                raise CacheMiss(f"{len(miss)} rerank score(s) not in the cache and no model available")
            for i, s in zip(miss, self._m.rerank(query, [passages[i] for i in miss]), strict=True):
                self._d["scores"][keys[i]] = round(float(s), 5)
            self.dirty = True
        return [self._d["scores"][k] for k in keys]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._d, sort_keys=True, separators=(",", ":")) + "\n")
        self.dirty = False
