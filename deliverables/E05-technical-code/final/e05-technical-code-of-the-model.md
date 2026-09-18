# E5 - Technical Code of the Model

**Date:** 2026-06-08
**Version:** V2026-06-08
**Deliverable:** E5 - Technical Code of the Model

## Purpose

E5 publishes the technical implementation of the SDS model: controlled standards data intake, canonical register validation, semantic model generation, API publication, calculations, unit and reference-data conversion, value handling, mapping controls, and operational governance hooks. The implementation follows the SDS model contracts used by E1, E2, E3, E4, and E6, and exposes them through executable code rather than documentation-only evidence. [@ETSI_NGSI_LD_CIM_009] [@W3C_JSON_LD_1_1] [@JSON_SCHEMA_2020_12]

The technical code is organized around four product functions:

- Load standards, mappings, units, reference data, and values into controlled SDS stores.
- Transform standards and operational inputs into the SDS canonical register, semantic, calculation, conversion, and value shapes.
- Export and serve model outputs through API, semantic, and evidence surfaces.
- Verify the implementation through deliverable gates, integration tests, coverage workflows, and DB-first runtime checks.

## Required E5 Closing Gate

The project closing gate for E5 requires functional load, transform, and export code; unit-test coverage evidence; integration tests; and documentation/examples.

| Required item | E5 implementation |
|---|---|
| Functional load | Import and bootstrap code loads indicators, standard-release metadata, mappings, units, reference data, values, and controlled sample records through scripts and API services. |
| Functional transform | Register, semantic, mapping, calculation, conversion, and value-versioning code normalizes inputs into SDS contracts before publication. |
| Functional export | API routers, NGSI-LD/semantic export utilities, signed dataset serialization, and deterministic evidence builders expose model outputs. |
| Unit-test coverage | The API coverage workflow executes with `api/scripts/dev.ps1 TestCoverage`, `make service-coverage`, and the source-native API CI mirror `make api-ci-local`. The workflow enforces `--cov-fail-under=97.01`. The latest measured API CI coverage run passed 2,621 passing tests, recorded 56 skipped tests, and reported 98% TOTAL coverage, with exact measured coverage of 97.51163540850652%, exceeding the >=90% E5 acceptance threshold and the current >97% quality objective. |
| Integration tests | Indicator, mapping, semantic, calculation, conversion, value, DB-first, and deliverable gates exercise the implementation across module boundaries. |
| Documentation/examples | Root/API docs, deliverable indexes, API docs, and executable Makefile targets document how the technical model is run and validated. |

## Technical Code Anchors

