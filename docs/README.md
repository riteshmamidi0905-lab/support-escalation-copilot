# Documentation map

Start with the [project README](../README.md), then:

| If you want to know… | Read |
|---|---|
| the system at a glance, with diagrams | [`architecture.md`](architecture.md) |
| how to run it from a clean checkout | [`getting-started.md`](getting-started.md) → [`demo-walkthrough.md`](demo-walkthrough.md) |
| what is protected, how, and what is not | [`security.md`](security.md) → [`threat-model.md`](threat-model.md) (the full attack catalogue) |
| what was measured, and what it is worth | [`evaluation.md`](evaluation.md) |
| what went wrong and what changed | [`engineering-lessons.md`](engineering-lessons.md) → [`risks.md`](risks.md) (all findings) |
| what is real vs simulated | [`real-vs-simulated.md`](real-vs-simulated.md) |
| why it is built this way | [`adr/`](adr/README.md), the original [`spec.md`](spec.md) |
| how to explain it | [`interview-guide.md`](interview-guide.md) |
| what may be quoted publicly | [`../content/public-claims.json`](../content/public-claims.json) |
| how the repository was audited for release | [`m6-repository-audit.md`](m6-repository-audit.md) |
| the one real-model run (release v0.7.0; v0.6.0 predates it) | [`m8-real-model-results.md`](m8-real-model-results.md), [`real-model-amendment-A1.md`](real-model-amendment-A1.md) |

## Per-milestone records (point-in-time artefacts)
These documents are **snapshots from the milestone that produced them** and were deliberately not rewritten later, so provenance is kept. Where a snapshot's counts differ from today's (for example
"61 of 68 attacks executable" in the M3 scenario report, or 75 of 76 in M4's), the current numbers are in [`evaluation.md`](evaluation.md) and `reports/m6/release-evidence.json`.

| Milestone | Documents |
|---|---|
| M0 foundations | [`spec.md`](spec.md), [`data-contracts.md`](data-contracts.md), [`threat-model.md`](threat-model.md), [`test-strategy.md`](test-strategy.md) |
| M1 data and tenancy | [`tenant-isolation.md`](tenant-isolation.md), ADR-0004, ADR-0012 |
| M2 retrieval (frozen evaluation) | [`eval-protocol.md`](eval-protocol.md) and `eval-freeze.json` (hash-locked), [`evaluation-methodology.md`](evaluation-methodology.md), [`m2-results.md`](m2-results.md), [`m2-conflict-order-rerun.md`](m2-conflict-order-rerun.md), ADR-0013 |
| M3 control plane | [`m3-scenarios.md`](m3-scenarios.md), ADR-0014 |
| M4 workflow | [`m4-scenarios.md`](m4-scenarios.md), [`m4-injection.md`](m4-injection.md), ADR-0015 |
| M5 operator experience | [`m5-results.md`](m5-results.md), [`m5-demos.md`](m5-demos.md), [`m5-browser-verification.md`](m5-browser-verification.md), [`m5-draft-steering.md`](m5-draft-steering.md), [`m5-real-model-readiness.md`](m5-real-model-readiness.md), [`m5-real-model-protocol.md`](m5-real-model-protocol.md) (frozen), ADR-0016 |
| M6 release | [`m6-repository-audit.md`](m6-repository-audit.md) and everything above marked M6 |
