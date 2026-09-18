PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
ALEMBIC ?= $(PYTHON) -m alembic -c alembic.ini
PROJECT_NAME ?= sustainability-dataspace-api
COMPOSE ?= docker compose --env-file .env -f compose.yml

ifeq ($(OS),Windows_NT)
ifneq ("$(wildcard .venv/Scripts/python.exe)","")
PYTHON := .venv/Scripts/python.exe
endif
else
ifneq ("$(wildcard .venv/bin/python)","")
PYTHON := .venv/bin/python
endif
endif

PUBLIC_TESTS := tests/test_settings_extra_env.py tests/test_require_database_mode.py tests/test_wave5_db_first_mode.py tests/test_main_lifespan_db_required.py tests/test_value_import_performance_gate.py tests/test_e4_raw_transform_gate.py

.PHONY: help check-python install install-dev test-public check-source public-env migrate build up down logs smoke gate-e4-r8 gate-e4-r10 verify-public

help:
	@printf '%s\n' 'SDS public source profile' \
	  '  make public-env   Generate ignored local secrets in api/.env' \
	  '  make up           Start PostgreSQL + API public-demo profile' \
	  '  make smoke        Wait for /healthz and /ready' \
	  '  make gate-e4-r8   Execute the synthetic 1,000-row transformation gate' \
	  '  make gate-e4-r10  Execute the synthetic 1,000-row import-performance gate' \
	  '  make test-public  Run source-only public regression tests' \
	  '  make verify-public Run source checks and both public gates'

check-python:
	@$(PYTHON) -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 10) else 'SDS public profile requires CPython 3.10')"

install: check-python
	$(PIP) install -r requirements.txt

install-dev: check-python
	$(PIP) install -r requirements-dev.txt

test-public: check-python
	$(PYTHON) -m pytest -q $(PUBLIC_TESTS)

check-source: check-python
	$(PYTHON) -m compileall -q src scripts

public-env: check-python
	$(PYTHON) scripts/bootstrap_public_env.py

migrate: check-python
	$(PYTHON) scripts/migrate_db.py

build:
	docker build -t $(PROJECT_NAME):latest .

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down --remove-orphans --volumes

logs:
	$(COMPOSE) logs --tail=200

smoke: check-python
	$(PYTHON) scripts/smoke_public_demo.py

gate-e4-r8: check-python
	$(PYTHON) scripts/gate_raw_value_transform_matrix.py

gate-e4-r10: check-python
	$(PYTHON) scripts/gate_value_import_performance.py

verify-public: test-public check-source smoke gate-e4-r8 gate-e4-r10
