# Makefile (service-level) for SustainabilityDataSpace API
#
# Goal: keep native local dev first, with optional Docker Compose quickstart
# workflows for operator testing and self-host reference runs.

.PHONY: help help-all check-python ensure-runtime setup install install-dev test test-unit test-coverage lint security-scan static-security dependency-audit dependency-audit-dev dependency-sbom dependency-license-inventory format clean start start-prod stop status-dev status-extra build up down logs ps quick-test start-minimal stop-all restart info migrate migrate-current migrate-stamp migrate-new gate-varch gate-varch-list gate-w0 backfill-semantic backfill-semantic-dry-run gate-w1 backfill-links backfill-links-dry-run gate-w15 project-semantic-catalog project-semantic-catalog-dry-run gate-semantic-catalog-projection generate-projection gate-w2 import-values demo-manifest demo-install demo-verify demo-reset demo-benchmark gate-w4 gate-value-import-performance gate-w5 canonical-mapping-shadow-workflow gate-canonical-mapping-parity

PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
PROJECT_NAME ?= sustainability-dataspace-api
ALEMBIC ?= $(PYTHON) -m alembic -c alembic.ini
COVERAGE_FAIL_UNDER ?= 97.01
COVERAGE_JSON ?= artifacts/coverage/coverage.json

# A caller-selected interpreter (from either its environment or make's command
# line) takes precedence over a convenience local virtual environment.
PYTHON_IS_DEFAULT := $(if $(filter environment command\ line,$(origin PYTHON)),,yes)
ifneq ($(PYTHON_IS_DEFAULT),)
ifeq ($(OS),Windows_NT)
ifneq ("$(wildcard .venv/Scripts/python.exe)","")
PYTHON := .venv/Scripts/python.exe
ALEMBIC := $(PYTHON) -m alembic -c alembic.ini
endif
else
ifneq ("$(wildcard .venv/bin/python)","")
PYTHON := .venv/bin/python
ALEMBIC := $(PYTHON) -m alembic -c alembic.ini
endif
endif
endif

HOST ?= 127.0.0.1
PORT ?= 8090

ENV_FILE ?= .env
COMPOSE_FILE ?= compose.yml
COMPOSE ?= docker compose

# Public-demo imports run in this host CLI process, separate from Compose.
# Keep the revision-write migration switches process-scoped and explicit.
DEMO_REVISION_WRITE_ENV = VALUE_REVISION_API_ENABLED=true VALUE_REVISION_DUAL_WRITE_ENABLED=true VALUE_REVISION_PRIMARY_READ_PATH=revision
# Empty by default: ordinary demo verification is database-backed. CI supplies
# the explicit disposable API-principal switch without placing a secret in argv.
DEMO_VERIFY_ARGS ?=

