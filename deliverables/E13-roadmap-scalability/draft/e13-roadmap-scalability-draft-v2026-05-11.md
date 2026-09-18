# E13 - Roadmap, Scalability, Sustainability, and Interoperability

**Version:** Draft v2026-05-11
**Status:** First repo-local draft. Not final evidence.
**Scope:** Consolidated roadmap for scaling and sustaining the SustainabilityDataSpace project after the current API and deliverables baseline.

## 1. Executive Summary

SDS currently has a working API product surface, official deliverables register, governance artifacts, semantics assets, and an evidence-backed quality gate layer. The next phase should focus on production hardening, explicit evidence closure for remaining deliverables, and continued expansion of canonical mapping evidence after the operational mapping surface moved off legacy fallback.

The roadmap deliberately separates three things:

- Production API readiness.
- Official deliverable closeout.
- Future canonical mapping expansion after cutover.

This avoids treating a green API gate as proof that every official project deliverable is closed.

## 2. Current Baseline

| Area | Current state | Roadmap implication |
|---|---|---|
| API production profile | Local production profile is green, with DB-backed runtime and retired guided routes absent | Ready for deployment planning with real secrets and allowed origins |
| Deliverables register | E1-E6 and E8-E11 are represented locally; E7 remains external; E12/E13 are draft/pending | Close evidence gaps before claiming full project closure |
| Governance | E6 policy, trust, audit, onboarding, and EDC artifacts are published locally | Keep governance artifacts synchronized with deployment and connector evolution |
| Semantics and interoperability | NGSI-LD, DCAT, SHACL, ODRL, and semantics bundle gates are available | Preserve deterministic validation before partner-facing integration |
| Canonical mapping | Product mapping behavior is backed by reviewed relationship evidence; active legacy fallback rows are retired in dev evidence | Continue expanding reviewed coverage; do not re-activate legacy rows as operational fallback |

## 3. Roadmap Phases

| Phase | Objective | Key outputs | Acceptance gate |
|---|---|---|---|
| Phase 1 - Close evidence boundary | Finalize E12/E13 and resolve E7 treatment | Final communication impact report, final roadmap report, explicit E7 external/import decision | Deliverables register and privacy check pass |
| Phase 2 - Production deployment | Deploy API using production profile | Production `.env`, explicit origins, DB backup/restore runbook, smoke evidence | Health, readiness, login, catalog, and retired guided-surface checks pass |
| Phase 3 - Partner integration | Support controlled partner/connector usage | Integration checklist, EDC policy bundle, NGSI-LD/DCAT examples | Governance and semantics gates pass |
| Phase 4 - Mapping cutover closure | Keep reviewed mapping as the only operational API behavior and close remaining evidence gaps | Reviewed relationship imports, publication evidence, retired legacy audit trail, rollback backup | No active legacy mapping rows remain; unresolved semantic gaps stay outside the operational surface |
| Phase 5 - Scale operations | Move from pilot operation to repeatable service operation | Monitoring, support workflow, release cadence, data stewardship process | Operating model review passes |

## 4. Scalability Plan

| Dimension | Current capability | Next improvement |
|---|---|---|
| Runtime scaling | FastAPI service with Docker production profile and PostgreSQL dependency | Add deployment-specific sizing, backup schedule, and monitoring thresholds |
| Data scaling | Standardized-register ingestion and DB-backed runtime paths | Define import SLAs, batch limits, and retry/idempotency expectations for production data loads |
| Governance scaling | Deny-by-default policies, audit schema, trusted issuer registry | Add operational review cadence and owner assignment for policy updates |
| Partner scaling | NGSI-LD/DCAT/EDC artifacts available | Create a partner onboarding pack with test datasets and validation commands |
| Knowledge scaling | Reviewed relationship validation and parity workflow | Keep source granulation evidence separate from SDS runtime publication |

## 5. Sustainability Plan

| Sustainability concern | Proposed control |
|---|---|
| Maintainability | Keep API gates, deliverables gates, and mapping gates separate so changes can be validated independently |
| Operational continuity | Maintain backup/restore procedure, production smoke checks, and a short deployment checklist |
| Evidence continuity | Keep `deliverables/` as the public canonical surface and use sanitized source references for external material |
| Knowledge continuity | Keep source mapping evidence separate until deliberately promoted through SDS validation |
| Cost control | Prefer offline-safe validation and deterministic gates before introducing external services |

## 6. Interoperability Plan

SDS should keep interoperability grounded in implemented assets:

- ESRS and GRI as the active ESG reporting standards scope.
- NGSI-LD contexts and exports for data-space exchange.
- DCAT-AP catalog artifacts for dataset publication.
- ODRL and EDC policies for usage control.
- SHACL/JSON Schema validation for contract conformance.
- Public validation criteria for upstream indicator, value, and mapping intake.

Future interoperability work should not expand the reporting-standard claim without new evidence and acceptance gates.

## 7. Risks And Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Overclaiming deliverable closure | Project reporting could imply evidence that is not in the repo | Keep E7/E12/E13 status explicit until final or accepted external evidence exists |
| Mapping coverage overclaim | Product mappings could be treated as full ESRS/GRI equivalence coverage even though some legacy rows were intentionally not copied | Publish reviewed behavior as the operational surface, keep legacy rows inactive for audit, and continue evidence-backed coverage expansion |
| Production secrets or origins misconfigured | Deployment could be insecure or fail startup | Require strong secrets, explicit `ALLOWED_ORIGINS`, and production smoke verification |
| Governance drift | Policies, contracts, and runtime behavior could diverge | Run E6 gates and regenerate/verify EDC artifacts when governance changes |
| Partner integration ambiguity | External adopters may not know which artifacts are authoritative | Publish a compact integration checklist tied to current gates |

## 8. Recommended Next Actions

1. Finalize E12 by importing sanitized communication impact evidence or explicitly recording evidence absence/externally held evidence.
2. Finalize E13 by approving this roadmap, resolving placeholders, and moving the approved file to `final/`.
3. Decide E7 treatment: import sanitized workshop evidence or record explicit external-evidence acceptance.
4. Prepare a deployment checklist for the API production profile with secret handling, allowed origins, backup, restore, and smoke checks.
5. Continue canonical mapping expansion from the current operational subset; keep inactive legacy rows as audit history, not API fallback.

## 9. Finalization Checklist

- [ ] Confirm whether this roadmap is accepted as the final E13 structure.
- [ ] Add deployment-specific production environment details without secrets.
- [ ] Add owner placeholders or approved role names for each roadmap phase.
- [ ] Confirm E7/E12 evidence status.
- [ ] Confirm no private names, emails, phone numbers, secrets, local paths, or raw analytics exports are included.
- [ ] Move the approved report to `final/`.
- [ ] Update `deliverables/deliverables-register.csv` with final status, path, checksum, and source reference.
