.PHONY: benchmark benchmark-live setup lint test test-db db-up db-down check generate rebuild threat-model
PY ?= python
setup:            ## editable install with dev tools
	$(PY) -m pip install -e ".[dev]"
lint:
	ruff check .
test:             ## everything that needs no database
	pytest -q
test-db:          ## database tests against an ephemeral local Postgres (pgserver, includes pgvector)
	$(PY) scripts/with_local_pg.py $(PY) -m pytest -q
db-up:            ## Postgres+pgvector via Docker (needs POSTGRES_PASSWORD, see .env.example)
	docker compose --env-file .env up -d --wait
db-down:
	docker compose down -v
check: lint test-db
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
