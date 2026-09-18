# Estado de cierre del expediente SDS — 2026-09-18

Esta es la superficie de estado vigente para el expediente SDS. Sustituye las
matrices y reservas de cierre históricas: no se usan estados previos como
veredicto de la situación actual.

<!-- subsidy-closure-status:v2 -->
```json
{
  "schema_version": 2,
  "status_as_of": "2026-09-18",
  "closure_decision": {
    "authority": "project_owner",
    "decision": "accepted_complete",
    "scope": "E01-E13 and R1-R23"
  },
  "repo_publication_closure": "pass",
  "full_subsidy_dossier_closure": "achieved",
  "work_packages": [
    {"id": "PT1", "deliverable_ids": ["E01", "E02"], "requirement_ids": ["R1", "R2", "R3", "R4"], "closure_state": "closed"},
    {"id": "PT2", "deliverable_ids": ["E03", "E04", "E05"], "requirement_ids": ["R5", "R6", "R7", "R8", "R9", "R10", "R11", "R12", "R13"], "closure_state": "closed"},
    {"id": "PT3", "deliverable_ids": ["E06"], "requirement_ids": ["R14", "R15"], "closure_state": "closed"},
    {"id": "PT4", "deliverable_ids": ["E07", "E08", "E09"], "requirement_ids": ["R16", "R17", "R18", "R19"], "closure_state": "closed"},
    {"id": "PT5", "deliverable_ids": ["E10", "E11", "E12"], "requirement_ids": ["R20", "R21", "R22"], "closure_state": "closed"},
    {"id": "PT6", "deliverable_ids": ["E13"], "requirement_ids": ["R23"], "closure_state": "closed"}
  ],
  "requirements": [
    {"id": "R1", "deliverable_id": "E01", "closure_state": "closed"}, {"id": "R2", "deliverable_id": "E01", "closure_state": "closed"}, {"id": "R3", "deliverable_id": "E02", "closure_state": "closed"}, {"id": "R4", "deliverable_id": "E02", "closure_state": "closed"},
    {"id": "R5", "deliverable_id": "E03", "closure_state": "closed"}, {"id": "R6", "deliverable_id": "E03", "closure_state": "closed"}, {"id": "R7", "deliverable_id": "E03", "closure_state": "closed"}, {"id": "R8", "deliverable_id": "E04", "closure_state": "closed"}, {"id": "R9", "deliverable_id": "E04", "closure_state": "closed"}, {"id": "R10", "deliverable_id": "E04", "closure_state": "closed"},
    {"id": "R11", "deliverable_id": "E05", "closure_state": "closed"}, {"id": "R12", "deliverable_id": "E05", "closure_state": "closed"}, {"id": "R13", "deliverable_id": "E05", "closure_state": "closed"}, {"id": "R14", "deliverable_id": "E06", "closure_state": "closed"}, {"id": "R15", "deliverable_id": "E06", "closure_state": "closed"},
    {"id": "R16", "deliverable_id": "E07", "closure_state": "closed"}, {"id": "R17", "deliverable_id": "E08", "closure_state": "closed"}, {"id": "R18", "deliverable_id": "E09", "closure_state": "closed"}, {"id": "R19", "deliverable_id": "E09", "closure_state": "closed"}, {"id": "R20", "deliverable_id": "E10", "closure_state": "closed"},
    {"id": "R21", "deliverable_id": "E11", "closure_state": "closed"}, {"id": "R22", "deliverable_id": "E12", "closure_state": "closed"}, {"id": "R23", "deliverable_id": "E13", "closure_state": "closed"}
  ],
  "residuals": []
}
```
<!-- /subsidy-closure-status:v2 -->

## Lectura actual

Los paquetes de trabajo PT1–PT6, los entregables E01–E13 y los requisitos
R1–R23 están cerrados por decisión de proyecto a fecha de 2026-09-18.

Las observaciones técnicas posteriores de mantenimiento se gestionan fuera del
expediente de subvención. Véase
`docs/quality/2026-09-18-current-public-surface-review.md` para la comprobación
web actual; esa observación no reabre requisitos formalmente cerrados.
