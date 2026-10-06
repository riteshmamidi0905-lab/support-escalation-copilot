# Browser verification of the operator UI (M5)

**The automated tests are authoritative** (`tests/db/test_m5_app_security.py`, `test_m5_app_flows.py`: 34 tests through HTTP-shaped requests). This is a manual pass with the in-app browser against the
running demo server (`scripts/run_demo_server.py`; synthetic data, simulated sign-in, stand-in model), at the desktop pane size and an emulated 375×812 phone, to see what a person sees and to catch what
parsers do not. Screenshots: [`docs/m5/screenshots/`](m5/screenshots). The pass found and fixed real defects (below); it is not a user study and was done by the developer.

## What was exercised
| flow | result |
|---|---|
| Sign in as each simulated persona | works; the header always shows persona, role and account grants |
| Demo B (re-sync): open case → read evidence/applicability → approval panel | exact canonical action, action hash, required role, expiry countdown, "what the approver was shown"; the Tier-2 engineer sees **no** approve button and the reason why |
| Amend the action as Tier-2, then sign in as the SRE | new action id, `AMENDED` badge, new hash, the old approval listed under *Superseded approvals (void)*, the queue lists only the new approval |
| Approve (with a reason) as the SRE | case moves to CLOSED through EXECUTE and DRAFT; one effect; draft shown as **unreviewed** |
| Demo C (obedient model + injection): dropped proposals, refused credit, pending re-sync showing the injected text *as data* → **deny** | case ends REFUSED with disposition DENIED, "Nothing was executed" |
| Demo E (uncertain write) → reconcile as the SRE | UNCERTAIN banner ("did NOT retry"), reconciliation form requires what was checked; after recording: RECONCILED |
| Demo D2 (conflict) → itemised draft review | with nothing ticked the **server** refused (HTTP 400, `ACKNOWLEDGE_EVERY_FLAG`, shown in a `role=alert` banner); with both flags ticked: "REVIEWED BY tier2.lee" |
| Demo F (status API outage) | DEGRADED banner with a plain-language reason, actions disabled, no approval requested |
| Tenant isolation: sign in as Sam (unrelated accounts) and open a real foreign case and a nonexistent case | **identical** "Not found" page, no existence hint; Sam's case list is empty |
| Operations dashboard + audit correlation trace as the auditor | every figure matches the events produced in the session; trace table links request → model invocation → approval → audit events |

## Accessibility (measured in the page, not just eyeballed)
Single `h1` and no skipped heading levels on all pages; one banner/nav/main/footer landmark each and a skip link that is the **first Tab stop** with a visible 3 px focus ring; every form control has a
label; every table has a caption and header scopes; status is always carried by **text** (badges say APPLIES / CONFLICT / UNCERTAIN…), never by colour alone; contrast computed for 263 text elements
on the demo case page: **0 failures against WCAG AA in light mode and 0 in dark mode** (`prefers-color-scheme`). No horizontal overflow at 375 px on any page after the fixes below.
Not done: a screen-reader session, zoom/reflow beyond 375 px, forced-colours mode, voice control.

## Defects the browser pass found (all fixed, regression-covered where testable)
1. **Mobile overflow** (case page 602 px wide, audit page 663 px at a 375 px viewport) from unbroken 64-character hashes → wrapping rules; re-measured 375 = 375 on every page.
2. Amend form showed the current value as a *placeholder* in a narrow field → prefilled, full-width field.
3. The approver had to expand "exact action" to see it → open by default for someone who can decide.
4. Degraded banner read "Status api:server error retries exhausted Actions are disabled" → reasons split and punctuated (`why()` tested).
5. After execution the draft still said "needs approval" and after reconciliation it was stale → draft wording follows the disposition; a "case changed after this draft was written" notice after reconciliation (R-55).
6. The dashboard showed a voided approval as `expired` → `superseded` (R-56).
7. REFUSED+DENIED banner did not mention the denial → it does.

## Console and network
No JavaScript exists to throw. The console listed four resource-status messages, all **deliberate negative requests**: three 404s (a foreign case twice, a nonexistent case) and one 400 (draft review with no acknowledgements). The network log showed two `net::ERR_ABORTED` that came from the automation navigating while a click-triggered navigation was in flight, not from the app. Every other request returned 200 or the expected 303.

## Limits of this verification
One browser engine (the in-app Chromium), one operator, synthetic data, the stand-in model. The approval-timeout countdown and the expiry transition were verified by tests, not in the browser; the recovery worker ran in the background of the demo server but its effect was not observed separately in the browser.
