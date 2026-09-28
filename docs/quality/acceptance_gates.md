# Acceptance Gates & Baselines

This document fixes acceptance thresholds and evaluation checklists for each deliverable. Track baselines and evidence links (file paths + sheet/page/line refs).

## Cross-plane gates (Blueprint)
- `make gate-semantics` — Semantics contract tests (JSON-LD context + SHACL if deps present).
- `make gate-governance` — Fail-closed pointer to the direct Python E6 gate;
  GNU Make does not carry the external package path.
- `make gate-pipeline` — Structural E4 ledger gate (alias of `make e4-gate`);
  it is not the PostgreSQL runtime-performance gate.
- `make service-semantic-catalog-projection` — DB semantic projection coverage gate. The active `indicators` catalogue is the production catalogue; every active indicator must have one deterministic DB-backed concept in `concepts` before release or hosted smoke is accepted.
- `make -C api gate-w2` — Generated ontology projection gate (runtime must load `core_tbox.owl + generated_projection.owl` and cover all DB semantic concepts).
- `make service-wave4` — Operational value-import isolation gate (strict values CSV contract must reject semantic/catalog headers, and importing values must leave semantic/catalog/reference tables unchanged).
- `make service-value-import-performance` — Operational value-import performance gate. It loads 1,000 strict CSV values through the normal importer, checks `esg_values`, `value_contexts`, `value_revisions`, `value_revision_events`, and `current_value_pointers`, and fails if import exceeds 30 seconds or counts diverge. Immutable revision history is **not** deleted row by row: discard the entire isolated test database after reading its result, including on failure.
  - **Operating envelope:** fixed-size local PostgreSQL 15 regression tripwire, not a production SLA, concurrency proof, or general large-volume guarantee. The synthetic workload uses `batch_size=250`, two canonical water concepts, one hierarchy entity, sequential daily periods, and the revision-backed read/write path.
  - **Prerequisite:** a separately provisioned, freshly created and disposable PostgreSQL 15 instance with dedicated credentials, reachable on loopback and a database named `sds_value_import_disposable_<random>`. The exact URL must be exported as both `DATABASE_URL` and `SDS_VALUE_IMPORT_DISPOSABLE_DATABASE_URL`, with `SDS_VALUE_IMPORT_DISPOSABLE_ALLOW_WRITE=true` and `VALUE_REVISION_PRIMARY_READ_PATH=revision`; required application signing keys must be supplied externally. The two environment variables are a misuse guard, **not proof** that an endpoint is disposable. Never point this gate at an operational database. Default reports are ignored local outputs; explicit promotion and privacy review are required for public evidence. An unreachable DB returns `not_evaluated`.
  - **Evidence boundary:** the accepted E04/R10 report from before migration 048 is a historical snapshot, including its then-permitted row cleanup. It does not qualify current immutable-history cleanup or the present gate; a new result needs a real disposable-DB run plus observed database disposal.
- `make service-raw-value-transform-matrix` — E04/R8 evaluates synthetic raw/expected/observed conversions in a 1,000-row matrix on the same isolated PostgreSQL 15 envelope. It requires an explicit tenant and the same external disposable-target opt-in as R10. It does not delete committed value/revision/event history; discard the entire database after reading the result, even if import fails partway through. Matrix and report default to ignored local outputs; do not overwrite the accepted historical R8 matrix/report without a separate evidence promotion and privacy review. A prior formal R8 snapshot is historical evidence, not a passing run of the current gate.
- `make service-wave5` — DB-first production gate (strict mode must reject JSON/catalog fallback reads, require PostgreSQL-backed unit storage, require the generated split ontology pair instead of a single-file `base.owl`, expose only the `database` dependency surface in health/readiness, remove the legacy public sync surface, and return `503` instead of silent empty catalog responses or ontology fallbacks when canonical DB-backed catalog/semantic data is unavailable).
- `make conversion-gate` — Deterministic physical-unit and historical-FX conversion gate. The gate covers source-traced unit and ISO4217/ECB reference-data builders, grouped/chunked/idempotent ECB historical FX loading, compound unit expressions, DB bootstrap of unit metadata, bootstrap conversion catalog, fail-closed FX selection, API preview/import guards, value-ingest provenance, calculation traces, and upstream/SDS conversion metadata validation.
- `make atomizer-boundary-check` — SDS/Atomizer package-boundary gate. Full
  SDS package dry-runs must validate same-package register identifiers and
  calculation-contract value concepts without writing values, while real imports
  remain DB-backed and local Atomizer package data stays outside the public repo.
