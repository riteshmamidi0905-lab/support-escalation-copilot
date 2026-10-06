"""Data contracts: JSON Schemas in contracts/ plus the cross-record rules a schema cannot express.

A dataset directory looks like:
    manifest.json  accounts.jsonl  contracts.jsonl  integrations.jsonl  incidents.jsonl  deployments.jsonl
    tickets.jsonl  runbooks.jsonl  release_notes.jsonl  synthetic_labels.jsonl           (generator-derived)
and a SEPARATE directory for manually reviewed labels:
    manifest.json  hand_labels.jsonl                                                       (held out; never tuned against)
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

CONTRACTS_DIR = Path(__file__).resolve().parent.parent / "contracts"

# file name -> (schema name, id field)
TABLES = {
    "accounts.jsonl": ("account", "account_id"), "contracts.jsonl": ("contract", "contract_id"), "integrations.jsonl": ("integration", "integration_id"),
    "incidents.jsonl": ("incident", "incident_id"), "deployments.jsonl": ("deployment", "deployment_id"), "tickets.jsonl": ("ticket", "ticket_id"),
    "runbooks.jsonl": ("runbook_doc", "doc_id"), "release_notes.jsonl": ("release_note", "release_id"), "synthetic_labels.jsonl": ("synthetic_label", "ticket_id"),
    "scenarios.jsonl": ("scenario", "scenario_id"),
}
# Documentation-only address space (RFC 5737). Any other IPv4 literal in free text is treated as potentially real.
_DOC_NETS = ("192.0.2.", "198.51.100.", "203.0.113.")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+)")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[ -.]?)?\(?\d{3}\)?[ -.]?(\d{3})[ -.]?(\d{4})(?!\d)")


class ContractError(Exception):
    pass


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        self.errors.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_failed(self) -> None:
        if self.errors:
            raise ContractError("\n".join(self.errors[:50]) + (f"\n… and {len(self.errors) - 50} more" if len(self.errors) > 50 else ""))


_validators: dict[str, Draft202012Validator] = {}


def validator(name: str) -> Draft202012Validator:
    if name not in _validators:
        schema = json.loads((CONTRACTS_DIR / f"{name}.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        _validators[name] = Draft202012Validator(schema, format_checker=FormatChecker())
    return _validators[name]


def validate_record(schema_name: str, record: dict) -> list[str]:
    return [f"{schema_name}: {'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in validator(schema_name).iter_errors(record)]


def read_jsonl(path: Path) -> list[dict]:
    out = []
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ContractError(f"{path.name}:{i}: invalid JSON ({e.msg})") from e
    return out


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strings(v) -> Iterable[str]:
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)


def synthetic_marker_problems(record: dict, where: str) -> list[str]:
    """Free text must not contain anything that looks like a real address, public IP or real phone number."""
    probs = []
    for s in _strings(record):
        for m in _EMAIL.finditer(s):
            dom = m.group(1).lower().rstrip(".")
            if not (dom.endswith(".example") or dom.endswith(".example.test")):
                probs.append(f"{where}: email domain '{dom}' is not a reserved synthetic domain")
        for ip in _IPV4.findall(s):
            if not ip.startswith(_DOC_NETS) and not ip.startswith("10."):
                probs.append(f"{where}: IPv4 {ip} is outside documentation ranges")
        for m in _PHONE.finditer(s):
            if not m.group(1) == "555":
                probs.append(f"{where}: phone-like number is not in the fictional 555 range")
    return probs


def validate_manifest(dataset: Path, rep: Report, expect_class: str | None = None) -> dict | None:
    mf = dataset / "manifest.json"
    if not mf.exists():
        rep.add(f"{dataset.name}: manifest.json missing")
        return None
    m = json.loads(mf.read_text())
    for e in validate_record("dataset_manifest", m):
        rep.add(f"{dataset.name}/manifest.json: {e}")
    if expect_class and m.get("evidence_class") != expect_class:
        rep.add(f"{dataset.name}: evidence_class is '{m.get('evidence_class')}', expected '{expect_class}'")
    for f in m.get("files", []):
        p = dataset / f["path"]
        if not p.exists():
            rep.add(f"{dataset.name}: manifest lists missing file {f['path']}")
        elif sha256_file(p) != f["sha256"]:
            rep.add(f"{dataset.name}: sha256 mismatch for {f['path']} (dataset changed without a new manifest)")
    return m


def validate_dataset(dataset: Path) -> Report:
    """Validate a synthetic dataset directory: schemas, unique ids, referential integrity, document lineage, synthetic markers, manifest hashes."""
    rep = Report()
    m = validate_manifest(dataset, rep, "synthetic_label")
    rows: dict[str, list[dict]] = {}
    ids: dict[str, set[str]] = {}
    for fname, (schema, idf) in TABLES.items():
        p = dataset / fname
        if not p.exists():
            rep.add(f"{dataset.name}: missing {fname}")
            rows[schema], ids[schema] = [], set()
            continue
        rows[schema] = read_jsonl(p)
        seen: set[str] = set()
        for i, r in enumerate(rows[schema], 1):
            for e in validate_record(schema, r):
                rep.add(f"{fname}:{i}: {e}")
            for e in synthetic_marker_problems(r, f"{fname}:{i}"):
                rep.add(e)
            k = r.get(idf)
            if k in seen:
                rep.add(f"{fname}:{i}: duplicate {idf} {k}")
            seen.add(k)
        ids[schema] = seen
    acc, tck, doc = ids["account"], ids["ticket"], ids["runbook_doc"]
    for r in rows["contract"]:
        if r.get("account_id") not in acc:
            rep.add(f"contracts: {r.get('contract_id')} references unknown account {r.get('account_id')}")
    for r in rows["integration"]:
        if r.get("account_id") not in acc:
            rep.add(f"integrations: {r.get('integration_id')} references unknown account {r.get('account_id')}")
    for r in rows["ticket"]:
        if r.get("account_id") not in acc:
            rep.add(f"tickets: {r.get('ticket_id')} references unknown account {r.get('account_id')}")
    for r in rows["incident"]:
        for a in r.get("affected_account_ids", []):
            if a not in acc:
                rep.add(f"incidents: {r.get('incident_id')} references unknown account {a}")
    for r in rows["synthetic_label"]:
        if r.get("ticket_id") not in tck:
            rep.add(f"synthetic_labels: unknown ticket {r.get('ticket_id')}")
        for d in r.get("expected_runbook_ids", []):
            if d not in doc:
                rep.add(f"synthetic_labels: {r.get('ticket_id')} expects unknown runbook {d}")
    by_doc = {r["doc_id"]: r for r in rows["runbook_doc"] if "doc_id" in r}
    for d in by_doc.values():                                         # lineage: supersedes must exist, be same area, be older, and not form a cycle
        sup = d.get("supersedes")
        if sup:
            old = by_doc.get(sup)
            if old is None:
                rep.add(f"runbooks: {d['doc_id']} supersedes unknown {sup}")
                continue
            if old.get("product_area") != d.get("product_area"):
                rep.add(f"runbooks: {d['doc_id']} supersedes a document of a different product area")
            if old.get("effective_from", "") >= d.get("effective_from", ""):
                rep.add(f"runbooks: {d['doc_id']} does not post-date the document it supersedes")
        seen2, cur = set(), d
        while cur and cur.get("supersedes"):
            if cur["doc_id"] in seen2:
                rep.add(f"runbooks: supersession cycle through {cur['doc_id']}")
                break
            seen2.add(cur["doc_id"])
            cur = by_doc.get(cur["supersedes"])
    if m is not None and m.get("evidence_class") == "synthetic_label" and any(r.get("provenance") != "generator" for r in rows["synthetic_label"]):
        rep.add("synthetic_labels: non-generator provenance found in a synthetic dataset")
    return rep


def validate_hand_labels(hand_dir: Path, dataset: Path) -> Report:
    """Hand labels live in their own directory/manifest, reference documents that exist, and must be marked non-tunable.
    Tickets are either in the dataset (mini fixtures) or authored separately in hand_tickets.jsonl (the real hand-labelled set: tickets written by
    the reviewer, not drawn from generator templates). Label consistency rules are checked here; whether a label is *correct* is the reviewer's judgement."""
    rep = Report()
    m = validate_manifest(hand_dir, rep, "hand_labelled")
    p = hand_dir / "hand_labels.jsonl"
    if not p.exists():
        rep.add(f"{hand_dir.name}: missing hand_labels.jsonl")
        return rep
    accounts = {r["account_id"] for r in read_jsonl(dataset / "accounts.jsonl")}
    tickets = {r["ticket_id"] for r in read_jsonl(dataset / "tickets.jsonl")}
    own = hand_dir / "hand_tickets.jsonl"
    if own.exists():
        own_rows = read_jsonl(own)
        seen_t: Set[str] = set()
        for i, r in enumerate(own_rows, 1):
            for e in validate_record("ticket", r):
                rep.add(f"hand_tickets.jsonl:{i}: {e}")
            for e in synthetic_marker_problems(r, f"hand_tickets.jsonl:{i}"):
                rep.add(e)
            if r.get("ticket_id") in tickets or r.get("ticket_id") in seen_t:
                rep.add(f"hand_tickets.jsonl:{i}: ticket id {r.get('ticket_id')} collides with a generated or duplicate ticket")
            seen_t.add(r.get("ticket_id"))
            if r.get("account_id") not in accounts:
                rep.add(f"hand_tickets.jsonl:{i}: unknown account {r.get('account_id')}")
        tickets = tickets | seen_t
    docs = {r["doc_id"]: r for r in read_jsonl(dataset / "runbooks.jsonl")}
    incidents = {r["incident_id"] for r in read_jsonl(dataset / "incidents.jsonl")}
    seen: Set[str] = set()
    for i, r in enumerate(read_jsonl(p), 1):
        for e in validate_record("hand_label", r):
            rep.add(f"hand_labels.jsonl:{i}: {e}")
        t = r.get("ticket_id")
        if t in seen:
            rep.add(f"hand_labels.jsonl:{i}: duplicate label for {t}")
        seen.add(t)
        if t not in tickets:
            rep.add(f"hand_labels.jsonl:{i}: unknown ticket {t}")
        for d in r.get("expected_runbook_ids", []):
            if d not in docs:
                rep.add(f"hand_labels.jsonl:{i}: unknown runbook {d}")
        rel = [x["doc_id"] for x in r.get("relevant_docs", [])]
        auth = sorted(x["doc_id"] for x in r.get("relevant_docs", []) if x["role"] == "authoritative")
        if sorted(r.get("expected_runbook_ids", [])) != auth:
            rep.add(f"hand_labels.jsonl:{i}: expected_runbook_ids must equal the authoritative relevant_docs")
        for d in rel + [x["doc_id"] for x in r.get("must_not_retrieve", [])] + r.get("conflicting_doc_ids", []):
            if d not in docs:
                rep.add(f"hand_labels.jsonl:{i}: unknown runbook {d}")
        if set(rel) & {x["doc_id"] for x in r.get("must_not_retrieve", [])}:
            rep.add(f"hand_labels.jsonl:{i}: a document cannot be both relevant and must-not-retrieve")
        for x in r.get("relevant_docs", []):
            if x["role"] == "authoritative" and x["doc_id"] in docs and docs[x["doc_id"]]["status"] != "active":
                rep.add(f"hand_labels.jsonl:{i}: authoritative document {x['doc_id']} is {docs[x['doc_id']]['status']}")
        suff = r.get("evidence_sufficiency")
        if suff == "insufficient" and auth:
            rep.add(f"hand_labels.jsonl:{i}: insufficient evidence but authoritative documents listed")
        if suff == "sufficient" and not auth:
            rep.add(f"hand_labels.jsonl:{i}: sufficient evidence needs at least one authoritative document")
        if suff == "conflicting" and len(r.get("conflicting_doc_ids", [])) < 2:
            rep.add(f"hand_labels.jsonl:{i}: conflicting evidence needs at least two conflicting documents")
        if suff != "conflicting" and r.get("conflicting_doc_ids"):
            rep.add(f"hand_labels.jsonl:{i}: conflicting_doc_ids set but sufficiency is {suff}")
        for inc in r.get("incident_evidence", []):
            if inc not in incidents:
                rep.add(f"hand_labels.jsonl:{i}: unknown incident {inc}")
    if m is not None and m.get("tuning_allowed") is not False:
        rep.add("hand-labelled manifest must have tuning_allowed=false")
    return rep


def heldout_ticket_ids(hand_dir: Path) -> set[str]:
    return {r["ticket_id"] for r in read_jsonl(hand_dir / "hand_labels.jsonl")}


def tuning_view(tickets: Iterable[dict], hand_dir: Path) -> list[dict]:
    """The ONLY sanctioned way for tuning code to obtain tickets: held-out ones are removed so the final measurement is uncontaminated."""
    held = heldout_ticket_ids(hand_dir)
    return [t for t in tickets if t["ticket_id"] not in held]
