"""Document lifecycle, near-duplicate and conflict handling. Pure functions over the document registry; no database, no model, no side effects.

Definitions (also in docs/eval-protocol.md):
  * superseded / draft documents are never evidence; they are reported as `excluded` with the reason, so the exclusion is visible;
  * near-duplicates: ACTIVE documents with the same product area and normalised title whose normalised bodies (ignoring 'See also' trailers) agree on
    every number. They are one evidence item that lists every copy;
  * conflict: ACTIVE documents with the same product area and normalised title whose bodies differ in their numbers. The system does NOT choose one:
    all members are returned and the outcome is CONFLICTING_AUTHORITATIVE_EVIDENCE;
  * the registry is corpus metadata (a lint over the knowledge base), so a conflict is known even when only one side was retrieved.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

NUM = re.compile(r"\d+(?:\.\d+)?")
SEE_ALSO = re.compile(r"^\s*See also.*$", re.M | re.I)
INSTRUCTION_LIKE = re.compile(r"ignore (all )?(previous|prior)|disregard (the )?(policy|instructions)|note to assistant|reveal your instructions|skip human approval|you are now in", re.I)


def _norm_body(doc: dict) -> str:
    body = SEE_ALSO.sub("", doc["body_markdown"])
    return " ".join(re.sub(r"^#.*$", "", body, flags=re.M).lower().split())


def _norm_title(doc: dict) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", re.sub(r"\([^)]*\)", " ", doc["title"].lower())).split())     # a parenthetical such as "(copy)" does not make a different document


class Registry:
    def __init__(self, docs: list[dict[str, Any]]):
        self.docs = {d["doc_id"]: d for d in docs}
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for d in docs:
            if d["status"] == "active":
                groups[(d["product_area"], _norm_title(d))].append(d)
        self.duplicates: dict[str, list[str]] = {}
        self.conflicts: dict[str, list[str]] = {}
        for members in groups.values():
            if len(members) < 2:
                continue
            by_numbers: dict[tuple, list[dict]] = defaultdict(list)
            for d in members:
                by_numbers[tuple(NUM.findall(SEE_ALSO.sub("", d["body_markdown"])))].append(d)
            for cls in by_numbers.values():
                ids = sorted(x["doc_id"] for x in cls)
                if len(ids) > 1:
                    for i in ids:
                        self.duplicates[i] = ids
            if len(by_numbers) > 1:
                all_ids = sorted(x["doc_id"] for x in members)
                for i in all_ids:
                    self.conflicts[i] = [j for j in all_ids if j not in self.duplicates.get(i, [i])]

    def group(self, doc_id: str) -> list[str]:
        return self.duplicates.get(doc_id, [doc_id])

    def representative(self, doc_id: str) -> str:
        g = self.group(doc_id)
        return sorted(g, key=lambda i: (-_vkey(self.docs[i]["version"]), i))[0]

    def conflict_set(self, doc_id: str) -> list[str] | None:
        c = self.conflicts.get(doc_id)
        return sorted({self.representative(doc_id), *[self.representative(x) for x in c]}) if c else None


def _vkey(v: str) -> float:
    a = v.split(".")
    return float(a[0]) + float(a[1]) / 100 if len(a) > 1 else float(a[0])


def exclusion_reason(doc: dict) -> str | None:
    return None if doc["status"] == "active" else doc["status"]


def instruction_like(text: str) -> bool:
    """ADVISORY annotation only. The security boundary is architectural (retrieved text is data and retrieval has no side effects), never this regex."""
    return bool(INSTRUCTION_LIKE.search(text))