| Model area | Primary implementation anchors | What the code proves |
|---|---|---|
| Register and package boundary | `scripts/prepare_register_for_import.py`, `semantics/schema/register_schema.json`, `semantics/shacl/shapes_register_shacl.ttl` | Standards package rows are normalized into the SDS register contract with schema and SHACL validation. |
| Standard-release metadata | `api/src/services/standard_versioning_import.py`, `api/scripts/import_standard_versioning.py`, `api/src/database/models.py` | Standard releases and datapoint lineage can be stored as versioned metadata before runtime publication. |
| Indicator catalog | `api/src/services/indicator_import.py`, `api/src/services/indicator_store.py`, `api/src/api/routers/indicators.py` | Indicators can be imported, queried, searched, exported, and tracked through dataset history/change surfaces. |
| Semantic model | `api/src/services/concept_service.py`, `api/src/services/ontology_service.py`, `api/src/ontology/projection_generator.py`, `semantics/context/`, `semantics/shacl/` | Concepts, relationships, JSON-LD context, SHACL constraints, and generated ontology projection are executable. [@W3C_SHACL_2017] [@W3C_OWL2_2012] |
| Mapping model | `api/src/services/canonical_mapping_package.py`, `api/src/services/canonical_pairwise_materialization.py`, `api/src/api/routers/mappings.py`, `api/src/api/routers/mapping_assertions.py` | Reviewed relationships can be validated, materialized, queried, and kept separate from unreviewed candidates. |
| Calculation contracts | `api/src/calculation/contracts.py`, `api/src/calculation/engine.py`, `api/src/services/calculation_contract_import.py`, `api/src/api/routers/calculations.py` | Formula inputs, calculation dependencies, conversion policy, trace output, and executable calculation contracts are represented in code. |
| Units and conversions | `api/src/calculation/conversion/`, `api/src/calculation/unit_converter.py`, `api/src/services/fx_service.py`, `api/src/database/bootstrap_units.py`, `api/src/database/bootstrap_conversion_catalog.py`, `api/src/api/routers/units.py`, `api/src/api/routers/fx.py` | Physical-unit conversion, compound expressions, time-series FX policy, unit catalog bootstrap, and conversion traces are deterministic and testable. |
| Values and revisions | `api/src/services/value_ingest.py`, `api/src/services/value_csv_import.py`, `api/src/services/value_revision_store.py`, `api/src/services/value_versioning.py`, `api/src/api/routers/values.py` | Operational value imports, strict value contracts, revision metadata, lineage, and read surfaces are implemented without conflating values with structural standards metadata. |
| Runtime interoperability | `api/src/services/value_resolution.py`, `api/src/services/runtime_readiness.py`, `api/src/api/routers/interoperability.py`, `api/src/api/routers/values.py`, `api/src/api/openapi_docs.py` | Runtime value resolution can use direct values, executable calculation contracts, exact/equivalent mappings, supported unit conversion, and fail-closed refusal semantics. |
| Runtime API and security | `api/src/api/main.py`, `api/src/api/routers/auth.py`, `api/src/api/routers/ontology.py`, `api/src/api/routers/hierarchies.py`, `api/src/config/settings.py` | The model is exposed through FastAPI with authentication, RBAC, DB-first settings, health/readiness, and modular routers. |
| Dataset export and provenance | `api/src/services/dataset_serialization.py`, `api/src/services/dataset_manifest.py`, `api/src/services/dataset_history.py`, `api/src/services/export_signing.py`, `packages/sds_core/` | Dataset outputs can carry manifest, history, signing, serialization, and NGSI-LD/DCAT-aligned metadata. [@SEMIC_DCAT_AP] |

## Functional Proof

### Load

The SDS codebase loads the technical model through controlled stores and scripts:

- Indicator and standard-release import services validate source shape before persistence.
- Mapping services validate installed-standard compatibility before materialization.
- Unit and conversion bootstraps seed deterministic reference data into the runtime store.
- Value import services enforce strict value contracts and keep operational values separate from standards/catalog evidence.
- DB-first runtime settings prevent silent fallback to bundled catalogs when production mode requires PostgreSQL.

### Transform

The model transformation layer converts input evidence into executable SDS structures:

- Register rows are checked against schema and semantic constraints.
- Identifiers are canonicalized before catalog publication.
- Relationship evidence is typed and materialized only after review.
- Calculation contracts bind formula inputs, units, conversion policy, and trace metadata.
- Physical-unit and FX conversion flows produce deterministic trace steps.
- Value revisions preserve context hashes, events, and current/reported pointers.

### Export

The model is exposed through product and evidence surfaces:

- API routers publish indicators, concepts, mappings, calculations, units, FX/reference data, values, hierarchies, auth, and health/readiness.
- Runtime interoperability endpoints publish readiness checks and value
  resolution for manual and programmatic use. The resolver is evidence-based:
  it returns resolved values only when the active runtime has a stored value,
  executable calculation contract, exact/equivalent mapping, or compatible unit
  conversion. Unsupported or under-evidenced routes return structured refusal
  statuses rather than fabricated values.
- NGSI-LD, JSON-LD, SHACL, JSON Schema, and DCAT-aligned artifacts support semantic and dataset exchange. [@ETSI_NGSI_LD_CIM_009] [@W3C_JSON_LD_1_1] [@W3C_SHACL_2017] [@SEMIC_DCAT_AP]
- Dataset serialization, manifests, history, and signing services support reproducible export evidence.

## Verification Surface

E5 is validated through a layered test and gate surface:

| Verification layer | Command or evidence |
|---|---|
| E5 public package gate | `make e5-gate` |
| Deliverables register, checksum, and privacy gate | `make deliverables-check` |
| Repository closure layer | `python scripts/repo_closure_check.py` |
| Conversion and reference-data gate | `make conversion-gate` |
| Semantics contract gate | `make gate-semantics` |
| DB-first production gate | `make service-wave5` |
| API test suite | `api/scripts/dev.ps1 Test` or `make service-test` |
| Coverage workflow | `api/scripts/dev.ps1 TestCoverage`, `make service-coverage`, or `make api-ci-local` |

