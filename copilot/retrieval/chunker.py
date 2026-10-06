"""Structure-aware chunking of runbook markdown.

Rules: split at markdown headings; inside a section, split into paragraphs on blank lines and pack consecutive paragraphs up to CHUNK_TARGET characters
(a paragraph is never cut mid-way). Every chunk records the exact character range of the SOURCE body it came from, so a citation always resolves:
`body_markdown[char_start:char_end] == text`. Doc id, version, lifecycle status and provenance are not duplicated into chunks; they are read from the
document record, which is the single source of truth.
"""
from __future__ import annotations

import re
from typing import Any

CHUNK_TARGET = 600
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _sections(body: str) -> list[tuple[str, int, int]]:
    """(heading, start, end) of each section's body text (heading line excluded)."""
    out: list[tuple[str, int, int]] = []
    heading, start, pos = "", 0, 0
    for line in body.splitlines(keepends=True):
        m = _HEADING.match(line.rstrip("\n"))
        if m:
            out.append((heading, start, pos))
            heading, start = m.group(2), pos + len(line)
        pos += len(line)
    out.append((heading, start, pos))
    return [s for s in out if body[s[1]:s[2]].strip()]


def _paragraphs(body: str, start: int, end: int) -> list[tuple[int, int]]:
    spans, pos = [], start
    for m in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", body[start:end]):
        a, b = start + m.start(), start + m.end()
        spans.append((a, b))
        pos = b
    del pos
    return spans


def chunk_document(doc: dict[str, Any]) -> list[dict[str, Any]]:
    body, title = doc["body_markdown"], doc["title"]
    chunks: list[dict[str, Any]] = []
    for heading, s, e in _sections(body):
        cur: tuple[int, int] | None = None
        for a, b in _paragraphs(body, s, e):
            if cur is not None and b - cur[0] > CHUNK_TARGET:
                chunks.append((heading, cur))
                cur = None
            cur = (a, b) if cur is None else (cur[0], b)
        if cur is not None:
            chunks.append((heading, cur))
    if not chunks:                                                  # a document with only a title is still one (empty-bodied) retrievable unit
        chunks = [(title, (0, len(body)))]
    out = []
    for n, (heading, (a, b)) in enumerate(chunks):
        text = body[a:b]
        section = heading or title
        embed_text = f"{title}. {text}" if section == title else f"{title} / {section}. {text}"
        out.append({"chunk_id": f"{doc['doc_id']}#c{n}", "doc_id": doc["doc_id"], "ordinal": n, "section": section, "char_start": a, "char_end": b,
                    "text": text, "embed_text": " ".join(embed_text.split())})
    return out


def chunk_corpus(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for d in docs for c in chunk_document(d)]