- Semantic/data correctness assurance ledger:
  `docs/quality/semantic-data-correctness-assurance-ledger.md` covers package
  boundaries, fail-closed behavior, canonical mapping semantics, DB-first
  fallbacks, unit-helper boundaries, standard-versioning approval, and operator
  responsibilities.

## Repo engineering gates (Quality + Security)
- Local API quality: `make -C api lint` and `make -C api test`.
- Local API security: `make -C api security-scan` runs Bandit over `src/`,
  strict `pip-audit` over the selected Python environment after installing
  `requirements-dev.txt` (including runtime and transitive dependencies),
  generates an installed-environment CycloneDX SBOM, and exports its license
  inventory. Historical report filenames containing `requirements` or `runtime`
  are retained; their scope includes dev tooling. This avoids the temporary
  `ensurepip` bootstrap and audits resolved versions of non-exact dev requirements;
  see `api/docs/tests.md` for bootstrap and scope. Bandit Low findings remain
  report-only; Medium/High findings, dependency collection failures,
  runtime/dev dependency vulnerabilities, and hard-blocked license markers such
  as AGPL/SSPL fail the gate. GPL/LGPL-like runtime notices are captured as
  license-review evidence because some packages publish exceptions or bundled
  notices that require human/legal interpretation rather than automatic failure.
- Local API CI mirror: `make api-ci-local` runs the source-native API branch
  gate in one command: dev dependency bootstrap, lint, security scan, and the
  full API test suite with the API coverage fail-under gate.
- Unit helper boundary guard: production API runtime must not import the
  offline `sds_core.units.conversion` helper as its unit authority. The boundary
  is enforced by `make atomizer-boundary-check`; tests:
  `api/tests/test_dependency_boundaries.py`.
- Local pre-push guard: `make install-hooks` installs a checkout-local
  `pre-push` hook. For direct pushes to `main`, it runs `make api-ci-local`
  when the pushed diff touches `api/**`, `.github/workflows/api-ci.yml`,
  `.github/workflows/docker-quickstart-smoke.yml`, root `Makefile`,
  `scripts/api_ci_pre_push.py`, `scripts/install_api_ci_pre_push_hook.py`, or
  this acceptance-gates document.
- Optional self-host Docker smoke: `api/scripts/dev.ps1 ProductionInstall`
  must pass health, readiness, admin login, concept list, and
  PostgreSQL migration checks before claiming readiness for that Docker Compose
  reference profile. The Docker reference is intentionally single-worker until
  migration/bootstrap orchestration is externalized or explicitly
  concurrency-tested. Latest local evidence (2026-05-30, A02 adversarial
  readiness): disposable project `sds-a02-prod` on API `8093` and PostgreSQL
  `55434` passed `ProductionInstall` with `/healthz`, `/ready`, admin login,
  concept list, Alembic head
  `028_reconcile_alembic_metadata_drift`, no `DuplicateColumn` log entries,
  and `ProductionDown -RemoveVolumes` left no disposable containers/volumes
  while the persistent `sds-api-prod` stack and volume stayed present. Prior
  release-gate evidence:
  `docs/quality/2026-05-23-production-release-gate.md`.
- Hosted native production gate: the current protected public API surface at
  `https://sds.ueporreres.com/` is documented in
  `docs/quality/2026-06-04-hosted-production-readiness-gate.md`. This gate
  covers the native `sds-api.service`, Apache/Virtualmin HTTPS reverse proxy,
  Basic Auth challenge, loopback-only API/PostgreSQL listeners, loopback
  `/healthz` and `/ready`, migration head, Nordhaven value counts, and absence
  of legacy sample users/FX rows. The 2026-06-06 operator credential reset
  updated both Apache Basic Auth and FastAPI admin access from the private host
  environment, then reverified the no-secret public challenge, credentialed
  app reachability, API login, service state, and loopback health/readiness
  without recording secrets in the repo. It is an API production gate, not E11
  website availability and not full subsidy/dossier closure.
