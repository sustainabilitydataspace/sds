# E1 - Key Data Inventory and Raw Variables

Project: Sustainability Data Spaces (SDS)
Deliverable: E1 - Key Data Inventory and Raw Variables
Version: V2026-06-08
Date: 2026-06-08
Owner: SDS Program Office

## Summary

E1 defines the SDS inventory of sustainability datapoints, raw variables, measurement metadata, and technical representation needed for standards-aligned reporting. It satisfies the project acceptance requirements for normative coverage and clear categorisation, and it records the accepted SDS technical-baseline metadata for ESRS, GRI, and voluntary additional GHG Protocol coverage as of this version. GHG Protocol was not part of the minimum E1 acceptance scope; it is documented here as additional technical coverage. [@EU_CSRD_2022_2464] [@EU_ESRS_2023_2772] [@GRI_STANDARDS_2025] [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD]

E1 uses the official standard source for each framework as the denominator and applies a controlled indicator-granulation and technical validation process. The resulting SDS evidence records attributes, units, formula readiness, calculation dependencies, and traceability as standard-level technical evidence. It is structural, catalogue, and calculation-readiness evidence; it is not a company operational-value submission.

## Public Evidence

E1 uses two public evidence layers:

- External public standards: ESRS, GRI Standards, and GHG Protocol corporate and value-chain standards. [@EU_ESRS_2023_2772] [@GRI_STANDARDS_2025] [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD]
- Project deliverable evidence: the E01 public artifact. Artifact control is recorded separately in `deliverables/deliverables-register.csv`, which records canonical path, version, checksum, privacy class, and sanitized source reference.

The public deliverable states the technical outcome at standard level and records only the evidence categories needed for public review.

## Project Acceptance Criteria

| Requirement | Public acceptance shape |
|---|---|
| `R1` Normative coverage | The inventory covers at least `90%` of variables needed for the selected standards. |
| `R2` Clear categorisation | Variables are classified by sustainability area and include measurement metadata such as unit, source, and collection frequency. |
| Standard reference integrity | Inventory rows keep at least one standard reference for the selected standards. |
| Traceability | Inventory evidence keeps source and review traceability sufficient for public acceptance review. |

## Official-Standard Technical Baseline

This table is the date-qualified technical baseline summary for E1 `V2026-06-05`. The E1 gate validates the public artifact, calculation annex, grouped ledger, standards-family coverage summaries, and dimension-expanded value-coordinate totals bound to this public artifact and the E01 register checksum. ESRS and GRI are the minimum E1 acceptance scope; GHG Protocol is voluntary additional technical coverage.

| Standard | Normative denominator | Controlled granulation coverage | Canonical SDS indicator definitions | Calculation-ready definitions |
|---|---:|---:|---:|---:|
| ESRS [@EU_ESRS_2023_2772] | `1,200` official inventory rows / `99` scopes | `1,200 / 1,200` rows, `99 / 99` scopes | `1,242` definitions | `1,249` nodes |
| GRI [@GRI_STANDARDS_2025] | `3,964` GRI Standards source rows / `573` scopes | `3,964 / 3,964` rows, `573 / 573` scopes | `3,666` definitions | `4,252` nodes |
| GHG Protocol - voluntary additional technical coverage [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD] | `18` official scopes | `18 / 18` scopes | `113` definitions | `317` nodes |

The `Normative denominator`, `Controlled granulation coverage`, `Canonical SDS indicator definitions`, and `Calculation-ready definitions` columns are accepted technical data-space metadata for representation and computation. They do not add to or redefine the official reporting obligations in the normative denominator.

The definition counts are not a count of dimension-expanded operational collection fields. In a Sygris-style implementation, one canonical indicator definition can create multiple fillable value records when the reporting surface expands it by entity, site, activity, reporting period, consolidation boundary, GHG scope/category, scenario, or other dimensions. That operational value-instance expansion is implementation/runtime evidence, not part of the E1 structural inventory count.

### Dimension-Expanded Value Coordinate Interpretation

For audit and implementation sizing, SDS also records a dimension-expanded interpretation of the same technical surface. In this view, each canonical datapoint is expanded across the finite dimensions defined by the relevant standard or SDS calculation contract, while reporting period, entity perimeter, site, location, and other company-specific repetitions are excluded. Typed or open dimensions are treated as governed runtime dimensions, not as a fixed pre-counted indicator list.

Under this interpretation, E1 represents approximately `87,547` reportable closed-dimension value coordinates:

| Standard surface | Reportable closed-dimension value coordinates | Basis |
|---|---:|---|
| ESRS | `82,858` | Finite ESRS Set 1 XBRL dimensional tables expanded; period and perimeter repetitions excluded. |
| GRI | `4,399` | SDS public register rows expanded across closed dimensions in the controlled GRI-derived package. |
| GHG Protocol - voluntary additional technical coverage | `290` | SDS public register rows expanded across closed dimensions in the voluntary GHG Protocol technical extension. |
| **Total** | **`87,547`** | Reportable closed-dimension value coordinates. |

