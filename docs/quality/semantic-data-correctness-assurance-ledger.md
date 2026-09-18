# Semantic Data Correctness Assurance Ledger

> Version: 2026-05-31
> Scope: package boundaries, calculation/value/unit/FX fail-closed behavior,
> canonical mapping semantics, DB-first fallback boundaries, verification gates,
> accepted residuals, and operator responsibilities.
> Out of scope: live DB row counts, Atomizer package production scheduling,
> deliverable status changes, and subsidy-completion claims.

## Assurance Summary

SDS has a defensible semantic/data correctness story because public runtime
behavior is DB-first, package imports are explicit and staged, calculations and
conversions fail closed, and canonical mappings materialize only reviewed
relationship evidence. This ledger is a reviewer-facing index of those controls.
It does not record environment-specific facts such as installed standards or row
counts.

## Package Boundary

- Atomizer produces SDS indicator/value packages; SDS consumes them through
  controlled import scripts.
- The SDS public repository keeps generic import scripts, docs, validation
  gates, tests, and runtime code. It does not commit Atomizer export packages.
- Canonical package inputs are `sds_dataset_register.csv`, optional
  `sds_values.csv`, optional `sds_dataset_register.json`, `manifest.json`, and
  `MANIFEST.sha256`.
- Legacy compatibility inputs such as `framework_datapoints.csv` and
  `atomized_variables.csv` are migration paths, not the preferred contract.
- Cross-repo coordination is internal and outside this public documentation
  surface.

## Fail-Closed Runtime Behavior

- Production runtime is DB-first when `REQUIRE_DATABASE=true`. Missing canonical
  PostgreSQL-backed catalog, unit, semantic, or mapping data must return `503`
  or raise `CanonicalDataUnavailableError`, not silently read JSON fallbacks.
- Strict value ingestion rejects unregistered entities and invalid value
  metadata instead of creating placeholders.
- Calculation contracts accept only `fail_closed` conversion policy. Executable
  `runtime_expression` values are validated with the runtime formula parser.
- Currency conversion requires reproducible context. `expected_currency` and
  `fx_policy_id` must appear together, and values needing FX must provide a
  date or period window. SDS must not use an unbounded latest-rate fallback.
- Physical unit conversion at runtime is backed by the database unit catalog and
  conversion rules, not by a hardcoded helper.

## Unit Helper Boundary

- `packages/sds_core/src/sds_core/units/conversion.py` is a narrow offline helper
  for deterministic examples and package-level utilities.
- It is not the production DB-backed unit catalog or authoritative runtime
  conversion engine.
- Production API runtime code must use `api/src/services/unit_service.py` and
  `UnitRepository`.
- `make atomizer-boundary-check` includes a static guard that fails if `api/src`
  imports `sds_core.units`. Tests may import the helper for fixture-only checks.

## Canonical Mapping Boundary

- `/api/v1/mappings` is backed by the canonical
  `materialized_pairwise_mappings` read model.
- Canonical mapping package import writes canonical shadow tables only. It does
  not write legacy `standard_mappings` rows and does not auto-materialize
  pairwise rows.
- Legacy `standard_mappings` references in docs are for comparison,
  classification, or historical cutover checks. They are not an active runtime
  fallback.
- Mapping packages never auto-install standards. Rows for missing standards stay
  `pending_missing_standard` and cannot feed live mappings, calculations,
  search, exports, or pairwise materialization.
- Pairwise materialization emits `equivalent` only for complete equivalent
  footprints. Incomplete, component, aggregate, directional, or unsupported
  relationships stay weaker or blocked according to reviewed evidence.

## Import Approval And Operational Subsets

- Real package imports require fresh dry-run evidence against the target
  environment and explicit human/process approval before production promotion.
- Strict compatibility mode blocks rows for missing standards.
- Partial compatibility mode may import only installed-compatible assertion
  groups; missing-standard rows remain pending.
- Operational-subset materialization is allowed only as an explicit reviewed
  promotion decision. It must preserve fail-closed semantics and must not broaden
  equivalence claims.

## Verification Gates

| Gate | Command | Assurance |
|---|---|---|
| Semantics contract | `make gate-semantics` | JSON-LD context and SHACL contracts pass where dependencies exist. |
| Conversion | `make conversion-gate` | Deterministic physical-unit and historical-FX behavior with provenance and fail-closed selection. |
| Atomizer boundary | `make atomizer-boundary-check` | Forbidden dependencies, package dry-run contracts, and API runtime unit-helper boundary are enforced. |
| Semantic catalogue projection | `make service-semantic-catalog-projection` | Every active production indicator has a deterministic DB-backed concept in `concepts`; missing projections block release/smoke acceptance. |
| Value isolation | `make service-wave4` | Strict values CSV import cannot mutate catalog/reference tables. |
| DB-first runtime | `make service-wave5` | Production strict mode rejects JSON/catalog/ontology fallbacks. |
| API CI | `make api-ci-local` | Lint, dependency/security scan, and full API tests with coverage gate. |

Focused tests:

- `api/tests/test_dependency_boundaries.py`
- `api/tests/test_standard_mapping_store.py`
- `api/tests/test_canonical_mapping_db_import.py`
- `api/tests/test_canonical_pairwise_materialization.py`
- `api/tests/test_calculation_contract_import.py`
- `api/tests/test_value_csv_import.py`

## Residuals

- Exact installed standards, row counts, and current pairwise mapping counts are
  live DB-state facts. Verify them fresh before reporting.
- Future package production promotion still needs target-environment dry-run
  evidence, explicit approval, rollout notes, and rollback planning.
- Candidate mapping rows remain non-operational until reviewed, imported under
  installed-standard rules, and explicitly materialized.

## Operator Responsibilities

| Action | Evidence Required |
|---|---|
| Dry-run an Atomizer package | Dry-run output with zero material blockers. |
| Approve a real package import | Dry-run evidence, package manifest/checksums, reviewer approval, and rollback plan. |
| Verify live semantic state | Fresh DB query against the target environment. |
| Verify catalogue projection | `make service-semantic-catalog-projection` against the target DB after imports, restores, and deploys. |
| Promote mappings | Validation, import job result, materialization preview, and production decision record. |
| Release SDS runtime | Relevant gate logs plus `make api-ci-local` for API-impacting changes. |
