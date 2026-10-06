.PHONY: benchmark benchmark-live setup lint test test-db db-up db-down check generate rebuild threat-model demo demo-docker demo-smoke check-links release-checks
PY ?= python
setup:            ## editable install with dev tools
	$(PY) -m pip install -e ".[dev]"
lint:
	$(PY) -m ruff check .
test:             ## everything that needs no database
	$(PY) -m pytest -q
test-db:          ## database tests against an ephemeral local Postgres (pgserver, includes pgvector)
	$(PY) scripts/with_local_pg.py $(PY) -m pytest -q
db-up:            ## Postgres+pgvector via Docker (needs POSTGRES_PASSWORD, see .env.example)
	docker compose --env-file .env up -d --wait
db-down:
	docker compose down -v
check: lint test-db check-links
demo:             ## operator UI + demo scenarios A-F on an embedded PostgreSQL (no Docker): http://127.0.0.1:8765/login
	$(PY) scripts/with_local_pg.py $(PY) scripts/run_demo_server.py
demo-docker:      ## the same against the Compose database: make db-up, then export COPILOT_DEMO_ADMIN_DSN (see docs/getting-started.md)
	$(PY) scripts/run_demo_server.py
demo-smoke:       ## walks demos A-F over HTTP on an embedded PostgreSQL and checks what happened (21 checks)
	$(PY) scripts/with_local_pg.py $(PY) scripts/demo_smoke.py
check-links:      ## every relative Markdown link and #anchor resolves
	$(PY) scripts/check_links.py
release-checks:   ## manifest/evidence/docs drift checks (what CI runs); evidence itself: scripts/collect_release_evidence.py
	$(PY) scripts/public_claims.py check && $(PY) -m pytest -q tests/test_release_claims.py
generate:
	$(PY) -m copilot.data.cli generate --seed 20260101 --out data/meridian-seed-20260101
	$(PY) -m copilot.data.cli verify data/meridian-seed-20260101
rebuild:          ## needs COPILOT_ADMIN_DSN and the passwords/secret (see .env.example)
	$(PY) -m copilot.db.rebuild --dbname copilot_dev
threat-model:     ## regenerate the attack tables from copilot/invariants.py
	$(PY) scripts/sync_threat_model.py
benchmark:        ## M2 retrieval benchmark from a clean database, replaying the committed embedding/rerank caches (no model needed)
	$(PY) scripts/with_local_pg.py $(PY) scripts/run_benchmark.py --out reports/m2-replay
benchmark-live:   ## same, but recomputes with the real local models (pip install -e ".[models]")
	$(PY) scripts/with_local_pg.py $(PY) scripts/run_benchmark.py --live