# Colors
RED := \033[0;31m
GREEN := \033[0;32m
YELLOW := \033[0;33m
BLUE := \033[0;34m
NC := \033[0m

help: ## Show common commands
	@echo "$(BLUE)SustainabilityDataSpace API$(NC)"
	@echo ""
	@echo "$(YELLOW)Local (Python)$(NC)"
	@echo "$(GREEN)  check-python$(NC) Check supported Python version (3.10-3.12)"
	@echo "$(GREEN)  ensure-runtime$(NC) Create/repair .venv runtime dependencies"
	@echo "$(GREEN)  setup$(NC)        Install deps + create .env (if missing)"
	@echo "$(GREEN)  start$(NC)        Run API locally (auto-installs missing deps) at http://$(HOST):$(PORT)"
	@echo "$(GREEN)  stop$(NC)         Stop local API (best-effort; otherwise Ctrl+C)"
	@echo ""
	@echo "$(YELLOW)Testing$(NC)"
	@echo "$(GREEN)  test$(NC)         Run tests"
	@echo "$(GREEN)  test-coverage$(NC)Run tests with coverage"
	@echo "$(GREEN)  gate-w0$(NC)      Check Wave 0 schema migration state"
	@echo "$(GREEN)  backfill-semantic$(NC) Backfill Wave 1 semantic tables from frozen ontology"
	@echo "$(GREEN)  gate-w1$(NC)      Check Wave 1 ontology/DB semantic parity"
	@echo "$(GREEN)  backfill-links$(NC) Backfill Wave 1.5 concept-indicator links"
	@echo "$(GREEN)  gate-w15$(NC)     Check Wave 1.5 linkage coverage"
	@echo "$(GREEN)  project-semantic-catalog$(NC) Project active indicators into DB-backed concepts"
	@echo "$(GREEN)  gate-semantic-catalog-projection$(NC) Check active indicator concept coverage"
	@echo "$(GREEN)  generate-projection$(NC) Generate Wave 2 core/projection ontology artifacts"
	@echo "$(GREEN)  gate-w2$(NC)      Check Wave 2 generated ontology projection coverage"
	@echo "$(GREEN)  import-values$(NC) Import operational ESG values from strict CSV contract (use CSV=...)"
	@echo "$(GREEN)  demo-manifest$(NC) Verify the public synthetic demo package integrity"
	@echo "$(GREEN)  demo-install$(NC) Install the synthetic demo through the synchronous CSV CLI"
	@echo "$(GREEN)  demo-verify$(NC) Verify persisted UC-01..UC-06 demo outcomes"
	@echo "$(GREEN)  demo-reset$(NC) Remove only synthetic demo rows"
	@echo "$(GREEN)  demo-benchmark$(NC) Run the local/CI 1k strict-import regression tripwire"
	@echo "$(GREEN)  gate-w4$(NC)      Check Wave 4 value-import isolation"
	@echo "$(GREEN)  gate-value-import-performance$(NC) Check 1k strict value import throughput and persistence parity"
	@echo "$(GREEN)  gate-raw-value-transform-matrix$(NC) Execute the E4/R8 1k raw-to-expected transformation matrix"
	@echo "$(GREEN)  gate-w5$(NC)      Check Wave 5 DB-first no-fallback enforcement"
	@echo "$(GREEN)  canonical-mapping-shadow-workflow$(NC) Run canonical mapping import/materialize/parity shadow flow"
	@echo "$(GREEN)  gate-canonical-mapping-parity$(NC) Compare legacy and canonical-derived mappings before cutover"
	@echo "$(GREEN)  migrate$(NC)      Apply Alembic migrations to head"
	@echo ""
	@echo "$(YELLOW)Optional Docker Quickstart$(NC)"
	@echo "$(GREEN)  up$(NC)           docker compose up -d --build (compose.yml)"
	@echo "$(GREEN)  down$(NC)         docker compose down"
	@echo "$(GREEN)  logs$(NC)         docker compose logs -f"
	@echo "$(GREEN)  ps$(NC)           docker compose ps"
	@echo ""
	@echo "More: $(BLUE)make help-all$(NC)"

help-all: ## Show all commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "$(GREEN)%-20s$(NC) %s\n", $$1, $$2}'

check-python: ## Check supported Python version
	@$(PYTHON) -c "import sys; v=sys.version_info; raise SystemExit(0 if (v.major == 3 and 10 <= v.minor < 13) else 'ERROR: SDS API local Python supports 3.10-3.12. Use Python 3.12 on macOS/Homebrew and recreate .venv. Current: %s.%s.%s' % (v.major, v.minor, v.micro))"

ensure-runtime: check-python ## Create/repair .venv runtime dependencies
	$(PYTHON) scripts/ensure_runtime.py

setup: check-python ## Install deps and create .env (if missing)
	@echo "$(YELLOW)📦 Installing dependencies...$(NC)"
	@if [ ! -x ".venv/bin/python" ] && [ ! -x ".venv/Scripts/python.exe" ]; then \
		$(PYTHON) -m venv .venv; \
	fi
	@if [ -x ".venv/bin/python" ]; then \
		.venv/bin/python -m pip install --upgrade pip; \
		.venv/bin/python -m pip install -r requirements.txt; \
	elif [ -x ".venv/Scripts/python.exe" ]; then \
		.venv/Scripts/python.exe -m pip install --upgrade pip; \
		.venv/Scripts/python.exe -m pip install -r requirements.txt; \
	else \
		$(PIP) install -r requirements.txt; \
	fi
	@if [ ! -f "$(ENV_FILE)" ] && [ -f ".env.example" ]; then \
		cp .env.example "$(ENV_FILE)"; \
		echo "$(GREEN)✅ Created $(ENV_FILE) from .env.example$(NC)"; \
	fi
	@echo "$(GREEN)✅ Setup complete$(NC)"

install: check-python ## Install runtime dependencies
	$(PIP) install -r requirements.txt

install-dev: check-python ## Install dev dependencies
	$(PIP) install -r requirements-dev.txt

test: check-python ## Run tests
	$(PYTHON) -m pytest tests/ -v --tb=short

test-unit: check-python ## Run unit tests
	$(PYTHON) -m pytest tests/test_*.py -v --tb=short

test-coverage: check-python ## Run tests with coverage
	$(PYTHON) -c "from pathlib import Path; Path('artifacts/coverage').mkdir(parents=True, exist_ok=True)"
	$(PYTHON) -m pytest tests/ --cov=src --cov-report=term --cov-report=html --cov-report=json:$(COVERAGE_JSON) --cov-fail-under=$(COVERAGE_FAIL_UNDER)

gate-varch: ## Run the progressive VARCH semantic-atomization gates (with per-gate observability)
	$(PYTHON) scripts/gate_varch.py

gate-varch-list: ## List the VARCH gate registry (no run)
	$(PYTHON) scripts/gate_varch.py --list

gate-w0: ## Validate Wave 0 schema migration state
	$(PYTHON) scripts/gate_wave0_schema.py

backfill-semantic: ## Backfill Wave 1 semantic tables from the frozen ontology
	$(PYTHON) scripts/backfill_semantic_model.py

backfill-semantic-dry-run: ## Inspect Wave 1 semantic backfill without mutating the DB
	$(PYTHON) scripts/backfill_semantic_model.py --dry-run

gate-w1: ## Validate Wave 1 semantic parity between ontology and DB
	$(PYTHON) scripts/gate_wave1_parity.py

backfill-links: ## Backfill Wave 1.5 concept-indicator links
	$(PYTHON) scripts/backfill_concept_indicator_links.py

backfill-links-dry-run: ## Inspect Wave 1.5 concept-indicator links without mutating the DB
	$(PYTHON) scripts/backfill_concept_indicator_links.py --dry-run

gate-w15: ## Validate Wave 1.5 concept-indicator linkage coverage
	$(PYTHON) scripts/gate_wave15_linkage.py

project-semantic-catalog: ## Project active indicators into canonical/read-model semantic concepts
	$(PYTHON) scripts/project_semantic_catalog.py

project-semantic-catalog-dry-run: ## Preview active-indicator semantic concept projection
	$(PYTHON) scripts/project_semantic_catalog.py --dry-run

gate-semantic-catalog-projection: ## Validate active indicators have deterministic semantic concepts
	$(PYTHON) scripts/gate_semantic_catalog_projection.py

generate-projection: ## Generate Wave 2 split ontology artifacts from DB semantics
	$(PYTHON) scripts/generate_ontology_projection.py

gate-w2: ## Validate Wave 2 generated ontology projection coverage
	$(PYTHON) scripts/gate_wave2_projection.py

import-values: ## Import operational ESG values from strict CSV contract (use CSV=/path/file.csv)
	@test -n "$(CSV)" || (echo "Use: make import-values CSV=/abs/path/values.csv [DEFAULT_ENTITY=nh_es_valencia_plant]" && exit 1)
	$(PYTHON) scripts/import_values_csv.py --csv "$(CSV)" $(if $(DEFAULT_ENTITY),--default-entity "$(DEFAULT_ENTITY)")

demo-manifest: ## Verify public synthetic demo package integrity
	$(PYTHON) scripts/demo_runtime.py manifest

demo-install: ## Install public synthetic demo via supported synchronous CSV importer
	$(DEMO_REVISION_WRITE_ENV) $(PYTHON) scripts/demo_runtime.py install

demo-verify: ## Verify persisted public synthetic demo outcomes
	$(DEMO_REVISION_WRITE_ENV) $(PYTHON) scripts/demo_runtime.py verify $(DEMO_VERIFY_ARGS)

demo-reset: ## Remove only public synthetic demo rows
	$(DEMO_REVISION_WRITE_ENV) $(PYTHON) scripts/demo_runtime.py reset

demo-benchmark: ## Run the 1k strict-import local/CI regression tripwire
	$(DEMO_REVISION_WRITE_ENV) $(PYTHON) scripts/demo_runtime.py benchmark

gate-w4: ## Validate Wave 4 value-import isolation
	$(PYTHON) scripts/gate_wave4_value_isolation.py

gate-value-import-performance: ## Validate 1k strict value import throughput and persistence parity
	$(PYTHON) scripts/gate_value_import_performance.py --tenant-id value_import_gate

gate-raw-value-transform-matrix: ## Validate E4/R8 raw-to-expected transformations
	$(PYTHON) scripts/gate_raw_value_transform_matrix.py --tenant-id value_import_gate

gate-w5: ## Validate Wave 5 DB-first no-fallback enforcement
	$(PYTHON) scripts/gate_wave5_db_first.py

canonical-mapping-shadow-workflow: ## Run canonical mapping import/materialize/parity shadow flow
	$(if $(or $(SKIP_IMPORT),$(PACKAGE_DIR)),,$(error Use: make canonical-mapping-shadow-workflow PACKAGE_DIR=/abs/path/to/canonical-mapping-package [SOURCE_STANDARD=ESRS TARGET_STANDARD=GRI REPORT=... WORKLIST_CSV=...]))
	$(PYTHON) scripts/run_canonical_mapping_shadow_workflow.py $(if $(PACKAGE_DIR),--package-dir "$(PACKAGE_DIR)") $(if $(SKIP_IMPORT),--skip-import) $(if $(SKIP_MATERIALIZE),--skip-materialize) $(if $(SOURCE_STANDARD),--source-standard "$(SOURCE_STANDARD)") $(if $(TARGET_STANDARD),--target-standard "$(TARGET_STANDARD)") $(if $(REPORT),--report "$(REPORT)") $(if $(WORKLIST_CSV),--worklist-csv "$(WORKLIST_CSV)") $(if $(ALLOW_DRIFT),--allow-drift) $(if $(APPROVAL_STATUS),--approval-status "$(APPROVAL_STATUS)")

gate-canonical-mapping-parity: ## Compare legacy mappings with canonical materialized pairwise mappings
	$(PYTHON) scripts/compare_canonical_mapping_parity.py $(if $(SOURCE_STANDARD),--source-standard "$(SOURCE_STANDARD)") $(if $(TARGET_STANDARD),--target-standard "$(TARGET_STANDARD)") $(if $(REPORT),--report "$(REPORT)") $(if $(WORKLIST_CSV),--worklist-csv "$(WORKLIST_CSV)") $(if $(ALLOW_DRIFT),--allow-drift)

migrate: ## Apply Alembic migrations to head
	$(PYTHON) scripts/migrate_db.py

migrate-current: ## Show current Alembic revision
	$(PYTHON) scripts/migrate_db.py --current

migrate-stamp: ## Stamp the current database at Alembic head
	$(ALEMBIC) stamp head

migrate-new: ## Create a new Alembic revision (use MSG="...")
	@test -n "$(MSG)" || (echo "Use: make migrate-new MSG=\"your message\"" && exit 1)
	$(ALEMBIC) revision --autogenerate -m "$(MSG)"

lint: ## Run linters (flake8/black/isort)
	$(PYTHON) -m flake8 src/ tests/
	$(PYTHON) -m black --check src/ tests/
	$(PYTHON) -m isort --check-only src/ tests/

security-scan: static-security dependency-audit dependency-audit-dev dependency-license-inventory ## Run static and dependency security scans

static-security: ## Scan Python and privileged deployment surfaces
	$(PYTHON) -c "from pathlib import Path; Path('artifacts/security').mkdir(parents=True, exist_ok=True)"
	$(PYTHON) -m bandit -r src/ scripts/ ../scripts/ --severity-level medium --confidence-level medium -f json -o artifacts/security/bandit-src-medium.json
	$(PYTHON) ../scripts/check_privileged_surfaces.py --root ..

# Audit the resolved environment after install-dev (as in API CI/api-ci-local).
# Requirements mode bootstraps a temporary venv via ensurepip, which can fail
# under uv CPython. Dev ranges are not a lock: retain installed transitives and
# fail on uncollectable packages with --strict. See docs/tests.md for scope.
dependency-audit: ## Audit installed dependencies, including runtime, for known vulnerabilities
	$(PYTHON) -c "from pathlib import Path; Path('artifacts/security').mkdir(parents=True, exist_ok=True)"
	$(PYTHON) -m pip_audit --strict -f json -o artifacts/security/pip-audit-requirements.json

dependency-audit-dev: ## Audit installed dependencies, including dev/CI, for known vulnerabilities
	$(PYTHON) -c "from pathlib import Path; Path('artifacts/security').mkdir(parents=True, exist_ok=True)"
	$(PYTHON) -m pip_audit --strict -f json -o artifacts/security/pip-audit-requirements-dev.json

dependency-sbom: ## Generate installed-environment SBOM evidence (includes runtime)
	$(PYTHON) -c "from pathlib import Path; Path('artifacts/security').mkdir(parents=True, exist_ok=True)"
	$(PYTHON) -m pip_audit --strict -f cyclonedx-json -o artifacts/security/runtime-sbom.cdx.json

dependency-license-inventory: dependency-sbom ## Export installed-environment license inventory from the SBOM
	$(PYTHON) scripts/export_dependency_license_inventory.py --sbom artifacts/security/runtime-sbom.cdx.json --output artifacts/security/runtime-license-inventory.json

format: ## Format code (black/isort)
	$(PYTHON) -m black src/ tests/
	$(PYTHON) -m isort src/ tests/

clean: ## Remove caches and coverage artifacts
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	rm -rf .pytest_cache htmlcov .coverage

start: ensure-runtime ## Run API locally with reload
	$(PYTHON) scripts/ensure_runtime.py --start --reload --host $(HOST) --port $(PORT)

start-prod: ensure-runtime ## Run API locally (no reload) binding to 0.0.0.0
	$(PYTHON) scripts/ensure_runtime.py --start --host 0.0.0.0 --port $(PORT)

stop: ## Stop local API (best-effort)
	@pids=$$(pgrep -f "uvicorn src.api.main:app" || true); \
	if [ -z "$$pids" ]; then \
		echo "$(YELLOW)ℹ️  No uvicorn process found. If you're running it in the foreground, press Ctrl+C.$(NC)"; \
		exit 0; \
	fi; \
	echo "$$pids" | xargs kill; \
	echo "$(GREEN)✅ Stopped uvicorn$(NC)"

status-dev: ## Print local dev URL hints
	@echo "$(BLUE)Local dev URLs:$(NC)"
	@echo " - API:  http://$(HOST):$(PORT)"
	@echo " - Docs: http://$(HOST):$(PORT)/docs"
	@echo " - Health: http://$(HOST):$(PORT)/healthz"

build: ## Build Docker image (used by compose)
	docker build -t $(PROJECT_NAME):latest .

up: ## Start VM stack using Docker Compose (compose.yml)
	$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) up -d --build

down: ## Stop VM stack
	$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) down --remove-orphans

logs: ## Tail Docker Compose logs
	$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) logs -f --tail=200

ps: ## Show Docker Compose service status
	$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) ps

# Comandos de desarrollo rápido
quick-test: ## Test rápido (solo archivos modificados)
	@echo "$(YELLOW)⚡ Ejecutando tests rápidos...$(NC)"
	$(PYTHON) -m pytest tests/ -x --tb=line

start-minimal: ## Iniciar solo PostgreSQL (Docker) y API (local)
	@echo "$(YELLOW)🚀 Iniciando PostgreSQL (Docker) + API local...$(NC)"
	@$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) up -d postgres
	@echo "$(GREEN)✅ PostgreSQL iniciado (si .env está configurado)$(NC)"
	@$(MAKE) -s start

