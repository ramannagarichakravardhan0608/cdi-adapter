# Developer shortcuts (TECH_STACK §13 "Make — developer convenience").
PY ?= .venv/bin/python
COMPOSE = docker compose -f infra/compose/docker-compose.yml

.PHONY: venv lock loadtest ci test test-unit lint up down nuke logs migrate api worker webapp mlserve watch samples smoke

venv:
	python3 -m venv .venv && $(PY) -m pip install -e ".[dev,ocr,load]"

# E1-S4: regenerate infra/runpod/requirements.lock from pyproject.toml + infra/runpod/versions.env
lock:
	$(PY) scripts/lock_pod_deps.py

# E1-S7: 30 patients x 5-10 documents against HOST (e.g. make loadtest HOST=https://emr.example.in)
loadtest:
	$(PY) scripts/load_test.py --host $(HOST)

ci:
	$(PY) scripts/ci.py

test:
	$(PY) -m pytest

test-unit:
	$(PY) -m pytest -m "not integration"

lint:
	$(PY) -m ruff check src tests db && $(PY) -m ruff format --check src tests db && $(PY) -m mypy src

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

nuke:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f --tail=100

migrate:
	$(PY) -m alembic upgrade head

api:
	$(PY) -m uvicorn cdi_adapter.api:app --host 127.0.0.1 --port 8080

worker:
	$(PY) -m celery -A cdi_adapter.worker.celery_app worker -Q cdi

webapp:
	$(PY) -m cdi_adapter.webapp

mlserve:
	$(PY) -m cdi_adapter.mlserve

watch:
	$(PY) -m cdi_adapter.ingest.watcher

samples:
	$(PY) scripts/make_sample_docs.py --out data/samples

smoke:
	$(PY) scripts/pipeline_smoke.py data/samples
