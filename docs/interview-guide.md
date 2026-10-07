# Interview guide

For explaining this project accurately. It is not résumé marketing: every statement below is something the repository can back up, and each section says where. If a question goes beyond the evidence,
say so; the honest edge of the work is part of the story. Numbers come from the generated block at the end (re-checked by CI), not from memory.

## 30 seconds
"It's a reference implementation of an approval-gated AI case workflow for a fictional B2B SaaS support team. Hard tickets get a case file (cited evidence, a diagnosis, a proposed plan, a draft reply) but the
model never gets to act: it proposes typed actions, deterministic code decides whether they're allowed, a human with the exact role approves the exact action, and every write is idempotent and audited. I
built it to find out where an agent can be trusted and where it can't, and I attacked it to see: there are 92 catalogued attacks with executable tests. The default model in it is a deterministic stand-in, not an LLM; one real-model run (a 4B local model, one pass) exists and mostly exposed interface failures, and I
haven't run a real model against it; that's stated everywhere."

## 2 minutes
Start with the customer problem: Tier-2 engineers lose time reading drifting runbooks, querying ops databases and chasing approvals in chat, and the costly mistakes are risky remedies (re-syncing a feed twice,
a credit outside policy) and cross-account slips. Then the design choice: a **fixed workflow** (intake → scope → retrieve → verify → diagnose → plan → review → execute → draft), not free-form agent planning, because a
customer deployment needs predictable, auditable steps; the model works *inside* stages and returns structured conclusions, nothing else. Then the three decisions that matter: (1) the model is outside the
trust boundary (it cannot choose tenant, role, approver, expiry, state or what's allowed); (2) approvals are bound to the exact action by hash, the exact role and the tenant, and are re-checked with fresh policy at execution;
(3) tenant isolation is in the database (forced row-level security under a signed scope), not in application code. Then evidence: held-out retrieval evaluation that picked vector search over hybrid, a threat catalogue
where every attack has a test, mutation checks that break each defence, and the findings that went wrong. Close with the limits: stand-in default model, one real-model run on one small model, simulated auth and customer systems, one AI reviewer for the retrieval labels.

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
ignored (A-I1-06). I tested this against the stand-in and a deliberately obedient scripted model: that proves the *architecture* holds when a model misbehaves; it doesn't tell you how often a real model misbehaves (the one real-model run measured the controls, not adversarial behaviour).

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
The model (a rule-based stand-in whose heuristics were tuned while looking at the scenarios, so its S1-S16 match rate is *not* model accuracy), the customer systems (deterministic mocks with fault injection), authentication (a persona picker), the human reviewers (me), and all data (synthetic; the customer is fictional). One real-model evaluation was executed (v0.7.0; one 4B model, one pass); earlier releases had none. See [`real-vs-simulated.md`](real-vs-simulated.md).

## What would change for production
Real authentication and session revocation behind TLS; separate credentials per trusted component and key management; an external anchor for the audit chain and a retention policy; real integrations with an authoritative lookup for reconciliation; metrics and alerting; a measured real-model evaluation and a human-review study of drafts; an independent security review. ([`security.md`](security.md))

## Likely questions, honest short answers
| Question | Answer (evidence) |
|---|---|
| Why an agent at all? | Reading evidence and drafting are where a model may help; deciding and acting are not. The workflow is fixed; the model's judgement is advisory (`architecture.md`, ADR-0002). |
| Did you evaluate it with an LLM? | Once, with one small local model (Qwen3-4B Q4): 10 of 22 frozen cases reached the expected outcome, and the failures were at the interface (schema, evidence handles, action parameters, drafts). v0.6.0 predates it. |
| Is 10/22 your accuracy? | No. It is expected-outcome attainment against expectations written for the stand-in, one model, one pass; the controls held and that is scored separately. |
| Is 280/315 your accuracy? | No. It's the stand-in's development result; it shows orchestration reaches expected states, nothing about model quality. |
| How do you know the defences work? | Every attack has an executable test, and mutation checks break each defence and require a failing test; a first run had a survivor that produced a test. |
| What's the weakest part? | Drafts: a misleading draft made of grounded words isn't detectable by rules. Then simulated auth and unlabelled-secret redaction. |
| Did you test on real customers/data? | No. Fictional customer, synthetic data. |
| Is it production-ready? | No. It's a reference implementation; see the production list above. |

## Claims that may be said out loud (generated)
<!-- claims:interview:begin -->
- **The customer, Meridian Freight Systems, is fictional and every account, ticket, runbook, incident and integration is synthetic, generated reproducibly from a seed.**  
  *Limit:* Synthetic data has designed difficulty, not real-world distribution; no real customer or customer data was used. *(use: CV, portfolio; evidence: documented design; id `scope-fictional-synthetic`)*
- **This is a reference implementation run locally; it has never been deployed and has no production use or real customers.**  
  *Limit:* Demo mode, simulated sign-in and mock customer systems are deliberate; see docs/real-vs-simulated.md for the full list and docs/security.md for what a real deployment would need. *(use: CV, portfolio; evidence: documented design; id `scope-reference-implementation`)*
- **Every model result in this repository comes from RuleCaseModel, a deterministic rule-based stand-in (plus scripted misbehaving wrappers), not a language model.**  
  *Limit:* The stand-in's heuristics were developed while looking at the scenario set, so its outcome rates are development results about the orchestration, not model quality. *(use: CV, portfolio; evidence: documented design; id `model-is-standin`)*
- **Real-model evaluation was not executed: no local language-model runtime was available, and no paid API was used; the method is frozen and hash-locked for a future run.**  
  *Limit:* The provider boundary (ConfiguredProvider) is tested against a protocol stub only; nothing is known about any real model's behaviour here. *(use: portfolio only; evidence: not executed; id `real-model-evaluation-status`)*
- **The model cannot choose the tenant, the approver, the required role, the approval expiry, the workflow state or the action vocabulary: its output is untrusted structured data validated by deterministic code, and a model that claims otherwise is ignored.**  
  *Limit:* Shown with the stand-in and with deliberately misbehaving scripted models (obedient to injected text, privileged fields, invented and forbidden actions); behaviour of a real LLM is not measured. *(use: portfolio only; evidence: deterministic tests; id `model-cannot-authorise`)*
- **The only write vocabulary is 5 typed actions (3 human-gated: escalate_engineering, request_sla_credit, trigger_resync); no action, tool, client or code path can send customer email, and 16 dangerous request names are rejected by name.**  
  *Limit:* Customer email is excluded by construction (invariant I3); the mock escalation system only accepts internal destinations. *(use: CV, portfolio; evidence: deterministic tests; id `typed-actions-no-email`)*
- **An approval is bound to the exact canonical action (hash), its role, tenant, case and expiry; policy is re-evaluated from fresh facts at execution; an amended action voids the old approval and needs a new one that its amender cannot give.**  
  *Limit:* Approvers are simulated (signed mock identities); there is no real identity provider. *(use: CV, portfolio; evidence: deterministic tests; id `approval-bound-to-exact-action`)*
- **Every customer write carries a deterministic idempotency key recorded in a ledger; a write whose outcome is unknown is never retried blindly, and a human reconciles it.**  
  *Limit:* Customer systems are deterministic mocks with fault injection; reconciliation trusts the operator's recorded note because the mocks offer no lookup. *(use: CV, portfolio; evidence: deterministic tests; id `idempotent-execution-and-reconciliation`)*
- **Tenant isolation is enforced in the database: forced row-level security keyed to a signed, short-lived scope, a fixed query catalogue and a least-privilege role for the model-facing code; operator reads are authorised server-side by signed account grants with a uniform 404.**  
  *Limit:* Single PostgreSQL instance and a mock identity provider; signing-key custody and rotation are out of scope. *(use: CV, portfolio; evidence: deterministic tests; id `tenant-isolation-rls`)*
- **Every proposal, policy decision, approval, execution and refusal is written to an append-only, hash-chained audit log that also scrubs secrets and masks e-mail addresses.**  
  *Limit:* Tamper-evidence, not immutability: removing the most recent events is detectable only with an external anchor, which is not deployed. *(use: CV, portfolio; evidence: deterministic tests; id `tamper-evident-audit`)*
- **A PostgreSQL lease-based recovery worker resumes cases from durable state; tests with concurrent workers, including workers that ignore the lease, produce exactly one effect per approved action.**  
  *Limit:* Tested on one database with threads/processes on one host; not tested under real network partitions or multiple hosts. *(use: portfolio only; evidence: deterministic tests; id `recovery-with-leases`)*
- **Operational metrics are derived from the application's own events and tables (an empty system reports zeros) and correlated by request, model invocation, action, approval and execution ids; telemetry never stores prompts, reasoning or secrets.**  
  *Limit:* No external metrics backend, tracing system, alerting or retention policy. *(use: portfolio only; evidence: deterministic tests; id `observability-from-real-events`)*
- **The operator UI is server-rendered with no JavaScript, a strict Content-Security-Policy and CSRF-protected forms; every read and action is re-authorised on the server because the browser is not trusted.**  
  *Limit:* Sign-in is simulated (a persona picker); no session revocation, rate limiting or MFA. *(use: portfolio only; evidence: deterministic tests; id `operator-ui-no-javascript`)*
- **472 automated tests pass with 1 documented expected failure (the frozen runtime's approver hook is a bool callback, wrapped at the control boundary) and 0 failures; 0 skipped.**  
  *Limit:* One full run at the evidence commit on one machine; the database tests need PostgreSQL 16 with pgvector and are executed (not skipped) in CI. Python 3.12.15. *(use: CV, portfolio; evidence: deterministic tests; id `test-suite`)*
- **92 attacks against the four invariants are catalogued, and 92 have executable tests that attempt the violation (I1 34, I2 37, I3 9, I4 12).**  
  *Limit:* The catalogue is the author's own; tests attempt known attack classes, not an independent penetration test. *(use: CV, portfolio; evidence: deterministic tests; id `threat-catalogue`)*
- **Deliberately breaking each defence makes the tests fail: M2 retrieval 9/9, M3 control plane 31/31, M4 workflow 20/20, M5 operator surface 30/30 mutations killed.**  
  *Limit:* Hand-chosen mutations by the author (not a systematic mutation tool); a first M5 run had one survivor, which led to an added test. Mutation checks are manual, not run in CI. *(use: CV, portfolio; evidence: mutation checks; id `mutation-checks`)*
- **Across 30 control-plane scenarios, 315 end-to-end case executions and 32 injection attack runs, no invariant was violated and the audit hash chain verified over 5385 events.**  
  *Limit:* This measures the controls around the model, not the model: the model is the stand-in and, in the injection runs, a deliberately obedient scripted model. Containment is not the same as a correct outcome. *(use: portfolio only; evidence: stand-in workflow runs; id `invariants-held-in-scenario-runs`)*
- **On a frozen 40-ticket hand-labelled held-out set, pgvector search (Hit@1 0.80, MRR@10 0.87) ranked better than lexical full-text (Hit@1 0.60, MRR@10 0.70), equal-weight hybrid fusion (0.68, 0.79) and hybrid plus cross-encoder rerank (0.76, 0.84); vector was chosen.**  
  *Limit:* Labelled by a single AI reviewer in one session, not independent human annotation; n=25 answerable tickets, so differences are descriptive and the 95% intervals overlap; one embedding model and one reranker; 60-document corpus. *(use: portfolio only; evidence: frozen held-out retrieval eval; id `retrieval-vector-beat-hybrid`)*
- **The generator-labelled development set flattered every retrieval strategy: vector Hit@1 0.97 on dev vs 0.80 held-out, lexical 0.86 vs 0.60.**  
  *Limit:* Dev tickets are templated and labelled by the same generator (circular); they were used only to choose parameters; held-out remains single-reviewer and small. *(use: portfolio only; evidence: synthetic dev retrieval eval; id `synthetic-dev-set-flatters-retrieval`)*
- **Retrieval confidence does not detect missing evidence: on unanswerable held-out tickets every strategy returned look-alike pages as evidence 45%/55%/45%/36% of the time (lexical/vector/hybrid/rerank, n=11), so sufficiency is judged from content and policy, with escalation when unsure.**  
  *Limit:* n=11 unanswerable tickets, single AI reviewer; an engineering finding that shaped the design, not a benchmark score. *(use: portfolio only; evidence: frozen held-out retrieval eval; id `similarity-is-not-sufficiency`)*
- **The cross-encoder reranker was not adopted: no ranking gain over plain vector search (MRR@10 0.84 vs 0.87) for about 107 ms of extra CPU per query and a 347 MB model snapshot.**  
  *Limit:* One reranker model, one laptop, 60 chunks; latency says nothing about scale. *(use: portfolio only; evidence: frozen held-out retrieval eval; id `reranker-not-justified`)*
- **Deterministic grounding checks on draft replies caught 20/20 steered drafts they were developed against, but 0/8 rephrasings of the same harms and 0/10 misleading drafts built only from grounded words (and flagged 0/13 benign drafts); the control for what rules cannot see is mandatory, itemised human review.**  
  *Limit:* Hand-written corpus by the author of the rules (two rules were corrected after the first run): development results; the residual weakness is preserved on purpose; no human-review study exists. *(use: portfolio only; evidence: adversarial dev corpus; id `draft-steering-result`)*
- **A manual browser pass at desktop and 375 px found and fixed seven UI defects (including mobile overflow); scripted checks found one h1 per page, labelled controls, captioned tables, a first-tab skip link, and 0 WCAG AA contrast failures across 263 text elements in light and dark mode.**  
  *Limit:* One browser engine, one developer-operator, synthetic data; no screen-reader session; the automated tests, not this pass, are authoritative. *(use: portfolio only; evidence: manual browser pass; id `browser-accessibility-pass`)*
- **Pattern-based redaction removes labelled secrets (Bearer tokens, NAME=value, PEM blocks, DSN passwords) but not a bare unlabelled token typed into a ticket; that token is stored and shown to operators entitled to that tenant.**  
  *Limit:* Pinned by a test as a documented residual (R-54); entropy heuristics would add false positives and were not attempted. *(use: portfolio only; evidence: deterministic tests; id `limit-unlabelled-secrets`)*
- **A convincing wrong draft made only of grounded words and numbers cannot be detected by any deterministic rule here; the only control is human review, which is recorded but is not a technical gate on use.**  
  *Limit:* Human review effectiveness was not measured (R-58). *(use: portfolio only; evidence: adversarial dev corpus; id `limit-misleading-grounded-drafts`)*
- **Sign-in is simulated: a persona picker mints a signed identity; there is no real authentication, session revocation, rate limiting or MFA.**  
  *Limit:* Demo-only by design (R-59); a deployment needs an identity provider. *(use: CV, portfolio; evidence: documented design; id `limit-simulated-authentication`)*
- **Reconciling an uncertain write as applied trusts the operator's recorded note, and the audit log has no external anchor, so deletion of the most recent events is detectable only with one.**  
  *Limit:* Documented residuals (R-32, R-62). *(use: portfolio only; evidence: documented design; id `limit-reconciliation-and-audit-anchor`)*

*Generated from [`content/public-claims.json`](../content/public-claims.json) at code commit `60850ee1ae`. Claims marked "portfolio only" are not for a CV.*
<!-- claims:interview:end -->