Latest coverage evidence:

- `make api-ci-local` completed successfully on 2026-06-23 with 2,621 passing tests, 56 skipped tests, and an enforced `--cov-fail-under=97.01` quality line.
- The same run reported TOTAL coverage of 98%, with exact measured coverage of 97.51163540850652% (`21,161` covered statements out of `21,701`, with `540` missed statements).
- The documented E5 acceptance gate requires >=90% unit-test coverage; that threshold is exceeded without changing the formal gate, and the current quality objective of >97% exact coverage is met by the executable coverage command.
- The evidence does not claim 100% coverage; the current missed statements are not being chased with artificial tests or no-cover pragmas.

This document therefore records the executable code package and the current verification surface as evidence that the documented E5 coverage threshold is met.

## Additional SDS Capabilities Beyond The Minimum E5 Scope

The original E5 gate requires load/transform/export code, coverage evidence, integration tests, and docs/examples. SDS includes additional technical capabilities beyond that minimum:

| Additional capability | Why it exceeds the minimum E5 gate |
|---|---|
| Versioned standard-release metadata | The model can preserve release identity and datapoint lineage instead of treating all standards as a flat current catalog. |
| Reviewed mapping materialization | Relationship publication is controlled through reviewed evidence and installed-standard compatibility checks. |
| Deterministic physical-unit and time-series FX conversion | Unit and currency conversion are modeled as traceable runtime services, not simple label normalization. |
| Calculation contracts with conversion policy | Formula execution can bind inputs, units, conversion behavior, and trace output. |
| Runtime value resolution across standards | The API can resolve operational values through direct storage, calculation contracts, exact/equivalent mappings, compatible unit conversion, and explicit refusal semantics. |
| Interoperability readiness endpoint | Operators can verify the loaded runtime surfaces before executing manual Swagger interoperability checks. |
| Value revision and lineage surfaces | Operational values can carry revision events, context hashes, current pointers, and reported pointers. |
| DB-first no-fallback runtime mode | Production behavior can fail closed when canonical PostgreSQL-backed model data is unavailable. |
| Dataset signing, manifest, and history services | Export evidence can include provenance and reproducibility metadata beyond a basic code listing. |

These capabilities are part of the current SDS technical product. They should be described as added technical depth beyond the original E5 closing gate, not as additional obligations in the original project scope.

## Compliance Status

E5 is implemented and published as the technical code package for the SDS model. The codebase implements the model load, transform, export, semantic, mapping, calculation, conversion, value, and runtime API layers, and provides executable gates and tests for reviewing the technical implementation.

Formal E5 gate acceptance is closed against the documented >=90% unit-test coverage threshold. The current evidence does not claim 100% coverage; it records exact measured coverage of 97.51163540850652% and closure of the defined E5 acceptance gate.

## References

- European Telecommunications Standards Institute. ETSI GS CIM 009, Context Information Management (CIM); NGSI-LD API. https://cim.etsi.org/NGSI-LD/official/front-page.html. [@ETSI_NGSI_LD_CIM_009]
- Sporny, Manu, Dave Longley, Gregg Kellogg, Markus Lanthaler, Pierre-Antoine Champin, and Niklas Lindstrom. JSON-LD 1.1. W3C Recommendation, July 16, 2020. https://www.w3.org/TR/json-ld11/. [@W3C_JSON_LD_1_1]
- JSON Schema. JSON Schema Draft 2020-12. https://json-schema.org/draft/2020-12. [@JSON_SCHEMA_2020_12]
- Knublauch, Holger, and Dimitris Kontokostas. Shapes Constraint Language (SHACL). W3C Recommendation, July 20, 2017. https://www.w3.org/TR/shacl/. [@W3C_SHACL_2017]
- W3C OWL Working Group. OWL 2 Web Ontology Language Document Overview, second edition. W3C Recommendation, December 11, 2012. https://www.w3.org/TR/owl2-overview/. [@W3C_OWL2_2012]
- SEMIC. DCAT Application Profile for data portals in Europe. https://semiceu.github.io/DCAT-AP/. [@SEMIC_DCAT_AP]
