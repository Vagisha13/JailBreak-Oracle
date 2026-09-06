# Jailbreak Oracle — developer task runner.
#
# Usage:  make <target>
# The backend runs from its own directory; every backend target cd's into
# `backend/` so layouts stay stable (DB, alembic, pytest rootdir).
#
# Override the interpreter with `make <target> PY=python3` if needed.

PY ?= python
PIP := $(PY) -m pip

.PHONY: help setup test lint typecheck check benchmark api worker migrate migration \
        compose-build compose-up compose-down

help:
	@echo "Targets:"
	@echo "  setup            Create venv, install backend deps, seed .env"
	@echo "  test             Run the backend test suite (isolated DB)"
	@echo "  lint             flake8 (backend app + tests + benchmarks)"
	@echo "  typecheck        mypy (backend app + benchmarks)"
	@echo "  check            lint + typecheck + test + benchmark (CI entrypoint)"
	@echo "  benchmark        Run the reproducible benchmark (writes benchmark-report.json)"
	@echo "  api              Run the API dev server with reload"
	@echo "  worker           Run the campaign worker"
	@echo "  migrate          Apply Alembic migrations"
	@echo "  migration        Autogenerate an Alembic migration"
	@echo "  compose-build    Build Docker images"
	@echo "  compose-up       Start full stack via docker-compose (detached)"
	@echo "  compose-down     Stop full stack"

setup:
	$(PY) -m venv .venv
	$(PIP) install --upgrade pip
	cd backend && $(PIP) install -r requirements.txt
	@if [ ! -f backend/.env ]; then cp backend/.env.example backend/.env; fi
	@echo "Done. Edit backend/.env and run: make api"

test:
	cd backend && $(PY) -m pytest

lint:
	cd backend && $(PY) -m flake8 app tests benchmarks --config=.flake8

typecheck:
	cd backend && $(PY) -m mypy --config-file=mypy.ini app benchmarks

check: lint typecheck test benchmark

benchmark:
	cd backend && $(PY) -m benchmarks.run_benchmark --json

api:
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	cd backend && $(PY) -m app.worker

migrate:
	cd backend && alembic upgrade head

migration:
	cd backend && alembic revision --autogenerate

compose-build:
	docker compose build

compose-up:
	docker compose up -d

compose-down:
	docker compose down