- Operator proof: the README "Tarjeta de aceptacion operativa" is the
  public 30-minute reviewer path linking the DB-backed quickstart,
  health/readiness, manual Swagger checks with Nordhaven values, package
  dry-run, backup/restore drill, and self-host smoke.
  Public examples must use `<operator-drill-dir>` for local env/backup outputs,
  and removed guided walkthrough docs must stay absent from the current public
  boundary. Focused tests:
  `api/tests/test_deliverables_public_boundary.py` and
  `api/tests/test_backup_restore_meta.py`.
- Hosted Docker quickstart smoke: `.github/workflows/docker-quickstart-smoke.yml`
  runs on manual dispatch, weekly schedule, and PR/main changes to Docker/runtime
  inputs. It builds `api/compose.yml`, probes `/healthz` and `/ready`, and tears
  the stack down with volumes removed.
- Deliverables publication: `make deliverables-check` validates `deliverables/deliverables-register.csv`, canonical paths, checksums, per-deliverable indexes, and public privacy heuristics.
- `make repo-closure-check` is a publication-presence boundary: it checks that
  the declared repo closure paths exist and that obvious stale E11 markers are
  absent. It is not a semantic or subsidy-closure certificate; pair it with
  `make deliverables-check`, the applicable deliverable gates, and
  `make subsidy-closure-check`.
- Subsidy/dossier closure boundary: `make subsidy-closure-check` validates
  the public status surface at
  `deliverables/evidence-public/dossier-traceability-status-sds-v2026-06-01.md`.
  A pass means the repo-vs-subsidy status boundary and residual ledger are
  explicit and internally consistent. It does not mean full subsidy/dossier
  closure is achieved; the command reports `NOT_FULL_SUBSIDY_CLOSED` while
  `E02`/`R3`, `E10`/`R20`, and `E11`/`R21` remain conditional.
- Deliverable citation system: all deliverables inherit `deliverables/citation-system.md`; Markdown citations use `[@CITATION_KEY]` markers and DOCX/PDF renderings must use Chicago-style notes plus bibliography. `make deliverables-check` validates citation-system presence and Markdown marker hygiene.
- CI API workflow: `.github/workflows/api-ci.yml` runs the required API lint,
  security scan, full tests with coverage fail-under, and uploads security JSON
  reports. Docker image validation is a separate quickstart smoke in
  `.github/workflows/docker-quickstart-smoke.yml`.
- CI deliverables workflow: `.github/workflows/deliverables-check.yml` runs the deliverables register check and repo closure check when deliverables or their validation scripts change, with a 10-minute job timeout and cancel-in-progress concurrency.
- Additional CI workflows must not be claimed as evidence unless the corresponding `.github/workflows/` files exist in the repo snapshot.

## E1 — Inventory
- Project acceptance shape (KPI R1): standards-aligned inventory coverage for the selected CSRD/ESRS + GRI scope, with ESG categorization and measurement metadata present. GHG Protocol is not part of this minimum E1 acceptance scope.
- Current evidence basis: the E01 public artifact plus the dimension-expanded value-coordinate annex and machine-readable calculation ledger dated `V2026-06-05`.
- Current evidence gate: `make e1-gate` validates the E01 artifact, the calculation annex, the grouped ledger, the current official-standard coverage summaries, the `87,547` reportable closed-dimension coordinate total, and the `88,397` broader SDS technical-capacity total. The retired row-count coverage files are not accepted evidence for the active E1 gate.
- Accepted technical baseline metadata: E1 records official standard denominators plus controlled indicator-granulation and technical-validation evidence. ESRS and GRI are the minimum E1 acceptance scope; GHG Protocol is voluntary additional technical coverage:
  - ESRS: `1,200 / 1,200` official rows, `99 / 99` scopes, `1,242` canonical SDS indicator definitions, `1,249` calculation-ready nodes.
  - GRI: `3,964 / 3,964` GRI Standards source rows, `573 / 573` scopes, `3,666` canonical SDS indicator definitions, `4,252` calculation-ready nodes.
  - GHG Protocol: `18 / 18` official scopes, `113` canonical SDS indicator definitions, `317` calculation-ready nodes as voluntary additional technical coverage.
  - Dimension-expanded operational collection fields are not counted here; a Sygris-style implementation may expand one canonical definition into multiple fillable value records by entity, site, activity, period, consolidation boundary, GHG scope/category, scenario, or other dimensions.
