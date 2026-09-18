# SDS Dossier Traceability Status - 2026-06-01

This public status surface maps the submitted SDS dossier shape to repo-local
evidence without importing the dossier itself. `SOURCE_DOSSIER_1` is the
sanitized source reference for the official dossier named in the local agent
instructions; raw local paths and private evidence stay outside this repository.

This file is a traceability and status boundary, not a subsidy-closure
certificate. It distinguishes:

- repo publication closure: public repo evidence is explicit and internally
  consistent;
- full subsidy/dossier closure: still conditional while the listed residuals
  remain unresolved.

<!-- subsidy-closure-status:v1 -->
```json
{
  "schema_version": 1,
  "status_as_of": "2026-07-10",
  "source_ref": "SOURCE_DOSSIER_1",
  "repo_publication_closure": "pass",
  "full_subsidy_dossier_closure": "not_achieved_conditional",
  "activity_count_note": {
    "source_summary_count": 23,
    "explicit_activity_identifiers_recorded": 21,
    "note": "SOURCE_DOSSIER_1 summary states 23 activities; the explicit activity identifiers captured in this public status surface are A1.1-A6.3. The count discrepancy remains a source-dossier reconciliation item before full subsidy closure."
  },
  "work_packages": [
    {
      "id": "PT1",
      "name": "Mapeo de Datos, Normativas y Estandares",
      "activity_ids": [
        "A1.1",
        "A1.2",
        "A1.3",
        "A1.4"
      ],
      "deliverable_ids": [
        "E01",
        "E02"
      ],
      "requirement_ids": [
        "R1",
        "R2",
        "R3",
        "R4"
      ],
      "repo_status": "published-local",
      "residual_state": "conditional-r3"
    },
    {
      "id": "PT2",
      "name": "Modelado de Datos e Interoperabilidad Tecnica",
      "activity_ids": [
        "A2.1",
        "A2.2",
        "A2.3"
      ],
      "deliverable_ids": [
        "E03",
        "E04",
        "E05"
      ],
      "requirement_ids": [
        "R5",
        "R6",
        "R7",
        "R8",
        "R9",
        "R10",
        "R11",
        "R12",
        "R13"
      ],
      "repo_status": "published-local",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "PT3",
      "name": "Diseno del Marco de Gobernanza y Soberania de Datos",
      "activity_ids": [
        "A3.1",
        "A3.2",
        "A3.3",
        "A3.4"
      ],
      "deliverable_ids": [
        "E06"
      ],
      "requirement_ids": [
        "R14",
        "R15"
      ],
      "repo_status": "published-local",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "PT4",
      "name": "Workshops con Stakeholders",
      "activity_ids": [
        "A4.1",
        "A4.2",
        "A4.3",
        "A4.4"
      ],
      "deliverable_ids": [
        "E07",
        "E08",
        "E09"
      ],
      "requirement_ids": [
        "R16",
        "R17",
        "R18",
        "R19"
      ],
      "repo_status": "published-local",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "PT5",
      "name": "Comunicacion, Formacion y Difusion",
      "activity_ids": [
        "A5.1",
        "A5.2",
        "A5.3"
      ],
      "deliverable_ids": [
        "E10",
        "E11",
        "E12"
      ],
      "requirement_ids": [
        "R20",
        "R21",
        "R22"
      ],
      "repo_status": "published-local",
      "residual_state": "conditional-r20-r21"
    },
    {
      "id": "PT6",
      "name": "Hoja de Ruta y Escalabilidad",
      "activity_ids": [
        "A6.1",
        "A6.2",
        "A6.3"
      ],
      "deliverable_ids": [
        "E13"
      ],
      "requirement_ids": [
        "R23"
      ],
      "repo_status": "published-local",
      "residual_state": "closed-repo-local"
    }
  ],
  "requirements": [
    {
      "id": "R1",
      "name": "Cobertura normativa",
      "deliverable_id": "E01",
      "status": "published-local",
      "evidence_path": "deliverables/E01-mapeo-datos-normativas/final/e01-inventario-datos-clave-variables-brutas-v2026-06-09.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R2",
      "name": "Categorizacion clara",
      "deliverable_id": "E01",
      "status": "published-local",
      "evidence_path": "deliverables/E01-mapeo-datos-normativas/final/e01-inventario-datos-clave-variables-brutas-v2026-06-09.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R3",
      "name": "Mapeo completo entre estandares",
      "deliverable_id": "E02",
      "status": "published-local",
      "evidence_path": "deliverables/E02-interoperabilidad-crosswalks/final/e02-informe-interoperabilidad-correspondencias-v2026-06-09.md",
      "residual_state": "conditional-key-point-denominator"
    },
    {
      "id": "R4",
      "name": "Recomendaciones tecnicas",
      "deliverable_id": "E02",
      "status": "published-local",
      "evidence_path": "deliverables/E02-interoperabilidad-crosswalks/final/e02-informe-interoperabilidad-correspondencias-v2026-06-09.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R5",
      "name": "Cobertura de variables clave",
      "deliverable_id": "E03",
      "status": "published-local",
      "evidence_path": "deliverables/E03-modelo-ngsi-ld/final/e03-modelo-comun-ngsi-ld-paquete-semantico-v2026-06-09.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R6",
      "name": "Compatibilidad con estandares internacionales",
      "deliverable_id": "E03",
      "status": "published-local",
      "evidence_path": "deliverables/E03-modelo-ngsi-ld/final/e03-modelo-comun-ngsi-ld-paquete-semantico-v2026-06-09.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R7",
      "name": "Validacion funcional",
      "deliverable_id": "E03",
      "status": "published-local",
      "evidence_path": "deliverables/E03-modelo-ngsi-ld/final/e03-modelo-comun-ngsi-ld-paquete-semantico-v2026-06-09.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R8",
      "name": "Transformacion de datos",
      "deliverable_id": "E04",
      "status": "published-local",
      "evidence_path": "deliverables/E04-api-prototype/final/e04-prototipo-api-transformacion-datos-v2026-06-09.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R9",
      "name": "Documentacion tecnica",
      "deliverable_id": "E04",
      "status": "published-local",
      "evidence_path": "deliverables/E04-api-prototype/final/e04-prototipo-api-transformacion-datos-v2026-06-09.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R10",
      "name": "Rendimiento del prototipo",
      "deliverable_id": "E04",
      "status": "published-local",
      "evidence_path": "deliverables/E04-api-prototype/final/e04-prototipo-api-transformacion-datos-v2026-06-09.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R11",
      "name": "Funcionalidad completa",
      "deliverable_id": "E05",
      "status": "published-local",
      "evidence_path": "deliverables/E05-technical-code/final/e05-codigo-tecnico-modelo-v2026-06-09.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R12",
      "name": "Cobertura de pruebas",
      "deliverable_id": "E05",
      "status": "published-local",
      "evidence_path": "deliverables/E05-technical-code/final/e05-codigo-tecnico-modelo-v2026-06-09.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R13",
      "name": "Documentacion completa",
      "deliverable_id": "E05",
      "status": "published-local",
      "evidence_path": "deliverables/E05-technical-code/final/e05-codigo-tecnico-modelo-v2026-06-09.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R14",
      "name": "Marco organizativo definido",
      "deliverable_id": "E06",
      "status": "published-local",
      "evidence_path": "deliverables/E06-governance/final/e06-gobernanza-politica-datos-v2026-06-09.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R15",
      "name": "Politicas de acceso y control de datos",
      "deliverable_id": "E06",
      "status": "published-local",
      "evidence_path": "deliverables/E06-governance/final/e06-gobernanza-politica-datos-v2026-06-09.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R16",
      "name": "Registro detallado de los workshops",
      "deliverable_id": "E07",
      "status": "published-local",
      "evidence_path": "deliverables/E07-workshops/final/e07-actas-conclusiones-talleres-v1-0.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R17",
      "name": "Priorizacion de necesidades sectoriales",
      "deliverable_id": "E08",
      "status": "published-local",
      "evidence_path": "deliverables/E08-use-cases/final/e08-casos-de-uso-y-prioridades-sectoriales.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R18",
      "name": "Justificacion y claridad de los ajustes",
      "deliverable_id": "E09",
      "status": "published-local",
      "evidence_path": "deliverables/E09-adjustments/final/e09-adjustments-report-v2026-02-03.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R19",
      "name": "Consistencia con los objetivos estrategicos",
      "deliverable_id": "E09",
      "status": "published-local",
      "evidence_path": "deliverables/E09-adjustments/final/e09-adjustments-report-v2026-02-03.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R20",
      "name": "Contenido integral del plan",
      "deliverable_id": "E10",
      "status": "published-local",
      "evidence_path": "deliverables/E10-communications-plan/final/e10-communications-plan-v2026-02-03.md",
      "residual_state": "partial-preapproval-evidence"
    },
    {
      "id": "R21",
      "name": "Contenido y funcionalidades completas",
      "deliverable_id": "E11",
      "status": "official-url-recorded",
      "evidence_path": "deliverables/E11-website/final/e11-website-publication-report.md",
      "residual_state": "partial-content-functional-validation"
    },
    {
      "id": "R22",
      "name": "Cobertura completa de actividades realizadas",
      "deliverable_id": "E12",
      "status": "published-local",
      "evidence_path": "deliverables/E12-communications-impact/final/e12-reporte-final-difusion-impacto-v1-0.md",
      "residual_state": "closed-repo-local"
    },
    {
      "id": "R23",
      "name": "Cobertura completa de temas clave",
      "deliverable_id": "E13",
      "status": "published-local",
      "evidence_path": "deliverables/E13-roadmap-scalability/final/e13-informe-final-recomendaciones-futuro-espacio-datos-v1-0.md",
      "residual_state": "closed-repo-local"
    }
  ],
  "residuals": [
    {
      "deliverable_id": "E02",
      "requirement_ids": [
        "R3"
      ],
      "status": "published-local",
      "repo_gate_status": "pass",
      "full_closure_status": "conditional",
      "why_repo_gates_pass": "The thematic gate proves >=75% coverage for the project-selected Energy, GHG, and Water subset.",
      "decision_needed": "Justify and formally accept the 292-row selection as the complete R3 key-point denominator, or expand the analysis to an approved denominator."
    },
    {
      "deliverable_id": "E10",
      "requirement_ids": [
        "R20"
      ],
      "status": "published-local",
      "repo_gate_status": "pass",
      "full_closure_status": "conditional",
      "why_repo_gates_pass": "E10 covers the elements enumerated by R20 without inventing committee validation.",
      "decision_needed": "Import an act, agreement, or equivalent evidence that the steering committee validated the plan."
    },
    {
      "deliverable_id": "E11",
      "requirement_ids": [
        "R21"
      ],
      "status": "official-url-recorded",
      "repo_gate_status": "pass",
      "full_closure_status": "conditional",
      "why_repo_gates_pass": "E11 records dated reachability and separates it from content and functional acceptance.",
      "decision_needed": "Add a dated content inventory and functional verification for forms, downloads, and required sections."
    }
  ]
}
```
<!-- /subsidy-closure-status:v1 -->

