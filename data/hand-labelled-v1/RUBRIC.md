# Hand-labelling rubric (version r1) and methodology

**Status: written BEFORE any retrieval strategy was scored.** The set below is frozen by `docs/eval-freeze.json` (SHA-256 of every file) before the first benchmark run; a test fails if any file changes afterwards.

## Who labelled, and the limits of that (read this first)
The tickets and labels were written by **Claude (an AI assistant), in a single working session, as a single reviewer**. This is **not independent human annotation and not multi-reviewer**. Specifically:
- the same agent designed the synthetic generator and the retrieval systems, so it knows how the corpus was built; this is a **contamination risk** that cannot be removed, only reduced: tickets were written in free natural language (not generator templates), labels were decided by reading the actual runbook text and lifecycle metadata, and the generator's answer key was **not consulted** while labelling;
- two passes were made in the same session (author, then re-read every label against the document text; changes recorded in `label-review-log.md`), not a day apart as the original methodology wished;
- there is no inter-annotator agreement figure. Results on this set are therefore evidence about *this corpus under one reviewer's judgement*, not general retrieval quality.
Where the hand label disagrees with the generator's belief, the hand label stands and the disagreement is reported (e.g. documents the generator treats as "look-alike distractors" that in fact answer the question).

## What a ticket is
40 tickets written for the Meridian (fictional) dataset **seed 20260101**. Labels are bound to that corpus (doc ids and `source` hashes in the manifest). They are *not* valid for any other seed.

## What was labelled for each ticket
1. **Evidence sufficiency** — `sufficient` (the corpus answers the question), `insufficient` (it does not), or `conflicting` (two active documents disagree on a material fact, e.g. a number).
2. **Relevant documents** with a role: `authoritative` (an ACTIVE document that gives the answer) or `supporting` (related, not sufficient alone).
3. **Must-not-retrieve** documents with a reason: `superseded` (obsolete version), `draft`, `adversarial` (contains injected instructions and no legitimate content), `lookalike_without_answer` (shares vocabulary, gives no answer), `wrong_topic`.
4. **Conflicting documents** (when sufficiency is `conflicting`).
5. **Incident evidence**: open incidents affecting *this ticket's account* and relevant to the ticket's component (a tenant lookup, scored separately from ranking).
6. **Primary slice** (one per ticket, used for reporting): routine · terminology · conflicting · stale · near_duplicate · distractor · evidence_gap · injection_exposure; plus free `extra_tags`.

## Decision rules
- Judge by what the document **says**, not by its title.
- Only an **active** document can be authoritative. Superseded and draft documents are never authoritative; retrieving them as authority is a failure ("obsolete evidence").
- **Near-duplicates** (same content, different owner/path/version): all copies are labelled authoritative-equivalent; a system is not penalised for citing any one of them, and *should* recognise them as duplicates rather than as two sources.
- **Conflict** means a material, checkable disagreement (different numbers or opposite instruction) between two ACTIVE documents. Two active versions with identical content are duplicates, not a conflict.
- A document that merely shares topic words but contains no usable answer (stub/filler pages, "typical values are 45 units") is `lookalike_without_answer`; a question answerable only from such a page is `insufficient`.
- A document that answers *negatively* ("not supported", "not available") **does** answer the question: sufficient.
- Documents containing instruction-like text are never relevant evidence for what the instruction asks; they are labelled `adversarial` when they have no legitimate content.
- When unsure, the ticket is labelled with the stricter reading and the doubt is written in `notes`.

## Scoring protocol
Defined in `docs/eval-protocol.md` (also frozen before scoring): document-level Hit@K, MRR@10, Recall@K, citation precision, forbidden-evidence rate, abstention metrics, per-slice reporting. No thresholds or parameters were tuned on this set; abstention thresholds are chosen on the generator's tickets (development set) and frozen.
