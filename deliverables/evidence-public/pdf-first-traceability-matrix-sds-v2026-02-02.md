# Traceability Matrix — SDS Repo

**Updated (UTC):** 2026-06-23T00:00:00Z
**Scope:** this repository only
**Purpose:** provide a public traceability layer for the deliverables published in this repository

## Important note

This traceability matrix reflects the current public repository state. It does not claim that all historical deliverables have already been republished here.

Where supporting material exists outside this workspace, it is labeled as **external evidence** until imported.

## Summary

| Deliverable | Current status | Repo-local evidence | External evidence | Gap to close in this repo |
|---|---|---|---|---|
| `E1` | `Green` | `deliverables/E01-mapeo-datos-normativas/final/e01-inventario-datos-clave-variables-brutas-v2026-06-09.md`, `deliverables/E01-mapeo-datos-normativas/evidence/dimension-expanded-value-coordinate-annex-v2026-06-05.md`, `deliverables/E01-mapeo-datos-normativas/evidence/dimension-expanded-value-coordinate-calculation-v2026-06-05.csv`, `deliverables/E01-mapeo-datos-normativas/evidence/e01-inventario-variables-clave-v2026-06-12.csv`, `scripts/e1_check_inventory.py`, `api/tests/test_e1_inventory_pack.py` | none required for repo closure | keep the current E1 artifact, dimension-expanded evidence, machine-readable annex, and active gate aligned |
| `E2` | `Green` | `deliverables/E02-interoperabilidad-crosswalks/final/e02-informe-interoperabilidad-correspondencias-v2026-06-09.md`, `data/extracted/analysis/e2_crosswalks_esrs_gri_full_atomized.csv`, `data/extracted/analysis/e2_completeness_by_topic.csv`, `data/extracted/analysis/e2_detection_by_topic.csv`, `scripts/e2_gate.py`, `scripts/e2_gate_detection.py`, `api/tests/test_e2_interoperability_pack.py`, `api/tests/test_e2_topic_metrics_gate.py` | none required for repo closure | keep the source crosswalk, source-derived summaries, public artifact, and active gates aligned |
| `E3` | `Green` | `deliverables/E03-modelo-ngsi-ld/final/e03-modelo-comun-ngsi-ld-paquete-semantico-v2026-06-09.md`, `deliverables/deliverables-register.csv` | historical model-source evidence held externally | keep the public model summary, register row, and semantics evidence aligned as the model evolves |
| `E4` | `Green` | `deliverables/E04-api-prototype/final/e04-prototipo-api-transformacion-datos-v2026-06-09.md`, `scripts/e4_gate.py`, `api/tests/test_e4_gate.py`, `data/extracted/analysis/e4_performance_report.txt`, `data/extracted/analysis/e4_performance_summary.csv` | historical prototype drafts exist externally | keep the gate evidence current when the source granulation baseline changes |
| `E5` | `Green` | `deliverables/E05-technical-code/final/e05-codigo-tecnico-modelo-v2026-06-09.md`, `api/` runtime, tests, docs; `api/src/data/units_database.json`; `api/src/database/bootstrap_units.py`; `api/tests/test_unit_catalog_expansion.py`; `api/tests/test_bootstrap_units.py`; `api/tests/test_main_lifespan_db_required.py`; `api/tests/test_e5_technical_code_pack.py`; refreshed technical docs in `README.md` and `api/docs/` | service-level evidence history exists externally | keep the DB-backed unit catalog, bootstrap path, live smoke/test evidence, and the local E5 evidence index current |
| `E6` | `Green` | `deliverables/E06-governance/final/e06-gobernanza-politica-datos-v2026-06-09.md`, `deliverables/deliverables-register.csv` | governance-source evidence held externally | keep the public governance model summary, register row, and governance evidence aligned with policy, trust, and register evidence |
| `E7` | `External/not imported` | none in this repo | stakeholder/workshop evidence held externally | keep explicitly labeled as external evidence until imported |
| `E8` | `Green` | `deliverables/E08-use-cases/final/e08-casos-de-uso-y-prioridades-sectoriales.md`, `api/tests/test_e8_use_cases_and_prioritization.py` | stakeholder/workshop validation held externally | keep the prioritization matrix aligned if sector priorities change |
| `E9` | `Green` | `deliverables/E09-adjustments/final/e09-adjustments-report-v2026-02-03.md`, `api/tests/test_e9_adjustments_report.py` | workshop evidence held externally | refresh the report if later closure waves materially change the remediation story |
| `E10` | `Green` | `deliverables/E10-communications-plan/final/e10-communications-plan-v2026-02-03.md`, `api/tests/test_e10_comms_plan.py` | steering ratification record may remain external | import formal approval evidence only if needed in this repo |
| `E11` | `Amber` | `deliverables/E11-website/evidence/official-url.md`, `deliverables/E11-website/final/e11-website-publication-report.md`, `api/tests/test_e11_website_pack.py` | `https://sustainabilitydataspace.com/` recorded as canonical URL; historical workspace checks returned HTTP 404 on 2026-05-30, 2026-05-31, 2026-06-01, and 2026-06-08; HEAD and GET checks on 2026-06-23 returned HTTP 200 reachability | keep reachability distinct from content acceptance, analytics, or impact closure; `web/sds_website/` is excluded as a local test artifact |
| `E12` | `Amber` | `deliverables/E12-communications-impact/README.md`, `deliverables/E12-communications-impact/draft/e12-communications-impact-draft-v2026-05-11.md` | impact metrics or analytics may remain external | import sanitized metrics or explicitly accept external evidence before final publication |
| `E13` | `Amber` | `deliverables/E13-roadmap-scalability/README.md`, `deliverables/E13-roadmap-scalability/final/e13-informe-final-recomendaciones-futuro-espacio-datos-v1-0.md` | version 1.0 is repo-local; full closure remains conditional | decide whether version 1.0 is accepted as subsidy evidence |

\* `E7` remains external/not imported in this repo; current planning may reference external workshop evidence only as an external source.

## Current repo-level anchors

- `deliverables/README.md` — canonical deliverables publication policy
- `deliverables/deliverables-register.csv` — public deliverables register with status, canonical path, checksum, privacy class, and sanitized source reference
- `scripts/repo_closure_check.py` — closure-layer validation
- `Makefile` and `scripts/gates.ps1` — root helper entrypoints

## Immediate follow-up

1. Keep `E7` explicitly marked as external evidence until imported.
2. Review and promote E12 plus the E13 version 1.0 package only after placeholders and external-evidence decisions are resolved.
3. Keep the external source-granulation boundary explicit and avoid reintroducing any retired intermediary repository into the active architecture.