- Technical checks:
  - Duplicate SDS identifiers: `0`.
  - Numeric rows missing unit metadata: `0`.
  - Unit labels outside the SDS unit catalog: `0`.
  - Structural dry-runs pass with `--require-calculation-contract`.
  - No operational values are included; the E1 evidence is structural/catalog/calculation evidence, not operational value imports.
- Additional technical coverage documented in E1: GHG Protocol coverage outside the minimum E1 acceptance scope, full official-denominator coverage, validated source evidence, SDS calculation metadata, SDS unit-catalog normalization, and technical structural and calculation-readiness validation.

## E2 — Interoperability & Detection
- Subsidy threshold:
  - Interoperability (crosswalk completeness): >=75% “with_both” across scoped topics (energy, GHG, water). Command: `make e2-gate`.
  - Detection (raw variable detection): >=90% rows have ESRS or GRI present per topic. Command: `make e2-gate-detection`.
- Accepted evidence: full atomized crosswalk source CSV; source-derived `e2_completeness_by_topic.csv`; source-derived `e2_detection_by_topic.csv`; example diagrams covering 1-to-1, 1-to-n, n-to-1, and 1-to-none gap cases; normalization/traceability+iXBRL notes; Sygris catalogue mappings for scoped topics; controlled crosswalk guardrail results where applicable.
- Accepted source-derived topic baseline:
  - Energy: `80 / 85` completeness (94%), `85 / 85` detection (100%).
  - GHG: `146 / 154` completeness (95%), `154 / 154` detection (100%).
  - Water: `43 / 53` completeness (81%), `53 / 53` detection (100%).
- Source-of-truth rule: `make e2-gate` and `make e2-gate-detection` derive topic metrics from `e2_crosswalks_esrs_gri_full_atomized.csv` and fail if the summary CSVs drift from the source.
- Operational baseline: E2 is a reviewed relationship inventory between official standard anchors. It should not use raw indicator counts as the denominator. Relationship rows must be typed as `equivalent`, `broader`, `narrower`, `component`, `transform`, `support_context`, `no_match`, or `pending_review`.
- Added-value GHG Protocol guardrail: `make e2-ghg-crosswalk-gate` validates the exact-equivalence evidence at `data/extracted/analysis/e2_crosswalks_ghg_protocol_esrs_gri_strict_exact_v2026-05-14.csv`. This is additional interoperability coverage beyond the minimum E2 acceptance baseline. The gate deliberately rejects Scope 3 and non-equivalent candidates.
- Current SDS mapping guardrails:
  - Product mapping behavior is backed by reviewed relationship evidence.
  - Controlled validation workflows validate, import, and preview relationship evidence before publication.
  - Installed-standard validation prevents rows for missing standards from becoming live mappings.
  - Mapping packages do not auto-install standards.
- Additional technical scope: GHG Protocol as a third framework, exact-equivalence evidence, relationship-type taxonomy, no-false-equivalence discipline, canonical mapping package APIs, installed-standard guards, materialization previews, and calculation-contract-aware transform/component handling.

