import json
import shutil
from pathlib import Path

import pytest

from copilot import contracts as C

EX = Path(__file__).resolve().parent.parent / "contracts" / "examples" / "valid"
SYN, HAND = EX / "mini_synthetic", EX / "mini_hand_labelled"


def clone(tmp_path, *dirs):
    out = []
    for d in dirs:
        t = tmp_path / d.name
        shutil.copytree(d, t)
        out.append(t)
    return out


def rewrite(path: Path, fn):
    rows = C.read_jsonl(path)
    fn(rows)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    # keep the manifest honest unless a test wants it stale
    m = json.loads((path.parent / "manifest.json").read_text())
    for f in m["files"]:
        if f["path"] == path.name:
            f["sha256"] = C.sha256_file(path)
    (path.parent / "manifest.json").write_text(json.dumps(m))


def test_all_schemas_are_valid_json_schema():
    for p in C.CONTRACTS_DIR.glob("*.schema.json"):
        C.validator(p.name.removesuffix(".schema.json"))


def test_example_datasets_pass():
    assert C.validate_dataset(SYN).ok, C.validate_dataset(SYN).errors
    assert C.validate_hand_labels(HAND, SYN).ok, C.validate_hand_labels(HAND, SYN).errors


def errs(rep):
    return "\n".join(rep.errors)


def test_real_looking_email_domain_is_rejected(tmp_path):
    (d,) = clone(tmp_path, SYN)
    rewrite(d / "accounts.jsonl", lambda rows: rows[0]["contacts"][0].update(email="dana@gmail.com"))
    assert not C.validate_dataset(d).ok


def test_email_in_free_text_with_real_domain_is_rejected(tmp_path):
    (d,) = clone(tmp_path, SYN)
    rewrite(d / "tickets.jsonl", lambda rows: rows[0].update(body="please mail jane.doe@acme-corp.com"))
    assert "not a reserved synthetic domain" in errs(C.validate_dataset(d))


def test_public_ip_in_text_is_rejected_but_documentation_range_allowed(tmp_path):
    (d,) = clone(tmp_path, SYN)
    rewrite(d / "tickets.jsonl", lambda rows: rows[0].update(body="host 8.8.8.8 is down"))
    assert "outside documentation ranges" in errs(C.validate_dataset(d))
    rewrite(d / "tickets.jsonl", lambda rows: rows[0].update(body="host 203.0.113.7 is down"))
    assert C.validate_dataset(d).ok


def test_real_looking_phone_is_rejected_but_555_allowed(tmp_path):
    (d,) = clone(tmp_path, SYN)
    rewrite(d / "tickets.jsonl", lambda rows: rows[0].update(body="call me on 415-867-5309"))
    assert "555" in errs(C.validate_dataset(d))
    rewrite(d / "tickets.jsonl", lambda rows: rows[0].update(body="call me on 415-555-0142"))
    assert C.validate_dataset(d).ok


@pytest.mark.parametrize("table,mutate,needle", [
    ("tickets.jsonl", lambda r: r[0].update(account_id="ACC-9999"), "unknown account"),
    ("contracts.jsonl", lambda r: r[0].update(account_id="ACC-9999"), "unknown account"),
    ("integrations.jsonl", lambda r: r[0].update(account_id="ACC-9999"), "unknown account"),
    ("incidents.jsonl", lambda r: r[0].update(affected_account_ids=["ACC-9999"]), "unknown account"),
    ("synthetic_labels.jsonl", lambda r: r[0].update(ticket_id="TCK-9999"), "unknown ticket"),
    ("synthetic_labels.jsonl", lambda r: r[0].update(expected_runbook_ids=["RBK-9999"]), "unknown runbook"),
    ("accounts.jsonl", lambda r: r.append(dict(r[0])), "duplicate account_id"),
    ("tickets.jsonl", lambda r: r[0].update(severity="P0"), "is not one of"),
    ("tickets.jsonl", lambda r: r[0].pop("body"), "required"),
    ("tickets.jsonl", lambda r: r[0].update(created_at="yesterday"), "not a"),
    ("tickets.jsonl", lambda r: r[0].update(surprise=1), "Additional properties"),
])
def test_dataset_violations_are_caught(tmp_path, table, mutate, needle):
    (d,) = clone(tmp_path, SYN)
    rewrite(d / table, mutate)
    rep = C.validate_dataset(d)
    assert not rep.ok and needle in errs(rep)


