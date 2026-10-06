# Threat model

**Scope:** the Support Escalation Copilot for the *fictional* Meridian Freight Systems. All data is synthetic, but the controls are designed as if it were real, because the point of the project is to show how a deployment would be secured. Status of each control is stated honestly in `docs/real-vs-simulated.md`.

## Assets
Customer account data (tenant data), the ticketing system of record, production integrations (re-sync), credit/SLA entitlements, credentials (DB, API, model provider), the audit log, and the integrity of the runbook corpus.

## Trust boundaries
```
 untrusted:  ticket text · retrieved runbooks · API/tool responses · model output · client requests
 trusted:    case service code · policy YAML (reviewed) · fixed SQL catalogue · DB roles/RLS · approver identities (after authentication)
```
**Rule:** untrusted content is *evidence*, never authority over policy. The model proposes typed actions; only the policy engine plus a human approval can authorise them.

## Adversaries
(1) a customer writing a malicious ticket; (2) poisoned or stale internal documents; (3) a compromised or buggy tool/API returning hostile content; (4) a curious or careless internal user attempting to see other accounts; (5) a model that misbehaves (hallucinates, over-reaches, follows injected text); (6) an operator mistake (misconfiguration, leaked secret).
Out of scope for this project: a compromised host, a malicious DB administrator, supply-chain attacks on third-party packages beyond pinning.

## STRIDE summary
| Threat | Example | Control (designed unless noted) |
|---|---|---|
| Spoofing | forged approver identity | authenticated approver, role claims checked server-side (M3) |
| Tampering | edit audit history; replay approval for a different action | hash-chained append-only audit; approval bound to exact action hash (M3) |
| Repudiation | "I never approved that" | audit records approver, role, time, action diff |
| Information disclosure | cross-account data; secrets in logs | RLS + ScopeGuard (I2); redaction + canaries (I4) |
| Denial of service | tool outage, runaway loop | timeouts, retries, circuit breakers, budgets (runtime + M5) |
| Elevation of privilege | injection convinces the agent to act | tool tiers, typed actions, approval gates (I1) |

## Invariants and the attacks that must try to break them
Each attack below is a *test that attempts the violation*. `implemented` means a test exists in the repository now; `planned` names the milestone that adds it. The catalogue lives in `copilot/invariants.py`; `tests/test_invariant_catalog.py` keeps this document, the catalogue and the tests in sync (regenerate with `python scripts/sync_threat_model.py`).

### I1 — No gated action executes without a valid, role-authorised, unexpired human approval.
| Attack | Attempted violation | Milestone | Status |
|---|---|---|---|
| A-I1-01 | Call the executor for a gated action with no approval record. | M3 | planned |
| A-I1-02 | Execute with an approval that is denied. | M3 | planned |
| A-I1-03 | Execute with an approval that has expired. | M3 | planned |
| A-I1-04 | Approve with a user whose role is not the action's required role (e.g. manager approving a production re-sync). | M3 | planned |
| A-I1-05 | Replay an approval for action X to execute a different action Y or different params. | M3 | planned |
| A-I1-06 | Model/tool output claims 'approved' in text; executor must not accept text as approval. | M3 | planned |
| A-I1-07 | Ticket or runbook text instructs the agent to skip approval. | M4 | planned |
| A-I1-08 | Duplicate/concurrent execution of one approved action (idempotency). | M3 | planned |
| A-I1-09 | Approve via forged identity header or missing authentication. | M3 | planned |
| A-I1-10 | Request a credit above the agent-requestable threshold. | M3 | planned |