## E3 — Common Model
- Thresholds: ≥80% variables represented; ≥90% representation test.
- Evidence: JSON-LD/OWL skeleton; validation CSV (`data/extracted/analysis/e3_model_representation_summary_v2026-05-14.csv`); gap list (`data/extracted/analysis/e3_model_gap_list_v2026-05-14.csv`); Semantics Bundle (ZIP + manifest) generated via `make semantics-bundle`; runtime ontology split (`core_tbox.owl` + `generated_projection.owl`) generated from the canonical semantic model.
- Checks: SHACL/JSON Schema pass; gap remediation plan; DB semantic catalogue projection coverage passes (`make service-semantic-catalog-projection`), then generated ontology projection coverage passes (`make -C api generate-projection` followed by `make -C api gate-w2`).

## E4 — Prototype
- Thresholds: 1k rows <30s; errors logged; rules for ≥3 domains.
- Evidence: run log with timing; sample output CSV with validity/mapping flags.
- Checks: performance SLA; validation error rates; reproducibility.
- Gate: `make e4-gate` fails the build if the SLA is violated and writes:
  - `data/extracted/analysis/e4_performance_report.txt`
  - `data/extracted/analysis/e4_performance_summary.csv`
- `make e4-gate` is a structural dimension-ledger gate. Its default path
  accounts for and times precomputed reportable coordinates; it does not prove
  PostgreSQL runtime performance or a live raw-value transformation. Use
  `make service-value-import-performance` and
  `make service-raw-value-transform-matrix` against disposable PostgreSQL for
  those operational claims.

## E5 — Technical Code of the Model
- Thresholds: functional load/transform/export; ≥90% unit test coverage; integration tests passing; docs/examples.
- Evidence: code, tests, coverage report, integration logs.
- Recorded evidence (`data/extracted/analysis/e5_api_coverage_summary_v2026-06-23.json`, dated 2026-07-10, A01 API CI coverage gate): `make api-ci-local` ran the API `test-coverage` target, passed 2,682 passing tests with 57 skipped tests, enforced `--cov-fail-under=97.01`, and reported exact measured coverage of 97.35909967268977% (`21,714 / 22,303` covered statements; `589` missed statements), displayed as 97% TOTAL coverage. The documented >=90% E5 acceptance threshold and current >97% quality objective are both met, and the evidence deliberately does not claim 100% coverage.
- Recorded API CI mirror evidence (2026-07-10, A01 adversarial readiness):
  `make api-ci-local` passed lint, formatting/import-order checks, security
  scan, 2,682 API tests with 57 skipped tests, and the API coverage fail-under gate,
  including runtime interoperability readiness, value resolution, strict
  ESRS/GRI code ingestion, Swagger documentation examples, and fail-closed
  calculation-contract execution/import safeguards.
- This is a retained historical snapshot accepted for the dated E5 evidence
  boundary. `scripts/e5_check_coverage_evidence.py` validates internal
  consistency and thresholds but does not rerun the current checkout. Current
  execution evidence requires a fresh `make api-ci-local` result.

## E6 — Governance
- Purpose: governance model for dataspace operation (roles/decision rights, onboarding, usage control, auditability, incident response).
- Static gates validate artifact readiness and policy/service conformance. Operational KPI gates still require pilot evidence.
- Artifact gates (static readiness):
  - **G6-A1** Policy registry (machine-readable JSON): `docs/policies/policy_registry.json` + schema `docs/policies/policy_registry_schema.json`
  - **G6-A2** Data product terms template references policy IDs + audit expectations: `docs/policies/data_product_terms.md`
  - **G6-A3** Usage control examples include purpose+region+retention+duty mapping to connector policy JSON: `docs/policies/usage_control_examples.md`
  - **G6-A4** Trust-enforcement status (machine-readable JSON): `docs/governance/trust_issuers.json`. While its `runtimeEnforcement.status` is `not_activated`, the issuer list must be empty; any future activation requires issuer/JWK validation.
  - **G6-A5** Onboarding flow documents DID/VC binding + status checks: `docs/governance/onboarding_flow.md`
  - **G6-A6** Operating model RACI: `docs/governance/operating_model_raci.md`
  - **G6-A7** Audit event schema (JSON Schema): `docs/governance/audit_event_schema.json`
  - **G6-A8** Purpose binding profile: `docs/policies/purpose_binding_profile.md`
  - **G6-A9** Retention enforcement profile: `docs/policies/retention_enforcement.md`
  - **G6-A10** Legal binding matrix: `docs/policies/legal_binding_matrix.csv`
  - **G6-A11** E6 deliverable published: `deliverables/E06-governance/final/e06-gobernanza-y-politica-de-datos.md`
