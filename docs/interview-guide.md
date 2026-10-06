# Interview guide

For explaining this project accurately. It is not résumé marketing: every statement below is something the repository can back up, and each section says where. If a question goes beyond the evidence,
say so; the honest edge of the work is part of the story. Numbers come from the generated block at the end (re-checked by CI), not from memory.

## 30 seconds
"It's a reference implementation of an approval-gated AI case workflow for a fictional B2B SaaS support team. Hard tickets get a case file (cited evidence, a diagnosis, a proposed plan, a draft reply) but the
model never gets to act: it proposes typed actions, deterministic code decides whether they're allowed, a human with the exact role approves the exact action, and every write is idempotent and audited. I
built it to find out where an agent can be trusted and where it can't, and I attacked it to see: there are 92 catalogued attacks with executable tests. The model in it is a deterministic stand-in, not an LLM, and I
haven't run a real model against it; that's stated everywhere."

## 2 minutes
Start with the customer problem: Tier-2 engineers lose time reading drifting runbooks, querying ops databases and chasing approvals in chat, and the costly mistakes are risky remedies (re-syncing a feed twice,
a credit outside policy) and cross-account slips. Then the design choice: a **fixed workflow** (intake → scope → retrieve → verify → diagnose → plan → review → execute → draft), not free-form agent planning, because a
customer deployment needs predictable, auditable steps; the model works *inside* stages and returns structured conclusions, nothing else. Then the three decisions that matter: (1) the model is outside the
trust boundary (it cannot choose tenant, role, approver, expiry, state or what's allowed); (2) approvals are bound to the exact action by hash, the exact role and the tenant, and are re-checked with fresh policy at execution;
(3) tenant isolation is in the database (forced row-level security under a signed scope), not in application code. Then evidence: held-out retrieval evaluation that picked vector search over hybrid, a threat catalogue
where every attack has a test, mutation checks that break each defence, and the findings that went wrong. Close with the limits: stand-in model, simulated auth and customer systems, one AI reviewer for the retrieval labels.

## Architecture walkthrough (use [`architecture.md`](architecture.md))
1. **Operator → app:** a server-rendered UI with no JavaScript; the browser is untrusted; every read and action is re-authorised on the server with signed account grants.
2. **Case runner:** a durable state machine; transitions are versioned and the legal edges are also enforced by a database trigger; every stage persists before it advances, so a crash resumes safely (a PostgreSQL lease worker does the resuming).
3. **Evidence:** pgvector retrieval plus lifecycle governance (superseded and draft documents are excluded, near-duplicates collapsed, conflicting active documents surfaced rather than resolved), and tenant facts through a fixed query catalogue under a signed scope.
4. **Model stage:** structured output with bounded repair; the reply is untrusted data checked against schemas, citations and grounding rules.
5. **Control plane:** gateway → policy (facts and rules, never model confidence) → approval service → idempotency ledger → customer system; everything lands in a hash-chained audit log and in telemetry correlated by ids.

## Why vector retrieval beat the hybrid hypothesis
Hybrid-plus-rerank is the common expectation, and the project built all four strategies (lexical, vector, hybrid, hybrid + rerank) specifically so the claim would be measured rather than assumed (ADR-0005). On a frozen 40-ticket
hand-labelled held-out set, plain vector search ranked best (Hit@1 0.80, MRR@10 0.87); equal-weight hybrid fusion was lower (0.68, 0.79) because it inherited the lexical vocabulary misses, and the cross-encoder reranker added no
gain (0.76, 0.84) for ~107 ms and a 347 MB model. So vector + governance was chosen and hybrid and rerank stayed optional. **Qualify it every time:** the labels are from a single AI reviewer, n is small (25 answerable), the confidence
intervals overlap, the corpus is 60 short documents, and the synthetic dev set had flattered everything. The more useful finding was different: similarity confidence cannot tell you evidence is missing (36-55% of unanswerable
tickets still got evidence), so sufficiency is judged from content and policy.

## Why the model isn't trusted with authorization
Authorization depends on facts a model could be talked out of: who the user is, which tenant, which role may approve, whether a policy limit applies. So none of those are model inputs. The model's output is parsed against a
schema, any privileged field makes it invalid, citations must resolve to retrieved evidence, and the only thing that can create an effect is a gateway that re-derives everything from trusted facts. A model that says "approved" in text is
ignored (A-I1-06). I tested this against the stand-in and a deliberately obedient scripted model: that proves the *architecture* holds when a model misbehaves; it doesn't tell you how often a real model misbehaves.

## Approval design
An approval is a database record bound to the canonical action's hash, the exact role (no hierarchy: a manager can't approve a re-sync), the tenant and case, with an expiry where timeout means denial. The requester and the amender can't approve;
decided approvals are final (a trigger enforces it); amending an action voids the old approval and requires a new one. The approver sees the exact action and the evidence that was shown when it was requested. At execution the gateway re-evaluates policy from fresh
facts, re-checks the hash, then claims the idempotency key. There is no UI or API route that executes an action; approving only records a decision.