stop-all: ## Parar API + stack VM (Docker)
	@$(MAKE) -s stop
	@$(MAKE) -s down

restart: stop start ## Reiniciar servidor de desarrollo (uso diario)

status-extra: ## Ver estado adicional de la API local (best-effort)
	@echo "$(BLUE)📊 Estado de la API local:$(NC)"
	@pgrep -f "uvicorn src.api.main:app" > /dev/null && echo "$(GREEN)✅ API ejecutándose$(NC)" || echo "$(RED)❌ API parada$(NC)"
	@echo ""
	@echo "$(YELLOW)Puertos en uso:$(NC)"
	@lsof -i :8090 2>/dev/null || echo "Puerto 8090: libre"

# Información del proyecto
info: ## Mostrar información del proyecto
	@echo "$(BLUE)ℹ️  Información del proyecto:$(NC)"
	@echo ""
	@echo "$(YELLOW)Nombre:$(NC) SustainabilityDataSpace API"
	@echo "$(YELLOW)Descripción:$(NC) API para gestión de ontología ESG unificada"
	@echo "$(YELLOW)Versión:$(NC) 1.0.0"
	@echo "$(YELLOW)Python:$(NC) 3.10-3.13"
	@echo "$(YELLOW)Framework:$(NC) FastAPI"
	@echo ""
	@echo "$(YELLOW)Componentes principales:$(NC)"
	@echo "  • Motor de cálculo de indicadores ESG"
	@echo "  • Sistema de conversión de unidades"
	@echo "  • Agregaciones organizativas y temporales"
	@echo "  • Runtime DB-first con proyección ontológica generada"
	@echo ""
	@echo "$(YELLOW)Comandos útiles:$(NC)"
	@echo "  make setup     - Instalar dependencias + crear .env"
	@echo "  make test      - Ejecutar tests"
	@echo "  make start     - Servidor de desarrollo"
	@echo "  make help      - Ver todos los comandos"