## Human Readout

| Scope | Repo status | Full subsidy/dossier status |
| --- | --- | --- |
| `PT1` / `E01`-`E02` / `R1`-`R4` | Published locally | Condicional por el denominador de R3 |
| `PT2` / `E03`-`E05` / `R5`-`R13` | Published locally | Repo-local evidence closed |
| `PT3` / `E06` / `R14`-`R15` | Published locally | Repo-local evidence closed |
| `PT4` / `E07`-`E09` / `R16`-`R19` | Published locally | Repo-local evidence closed |
| `PT5` / `E10`-`E12` / `R20`-`R22` | E10/E12 published locally; E11 official URL recorded | Condicional por R20 y R21; R22 cerrado |
| `PT6` / `E13` / `R23` | Published locally | Repo-local evidence closed |

`scripts/repo_closure_check.py` checks the repo publication layer. It is not a
subsidy/dossier closure certificate. `scripts/subsidy_closure_check.py` checks
this status surface for explicit, internally consistent residual reporting and
therefore reports `NOT_FULL_SUBSIDY_CLOSED` while the conditional residuals
remain.

## Accepted Residual Ledger

| Deliverable | Requirements | Current status | Why repo-local gates pass | Full-closure decision still needed |
| --- | --- | --- | --- | --- |
| `E02` | `R3` | `published-local` | El gate temático supera el 75% en Energía, GEI y Agua dentro del subconjunto seleccionado. | Justificar y aceptar formalmente las 292 filas como el universo completo de puntos clave R3, o ampliar el análisis al denominador aprobado. |
| `E10` | `R20` | `published-local` | El plan cubre los elementos enumerados por R20 sin inventar validación. | Incorporar acta, acuerdo o evidencia equivalente de validación por el comité de dirección. |
| `E11` | `R21` | `official-url-recorded` | Registra disponibilidad fechada sin equipararla a aceptación funcional o de contenido. | Añadir inventario de contenido y validación funcional fechados. |


