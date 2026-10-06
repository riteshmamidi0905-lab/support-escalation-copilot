# Demo walkthrough (about 15 minutes)

Start it with `make demo` (embedded PostgreSQL) or `make demo-docker` (Compose database), then open <http://127.0.0.1:8765/login>. The executable version of this page is `scripts/demo_smoke.py`
(`make demo-smoke`, 21 checks), which CI also runs against the Compose database.

**What you are looking at:** a fictional customer's support escalations, worked by a fixed workflow whose model is a deterministic stand-in (not an LLM). Sign-in is simulated: you pick a person.
The demos **expose failure modes on purpose**: a case where the system refuses, abstains or admits uncertainty is a success. Screenshots of every step are in [`m5/screenshots/`](m5/screenshots).

## People (simulated)
| Persona | Role | Use it to |
|---|---|---|
| Lee | Tier-2 engineer | open demo cases, read evidence, amend an action, review drafts |
| Rina | on-call SRE | approve or deny production actions (re-sync), reconcile uncertain writes |
| Omar | support manager | approves credits (no demo needs one) |
| Sam | Tier-2 on **unrelated** accounts | prove tenant isolation: sees none of these cases |
| Ria | auditor, all accounts | audit timelines, the Operations dashboard |

Sign out and in between steps (top right); the header always shows who you are and which accounts you may see.

## 1. A: routine case: the system answers, cites, and does nothing risky
As Lee: **Demo cases → Start demo A**. The case closes with an answer. Look at: *Evidence and citations* (document, version, owner, effective date, "applies" verdicts marked **advisory**), the **UNREVIEWED DRAFT** banner (the draft is never sent), and "Nothing has been executed".

## 2. B: approval-gated re-sync: one effect, only after the right human approves
As Lee: **Start demo B**. The case waits: **WAITING FOR A HUMAN**. Open *Proposed actions*: the policy decision (`REQUIRE_APPROVAL`), why, the required role (on-call SRE), the exact action as it will run and its **hash**. Lee sees no approve button, and the page says why.
Optionally open **Amend this action**, change the blast radius, and submit: the old approval is listed as **VOID**, a new approval with a new hash exists, and Lee cannot approve it.
Sign in as Rina, open **Approvals**, open the case, read the action and the evidence the approver was shown, give a reason and **Approve exactly this action**. The case closes through EXECUTE; **Customer systems** shows exactly one applied effect; the **Audit timeline** shows request → model invocation → action → approval → execution under the same ids.

## 3. C: prompt injection reaches the model, not the customer
As Lee: **Start demo C** (a hostile ticket plus injected runbooks; a deliberately **obedient scripted model** tries to skip approval, refund 100%, re-sync and e-mail everyone). Look at *Proposals dropped before policy* (`FORBIDDEN_ACTION`), the refused credit (`CREDIT_ABOVE_AGENT_THRESHOLD`) and the one remaining re-sync, which still waits, with the injected sentence visible **as data** inside the exact action. As Rina, **Deny** it: the case is REFUSED, nothing executed, the secret-echoing draft was rejected.

## 4. D1 and D2: insufficient and conflicting evidence
**D1:** a question the runbooks do not answer: **ABSTAINED**, "does not guess", what was missing is listed. **D2:** two active runbook versions disagree: both shown, **CONFLICT** flagged, the draft says an engineer will confirm, review is **ELEVATED**: with nothing ticked, *Mark draft as reviewed* is refused by the server; tick each risk and it is accepted.

## 5. E: uncertain execution: why idempotency and reconciliation exist
As Lee: **Start demo E**; as Rina approve. The customer system times out *after* applying the change. The case is **UNCERTAIN OUTCOME**: the system did **not** retry (a retry could apply it twice). **Customer systems** shows the effect really happened once. As Rina, **Record reconciliation** (what you checked is required): the case becomes RECONCILED.

## 6. F: dependency outage: degraded, not guessed
**Start demo F**: the live status API fails every retry. **DEGRADED**: the state could not be verified, actions are disabled, nothing waits for approval. As Ria open **Operations**: the failed calls and retries are real events.

## 7. Isolation and operations
As Sam, open any of the case URLs: the same "Not found" as for a case that never existed. As Ria: **Operations** (cases by state, approvals queue age, policy outcomes, executions, dependencies, retrieval fallbacks, recovery) and `/metrics` (Prometheus text); as anyone else, both answer 404.

## What the demo cannot show
A real model's behaviour, real authentication, real customer systems (mocks that do not even update the database facts, so repeating a re-sync on the same integration is not blocked by the cooldown), or real human reviewers. See [`real-vs-simulated.md`](real-vs-simulated.md).