Including SDS internal calculation and support-contract nodes, the broader technical capacity represented by the same method is approximately `88,397` value coordinates.

This figure is a flat form-field equivalent, not a requirement for every company to report every value and not a requirement to create `87,547` separate master indicators. Actual reporting is reduced by materiality, applicability, phase-ins, alternative disclosures, unavailable activities, and company context. SDS preserves canonical datapoints once and represents the required reporting granularity through dimensions, members, gates, formulas, and value coordinates.

The calculation method, formulas, variables, grouped arithmetic, and reconciliation are recorded in `deliverables/E01-mapeo-datos-normativas/evidence/dimension-expanded-value-coordinate-annex-v2026-06-05.md` and its companion calculation ledger `deliverables/E01-mapeo-datos-normativas/evidence/dimension-expanded-value-coordinate-calculation-v2026-06-05.csv`.

## Technical Readiness KPIs

| KPI layer | SDS state |
|---|---|
| Official baseline coverage | ESRS `100%` and GRI `100%` for the minimum E1 acceptance scope; GHG Protocol `100%` for the voluntary additional technical scope |
| Structural and calculation-readiness validation | ESRS, GRI, and GHG Protocol evidence passes structural and calculation-readiness validation |
| Duplicate SDS identifiers | `0` in the ESRS, GRI, and GHG Protocol evidence sets |
| Numeric rows missing unit metadata | `0` in the ESRS, GRI, and GHG Protocol evidence sets |
| Unit labels outside SDS unit catalog | `0` in the ESRS, GRI, and GHG Protocol evidence sets |
| Operational values | Not included; E1 covers structural, catalogue, and calculation evidence, not company-value evidence |
| Dimension-expanded value coordinates | Reportable closed-dimension total: `87,547`; broader technical capacity: `88,397`; period, perimeter, location, and company master-data repetitions excluded |

## Inventory Shape

SDS inventory evidence uses three layers:

1. Official standard source: ESRS official inventory rows and GRI Standards source rows define the minimum E1 acceptance scope; GHG Protocol official scopes define the voluntary additional technical coverage included by SDS. [@EU_ESRS_2023_2772] [@GRI_STANDARDS_2025] [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD]
2. Indicator granulation and validation: the technical process defines publishable canonical datapoint definitions, validation rules, dimension axes, formulas, support rules, and demotions.
3. SDS technical representation: the standardized indicator register and calculation-ready metadata define the system-facing surface.

This separation prevents a self-referential coverage metric. The granulation process may decompose one official row into several SDS indicators or demote non-reportable containers; those changes improve technical fidelity but do not redefine the official denominator.

## Evidence Boundaries

E1 separates three evidence purposes:

| Evidence purpose | Role in E1 |
|---|---|
| Project acceptance evidence | Demonstrates that E1 satisfies the `R1` and `R2` acceptance requirements through the current public artifact, official-standard coverage summaries, calculation annex, grouped ledger, and E1 gate. |
| Operational service/catalogue evidence | Supports SDS runtime and catalogue use, but does not redefine the official-standard denominator. |
| Granulated technical baseline | Records the accepted ESRS, GRI, and voluntary additional GHG Protocol structural, catalogue, unit, and calculation-readiness metadata summarized in this deliverable. |

Any change to the E1 technical baseline requires a refreshed canonical artifact, checksum, privacy review, and `deliverables/deliverables-register.csv` update.

## Additional Technical Scope

The minimum project requirement is a standards-aligned inventory with at least `90%` coverage over selected CSRD/ESRS and GRI variables. GHG Protocol was not part of that minimum E1 scope. E1 covers the minimum requirement and separately documents the following additional technical scope:

| Additional scope item | Relationship to the minimum project requirement |
|---|---|
| Full official-standard denominator | E1 distinguishes official ESRS rows and GRI Standards source rows for the minimum acceptance scope, plus GHG Protocol scopes for the voluntary additional technical scope. |
| Full granular indicator coverage | Every official baseline row/scope in the technical scope is tied to validated source evidence, not just a selected inventory row. |
| GHG Protocol as a voluntary third framework | The minimum E1 requirement names CSRD/ESRS and GRI only. GHG Protocol official scopes are documented as voluntary additional technical coverage and are not used to satisfy the minimum `R1` / `R2` acceptance threshold. [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD] |
| Calculation-ready metadata | E1 carries calculation nodes, status, formulas, component references, validation rules, dimension axes, and support rules at definition level. |
| Unit catalog normalization | E1 validates all unit labels against the SDS unit catalog and records `0` unknown unit labels for ESRS, GRI, and GHG Protocol. |
| Technical evidence readiness | E1 validates evidence through structural and calculation-readiness validation rules. |
| Explicit value-import boundary | E1 states that the baseline is structural, catalogue, and calculation evidence. This prevents overclaiming operational company-value imports. |