R8 ya no es un residual: `data/extracted/analysis/e4_raw_transform_matrix.csv`
registra 1.000 entradas brutas, resultados esperados y observados, 1.000 éxitos,
0 errores y una tasa del 100%. R10 se mantiene cerrado por su gate PostgreSQL
independiente de rendimiento.

R3 permanece abierto: Energía, GEI y Agua superan el 75% dentro de las 292 filas
seleccionadas por el proyecto, pero el dossier no prescribe ese subconjunto como
el universo completo de puntos clave. R5 se cierra con `1.805 / 1.805` variables
representadas y relaciones jerárquicas válidas. R7 se cierra con un piloto
equilibrado de 100 variables ESRS/GRI y precisión del 100%.

R22 se cierra mediante la matriz E10→ejecución→evidencia: las 16 acciones del
plan y una actividad ejecutada adicional están documentadas (`17 / 17`, `100%`).
Ocho permanecen `not_evidenced` y dos
`partially_evidenced`; esos estados describen los huecos sin convertirlos en
actividades ejecutadas. La ausencia de analítica web limita el análisis de
impacto, pero no añade un criterio que el dossier no exige para R22.

## Activity Count Note

`SOURCE_DOSSIER_1` states a project-level total of 23 activities. The explicit
activity identifiers captured in this public status surface are the 21 visible
identifiers `A1.1` through `A6.3`. The public repo does not treat that count
discrepancy as resolved; it remains a source-dossier reconciliation item before
full subsidy closure.