### I2 — No data of one account is exposed in a case about another account.
| Attack | Attempted violation | Milestone | Status |
|---|---|---|---|
| A-I2-01 | Query tenant tables with no account scope set (must return zero rows). | M1 | implemented |
| A-I2-02 | Application passes the wrong account id; ScopeGuard rejects it before SQL, and the database independently returns nothing when the guard is bypassed. | M1 | implemented |
| A-I2-03 | Session-level scope leaks across pooled connections (must be transaction-local). | M1 | implemented |
| A-I2-04 | Table owner without FORCE ROW LEVEL SECURITY bypasses policies. | M1 | implemented |
| A-I2-05 | SQL executed by the app role sets the scope itself, with no or garbage signature (with signed scope this must return nothing). | M1 | implemented |
| A-I2-06 | Ticket text asks to compare with, or reveal, another account. | M4 | planned |
| A-I2-07 | Retrieved document or API response instructs the agent to fetch another account's data. | M4 | planned |
| A-I2-08 | Account id smuggled in tool arguments differs from the case's account. | M3 | planned |
| A-I2-09 | Search/retrieval returns another account's ticket history. | M2 | planned |
| A-I2-10 | Audit/log queries filtered by account return other accounts' events. | M5 | planned |
| A-I2-11 | Incident data reveals which OTHER accounts an incident affected. | M1 | implemented |
| A-I2-12 | Global runbook corpus contains tenant identifiers (ids, names, contact emails). | M1 | implemented |
| A-I2-13 | Forged scope: someone else's signature, wrong secret, garbage or malformed signature. | M1 | implemented |
| A-I2-14 | Tamper with any signed scope field (account, case, expiry, key id) or replay a token for a different case. | M1 | implemented |
| A-I2-15 | Use an expired scope. | M1 | implemented |
| A-I2-16 | A validly SIGNED scope whose case does not belong to its account (buggy or malicious signer) must fail closed. | M1 | implemented |
| A-I2-17 | Application role tries to read the signing key, call the HMAC function, or find any signing oracle. | M1 | implemented |
| A-I2-18 | Exception, SQL error, statement timeout or buggy session-level setting leaves scope behind for the next borrower of a pooled connection. | M1 | implemented |
| A-I2-19 | Direct references to another account's tickets, history, cases or rows through joins. | M1 | implemented |
| A-I2-20 | Ticket text or model-supplied arguments try to set or change the scope of a case. | M1 | implemented |
| A-I2-21 | Superuser/BYPASSRLS/owner behaviour: roles that bypass RLS see everything, the audit detects them, and FORCE binds the owner. | M1 | implemented |
| A-I2-22 | Arbitrary SQL, unknown query names, extra/mistyped parameters and SQL-injection payloads against the fixed catalogue. | M1 | implemented |
| A-I2-23 | Any code path outside copilot.db executes SQL, or builds SQL by string formatting (static check). | M1 | implemented |
| A-I2-24 | Application role attempts writes, DDL, role/policy changes, SET ROLE, COPY, file reads. | M1 | implemented |

### I3 — No customer email is ever sent by the system.
| Attack | Attempted violation | Milestone | Status |
|---|---|---|---|
| A-I3-01 | No action type, tool, client or code path named or capable of sending customer email exists (static + registry check). | M3 | planned |
| A-I3-02 | Agent tries to call a nonexistent 'send_email' tool; it must fail as unknown. | M3 | planned |
| A-I3-03 | Notifier webhook is restricted to internal destinations; customer addresses are rejected. | M3 | planned |
| A-I3-04 | Draft reply approved by a human is stored, never transmitted. | M4 | planned |
| A-I3-05 | Ticket asks 'email the customer now'; outcome is a draft only. | M4 | planned |

### I4 — No secret appears in logs, traces, audit payloads or API responses.
| Attack | Attempted violation | Milestone | Status |
|---|---|---|---|
| A-I4-01 | Canary secrets in env/config never appear in captured logs under normal and error paths. | M0 | implemented |
| A-I4-02 | Canary secrets typed into a ticket do not reach logs, traces, audit payloads or the model prompt. | M4 | planned |
| A-I4-03 | Exception messages containing DSNs/passwords are redacted before logging. | M0 | implemented |
| A-I4-04 | A deliberately leaky logger is detected by the canary scanner (positive control). | M0 | implemented |
| A-I4-05 | API error responses and stack traces contain no secrets. | M3 | planned |
| A-I4-06 | Tool/API responses containing secrets are redacted before storage and display. | M3 | planned |

**Totals:** 45 attacks; 22 have executable tests; 23 are planned.

## Threats specific to the design
- **T-I2-5 — RLS by session setting protects against application bugs, not against arbitrary SQL.** Any code that can run SQL as the app role can set `app.account_id` itself (demonstrated by A-I2-05). Mitigations: (a) the model and users can never author SQL — only a fixed, parameterised query catalogue exists; (b) ScopeGuard checks the scope against the case's account before every query (A-I2-02/08); (c) M1 evaluates *signed scope*: the case service mints an HMAC over (account, case, expiry) that a SQL function verifies using a secret the app role cannot read. The decision is recorded in ADR-0004.
- **T-S1..S4 — secrets.** S1 secrets in config → environment only, never committed; S2 secrets pasted in tickets → redacted before model/prompt/log; S3 secrets in exceptions → redaction at log boundary; S4 secrets in API responses → redacted on ingestion.
- **T-D1 — document poisoning.** Runbooks carry version/provenance; adversarial documents exist in the synthetic corpus on purpose (A-I1-07, A-I2-07).
- **T-D2 — stale evidence.** Contradictory versions are shown, not silently resolved (see ADR-0005 and data contract `runbook_doc`).
- **T-M1 — model over-confidence.** Low confidence disables actions and requires review; "insufficient evidence" is a first-class outcome.

## Residual risks (accepted, stated)
Regex-based injection and secret scanning are heuristics; permissions, approvals and RLS are the real protection. A real deployment would also need network segmentation, key management and a review of the model provider's data handling, none of which are simulated here.
