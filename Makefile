.PHONY: help status public-env up down smoke api-ci-local aws-profile-check install-hooks public-secret-scan repo-closure-check subsidy-closure-check deliverables-check atomizer-sync atomizer-boundary-check canonical-mapping-v42-classification canonical-mapping-v42-normalized-classification canonical-mapping-v42-defect-summary canonical-mapping-v42-gri-3-3-classification canonical-mapping-v42-gri-3-3-defect-summary canonical-mapping-v42-gri-2-social-classification canonical-mapping-v42-gri-2-social-defect-summary canonical-mapping-v42-gri-2-legal-classification canonical-mapping-v42-gri-2-legal-defect-summary canonical-mapping-v42-gri-2-reviewed-classification canonical-mapping-v42-gri-2-reviewed-defect-summary canonical-mapping-v42-reviewed-narrower-classification canonical-mapping-v42-reviewed-narrower-defect-summary conversion-gate e1-gate e2-gate e2-gate-detection e2-ghg-crosswalk-gate e3-export-ngsi semantics-bundle e4-gate e5-gate e6-refresh-edc e6-check e6-gate e6-evidence e7-gate e8-gate e9-gate e10-gate e11-gate e11-deploy-gate e12-gate e13-gate gate-semantics gate-governance gate-pipeline service-semantic-catalog-projection service-wave4 service-value-import-performance service-wave5 service-test service-coverage

PYTHON ?= python
COMPOSE ?= docker compose
SERVICE_DIR := api
CLOSURE_CHECK := scripts/repo_closure_check.py
# Prefer the established Windows service virtual environment when it exists.
# Else use the root caller's interpreter; ROOT_PY remains an explicit override.
ROOT_PY ?= $(if $(wildcard api/.venv/Scripts/python.exe),api/.venv/Scripts/python.exe,$(PYTHON))
SEMANTICS_GATE := scripts/run_semantics_gate.py
SDS_E4_REGISTER_PATH ?=
SDS_E4_DIMENSION_LEDGER_PATH ?= deliverables/E01-mapeo-datos-normativas/evidence/dimension-expanded-value-coordinate-calculation-v2026-06-05.csv
E4_SOURCE_ARG := $(if $(SDS_E4_REGISTER_PATH),--source-mode register --register "$(SDS_E4_REGISTER_PATH)",--source-mode dimension-ledger --dimension-ledger "$(SDS_E4_DIMENSION_LEDGER_PATH)")