def test_runbook_lineage_rules(tmp_path):
    (d,) = clone(tmp_path, SYN)
    rewrite(d / "runbooks.jsonl", lambda r: r[1].update(supersedes="RBK-7777"))
    assert "supersedes unknown" in errs(C.validate_dataset(d))
    rewrite(d / "runbooks.jsonl", lambda r: r[1].update(supersedes="RBK-0001", effective_from="2025-01-01"))
    assert "does not post-date" in errs(C.validate_dataset(d))
    def make_cycle(r):
        r[1].update(supersedes="RBK-0001", effective_from="2026-02-21")
        r[0].update(supersedes="RBK-0002", effective_from="2020-01-01")
    rewrite(d / "runbooks.jsonl", make_cycle)
    e = errs(C.validate_dataset(d))
    assert "cycle" in e or "does not post-date" in e      # a cycle necessarily violates date ordering; both guards exist on purpose
    rewrite(d / "runbooks.jsonl", lambda r: r[0].update(supersedes=None, effective_from="2025-06-01"))
    rewrite(d / "runbooks.jsonl", lambda r: r[1].update(product_area="billing_invoicing"))
    assert "different product area" in errs(C.validate_dataset(d))


def test_manifest_detects_silent_dataset_change(tmp_path):
    (d,) = clone(tmp_path, SYN)
    p = d / "tickets.jsonl"
    p.write_text(p.read_text().replace("Duplicate shipment events", "Changed subject"))
    assert "sha256 mismatch" in errs(C.validate_dataset(d))


def test_manifest_must_declare_fictional_and_customer(tmp_path):
    (d,) = clone(tmp_path, SYN)
    m = json.loads((d / "manifest.json").read_text())
    m["fictional"] = False
    (d / "manifest.json").write_text(json.dumps(m))
    assert not C.validate_dataset(d).ok
    m["fictional"] = True
    m["customer"] = "Acme Corp"
    (d / "manifest.json").write_text(json.dumps(m))
    assert not C.validate_dataset(d).ok


def test_evidence_classes_cannot_be_mixed(tmp_path):
    syn, hand = clone(tmp_path, SYN, HAND)
    # a hand-labelled dataset pretending to be synthetic, and vice versa
    m = json.loads((hand / "manifest.json").read_text())
    m["evidence_class"] = "synthetic_label"
    (hand / "manifest.json").write_text(json.dumps(m))
    assert not C.validate_hand_labels(hand, syn).ok
    m = json.loads((syn / "manifest.json").read_text())
    m["evidence_class"] = "hand_labelled"
    (syn / "manifest.json").write_text(json.dumps(m))
    assert not C.validate_dataset(syn).ok


def test_hand_labels_cannot_be_marked_tunable(tmp_path):
    syn, hand = clone(tmp_path, SYN, HAND)
    m = json.loads((hand / "manifest.json").read_text())
    m["tuning_allowed"] = True
    (hand / "manifest.json").write_text(json.dumps(m))
    assert not C.validate_hand_labels(hand, syn).ok


def test_hand_label_provenance_cannot_claim_generator(tmp_path):
    syn, hand = clone(tmp_path, SYN, HAND)
    rewrite(hand / "hand_labels.jsonl", lambda r: r[0].update(provenance="generator"))
    assert not C.validate_hand_labels(hand, syn).ok


def test_hand_label_for_unknown_ticket_or_duplicate(tmp_path):
    syn, hand = clone(tmp_path, SYN, HAND)
    rewrite(hand / "hand_labels.jsonl", lambda r: r[0].update(ticket_id="TCK-4242"))
    assert "unknown ticket" in errs(C.validate_hand_labels(hand, syn))
    rewrite(hand / "hand_labels.jsonl", lambda r: (r[0].update(ticket_id="TCK-0001"), r.append(dict(r[0]))))
    assert "duplicate label" in errs(C.validate_hand_labels(hand, syn))


def test_tuning_view_removes_held_out_tickets():
    tickets = C.read_jsonl(SYN / "tickets.jsonl")
    assert {t["ticket_id"] for t in tickets} == {"TCK-0001", "TCK-0002"}
    kept = C.tuning_view(tickets, HAND)
    assert [t["ticket_id"] for t in kept] == ["TCK-0002"]      # TCK-0001 is held out and cannot be tuned against
