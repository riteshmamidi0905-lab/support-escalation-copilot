# M6 repository audit

Done **before** any release documentation was written: the complete repository state from M0 to M5 was inspected for stale or contradictory documentation, obsolete architecture descriptions, temporary files,
accidental secrets, generated artefacts that should not be committed, broken links, misleading metric descriptions, and anything implying a real LLM was evaluated, a production deployment, or real customers or customer data.
Historical reports that establish provenance were **kept as written**.

## Method (reproducible)
| Check | How |
|---|---|
| tracked-file inventory, temporary or generated files | `git ls-files`, size ranking, name patterns (`.log .tmp .bak .swp .orig .DS_Store .pyc *.egg-info .env node_modules __pycache__`) |
| secrets and personal data | `git grep -nIE` for key/token/PEM/password patterns and for local paths, personal e-mail and the owner's name; every hit inspected |
| broken links | new `scripts/check_links.py` (relative links and `#anchors` across every Markdown file; GitHub's slug rules), now a CI step |
| stale or contradictory text | `git grep` for milestone references, future-tense statements and every numeric claim (attack counts, test counts, scenario matches), compared with the code and the reports |
| real-LLM / production / real-customer implications | `git grep` for LLM, model and product names, "production", "deployed", "customer data", "accuracy"; each context read |
| frozen artefacts | `tests/test_eval_freeze.py` and `tests/test_real_model_freeze.py` (hash locks) stay green and untouched |

## Findings and what was done
| # | Finding | Action |
|---|---|---|
| 1 | **No temporary or generated junk is tracked** (286 files before M6): no logs, backups, caches, `.env`, bytecode or egg-info; the largest files are the committed embedding caches the benchmark replays | none needed; `reports/m2-replay` and `*.egg-info` remain git-ignored |
| 2 | **No real secret in any tracked file.** Matches are the planted test canaries (`sk-CANARY…`, `CANARY-…`), a deliberately leaky scripted draft used to prove rejection, and the throw-away placeholder `dev-only-change-me` in `.env.example`. No local filesystem path and no personal e-mail address; `gmail.com` appears only as an address the tests expect to be **rejected**; the owner's GitHub organisation appears only in repository URLs | none needed |
| 3 | **No broken links** among the 38 relative links that existed; there were few cross-links at all | link checker added to CI; the new documents are cross-linked and checked (125+ links) |
| 4 | **`docs/architecture.md` was obsolete**: titled "target ... at M0", described FastAPI services, a YAML policy engine and a hybrid-retrieval default | rewritten from the code with four diagrams and a table of where the implementation differs from the spec |
| 5 | **README described M4** and predated the operator UI, observability, recovery and the evidence | rewritten |
| 6 | **`docs/real-vs-simulated.md`** still said "61 of 68 attacks executable" and listed deployment as an M6 item | updated (all 92 executable; Compose and deployment status stated exactly) |
| 7 | **`docs/risks.md` R-48** ("no scheduler, M5/M6") was resolved by M5 | marked resolved with a pointer to R-61's limits |
| 8 | `docs/data-contracts.md` said the "M5 UI will display" the case file; ADR-0011 named FastAPI; ADR-0015 said "no scheduler yet" | stale phrases fixed; ADR-0011 and ADR-0015 carry status/update notes pointing at ADR-0016 (ADRs are not rewritten) |
| 9 | `pyproject.toml` still said `0.0.1  # M0: foundations only`; `docker-compose.yml` said "application services arrive in later milestones" | version 0.6.0; comment corrected (the application is deliberately not containerised) |
| 10 | **Counts differ between snapshots**: the M3 report says 61 of 68 attacks, the M4 report 75 of 76, today 92 of 92; test counts grew | the snapshots are kept (provenance) and `docs/README.md` explains they are point-in-time; current numbers live in one generated place (`reports/m6/release-evidence.json` → `content/public-claims.json`) and CI fails on drift |
| 11 | **Possible misreading of `280/315`** as model accuracy | `docs/evaluation.md` leads that section with the warning; the claim is flagged *not* suitable for the portfolio or a CV in the manifest and a test enforces that no stand-in result is offered for a CV |
| 12 | **No text implies a real LLM was evaluated.** "Claude" appears once as the *labeller* of the held-out set (a disclosed single-AI-reviewer limitation), never as an evaluated system; real-model evaluation is stated as not executed in the README, evaluation, security and readiness documents, and `tests/test_real_model_freeze.py` pins that no result file exists | a manifest rule makes a `real_llm` claim impossible without a recorded result |
| 13 | **No text implies a production deployment or real customers.** The only uses are negations or "what a real deployment would have to change" | the README states it plainly at the top; the release checks reject unqualified overclaim phrases |
| 14 | `docs/spec.md` is the original approved specification and differs from the implementation in places (FastAPI mocks, YAML policy, hybrid hypothesis, ~400 accounts) | left unchanged (it says later documents win); the differences are tabulated in `architecture.md` §7 |
| 15 | `scripts/` had no index and one superseded script (`run_m4_local_model.py`) | `scripts/README.md` added; the superseded script is kept because its report is part of the record |
| 16 | CI annotations warn that `actions/checkout@v4` and `setup-python@v5` run on a deprecated Node version, and `ubuntu-latest` will move to a newer image on 2026-10-19 | **not changed**: noted, harmless today; revisit when the actions publish new majors |

## Not done, on purpose
No `SECURITY.md`, `CONTRIBUTING.md` or issue templates (a process nobody has asked for); no lockfile or SBOM (the agent runtime is pinned to a commit; other dependencies use lower bounds, listed as a residual in `security.md`); no deployment infrastructure of any kind.