help: ## Show root repo commands
	@echo "SustainabilityDataSpace (root)"
	@echo ""
	@echo "  make status              Show repo closure summary"
	@echo "  make api-ci-local        Run the same source-native API gate as API CI, including coverage"
	@echo "  make aws-profile-check   Validate the public no-apply AWS profile offline"
	@echo "  make install-hooks       Install the local API CI pre-push hook"
	@echo "  make public-secret-scan  Scan the public tree for high-confidence secrets"
	@echo "  make repo-closure-check  Validate closure-layer files and website truth markers"
	@echo "  make subsidy-closure-check  Validate explicit repo-vs-subsidy closure status"
	@echo "  make deliverables-check  Validate official deliverables register, checksums, and privacy heuristics"
	@echo "  make atomizer-sync       Refresh the pinned Atomizer contract or sync from ATOMIZER_EXPORT_DIR"
	@echo "  make atomizer-boundary-check  Validate the Atomizer boundary contract"
	@echo "  make canonical-mapping-v42-classification  Classify the frozen v42 mapping parity worklist"
	@echo "  make canonical-mapping-v42-normalized-classification  Classify v42 after legacy code normalization"
	@echo "  make canonical-mapping-v42-defect-summary  Summarize post-normalization legacy mapping defects"
	@echo "  make canonical-mapping-v42-gri-3-3-classification  Classify v42 after GRI 3-3 overrides"
	@echo "  make canonical-mapping-v42-gri-3-3-defect-summary  Summarize defects after GRI 3-3 overrides"
	@echo "  make canonical-mapping-v42-gri-2-social-classification  Classify v42 after cumulative GRI 3-3 + GRI 2 social overrides"
	@echo "  make canonical-mapping-v42-gri-2-social-defect-summary  Summarize defects after cumulative GRI 3-3 + GRI 2 social overrides"
	@echo "  make canonical-mapping-v42-gri-2-legal-classification  Classify v42 after cumulative GRI 3-3 + GRI 2 social/legal overrides"
	@echo "  make canonical-mapping-v42-gri-2-legal-defect-summary  Summarize defects after cumulative GRI 3-3 + GRI 2 social/legal overrides"
	@echo "  make canonical-mapping-v42-gri-2-reviewed-classification  Classify v42 after all reviewed safe GRI 2 narrower overrides"
	@echo "  make canonical-mapping-v42-gri-2-reviewed-defect-summary  Summarize defects after all reviewed safe GRI 2 narrower overrides"
	@echo "  make canonical-mapping-v42-reviewed-narrower-classification  Classify v42 after cumulative reviewed narrower overrides"
	@echo "  make canonical-mapping-v42-reviewed-narrower-defect-summary  Summarize defects after cumulative reviewed narrower overrides"
	@echo "  make conversion-gate     Validate deterministic unit and FX conversion behavior"
	@echo "  make e1-gate            Validate the imported E1 evidence pack"
	@echo "  make e2-gate            Validate the imported E2 completeness baseline"
	@echo "  make e2-gate-detection  Validate the imported E2 detection baseline"
	@echo "  make e2-ghg-crosswalk-gate  Validate the exact-only GHG Protocol crosswalk snapshot"
	@echo "  make e3-export-ngsi      Export the repo-local NGSI-LD E3 view"
	@echo "  make e3-r5-r7-gate       Validate E3 hierarchy coverage and functional pilot"
	@echo "  make semantics-bundle    Build the deterministic semantics bundle"
	@echo "  make gate-semantics      Run root semantics contract checks"
	@echo "  make e4-gate             Run the E4 prototype performance gate"
	@echo "  make gate-pipeline       Alias for the E4 prototype performance gate"
	@echo "  make e5-gate             Validate the E5 technical-code deliverable structure"
	@echo "  make e6-refresh-edc      Regenerate the EDC bundle from the current E1 register"
	@echo "  make e6-check            Validate imported E6 governance artifacts"
	@echo "  make e6-gate             Explain the required direct E6 gate invocation"
	@echo "  make gate-governance     Alias for e6-gate"
	@echo "  make e6-evidence         Generate the repo-local E6 evidence bundle"
	@echo "  make e7-gate             Validate E7 external evidence status boundary"
	@echo "  make e8-gate             Validate the E8 use-case catalogue structure"
	@echo "  make e9-gate             Validate the E9 adjustments report structure"
	@echo "  make e10-gate            Validate the E10 communications plan structure"
	@echo "  make e11-gate            Validate E11 official website evidence"
	@echo "  make e11-deploy-gate     Alias for E11 official website evidence validation"
	@echo "  make e12-gate            Validate E12 and complete R22 activity coverage"
	@echo "  make e13-gate            Validate the E13 roadmap/scalability draft structure"
	@echo "  make service-wave4       Run the Wave 4 value-import isolation gate"
	@echo "  make service-value-import-performance  Run the 1k strict value-import performance gate"
	@echo "  make service-raw-value-transform-matrix  Run the E4/R8 1k raw transformation matrix gate"
	@echo "  make service-semantic-catalog-projection  Check active indicator concept projection"
	@echo "  make service-wave5       Run the Wave 5 DB-first no-fallback gate"
	@echo "  make service-test        Run the Windows service test workflow"
	@echo "  make service-coverage    Run the Windows service coverage workflow"

status: ## Show repo closure summary
	@echo "STATUS.md"
	@echo ""
	@$(PYTHON) $(CLOSURE_CHECK)

public-env: ## Generate operator-owned credentials in ignored api/.env
	@$(PYTHON) api/scripts/bootstrap_public_env.py

up: ## Start full SDS API and PostgreSQL on loopback
	@$(MAKE) -C $(SERVICE_DIR) up COMPOSE="$(COMPOSE)"

down: ## Stop local stack without deleting its database volume
	@$(MAKE) -C $(SERVICE_DIR) down COMPOSE="$(COMPOSE)"

smoke: ## Exercise the running DB-backed API with local credentials
	@cd $(SERVICE_DIR) && NO_START=1 bash scripts/smoke_stack.sh minimal

