"""Metrics for the retrieval benchmark (definitions: docs/eval-protocol.md). Pure functions: no database, no model."""
from __future__ import annotations

import random
from collections import Counter, defaultdict
from typing import Any

from .governance import Registry
from .service import CONFLICT, EVIDENCE, NO_EVIDENCE

KS = (1, 3, 5)


def labels_from_hand(row: dict) -> dict:
    rel = {d["role"]: [] for d in row["relevant_docs"]}
    for d in row["relevant_docs"]:
        rel[d["role"]].append(d["doc_id"])
    return {"ticket_id": row["ticket_id"], "slice": row["primary_slice"], "tags": row["extra_tags"], "sufficiency": row["evidence_sufficiency"], "authoritative": rel.get("authoritative", []),
            "supporting": rel.get("supporting", []), "must_not": {d["doc_id"]: d["reason"] for d in row["must_not_retrieve"]}, "conflicting": row["conflicting_doc_ids"], "incidents": row["incident_evidence"]}


def labels_from_synthetic(row: dict, slice_name: str = "dev") -> dict:
    ids = row["expected_runbook_ids"]
    return {"ticket_id": row["ticket_id"], "slice": slice_name, "tags": [], "sufficiency": "sufficient" if ids else "insufficient", "authoritative": ids, "supporting": [], "must_not": {}, "conflicting": [], "incidents": []}


def score_ticket(lab: dict, raw: list[dict], result: dict, reg: Registry) -> dict[str, Any]:
    auth = {x for d in lab["authoritative"] for x in reg.group(d)}
    rel_all = auth | {x for d in lab["supporting"] for x in reg.group(d)}
    ranked_ids = [d["doc_id"] for d in raw]
    first = next((i for i, d in enumerate(ranked_ids[:10], 1) if d in auth), None)
    groups = {frozenset(reg.group(d)) for d in lab["authoritative"]}
    cited = [e["doc_id"] for e in result["evidence"]][:3] if result["outcome"] == EVIDENCE else []
    cited_all = {x for d in cited for x in (d, *reg.group(d))}
    evid_docs = {e["doc_id"] for e in result["evidence"]} | {x for e in result["evidence"] for x in e["duplicates"]}
    return {
        "ticket_id": lab["ticket_id"], "slice": lab["slice"], "tags": lab["tags"], "sufficiency": lab["sufficiency"], "outcome": result["outcome"],
        "first_rank": first, **{f"hit@{k}": first is not None and first <= k for k in KS},
        **{f"recall@{k}": (sum(1 for g in groups if any(d in g for d in ranked_ids[:k])) / len(groups)) if groups else None for k in KS},
        "rr": (1.0 / first) if first else 0.0,
        "cited": cited, "cited_relevant": sum(1 for d in cited if d in rel_all), "governed_hit": result["outcome"] == EVIDENCE and bool(auth & cited_all),
        "raw_top5_forbidden": sorted(d for d in ranked_ids[:5] if d in lab["must_not"]),
        "raw_top5_forbidden_reasons": sorted({lab["must_not"][d] for d in ranked_ids[:5] if d in lab["must_not"]}),
        "governed_forbidden": sorted(d for d in evid_docs if d in lab["must_not"]),
        "governed_forbidden_reasons": sorted({lab["must_not"][d] for d in evid_docs if d in lab["must_not"]}),
        "raw_top5_has_adversarial": any(reg.docs[d].get("adversarial") or d in {"RBK-0030", "RBK-0032", "RBK-0035", "RBK-0051"} for d in ranked_ids[:5]),
        "conflict_found": result["outcome"] == CONFLICT and bool(set(lab["conflicting"]) & {e["doc_id"] for e in result["evidence"]}),
        "raw_top3": ranked_ids[:3],
        "evidence_docs": [e["doc_id"] for e in result["evidence"]],
        "incident_recall": None,
    }


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def summarise(rows: list[dict]) -> dict[str, Any]:
    suff = [r for r in rows if r["sufficiency"] == "sufficient"]
    insuf = [r for r in rows if r["sufficiency"] == "insufficient"]
    conf = [r for r in rows if r["sufficiency"] == "conflicting"]
    cited_n = sum(len(r["cited"]) for r in suff)
    out: dict[str, Any] = {
        "n": len(rows), "n_sufficient": len(suff), "n_insufficient": len(insuf), "n_conflicting": len(conf),
        **{f"hit@{k}": _mean([float(r[f"hit@{k}"]) for r in suff]) for k in KS},
        **{f"recall@{k}": _mean([r[f"recall@{k}"] for r in suff if r[f"recall@{k}"] is not None]) for k in KS},
        "mrr@10": _mean([r["rr"] for r in suff]),
        "citation_precision": round(sum(r["cited_relevant"] for r in suff) / cited_n, 4) if cited_n else None,
        "governed_success_on_sufficient": _mean([float(r["governed_hit"]) for r in suff]),
        "wrongly_abstained_on_sufficient": _mean([float(r["outcome"] == NO_EVIDENCE) for r in suff]),
        "wrongly_conflict_on_sufficient": _mean([float(r["outcome"] == CONFLICT) for r in suff]),
        "abstained_on_insufficient": _mean([float(r["outcome"] == NO_EVIDENCE) for r in insuf]),
        "false_confidence_on_insufficient": _mean([float(r["outcome"] == EVIDENCE) for r in insuf]),
        "conflict_detected_on_conflicting": _mean([float(r["outcome"] == CONFLICT) for r in conf]),
        "conflict_pair_returned_on_conflicting": _mean([float(r["conflict_found"]) for r in conf]),
        "raw_top5_forbidden_rate": _mean([float(bool(r["raw_top5_forbidden"])) for r in rows if r["raw_top5_forbidden_reasons"] or r["slice"] in ("stale", "terminology", "injection_exposure", "distractor", "evidence_gap")]),
        "raw_top5_superseded_or_draft_rate": _mean([float(bool({"superseded", "draft"} & set(r["raw_top5_forbidden_reasons"]))) for r in rows if r["raw_top5_forbidden_reasons"] or r["slice"] in ("stale", "terminology", "evidence_gap")]),
        "governed_superseded_or_draft_count": sum(1 for r in rows if {"superseded", "draft"} & set(r["governed_forbidden_reasons"])),
        "governed_adversarial_returned": sum(1 for r in rows if "adversarial" in r["governed_forbidden_reasons"]),
        "raw_top5_has_injected_doc": sum(1 for r in rows if r["raw_top5_has_adversarial"]),
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "outcome_by_sufficiency": {s: dict(Counter(r["outcome"] for r in rows if r["sufficiency"] == s)) for s in ("sufficient", "insufficient", "conflicting")},
    }
    return out


def by_slice(rows: list[dict]) -> dict[str, dict]:
    g: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        g[r["slice"]].append(r)
    return {s: summarise(v) for s, v in sorted(g.items())}


def bootstrap_ci(rows: list[dict], metric, n: int = 2000, seed: int = 20260101) -> tuple[float, float]:
    rng = random.Random(seed)
    xs = [metric(r) for r in rows]
    if not xs:
        return (float("nan"), float("nan"))
    means = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(n))
    return round(means[int(0.025 * n)], 3), round(means[int(0.975 * n) - 1], 3)
