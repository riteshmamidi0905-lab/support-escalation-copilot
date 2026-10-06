# Synthetic data contracts

Machine-readable JSON Schemas (draft 2020-12) in `contracts/`, enforced by `copilot/contracts.py`. All data is **synthetic and fictional** (ADR-0001).

| Contract | File | Purpose |
|---|---|---|
| account, contract | `account`, `contract` | tenant key; SLA and **credit policy thresholds** (synthetic values) |
| integration, incident, deployment, release_note | … | what the verifier and diagnoser look at |
| ticket | `ticket` | untrusted free text; includes adversarial tickets by design |
| runbook_doc | `runbook_doc` | version, status, effective_from, supersedes, owner, source_path (**provenance**), `adversarial` flag |
| scenario | `scenario` | S1–S16 acceptance scenarios with expected outcome and invariants exercised |
| synthetic_label | `synthetic_label` | generator-derived answer key — evidence class `synthetic_label` |
| hand_label | `hand_label` | manually reviewed, **held out** — evidence class `hand_labelled` |
| dataset_manifest | `dataset_manifest` | evidence class, fictional flag, generator+seed, per-file SHA-256, `tuning_allowed` |
| action | `action` | the only write vocabulary; no email action exists |
| audit_event | `audit_event` | append-only hash-chained audit record |

## Rules beyond the schemas (checked by `validate_dataset` / `validate_hand_labels`)
- unique ids; referential integrity (contracts, integrations, tickets, incidents' affected accounts, labels → tickets/runbooks);
- runbook lineage: `supersedes` exists, same product area, strictly older, no cycles;
- synthetic markers: reserved email domains, documentation-range IPs, 555 phone numbers, in **all** string fields;
- manifest SHA-256 matches every file (a silent dataset edit fails validation);
- manifests must declare `fictional: true` and the customer `Meridian Freight Systems (fictional)`;
- evidence classes cannot be swapped: a hand-labelled manifest must be `tuning_allowed: false`, labels must have `provenance: hand_reviewed`, synthetic labels `generator`;
- `tuning_view(tickets, hand_dir)` is the only sanctioned way for tuning code to read tickets: held-out ones are removed.

`contracts/examples/valid/` holds two **tiny hand-written fixtures** to test the validators. They are not the real datasets and carry no evaluation meaning. The seeded generator is next (M0.5/M1).

## Planned dataset sizes (design choices, not results)
~40 accounts, ~60 runbooks (6 stale/conflicting versions, 5 near-duplicates, some adversarial), ~300 generated tickets (≈20 injection, ≈20 cross-tenant probes, ≈30 unanswerable), 12 incidents, 30 deployments, and a **separately authored** ~40-ticket hand-reviewed set.

## Hand-labelling methodology (to be followed in M2)
Author the 40 tickets and labels *before* looking at any retrieval output; two passes by the same reviewer at least a day apart with disagreements resolved and recorded; rubric versioned (`rubric_version`); labels stored in their own directory and manifest; never used for prompt, threshold or retrieval-parameter tuning. A single reviewer is a stated limitation.
