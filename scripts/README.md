# Scripts

Run database-backed scripts through `python scripts/with_local_pg.py python scripts/<name>.py` (an embedded PostgreSQL with pgvector) or point `COPILOT_TEST_DATABASE_URL` at a server you may create databases on.
"CI" = run by the CI workflow; "manual" = run by a person before a milestone report.

| Script | Purpose | When |
|---|---|---|
| `run_demo_server.py` | the operator UI + demo scenarios A-F on a throw-away database (`make demo`, `make demo-docker`) | manual |
| `demo_smoke.py` | walks demos A-F over real HTTP, 21 outcome checks (`make demo-smoke`) | CI (compose job) + manual |
| `check_links.py` | every relative Markdown link and `#anchor` resolves (`make check-links`) | CI |
| `sync_threat_model.py` | regenerates the attack tables in `docs/threat-model.md` from `copilot/invariants.py` | CI (drift check) |
| `public_claims.py` | builds / checks `content/public-claims.json` and the generated blocks in README, evaluation and interview docs from the recorded evidence | CI (`check`) + manual (`build`) |
| `collect_release_evidence.py` | runs the suite, the four mutation checks and the scenario re-runs; writes `reports/m6/release-evidence.json` | manual (about 25 min) |
| `mutation_check_m2.py` … `_m5.py` | deliberately break each defence and require a failing test; restore every file afterwards (do not edit sources while they run) | manual |
| `run_m3_scenarios.py`, `run_m4_scenarios.py`, `run_m4_injection.py` | M3 control-plane scenarios, M4 S1-S16 (315 case executions) and the injection matrix; with a directory argument they write JSON only | manual |
| `run_benchmark.py`, `tune_dev.py`, `render_m2_report.py`, `render_m3_conflict_order.py` | M2 retrieval benchmark (replays the committed caches), dev-only parameter selection, report rendering | manual |
| `embed_cache.py`, `embed_cache_m4.py` | build or verify the committed embedding / rerank caches with the real pinned local models (needs the `models` extra) | manual, rare |
| `build_hand_set.py`, `build_mini_example.py` | serialise the hand-authored held-out set and its manifest; build the tiny example datasets used to test validators | manual, rare |
| `run_draft_steering_eval.py` | adversarial evaluation of the draft grounding rules; writes `reports/m5/draft-steering.json` and its doc | manual |
| `probe_local_runtime.py` | read-only probe for a legitimate local language-model runtime; writes `reports/m5/real-model-probe.json` | manual |
| `measure_prompt_budget.py` | measures the real prompt sizes per stage (token figures are estimates) | manual |
| `freeze_real_model_protocol.py`, `run_real_model_eval.py` | freeze, then run, the real-model protocol; the runner **refuses** unless a local server lists the model, and writes nothing otherwise | manual; **never run** so far |
| `run_m4_local_model.py` | the M4-era real-model attempt (superseded by `run_real_model_eval.py`; its report records "not executed") | historical |
| `bench_env.py`, `with_local_pg.py`, `ci_annotate.py` | helpers: clean database + dataset; embedded PostgreSQL wrapper; failed tests as CI annotations | helpers |