## Tenant isolation
Forced row-level security keyed to a signed, short-lived scope that only trusted intake can mint; a fixed query catalogue with bound parameters; a least-privilege role for the model-facing code that can't see control tables; composite foreign keys pair every
row with its case and account. The reason it's in the database: the first design (a session setting) did **not** stop code that could run SQL from choosing its own scope (A-I2-05), which led to the signed scope (ADR-0012). For operators: signed account grants, and a foreign case returns the same 404 as a
nonexistent one, so existence isn't an oracle.

## Idempotency and reconciliation
Each write has a deterministic key stored in a ledger; a replay returns the original result; concurrent attempts produce one effect (8-thread and concurrent-worker tests). The interesting case is a timeout *after* the customer system applied the change: the
outcome is unknown, so the system records UNCERTAIN, does **not** retry (a retry could apply it twice), asks the system by key if it can be asked, and otherwise hands it to a human to reconcile with a recorded note. Demo E shows exactly this.

## Prompt-injection containment (never say "solved")
What I can say: injected text in a ticket or a retrieved runbook cannot by itself cause an unauthorised action. In 32 end-to-end runs, including with every injection detector switched off and with an obedient scripted model, no invariant was violated: forbidden proposals are dropped, the
credit is refused by policy, the re-sync still needs the SRE, and the draft can't be sent. What I can't say: that a model won't be steered. A steered *draft* is still possible, and my deterministic grounding checks caught 20/20 of the cases they were built for, 0/8 rephrasings and 0/10 misleading-but-grounded drafts; the control is mandatory human review, whose effectiveness I did not measure.

## One major failure and what changed
Pick the conflict-order bug: the frozen protocol said detect conflicts before abstaining, but my M2 implementation abstained first, which hid a known conflict between two active runbooks on one ticket. I found it in the held-out failure analysis, didn't rewrite the published results, corrected the order architecturally in M3, re-ran the same frozen benchmark, and
published the cost (vector governed success 0.88 → 0.84; false conflicts 0 → 0.04). Alternates in [`engineering-lessons.md`](engineering-lessons.md): the masked privilege bug that only mutation testing exposed, or the draft-grounding rules that missed every rephrasing.

## What is simulated
The model (a rule-based stand-in whose heuristics were tuned while looking at the scenarios, so its S1-S16 match rate is *not* model accuracy), the customer systems (deterministic mocks with fault injection), authentication (a persona picker), the human reviewers (me), and all data (synthetic; the customer is fictional). Real-model evaluation was not executed; the method is frozen for a future run. See [`real-vs-simulated.md`](real-vs-simulated.md).

## What would change for production
Real authentication and session revocation behind TLS; separate credentials per trusted component and key management; an external anchor for the audit chain and a retention policy; real integrations with an authoritative lookup for reconciliation; metrics and alerting; a measured real-model evaluation and a human-review study of drafts; an independent security review. ([`security.md`](security.md))

## Likely questions, honest short answers
| Question | Answer (evidence) |
|---|---|
| Why an agent at all? | Reading evidence and drafting are where a model may help; deciding and acting are not. The workflow is fixed; the model's judgement is advisory (`architecture.md`, ADR-0002). |
| Did you evaluate it with an LLM? | No. Not executed; no local runtime; frozen protocol and a tested provider boundary exist. |
| Is 280/315 your accuracy? | No. It's the stand-in's development result; it shows orchestration reaches expected states, nothing about model quality. |
| How do you know the defences work? | Every attack has an executable test, and mutation checks break each defence and require a failing test; a first run had a survivor that produced a test. |
| What's the weakest part? | Drafts: a misleading draft made of grounded words isn't detectable by rules. Then simulated auth and unlabelled-secret redaction. |
| Did you test on real customers/data? | No. Fictional customer, synthetic data. |
| Is it production-ready? | No. It's a reference implementation; see the production list above. |

## Claims that may be said out loud (generated)
<!-- claims:interview:begin -->
<!-- claims:interview:end -->
