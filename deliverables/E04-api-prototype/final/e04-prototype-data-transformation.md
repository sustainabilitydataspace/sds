# E4 - API Prototype and Data Transformation

**Date:** 2026-06-08
**Version:** V2026-06-08
**Deliverable:** E4 - API Prototype / Prototype Data Transformation

## Purpose

E4 demonstrates that a controlled standards indicator-granulation package can be validated, transformed into the SDS standardized register, and exposed through the SDS API prototype. The deliverable connects the E1 inventory baseline and the E3 common model with an executable service surface for catalog access, semantic lookup, mappings, values, calculations, unit conversion, and controlled import operations. [@ETSI_NGSI_LD_CIM_009] [@W3C_JSON_LD_1_1] [@JSON_SCHEMA_2020_12]

The prototype is not a mock. The default repo gate runs the current dimension-expanded calculation ledger, still accepts an explicit register package when supplied, writes reproducible gate evidence, and validates that the selected source can be processed within the E4 performance threshold.

## Delivery Scope

E4 covers the following prototype capabilities:

- Register-first intake from a standardized sustainability indicator package.
- Validation of required register fields, row counts, checksums, dimensions, and value types.
- Transformation into the SDS canonical register shape used by E1 and E3.
- API publication of indicators, concepts, mappings, values, calculations, unit conversion, and reference-data functions.
- Controlled import and preview operations for indicator and value data.
- Evidence generation for timing, success rate, family coverage, and reproducibility.
- Governance-aware access to protected API functions through the policy and authentication layer defined in E6.

The prototype supports standards-aligned data products for ESRS, GRI, and GHG Protocol through the same SDS register/API contract. The reporting obligations remain defined by the source standards; E4 proves the SDS technical transformation and API publication layer. [@EU_ESRS_2023_2772] [@GRI_STANDARDS_2025] [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD]

## Prototype Surface

| Prototype area | What E4 proves | Public traceability |
|---|---|---|
| Package intake | A standards package can be supplied as a structured register and validated before use. | `scripts/e4_gate.py`; E04 row in `deliverables/deliverables-register.csv` |
| Register transformation | The selected package is normalized into the SDS register shape used by E1/E3. | `data/extracted/analysis/e4_performance_report.txt`; `data/extracted/analysis/e4_performance_summary.csv` |
| Indicator API | Indicators can be listed, searched, exported, validated, imported asynchronously, and tracked through manifest/history/change/diff surfaces. | API indicator routes and E5 technical-code evidence |
| Semantic API | Concepts, equivalences, mappings, and taxonomy views can be queried using the SDS semantic model. | E3 model pack and API ontology/mapping routes |
| Value and calculation API | Values can be imported, queried, exported, and used by calculation endpoints with traceability. | API values/calculation routes and E5 technical-code evidence |
| Unit and reference-data API | Unit conversion and FX/reference-data preview/import controls are available through the service layer. | E5 technical-code evidence and E6 governance controls |
| Governance controls | Protected operations use authentication, permission checks, policy metadata, and audit-oriented boundaries. | E6 governance deliverable |

## Required E4 Closing Gate

The project closing gate for E4 requires:

| Required item | E4 implementation |
|---|---|
| Process at least 1,000 rows | The refreshed gate processed `87,547` reportable dimension-expanded value coordinates. |
| Complete within 30 seconds | The refreshed gate records an elapsed time below the 30-second threshold in the machine-readable summary. |
| Log errors and gate status | The gate writes a human-readable report and a machine-readable summary. |
| Cover at least three rule families | The gate evidence covers Environmental/carbon, Social, and Governance coordinate families. |
| Preserve reproducibility | The evidence includes timestamp, source label, checksum, thresholds, family counts, and pass/fail checks. |

The gate output deliberately uses sanitized source labels instead of absolute local paths, so the public evidence can be reviewed without exposing local environment structure.

## Gate Evidence

The current E4 gate evidence is:

