# Getting started: from a clean checkout to a running demo

Everything here is **synthetic and local**. No paid service, no API key, no model download and no real data is needed. Sign-in is simulated and the demo's model is a deterministic stand-in (not an LLM); the one real-model run is recorded separately and needs no model to read or replay.

## What was verified where
| Path | Verified by |
|---|---|
| Embedded PostgreSQL (no Docker): install, lint, full test suite, demo walk-through, demo server start | executed from a **fresh clone** on the development machine (macOS, Python 3.12) as part of the M6 release; results in [`evaluation.md`](evaluation.md) and the release notes |
| Docker Compose database: `docker compose up`, clean database rebuild, database tests, demo walk-through | **CI only** (the `compose` job). Docker is not installed on the development machine (R-7), so this path was **not executed locally** |
| Python 3.11 | CI (`lint + tests without a database`, 3.11 and 3.12) |

## Prerequisites
- Python 3.11 or newer, `git`, and network access for `pip` (it installs the pinned agent runtime from GitHub, `psycopg`, `jinja2`, and for the embedded database `pgserver`).
- Optional: Docker (only for the Compose path).
- Disk: a few hundred MB (the embedded PostgreSQL binaries). The embedding vectors the retrieval needs are committed (`data/embeddings`), so no model is downloaded.

## Configuration
- **Embedded path:** none. Passwords, signing secrets and the database are generated per run and thrown away.
- **Compose path:** `cp .env.example .env` (throw-away development values; `.env` is git-ignored). Nothing in the repository is a real secret; the only secret-looking strings are planted test canaries.

## Path A: no Docker (embedded PostgreSQL)
```bash
git clone https://github.com/riteshmamidi0905-lab/support-escalation-copilot.git
cd support-escalation-copilot
python -m venv .venv && . .venv/bin/activate
make setup          # editable install with the dev tools
make lint           # ruff
make test-db        # the FULL suite on an ephemeral embedded PostgreSQL 16 + pgvector (database tests run, they are not skipped)
make demo-smoke     # walks demos A-F over HTTP and checks what happened: 21 checks
make demo           # operator UI at http://127.0.0.1:8765/login (Ctrl-C to stop; its database is dropped on exit)
```
`make test` (no database) runs the tests that do not need PostgreSQL and **skips** the database tests with a stated reason; do not read that as the full suite.

What `make demo` does, step by step (all inside `scripts/run_demo_server.py`): create a throw-away database, apply the nine migrations, create the four database roles with random passwords, generate the synthetic dataset from seed 20260101 and validate it against the contracts, load it with the least-privileged loader role, insert two extra synthetic demo tickets, wire the services, start the recovery worker thread and serve the app. Then follow [`demo-walkthrough.md`](demo-walkthrough.md).

## Path B: Docker Compose database
```bash
cp .env.example .env
make db-up                                     # docker compose up -d --wait: PostgreSQL 16 + pgvector on 127.0.0.1:5433
export COPILOT_DEMO_ADMIN_DSN=postgresql://copilot_admin:dev-only-change-me@127.0.0.1:5433/copilot
make demo-docker                               # same app and demo, building its throw-away database on the Compose server
python scripts/demo_smoke.py                   # the walk-through, against the Compose database
COPILOT_TEST_DATABASE_URL=$COPILOT_DEMO_ADMIN_DSN pytest -q   # the full suite against the Compose database
make db-down                                   # teardown: removes the container and its volume
```
The application itself is deliberately **not** containerised: Compose provides only the database (restraint: nothing else was needed to demonstrate the design).

## Explicit database initialisation (what the tests and the demo automate)
```bash
python -c "import secrets;print(secrets.token_hex(32))"            # generate throw-away values for the COPILOT_* variables in .env.example
python -m copilot.db.rebuild --dbname copilot_dev                  # migrate, bootstrap roles, generate, validate, load, tenant sweep (needs the COPILOT_* variables)
python -m copilot.data.cli generate --seed 20260101 --out data/meridian-seed-20260101   # regenerate the dataset: identical bytes for the same seed (CI checks)
```

## Reset and teardown
- Embedded PostgreSQL and the demo database are temporary: stopping the demo drops its database; the embedded server's data directory is removed when the process exits.
- Compose: `make db-down` (removes the volume). Re-run `make db-up` for a clean database.
- Generated artefacts you may create locally (`.pytest_cache`, `*.egg-info`, `reports/m2-replay`) are git-ignored.

## Other useful commands
| Command | What it does |
|---|---|
| `make check-links` | every relative Markdown link and anchor resolves |
| `make threat-model` | regenerate the attack tables in `docs/threat-model.md` from `copilot/invariants.py` |
| `python scripts/with_local_pg.py python scripts/mutation_check_m5.py` | deliberately break each M5 defence and require a failing test (also `_m2`, `_m3`, `_m4`) |
| `make benchmark` | re-run the M2 retrieval benchmark from a clean database, replaying the committed embedding caches (no model needed) |
| `python scripts/with_local_pg.py python scripts/collect_release_evidence.py` | re-collect the release evidence (about 25 minutes, includes the mutation checks) |

## If something does not work
- `No PostgreSQL to build the demo on`: run through `scripts/with_local_pg.py` (Path A) or set `COPILOT_DEMO_ADMIN_DSN` (Path B).
- Database tests report `skipped`: no database URL was set; use `make test-db`.
- `pip` cannot fetch the pinned runtime: it is installed from `github.com/riteshmamidi0905-lab/ai-agent-from-scratch` at a fixed commit; a network proxy blocking GitHub will stop installation.
