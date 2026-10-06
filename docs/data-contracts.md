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

`contracts/examples/valid/` holds two **tiny hand-written fixtures** to test the validators; they carry no evaluation meaning. The real, seeded dataset is produced by `python -m copilot.data.cli generate --seed N --out DIR` (determinism: same seed ⇒ byte-identical files; regenerated and compared in CI on Linux/Python 3.11 and 3.12). `scenarios.jsonl` is part of every dataset.

## Generated dataset (seed 20260101, `data/meridian-seed-20260101/`) — actual counts
accounts 40 (Standard 18 / Premier 15 / Enterprise 7) · contracts 40 · integrations 119 · incidents 12 (4 open, 85 incident–account links) · deployments 30 · release notes 11 ·
runbook documents 60 (53 active, 3 superseded, 4 draft; 3 supersession chains; 3 topics with two *active, disagreeing* versions; 5 near-duplicates; 4 adversarial; 2 hard-negative documents; 27 distractors) ·
tickets 300 (25 carry history entries) · synthetic labels 300 · scenarios 16.
Ticket categories (scenario ids): routine/answer S14 ×130 · S4 re-sync approval ×25 · S5 credit within policy ×15 · S1 credit above policy ×15 · S2 prompt injection ×20 · S3 cross-account probes ×20 ·
S6 escalate ×20 (14 open-incident, 6 too-recent re-sync) · S7 undocumented ×30 · S8 conflicting runbooks ×12 · S15 secrets in ticket ×8 · S16 ambiguous ×5. S9–S13 are runtime behaviours (faults, denial, timeout, duplicates) exercised by the harness over these tickets, not separate tickets.
Expected-outcome mix in the generator's own answer key: ANSWER 150, REFUSE 55, APPROVAL 40, INSUFFICIENT_EVIDENCE 30, ESCALATE 20, CLARIFY 5. These are the generator's beliefs (evidence class `synthetic_label`), not validated truth.
The design deliberately includes difficulty (typos, terse text, near-duplicates, stale-but-active documents, hard negatives, `resync` vs `re-sync`) and is **not tuned for evaluation**. The 40-ticket hand-labelled set lives in `data/hand-labelled-v1/` (authored in M2, frozen before scoring).

## Hand-labelling methodology (as executed in M2; deviations stated)
Author the 40 tickets and labels *before* looking at any retrieval output; two passes by the same reviewer at least a day apart with disagreements resolved and recorded; rubric versioned (`rubric_version`); labels stored in their own directory and manifest; never used for prompt, threshold or retrieval-parameter tuning. A single reviewer is a stated limitation.

**What was actually done (M2):** rubric and protocol written and hashed before any strategy was scored (`docs/eval-freeze.json`, enforced by `tests/test_eval_freeze.py`); tickets written in free language by the reviewer, not from generator templates, and the generator's answer key was not consulted; every label re-read against the document text and lifecycle metadata in a second pass (`label-review-log.md`). **Deviations from the plan above:** the reviewer is a single AI (Claude), who also built the generator tooling (contamination risk); the two passes were in the same session, not a day apart; there is no second reviewer and no agreement statistic. Per-ticket labels record relevant documents with role (authoritative/supporting), must-not-retrieve documents with reason, sufficiency (sufficient / insufficient / conflicting), conflicting document ids, incident evidence, primary slice and tags (`contracts/hand_label.schema.json`; consistency rules in `copilot.contracts.validate_hand_labels`). Retrieval results follow `contracts/evidence.schema.json`.

## M3 contract changes
`contracts/action.schema.json` was tightened (case id and idempotency-key patterns, `maxLength` on every free-text field, bounded unique evidence refs, incident id pattern); `contracts/audit_event.schema.json` gained `account_id`, a `correlation` object (run/request/action/hash/approval/idempotency ids) and the event types `policy_decided`, `execution_attempted`, `execution_uncertain`, `idempotent_replay`, `idempotency_conflict_blocked`, `draft_created`, `forbidden_action_attempted`, `approval_denied_by_approver`. Nothing was loosened.