api-ci-local: ## Run the same source-native API gate as API CI, including coverage
	@$(MAKE) -C $(SERVICE_DIR) install-dev
	@$(MAKE) -C $(SERVICE_DIR) lint
	@$(MAKE) -C $(SERVICE_DIR) security-scan
	@$(MAKE) -C $(SERVICE_DIR) test-coverage

aws-profile-check: ## Validate the public AWS profile without credentials or cloud calls
	@"$(ROOT_PY)" api/deploy/aws-ec2-rds/validate.py api/deploy/aws-ec2-rds/operator.example.json
	@"$(ROOT_PY)" -m pytest api/tests/test_aws_public_profile.py -q
	@"$(ROOT_PY)" api/deploy/aws-ec2-rds/terraform_offline_check.py

install-hooks: ## Install local git hooks for this checkout
	@$(PYTHON) scripts/install_api_ci_pre_push_hook.py

public-secret-scan: ## Scan public files for high-confidence secrets
	@"$(ROOT_PY)" scripts/check_public_secrets.py

repo-closure-check: public-secret-scan ## Validate repo closure layer
	@$(PYTHON) $(CLOSURE_CHECK)

subsidy-closure-check: ## Validate explicit repo-vs-subsidy closure status
	@"$(ROOT_PY)" scripts/subsidy_closure_check.py

deliverables-check: ## Validate official deliverables register, checksums, and privacy heuristics
	@"$(ROOT_PY)" scripts/check_deliverables.py

atomizer-sync: ## Refresh manifest/checksums or sync Atomizer exports from ATOMIZER_EXPORT_DIR
	@"$(ROOT_PY)" scripts/sync_atomizer_exports.py $(if $(ATOMIZER_EXPORT_DIR),--source-dir "$(ATOMIZER_EXPORT_DIR)")

atomizer-boundary-check: ## Validate Atomizer boundary contract and forbidden dependencies
	@"$(ROOT_PY)" scripts/check_dependency_boundaries.py

canonical-mapping-v42-classification: ## Classify the frozen v42 mapping parity worklist
	@"$(ROOT_PY)" scripts/classify_canonical_mapping_parity_worklist.py --version v42 --source data/extracted/analysis/canonical-mapping-parity-worklist-v42-2026-05-11.csv --classified data/extracted/analysis/canonical-mapping-parity-worklist-v42-classified-2026-05-11.csv --summary data/extracted/analysis/canonical-mapping-parity-classification-summary-v42-2026-05-11.json

canonical-mapping-v42-normalized-classification: ## Classify v42 after legacy code normalization
	@"$(ROOT_PY)" scripts/classify_canonical_mapping_parity_worklist.py --version v42-legacy-normalized --source data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-2026-05-11.csv --classified data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-classified-2026-05-11.csv --summary data/extracted/analysis/canonical-mapping-parity-classification-summary-v42-legacy-normalized-2026-05-11.json

canonical-mapping-v42-defect-summary: ## Summarize post-normalization legacy mapping defects
	@"$(ROOT_PY)" scripts/summarize_canonical_mapping_defects.py --version v42-legacy-normalized --source data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-classified-2026-05-11.csv --csv-output data/extracted/analysis/canonical-mapping-legacy-defect-summary-v42-legacy-normalized-2026-05-11.csv --json-output data/extracted/analysis/canonical-mapping-legacy-defect-summary-v42-legacy-normalized-2026-05-11.json

canonical-mapping-v42-gri-3-3-classification: ## Classify v42 after GRI 3-3 overrides
	@"$(ROOT_PY)" scripts/classify_canonical_mapping_parity_worklist.py --version v42-legacy-normalized-gri-3-3-overrides --source data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-gri-3-3-overrides-2026-05-11.csv --classified data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-gri-3-3-overrides-classified-2026-05-11.csv --summary data/extracted/analysis/canonical-mapping-parity-classification-summary-v42-legacy-normalized-gri-3-3-overrides-2026-05-11.json

canonical-mapping-v42-gri-3-3-defect-summary: ## Summarize defects after GRI 3-3 overrides
	@"$(ROOT_PY)" scripts/summarize_canonical_mapping_defects.py --version v42-legacy-normalized-gri-3-3-overrides --source data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-gri-3-3-overrides-classified-2026-05-11.csv --csv-output data/extracted/analysis/canonical-mapping-legacy-defect-summary-v42-legacy-normalized-gri-3-3-overrides-2026-05-11.csv --json-output data/extracted/analysis/canonical-mapping-legacy-defect-summary-v42-legacy-normalized-gri-3-3-overrides-2026-05-11.json