- Technical validation gates:
  - **G6-T1** Deny-by-default: all policies in registry have `denyByDefault: true`; no allow-all patterns.
  - **G6-T2** `"<python-3.10>" scripts/run_e6_gate.py --register-package-dir "<external-package-dir>"` requires CPython 3.10 with Unicode data `13.0.0`, `api/requirements-dev.txt`, and the SDS-locked external Atomizer register package. The direct argument vector is the only accepted external-input boundary; GNU Make deliberately does not carry this path. The gate validates and snapshots the package, regenerates and byte-compares all three EDC JSON artifacts, then runs deliverable tests (`api/tests/test_e6_governance_pack.py`), policy tests (`api/tests/test_policy_enforcement.py`), bounded-generator input tests (`api/tests/test_edc_bundle_input_security.py`), checker tests (`api/tests/test_e6_check_governance.py`), and `scripts/e6_check_governance.py --strict` against the snapshot. Every subprocess has a finite timeout. Missing input, prerequisite, parity, timeout, or test failure stops the gate; missing schema-validation support is an error, never an accepted skip.
  - **G6-T3** NGSI-LD/DCAT-AP exports include `hasPolicy` relationships for primary and additive policies (policy selection consistent with EDC: purpose + region + optional explicit `policyId`; explicit policy IDs fail closed on region incompatibility).
  - **G6-T4** Audit events convertible to NGSI-LD: `scripts/e6_audit_to_ngsi_ld.py`.
  - **G6-T5** Evidence package automation: `make e6-evidence` generates audit package.
  - **G6-T6** EDC bundle consistency: `make e6-refresh-edc` generates connector policies/assets/contracts and `make e6-check` verifies asset and contract-definition counts against the delivery register.
- **Input reproducibility boundary (2026-09-24):** the strict E6 gate requires
  an external package directory and validates it against the SDS-controlled
  `configs/edc/register-input-lock.json`. It rejects package self-rebinding,
  snapshots the locked CSV outside the mutable source directory, and
  byte-compares regenerated `assets.json`, `policies.json` and
  `contract-definitions.json` before tests. The Atomizer package remains outside
  the public SDS tree. Host qualification passed 6/6, 32/32, 5/5 generator
  input-security tests and 19/19 checker tests plus the strict checker. Details:
  `docs/quality/e6-register-input-qualification.md`.
- Operational KPI gates (pilot evaluation targets):
  - Onboarding cycle time: average ≤24h (post KYB/KYC completion) with timestamps in onboarding records.
  - Policy enforcement coverage: 100% of negotiations log a policy evaluation outcome.
  - Audit completeness: ≥99% of successful accesses emit LOG events with required fields.
  - Policy propagation: policy registry changes effective across connectors in <15 minutes (staged test).
  - Incident containment: critical incident contained in <4 hours (tabletop drill + report).
  - Dispute handling SLA: initial response ≤5 working days; resolution ≤30 working days (ticket log + decision record).
- Validation commands:
  - `make e6-refresh-edc` — Regenerate connector policy/assets/contracts from the delivery register.
  - `make e6-check` — Run governance artifact validation.
  - `"<python-3.10>" scripts/run_e6_gate.py --register-package-dir "<external-package-dir>"` — Validate and snapshot the locked external register, prove EDC parity, then run deliverable, policy and checker tests in order and the strict artifact check. Run from a POSIX host or Linux/WSL environment that supports descriptor-relative no-follow opens.
  - `make e6-gate` / `make gate-governance` — Fail closed with the required direct command; GNU Make is not the external-input trust boundary.
  - `make e6-evidence` — Generate evidence package for auditors.