| Evidence field | Result |
|---|---:|
| Gate result | PASS |
| Source mode | Dimension ledger |
| Ledger groups | 512 |
| Reportable coordinates | 87,547 |
| Transformed rows | 87,547 |
| Elapsed seconds | Recorded in the current gate summary; below the 30.0-second threshold |
| Minimum rows required | 1,000 |
| Maximum seconds allowed | 30.0 |
| Success rate | 100.00% |
| Environmental/carbon coordinates | 79,983 |
| Social coordinates | 1,959 |
| Governance coordinates | 491 |
| Transversal coordinates | 5,114 |

Evidence files:

- `data/extracted/analysis/e4_performance_report.txt`
- `data/extracted/analysis/e4_performance_summary.csv`

## Additional SDS Capabilities Beyond The Minimum E4 Scope

The original E4 closing gate requires a prototype transformation with timing evidence, error logging, and at least three rule families. SDS includes the following additional capabilities beyond that minimum scope:

| Additional capability | Why it exceeds the minimum E4 gate |
|---|---|
| Register-first package contract | E4 can process a standardized package directly instead of relying only on compatibility inputs. |
| API catalog operations | The prototype exposes list/search/export/import/status operations rather than only writing a transformed CSV. |
| Manifest, history, changes, and diff surfaces | The API supports operational review of dataset state, not just one-off transformation evidence. |
| Semantic lookup and mappings | The prototype connects transformed rows to concepts, equivalences, and reviewed relationship metadata. |
| Values and calculations | The API includes operational value import/query/export and calculation endpoints beyond the transformation-only requirement. |
| Unit conversion and reference data | The prototype includes deterministic unit and FX/reference-data controls that are not required by the basic E4 row/time gate. |
| Governance enforcement | Protected API functions are tied to authentication, permissions, and policy controls from E6. |

These additions are part of the SDS technical product surface. They should be treated as project value beyond the minimum E4 closing evidence, not as extra obligations imposed by the original E4 definition.

## Validation Commands

The E4 evidence is validated with:

- `make e4-gate`
- `api/.venv/Scripts/python.exe -m pytest api/tests/test_e4_gate.py -q`
- `make deliverables-check`

When a reviewer wants to validate a specific controlled standards package, the same gate accepts `SDS_E4_REGISTER_PATH` as the package path and keeps the evidence output sanitized.

## Compliance Conclusion

E4 is delivered as a working API and transformation prototype. It validates that a standards indicator-granulation package can be converted into the SDS register shape, exposed through the API prototype, measured against the formal row/time threshold, and reviewed through reproducible public evidence.

## References

- European Telecommunications Standards Institute. ETSI GS CIM 009, Context Information Management (CIM); NGSI-LD API. https://cim.etsi.org/NGSI-LD/official/front-page.html. [@ETSI_NGSI_LD_CIM_009]
- Sporny, Manu, Dave Longley, Gregg Kellogg, Markus Lanthaler, Pierre-Antoine Champin, and Niklas Lindstrom. JSON-LD 1.1. W3C Recommendation, July 16, 2020. https://www.w3.org/TR/json-ld11/. [@W3C_JSON_LD_1_1]
- JSON Schema. JSON Schema Draft 2020-12. https://json-schema.org/draft/2020-12. [@JSON_SCHEMA_2020_12]
- European Commission. Commission Delegated Regulation (EU) 2023/2772, European Sustainability Reporting Standards. Official Journal of the European Union, December 22, 2023. [@EU_ESRS_2023_2772]
- Global Reporting Initiative. Consolidated Set of the GRI Standards. https://www.globalreporting.org/standards/. [@GRI_STANDARDS_2025]
- Greenhouse Gas Protocol. The Greenhouse Gas Protocol: A Corporate Accounting and Reporting Standard, revised edition. World Resources Institute and World Business Council for Sustainable Development. https://ghgprotocol.org/corporate-standard. [@GHG_PROTOCOL_CORPORATE_STANDARD]
- Greenhouse Gas Protocol. Corporate Value Chain (Scope 3) Accounting and Reporting Standard. World Resources Institute and World Business Council for Sustainable Development, 2011. https://ghgprotocol.org/corporate-value-chain-scope-3-standard. [@GHG_PROTOCOL_SCOPE3_STANDARD]
