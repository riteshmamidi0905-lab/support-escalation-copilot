"""Retrieval building blocks that need no database and no model: chunking, lifecycle/duplicate/conflict governance, embedding-cache integrity, boundaries."""
import ast
import json
import math
from pathlib import Path

import pytest

from copilot import contracts as C
from copilot.retrieval import chunker, embed, governance

ROOT = Path(__file__).resolve().parent.parent
DS = ROOT / "data" / "meridian-seed-20260101"
DOCS = C.read_jsonl(DS / "runbooks.jsonl")
BY = {d["doc_id"]: d for d in DOCS}


def test_chunks_are_structure_aware_and_every_citation_range_resolves():
    chunks = chunker.chunk_corpus(DOCS)
    assert len({c["chunk_id"] for c in chunks}) == len(chunks)
    assert {c["doc_id"] for c in chunks} == set(BY), "every document must be retrievable"
    for c in chunks:
        assert BY[c["doc_id"]]["body_markdown"][c["char_start"]:c["char_end"]] == c["text"]
        assert c["embed_text"].startswith(BY[c["doc_id"]]["title"]), "the title travels with the chunk text"


def test_chunking_splits_on_headings_and_never_cuts_a_paragraph():
    body = "# T\n\nintro paragraph.\n\n## Part A\n\n" + ("alpha " * 60) + "\n\n" + ("beta " * 60) + "\n\n## Part B\n\nlast."
    doc = {"doc_id": "RBK-9999", "title": "T", "body_markdown": body}
    ch = chunker.chunk_document(doc)
    assert [c["section"] for c in ch][:1] == ["T"] and "Part A" in {c["section"] for c in ch} and "Part B" in {c["section"] for c in ch}
    for c in ch:
        assert body[c["char_start"]:c["char_end"]] == c["text"]
        assert not c["text"].endswith("alph") and not c["text"].startswith("lpha"), "paragraphs are never cut"
    assert len([c for c in ch if c["section"] == "Part A"]) == 2, "two long paragraphs exceed the target and become two chunks"


def test_chunking_is_deterministic():
    assert chunker.chunk_corpus(DOCS) == chunker.chunk_corpus(list(DOCS))


def test_registry_finds_exactly_the_two_real_conflicts_and_the_duplicate_groups():
    reg = governance.Registry(DOCS)
    conflict_pairs = {tuple(sorted({d for d in reg.conflicts[k]} | {k})) for k in reg.conflicts}
    assert conflict_pairs == {("RBK-0015", "RBK-0017"), ("RBK-0023", "RBK-0049")}
    assert reg.group("RBK-0007") == ["RBK-0005", "RBK-0007", "RBK-0028"]
    assert reg.group("RBK-0031") == ["RBK-0031", "RBK-0057"] and reg.group("RBK-0029") == ["RBK-0029", "RBK-0050"]
    # identical text in two active versions is a duplicate, NOT a conflict (the generator's 'conflict' topic double-booking pair)
    assert reg.group("RBK-0040") == ["RBK-0040", "RBK-0055"] and "RBK-0040" not in reg.conflicts


def test_superseded_and_draft_documents_are_never_part_of_a_duplicate_or_conflict_group():
    reg = governance.Registry(DOCS)
    for d in DOCS:
        if d["status"] != "active":
            assert d["doc_id"] not in reg.duplicates and d["doc_id"] not in reg.conflicts
            assert governance.exclusion_reason(d) == d["status"]


def test_the_representative_of_a_duplicate_group_is_deterministic_and_the_latest_version():
    reg = governance.Registry(DOCS)
    assert reg.representative("RBK-0005") == reg.representative("RBK-0007") == reg.representative("RBK-0028") == "RBK-0005"      # v1.1, lowest id
    assert reg.representative("RBK-0055") == "RBK-0040"                                                                          # v2.1 beats v1.3


def test_instruction_like_flag_is_advisory_and_catches_the_four_injected_documents():
    flagged = {d["doc_id"] for d in DOCS if governance.instruction_like(d["body_markdown"])}
    assert flagged == {d["doc_id"] for d in DOCS if d["adversarial"]} == {"RBK-0030", "RBK-0032", "RBK-0035", "RBK-0051"}
    assert not governance.instruction_like("Rotate the key; the old key stays valid for 30 minutes.")


# ---- embeddings ---------------------------------------------------------------------------------------------------------------------------------
def test_embedding_cache_covers_the_corpus_and_both_query_sets_and_is_well_formed():
    cache = embed.CachedEmbedder()
    assert cache._d["model"] == embed.EMBED_MODEL and cache._d["revision"] == embed.EMBED_REVISION and cache._d["dim"] == 384
    chunks = chunker.chunk_corpus(DOCS)
    vecs = cache.embed_docs([c["embed_text"] for c in chunks])
    from copilot.retrieval.service import ticket_query
    for f in (DS / "tickets.jsonl", ROOT / "data" / "hand-labelled-v1" / "hand_tickets.jsonl"):
        for t in C.read_jsonl(f):
            vecs.append(cache.embed_query(ticket_query(t["subject"], t["body"])))
    assert len(vecs) == len(chunks) + 340
    for v in vecs:
        assert len(v) == 384 and all(math.isfinite(x) for x in v) and abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 1e-3


def test_cache_miss_is_an_error_not_a_silent_fallback():
    cache = embed.CachedEmbedder()
    with pytest.raises(embed.CacheMiss):
        cache.embed_query("a query that was never embedded " + "x" * 40)


def test_plumbing_embedder_is_labelled_non_semantic_and_has_no_semantic_quality():
    p = embed.PlumbingEmbedder()
    assert p.semantic is False and "NOT SEMANTIC" in p.name
    a, b = p.embed_query("resync the feed"), p.embed_query("re-sync the feed")
    assert embed.cosine(a, a) > 0.999 and embed.cosine(a, b) < 0.999                  # lexical overlap only; synonyms are not understood


def test_model_pins_are_full_revisions_not_branches():
    for rev in (embed.EMBED_REVISION, embed.RERANK_REVISION):
        assert len(rev) == 40 and set(rev) <= set("0123456789abcdef")
    assert json.loads((embed.CACHE_DIR / "rerank.json").read_text())["revision"] == embed.RERANK_REVISION


# ---- static boundaries --------------------------------------------------------------------------------------------------------------------------
def imports(path: Path) -> set[str]:
    out = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.add(n.module.split(".")[0])
    return out


def test_retrieval_package_has_no_email_network_subprocess_or_file_write_capability():          # A-I3-06 (static half), A-I1-11
    banned = {"smtplib", "email", "socket", "ssl", "http", "urllib", "requests", "httpx", "aiohttp", "subprocess", "ftplib", "imaplib", "poplib", "telnetlib", "shutil"}
    for p in (ROOT / "copilot" / "retrieval").glob("*.py"):
        assert not (imports(p) & banned), f"{p.name} imports {imports(p) & banned}"
        assert "os" not in imports(p) or p.name in ("embed.py", "config.py"), f"{p.name} uses os"
    # fastembed / huggingface_hub (model download) are confined to the embedding module and imported lazily inside methods
    top_level = [n for n in ast.parse((ROOT / "copilot/retrieval/embed.py").read_text()).body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any(getattr(n, "module", "") and n.module.startswith(("fastembed", "huggingface_hub")) for n in top_level)
