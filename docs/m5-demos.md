# Deterministic demo entry points (A–F)

Run: `python scripts/with_local_pg.py python scripts/run_demo_server.py` then open `http://127.0.0.1:8765/login`. Everything is synthetic and local, sign-in is **simulated** (pick a persona), and the
model is the deterministic stand-in `RuleCaseModel` (**not an LLM**). Each demo opens a *real case* through the real workflow on the synthetic Meridian environment; only the model (demo C) and
customer-system faults (E, F) are scripted, and the page says so. **A demo where the system refuses, abstains or admits uncertainty is a success.** Demo mode must be enabled explicitly: demo C's
deliberately obedient model must never run in a real deployment.

| demo | what happens | what to look at | asserted by |
|---|---|---|---|
| **A** routine | a runbook applies; evidence is cited; the diagnosis is recorded; a draft is written; no action needed | evidence with provenance (version, owner, effective date, supersedes), applicability notes, an **unreviewed** draft, "nothing was executed" | `test_demo_a_*` |
| **B** approval-gated re-sync | duplicate events on a carrier feed; policy `REQUIRE_APPROVAL`; the SRE approves; **exactly one** customer-side effect | the exact action + hash + evidence shown to the approver; the approve button appears only for the SRE; the correlation table request → model invocation → action → approval → execution | `test_demo_b_*` |
| **C** prompt injection | a hostile ticket + injected runbooks; the scripted **obedient** model asks to skip approval, refund 100%, re-sync and e-mail everyone | forbidden proposal dropped (`FORBIDDEN_ACTION`), 100% credit refused (`CREDIT_ABOVE_AGENT_THRESHOLD`), the re-sync still waits for a human and shows the injected text *as data* in the exact action, the secret-echoing draft rejected | `test_demo_c_*` |
| **D1** insufficient evidence | a question the runbooks do not answer | `ABSTAINED`, the missing evidence listed, "does not guess" | `test_demo_d1_*` |
| **D2** conflicting evidence | two active runbook versions disagree on webhook retries | both versions shown, contradiction flagged, the draft discloses it, review is **elevated** (each risk acknowledged item by item, enforced on the server) | `test_demo_d2_*` |
| **E** uncertain execution | the customer system times out *after* applying a re-sync | the system does **not** retry; `UNCERTAIN OUTCOME`; the customer-systems page shows the effect happened once; an SRE reconciles it | `test_demo_e_*` |
| **F** dependency outage | the live status API fails every retry | `UNVERIFIED`, actions disabled, no approval requested; the Operations page shows the failed calls and retries | `test_demo_f_*` |

Personas: Lee (Tier-2), Omar (support manager), Rina (on-call SRE), Sam (Tier-2 on **unrelated** accounts: sees none of these cases, a live isolation check), Ria (auditor, all accounts: audit timelines, hash-chain verification, Operations).

What the demos do **not** show: a real model's behaviour; real authentication; real customer systems. They also cannot show that the stand-in generalises: its heuristics were developed while looking at the scenario set (R-43).