## Acceptance Gate Mapping

| Acceptance point | Public evidence surface |
|---|---|
| Normative coverage `>=90%` | Accepted coverage evidence covers ESRS `1,200 / 1,200` official inventory rows and `99 / 99` scopes, plus GRI `3,964 / 3,964` GRI Standards source rows and `573 / 573` scopes for the minimum E1 acceptance scope. GHG Protocol coverage is `18 / 18` voluntary technical scopes and is not used to satisfy the minimum threshold. |
| Domain coverage visible by ESG group | The canonical SDS inventory classifies `1,242` ESRS definitions, `3,666` GRI definitions, and `113` voluntary GHG Protocol definitions by standard family and sustainability area. |
| No orphaned variables | Controlled granulation reconciles the selected official denominators to validated SDS definitions with `0` duplicate SDS identifiers in the ESRS, GRI, and GHG Protocol evidence sets. |
| Backfill traceability for section-level rows | Current E1 coverage and backfill traceability tie official rows/scopes to source references, reviewer traceability, and SDS canonical definitions. |
| Reviewer trail to acceptance closure | The E1 gate validates the public artifact, official-standard coverage summaries, calculation annex, grouped ledger, and accepted technical-metadata boundary. |
| Cross-standard curation baseline | ESRS, GRI, and voluntary GHG Protocol are represented as `5,021` canonical SDS definitions and `5,818` calculation-ready nodes across the technical baseline. |
| Service register traceability | The service-facing register surface is the canonical definition layer: ESRS `1,242`, GRI `3,666`, and voluntary GHG Protocol `113` definitions. |
| Granular indicator baseline | Dimension-expanded reportable value-coordinate evidence totals `87,547`: ESRS `82,858`, GRI `4,399`, and voluntary GHG Protocol `290`. |
| Technical evidence readiness | Structural and calculation-readiness validation passed; calculation-ready nodes total ESRS `1,249`, GRI `4,252`, and voluntary GHG Protocol `317`. |
| Unit catalog readiness | Numeric rows missing unit metadata: `0`; unit labels outside the SDS unit catalog: `0` across ESRS, GRI, and voluntary GHG Protocol evidence sets. |
| Operational collection expansion | Operational company values are excluded from E1 counts. The flat standards-level equivalent is `87,547` reportable value coordinates and `88,397` broader technical coordinates, before period, perimeter, site, location, and company master-data repetitions. |

## Validation Status

E1 validates the project normative-coverage and categorisation requirements against the current public artifact, official-standard coverage summaries, calculation annex, grouped ledger, and E1 gate for the minimum ESRS/GRI acceptance scope. The official-standard technical baseline is an accepted, date-qualified metadata summary for ESRS, GRI, and voluntary additional GHG Protocol structural, catalogue, and calculation-readiness evidence. Its indicator and node counts are definition-level counts, not dimension-expanded operational value-instance counts.

## References

- European Parliament and Council. Directive (EU) 2022/2464 of 14 December 2022 amending Regulation (EU) No 537/2014, Directive 2004/109/EC, Directive 2006/43/EC and Directive 2013/34/EU, as regards corporate sustainability reporting. Official Journal of the European Union, 2022. https://eur-lex.europa.eu/eli/dir/2022/2464/oj. [@EU_CSRD_2022_2464]
- European Commission. Commission Delegated Regulation (EU) 2023/2772 of 31 July 2023 supplementing Directive 2013/34/EU as regards sustainability reporting standards. Official Journal of the European Union, 2023. https://eur-lex.europa.eu/eli/reg_del/2023/2772/oj. [@EU_ESRS_2023_2772]
- Global Reporting Initiative. GRI Standards: full set of GRI Standards. Amsterdam: Global Reporting Initiative, 2025. https://www.globalreporting.org/standards/gri-standards-download-center/gri-standards/. [@GRI_STANDARDS_2025]
- Greenhouse Gas Protocol. The Greenhouse Gas Protocol: A Corporate Accounting and Reporting Standard, revised edition. World Resources Institute and World Business Council for Sustainable Development. https://ghgprotocol.org/corporate-standard. [@GHG_PROTOCOL_CORPORATE_STANDARD]
- Greenhouse Gas Protocol. Corporate Value Chain (Scope 3) Accounting and Reporting Standard. World Resources Institute and World Business Council for Sustainable Development, 2011. https://ghgprotocol.org/corporate-value-chain-scope-3-standard. [@GHG_PROTOCOL_SCOPE3_STANDARD]
