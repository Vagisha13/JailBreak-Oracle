# Jailbreak Oracle — developer task runner.
#
# Usage:  make <target>
# The backend runs from its own directory; every backend target cd's into
# `backend/` so layouts stay stable (DB, alembic, pytest rootdir).
#
# Override the interpreter with `make <target> PY=python3` if needed.

PY ?= python
PIP := $(PY) -m pip

.PHONY: help setup test lint typecheck check api worker migrate migration \
        compose-build compose-up compose-down

help:
	@echo "Targets:"
	@echo "  setup            Create venv, install backend deps, seed .env"
	@echo "  test             Run the backend test suite (isolated DB)"
	@echo "  lint             flake8 (backend app + tests)"
	@echo "  typecheck        mypy (backend app)"
	@echo "  check            lint + typecheck + test (CI entrypoint)"
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
	cd backend && $(PY) -m flake8 app tests --config=.flake8

typecheck:
	cd backend && $(PY) -m mypy --config-file=mypy.ini app

check: lint typecheck test

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