- H09 remains open for a real verifier and human decisions/evidence on key provenance and custody. The `not_activated` state intentionally publishes no issuer or JWK; passing its checker does not qualify runtime VC/EDC verification or close H09. Dated validation: `docs/quality/2026-09-12-h09-checker-gate-integrity.md`.

## E7 — Workshops
- Thresholds: minutes are complete, structured, and shared within 10 days (guiding dates); topics/decisions covered ≥90% (requires real workshop logs).
- Evidence:
  - E7 status/index: `deliverables/E07-workshops/README.md`
  - Sanitized minutes: `deliverables/E07-workshops/final/e07-actas-conclusiones-talleres-v1-0.md`
  - Feedback traceability: `deliverables/E07-workshops/evidence/e07-feedback-traceability-v1-0.csv`
- Gate (offline-safe):
  - `make e7-gate` validates the published package structure, register status,
    sanitized canonical minutes, and complete F01-F10 feedback rows. It does
    not prove the dossier timing or coverage thresholds; those require dated
    source workshop logs and an approved denominator.

## E8 — Use Cases & Sector Priorities
- Thresholds: at least 5 documented use cases; prioritization matrix includes impact/feasibility/urgency scoring and ranking; alignment to SDS technical framework is explicit.
- Evidence:
  - Use case catalogue: `deliverables/E08-use-cases/final/e08-casos-de-uso-y-prioridades-sectoriales.md`
- Gate (offline-safe):
  - `make e8-gate` — Validates use case count and prioritization matrix structure.

## E9 — Adjustments
- Thresholds: documented adjustments with justification and context; alignment to objectives; accessibility and structure.
- Evidence:
  - Adjustments report: `deliverables/E09-adjustments/final/e09-adjustments-report-v2026-02-03.md`
- Gate (offline-safe):
  - `make e9-gate` — Validates adjustments report structure.

## E10/E11/E13 — Communications & Roadmap
- Thresholds: publication‑ready comms artifacts; website artifact with required sections; consolidated roadmap/recommendations aligned to phases.
- Evidence (repo artifacts):
  - E10 plan: `deliverables/E10-communications-plan/final/e10-communications-plan-v2026-02-03.md`
  - E11 official website evidence: `deliverables/E11-website/evidence/official-url.md` records the canonical URL and dated reachability observations; it is not a content/impact acceptance gate.
  - E11 publication report: `deliverables/E11-website/final/e11-website-publication-report.md`
  - E11 legal-page maintenance closeout: `docs/quality/2026-07-22-website-legal-pages-closeout.md` records the 2026-07-22 visual/legibility correction for public legal pages; it is not analytics or E12 impact evidence.
  - E12 comms report status/index: `deliverables/E12-communications-impact/README.md`
  - E13 roadmap status/index: `deliverables/E13-roadmap-scalability/README.md`
- Gates (offline-safe):
  - `make e10-gate` — Validates comms plan structure (objectives/audiences/channels tables).
  - `make e11-gate` — Validates the official E11 URL record, dated reachability observations, and that the local website test artifact is excluded.
  - `make e11-deploy-gate` — Alias for the current E11 publication-evidence gate; it does not validate content acceptance, analytics, impact evidence, or the retired local website test artifact.
  - `make e12-gate` — Validates the published sanitized communications-impact
    report and its explicit evidence boundaries; it does not fabricate missing
    analytics or activity execution.
  - `make e13-gate` — Validates the published E13 V1.0 roadmap/scalability
    report structure and required themes. Publication status remains governed
    by `deliverables-register.csv` and `make deliverables-check`.

## Global KPIs & Evidence Discipline
- Track against KPIs R1–R23 and record evidence.
- Dossier-level traceability is maintained in
  `deliverables/evidence-public/dossier-traceability-status-sds-v2026-06-01.md`.
  It maps `PT1`-`PT6`, the explicit `A1.1`-`A6.3` activity identifiers,
  `R1`-`R23`, and `E01`-`E13` to repo-local evidence status and accepted
  residuals without importing the source dossier into the public repo.
- Weekly delta: coverage/mapping/model validation changes with links.