canonical-mapping-v42-gri-2-social-classification: ## Classify v42 after cumulative GRI 3-3 + GRI 2 social overrides
	@"$(ROOT_PY)" scripts/classify_canonical_mapping_parity_worklist.py --version v42-legacy-normalized-gri-3-3-gri-2-social-overrides --source data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-gri-3-3-gri-2-social-overrides-2026-05-11.csv --classified data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-gri-3-3-gri-2-social-overrides-classified-2026-05-11.csv --summary data/extracted/analysis/canonical-mapping-parity-classification-summary-v42-legacy-normalized-gri-3-3-gri-2-social-overrides-2026-05-11.json

canonical-mapping-v42-gri-2-social-defect-summary: ## Summarize defects after cumulative GRI 3-3 + GRI 2 social overrides
	@"$(ROOT_PY)" scripts/summarize_canonical_mapping_defects.py --version v42-legacy-normalized-gri-3-3-gri-2-social-overrides --source data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-gri-3-3-gri-2-social-overrides-classified-2026-05-11.csv --csv-output data/extracted/analysis/canonical-mapping-legacy-defect-summary-v42-legacy-normalized-gri-3-3-gri-2-social-overrides-2026-05-11.csv --json-output data/extracted/analysis/canonical-mapping-legacy-defect-summary-v42-legacy-normalized-gri-3-3-gri-2-social-overrides-2026-05-11.json

canonical-mapping-v42-gri-2-legal-classification: ## Classify v42 after cumulative GRI 3-3 + GRI 2 social/legal overrides
	@"$(ROOT_PY)" scripts/classify_canonical_mapping_parity_worklist.py --version v42-legacy-normalized-gri-3-3-gri-2-social-legal-overrides --source data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-gri-3-3-gri-2-social-legal-overrides-2026-05-11.csv --classified data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-gri-3-3-gri-2-social-legal-overrides-classified-2026-05-11.csv --summary data/extracted/analysis/canonical-mapping-parity-classification-summary-v42-legacy-normalized-gri-3-3-gri-2-social-legal-overrides-2026-05-11.json

canonical-mapping-v42-gri-2-legal-defect-summary: ## Summarize defects after cumulative GRI 3-3 + GRI 2 social/legal overrides
	@"$(ROOT_PY)" scripts/summarize_canonical_mapping_defects.py --version v42-legacy-normalized-gri-3-3-gri-2-social-legal-overrides --source data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-gri-3-3-gri-2-social-legal-overrides-classified-2026-05-11.csv --csv-output data/extracted/analysis/canonical-mapping-legacy-defect-summary-v42-legacy-normalized-gri-3-3-gri-2-social-legal-overrides-2026-05-11.csv --json-output data/extracted/analysis/canonical-mapping-legacy-defect-summary-v42-legacy-normalized-gri-3-3-gri-2-social-legal-overrides-2026-05-11.json

canonical-mapping-v42-gri-2-reviewed-classification: ## Classify v42 after all reviewed safe GRI 2 narrower overrides
	@"$(ROOT_PY)" scripts/classify_canonical_mapping_parity_worklist.py --version v42-legacy-normalized-gri-3-3-gri-2-reviewed-overrides --source data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-gri-3-3-gri-2-reviewed-overrides-2026-05-11.csv --classified data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-gri-3-3-gri-2-reviewed-overrides-classified-2026-05-11.csv --summary data/extracted/analysis/canonical-mapping-parity-classification-summary-v42-legacy-normalized-gri-3-3-gri-2-reviewed-overrides-2026-05-11.json

canonical-mapping-v42-gri-2-reviewed-defect-summary: ## Summarize defects after all reviewed safe GRI 2 narrower overrides
	@"$(ROOT_PY)" scripts/summarize_canonical_mapping_defects.py --version v42-legacy-normalized-gri-3-3-gri-2-reviewed-overrides --source data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-gri-3-3-gri-2-reviewed-overrides-classified-2026-05-11.csv --csv-output data/extracted/analysis/canonical-mapping-legacy-defect-summary-v42-legacy-normalized-gri-3-3-gri-2-reviewed-overrides-2026-05-11.csv --json-output data/extracted/analysis/canonical-mapping-legacy-defect-summary-v42-legacy-normalized-gri-3-3-gri-2-reviewed-overrides-2026-05-11.json

