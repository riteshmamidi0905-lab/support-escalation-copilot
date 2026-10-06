# Label review log (single reviewer: Claude; see RUBRIC.md for the limits)

Pass 1: tickets and labels authored from knowledge of the corpus (read in full earlier in the session).
Pass 2 (same session, before any retrieval code existed): every label re-read against the actual runbook text, lifecycle status and incident records. Changes made:

| Ticket | Pass-1 label | Pass-2 change | Reason |
|---|---|---|---|
| TCK-8025 | authoritative RBK-0007, RBK-0005 | added RBK-0028 | a third identical copy (regional-support v1.1) exists |
| TCK-8037 | no relevant docs | rate-limit pages (RBK-0007/0005/0028) as `supporting`; flagged `borderline` | they mention a 30-minute interval after a 429 but no general recommendation |
| TCK-8028, TCK-8029 | (considered insufficient, like the generator's belief) | sufficient (negative answer) | document explicitly states the capability is unavailable, which answers the question |

Verified against corpus text: every `authoritative` document is `active`; every superseded/draft/adversarial `must_not_retrieve` document has that status/flag; incident ids and account membership for `incident_evidence` match `incidents.jsonl`; both conflicting pairs state different numbers (30 attempts/30 h vs 20 attempts/90 h; 45-min vs 10-min slot).
Known unresolved doubts: TCK-8037 (borderline); whether TCK-8010 should be graded as needing an approval (not retrieval's concern).
Generator disagreements deliberately kept: TCK-8028, TCK-8029.
