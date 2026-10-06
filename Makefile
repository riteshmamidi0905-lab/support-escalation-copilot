.PHONY: setup lint test test-db db-up db-down check
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