canonical-mapping-v42-reviewed-narrower-classification: ## Classify v42 after cumulative reviewed narrower overrides
	@"$(ROOT_PY)" scripts/classify_canonical_mapping_parity_worklist.py --version v42-legacy-normalized-reviewed-narrower-overrides --source data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-reviewed-narrower-overrides-2026-05-11.csv --classified data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-reviewed-narrower-overrides-classified-2026-05-11.csv --summary data/extracted/analysis/canonical-mapping-parity-classification-summary-v42-legacy-normalized-reviewed-narrower-overrides-2026-05-11.json

canonical-mapping-v42-reviewed-narrower-defect-summary: ## Summarize defects after cumulative reviewed narrower overrides
	@"$(ROOT_PY)" scripts/summarize_canonical_mapping_defects.py --version v42-legacy-normalized-reviewed-narrower-overrides --source data/extracted/analysis/canonical-mapping-parity-worklist-v42-legacy-normalized-reviewed-narrower-overrides-classified-2026-05-11.csv --csv-output data/extracted/analysis/canonical-mapping-legacy-defect-summary-v42-legacy-normalized-reviewed-narrower-overrides-2026-05-11.csv --json-output data/extracted/analysis/canonical-mapping-legacy-defect-summary-v42-legacy-normalized-reviewed-narrower-overrides-2026-05-11.json

conversion-gate: ## Validate deterministic unit and FX conversion behavior
	@"$(ROOT_PY)" -m pytest api/tests/test_reference_data_builders.py api/tests/test_unit_catalog_expansion.py api/tests/test_bootstrap_units.py api/tests/test_conversion_dimensions.py api/tests/test_unit_expression_parser.py api/tests/test_physical_conversion_engine.py api/tests/test_fx_service.py api/tests/test_fx_repository_service.py api/tests/test_fx_history_import.py api/tests/test_conversion_orchestrator.py api/tests/test_bootstrap_conversion_catalog.py api/tests/test_calculation_engine_conversion_contracts.py api/tests/test_value_ingest_conversion_trace.py api/tests/test_fx_api.py api/tests/test_value_csv_import.py api/tests/test_import_atomizer_sds_package.py api/tests/test_calculation_contract_import.py api/tests/test_require_database_mode.py -q

e1-gate: ## Validate E1 inventory evidence and dimension-expanded totals
	@"$(ROOT_PY)" scripts/e1_check_inventory.py --strict
	@"$(ROOT_PY)" -m pytest api/tests/test_e1_inventory_pack.py -q

e2-gate: ## Validate E2 completeness threshold and publication structure
	@"$(ROOT_PY)" scripts/e2_gate.py --strict
	@"$(ROOT_PY)" -m pytest api/tests/test_e2_interoperability_pack.py -q

e2-gate-detection: ## Validate E2 detection threshold
	@"$(ROOT_PY)" scripts/e2_gate_detection.py --strict

e2-ghg-crosswalk-gate: ## Validate the exact-only GHG Protocol crosswalk snapshot
	@"$(ROOT_PY)" scripts/e2_ghg_crosswalk_gate.py --strict
	@"$(ROOT_PY)" -m pytest api/tests/test_e2_ghg_crosswalk_pack.py -q

e3-export-ngsi: ## Export NGSI-LD entities from the repo-local source register
	@"$(ROOT_PY)" scripts/e3_export_ngsi_ld.py --context-mode embed
	@"$(ROOT_PY)" scripts/e3_check_model.py --write-summary --strict
	@"$(ROOT_PY)" scripts/e3_r5_r7_gate.py
	@"$(ROOT_PY)" -m pytest api/tests/test_e3_model_pack.py -q

e3-r5-r7-gate: e3-export-ngsi ## Validate R5 hierarchy coverage and R7 functional pilot
	@echo "E3 R5/R7 evidence refreshed by e3-export-ngsi"

semantics-bundle: ## Build the deterministic SDS semantics bundle
	@"$(ROOT_PY)" scripts/semantics_bundle_build.py

gate-semantics: ## Run semantics contract checks from the repo root
	@"$(ROOT_PY)" $(SEMANTICS_GATE)

e4-gate: ## Run the E4 prototype gate and write performance evidence
	@"$(ROOT_PY)" scripts/e4_gate.py $(E4_SOURCE_ARG)

gate-pipeline: e4-gate ## Alias for the E4 prototype performance gate

e5-gate: ## Validate E5 technical code deliverable structure
	@"$(ROOT_PY)" scripts/e5_check_coverage_evidence.py --strict
	@"$(ROOT_PY)" -m pytest api/tests/test_e5_technical_code_pack.py -q

e6-refresh-edc: ## Regenerate the EDC bundle from the current E1 register
	@"$(ROOT_PY)" scripts/edc_bundle_from_register.py --strict

e6-check: ## Validate E6 governance artifacts
	@"$(ROOT_PY)" scripts/e6_check_governance.py

e6-gate: ## Run E6 policy enforcement tests and strict governance checks
	@echo "E6 gate: direct argument-vector invocation required:" >&2
	@echo '  "$(ROOT_PY)" scripts/run_e6_gate.py --register-package-dir "<external-package-directory>"' >&2
	@exit 2

gate-governance: e6-gate ## Alias for the E6 governance gate

e6-evidence: ## Generate the repo-local E6 evidence bundle
	@"$(ROOT_PY)" scripts/e6_generate_evidence.py

e7-gate: ## Validate E7 external evidence status boundary
	@"$(ROOT_PY)" scripts/e7_gate.py

e8-gate: ## Validate E8 use cases and prioritization matrix
	@"$(ROOT_PY)" -m pytest api/tests/test_e8_use_cases_and_prioritization.py -q

e9-gate: ## Validate E9 adjustments report structure
	@"$(ROOT_PY)" -m pytest api/tests/test_e9_adjustments_report.py -q

e10-gate: ## Validate E10 communications plan structure
	@"$(ROOT_PY)" -m pytest api/tests/test_e10_comms_plan.py -q

e11-gate: ## Validate E11 official website evidence
	@"$(ROOT_PY)" -m pytest api/tests/test_e11_website_pack.py -q

e11-deploy-gate: ## Alias for E11 official website evidence validation
	@"$(ROOT_PY)" -m pytest api/tests/test_e11_website_pack.py -q

e12-gate: ## Validate E12 and complete R22 activity coverage
	@"$(ROOT_PY)" scripts/e12_r22_activity_gate.py
	@"$(ROOT_PY)" -m pytest api/tests/test_e12_communications_impact.py -q

e13-gate: ## Validate E13 roadmap/scalability draft structure
	@"$(ROOT_PY)" -m pytest api/tests/test_e13_roadmap_scalability.py -q

service-wave4: ## Run the Wave 4 value-import isolation gate
	@powershell -NoProfile -ExecutionPolicy Bypass -Command "Set-Location '$(SERVICE_DIR)'; make gate-w4"

service-value-import-performance: ## Run the 1k strict value-import performance gate
	@powershell -NoProfile -ExecutionPolicy Bypass -Command "Set-Location '$(SERVICE_DIR)'; make gate-value-import-performance"

service-raw-value-transform-matrix: ## Run the E4/R8 1k raw transformation matrix gate
	@powershell -NoProfile -ExecutionPolicy Bypass -Command "Set-Location '$(SERVICE_DIR)'; make gate-raw-value-transform-matrix"

service-semantic-catalog-projection: ## Run the active-indicator semantic projection gate
	@powershell -NoProfile -ExecutionPolicy Bypass -Command "Set-Location '$(SERVICE_DIR)'; make gate-semantic-catalog-projection"

service-wave5: ## Run the Wave 5 DB-first no-fallback gate
	@powershell -NoProfile -ExecutionPolicy Bypass -Command "Set-Location '$(SERVICE_DIR)'; make gate-w5"

service-test: ## Run service tests from the Windows workflow
	@powershell -NoProfile -ExecutionPolicy Bypass -File "$(SERVICE_DIR)/scripts/dev.ps1" Test

service-coverage: ## Run service coverage from the Windows workflow
	@powershell -NoProfile -ExecutionPolicy Bypass -File "$(SERVICE_DIR)/scripts/dev.ps1" TestCoverage
