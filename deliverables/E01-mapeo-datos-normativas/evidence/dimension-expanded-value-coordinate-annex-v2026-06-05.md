# Annex - Dimension-Expanded Value Coordinate Calculation

Project: Sustainability Data Spaces (SDS)
Deliverable: E1 - Key Data Inventory and Raw Variables
Annex version: V2026-06-05
Date: 2026-06-05
Status: calculation evidence annex

## 1. Purpose

This annex explains how SDS converts canonical sustainability datapoints and
calculation-contract nodes into a flat value-coordinate equivalent. The
calculation is used for audit explanation, implementation sizing, and comparison
with systems that create one fillable field per matrix cell.

This annex does not redefine the official standards denominator used for E1
acceptance. The formal E1 acceptance scope remains the accepted ESRS and GRI
coverage baseline. GHG Protocol is included here as voluntary additional technical coverage and is not used to satisfy the minimum E1 acceptance threshold.

## 2. Evidence Files

This annex is supported by the machine-readable calculation ledger:

- `dimension-expanded-value-coordinate-calculation-v2026-06-05.csv`

The CSV is the audit backbone. It contains the grouped calculation rows used to
reproduce the headline totals, including standard, source version, group,
base-node count, dimensions, member counts, formula, reportable coordinates,
technical coordinates, exclusions, and source locators.

## 3. Source Inputs

| Surface | Source input | Role in calculation |
|---|---|---|
| ESRS | EFRAG ESRS Set 1 XBRL taxonomy, release package dated 2024-08-30 | Provides finite dimensional tables, role codes, line items, axes, and members. |
| GRI | SDS controlled closed GRI-derived register package generated 2026-05-22 | Provides public register nodes and calculation-contract dimensions. |
| GHG Protocol | SDS controlled closed GHG Protocol package generated 2026-05-22 | Provides voluntary public register nodes and calculation-contract dimensions. |

The ESRS calculation uses the taxonomy definition linkbases. The GRI and GHG
calculations use the SDS calculation contract because the SDS register is the
public indicator surface and the calculation contract is the runtime semantic
surface for dimensions, formulas, gates, and support nodes.

## 4. Counting Boundary

The count is a standards-level flat value-coordinate equivalent. It expands
finite standard matrices but does not multiply by reporting-period or
company-specific repetitions.

Included:

- reportable datapoints and reportable calculated values;
- finite closed dimensions with controlled member lists;
- SDS calculation-contract nodes when reporting the broader technical-capacity
  count;
- calculated or aggregate outputs where SDS stores or resolves them as value
  coordinates.

Excluded from the reportable headline:

- reporting period;
- entity perimeter;
- site, location, facility, asset, supplier, product, route, and other company
  master-data repetitions;
- typed/open dimensions beyond one governed runtime placeholder;
- duplicate technical representations;
- non-reportable containers.

For ESRS, the `ReportingScopeAxis` current/previous-restatement axis is excluded
from the headline because it is a technical restatement axis, not a separate
normal collection denominator for this sizing exercise.

## 5. Variable Dictionary

| Variable | Meaning |
|---|---|
| `base_nodes` | Count of reportable line items, public register rows, or calculation-contract nodes in the group. |
| `closed_dimension_axis` | A finite dimension with a controlled member list. |
| `member_count(axis)` | Number of allowed members counted for a closed dimension in the group. |
| `closed_multiplier` | Product of all closed-dimension member counts in the group. |
| `typed_open_axis` | A company-specific or open dimension, counted as `1` placeholder at standards level. |
| `reportable_coordinates` | Flat value-coordinate count for the public/reportable surface. |
| `technical_coordinates` | Flat value-coordinate count for the broader SDS calculation-contract surface. |
| `excluded_repetitions` | Period, perimeter, location, and company master-data repetitions deliberately excluded from the standards-level count. |

## 6. Formulas

For one node or line-item group:

```text
closed_multiplier = product(member_count(axis) for each closed_dimension_axis)
```

```text
expanded_coordinates(group) = base_nodes * closed_multiplier
```

For typed/open dimensions:

```text
member_count(typed_open_axis) = 1
```

For one standard:

```text
standard_reportable_total = sum(reportable_coordinates(group))
```

For the reportable E1 dimension-expanded headline:

```text
reportable_total = ESRS_reportable + GRI_reportable + GHG_reportable
```

```text
reportable_total = 82,858 + 4,399 + 290 = 87,547
```

For the broader SDS technical-capacity count:

```text
technical_total = ESRS_technical + GRI_contract + GHG_contract
```

```text
technical_total = 82,858 + 4,985 + 554 = 88,397
```

## 7. ESRS Calculation Rule

For ESRS, each XBRL disclosure role is counted as:

```text
ESRS_role_coordinates = non_abstract_line_items * product(finite_axis_member_counts)
```

The ESRS headline uses the following controls:

- utility and enumeration roles are excluded;
- non-abstract line items are counted as base nodes;
- finite axes are expanded by their member counts;
- typed axes are counted as `1`;
- `ReportingScopeAxis` is excluded;
- period, perimeter, entity, location, and company master-data repetitions are
  excluded;
- selected E1 climate target and emissions-pathway axes count explicit
  baseline/milestone/target members without duplicating the abstract
  current/root member already represented by the base disclosure coordinate.

The last control applies to ESRS taxonomy roles `301042` to `301048` and
`301064` to `301066`. It avoids double-counting the current-state coordinate in
E1 target and emissions-pathway tables.

### ESRS Reconciliation

| ESRS area | Reportable coordinates |
|---|---:|
| ESRS 2 | 711 |
| E1 | 33,433 |
| E2 | 44,929 |
| E3 | 411 |
| E4 | 490 |
| E5 | 430 |
| S1 | 1,185 |
| S2 | 259 |
| S3 | 263 |
| S4 | 252 |
| G1 | 491 |
| Other | 4 |
| **ESRS total** | **82,858** |

### ESRS Calculation Examples

Pollutants table:

```text
coordinates = line_items * current/baseline/target_members * range_members * pollutant_members
coordinates = 3 * 3 * 3 * 92 = 2,484
```

Substances of concern table:

```text
coordinates = line_items * substance_flow_members * hazard_category_members * hazard_class_members * current/baseline/target_members
coordinates = 12 * 6 * 9 * 18 * 3 = 34,992
```

E1 target pathway table:

```text
coordinates = line_items * decarbonisation_lever_members * ghg_category_members * baseline/milestone/target_members
coordinates = 24 * 9 * 9 * 8 = 15,552
```

The complete ESRS grouped arithmetic is in the CSV ledger. Each CSV row records
the relevant taxonomy roles, base nodes, dimensions, member counts, formula, and
subtotal.

## 8. GRI Calculation Rule

For GRI, the reportable headline expands SDS public register nodes across the
closed dimensions declared in the certified GRI calculation contract:

```text
GRI_reportable_coordinates(group) = public_register_nodes * product(closed_dimension_member_counts)
```

For the broader technical-capacity count, the same rule is applied to all GRI
calculation-contract nodes:

```text
GRI_technical_coordinates(group) = contract_nodes * product(closed_dimension_member_counts)
```

Typed/open dimensions are kept as one governed runtime placeholder:

```text
member_count(typed_open_dimension) = 1
```

### GRI Reconciliation

| GRI surface | Coordinates |
|---|---:|
| Public register reportable coordinates | 4,399 |
| Full calculation-contract technical coordinates | 4,985 |
| Public register rows | 3,666 |
| Calculation-contract nodes | 4,252 |

### GRI Calculation Examples

GRI 2-7 employee count group:

```text
coordinates = nodes * gender_members * employee_count_method_members * employee_count_period_basis_members
coordinates = 1 * 4 * 3 * 3 = 36
```

GRI water withdrawal by source and water-stress status:

```text
coordinates = nodes * water_source_members * water_stress_area_members
coordinates = 1 * 5 * 2 = 10
```

GRI Scope 3 category group:

```text
coordinates = nodes * scope3_category_members
coordinates = 1 * 16 = 16
```

The complete GRI grouped arithmetic is in the CSV ledger. Reportable rows and
technical-contract rows are separated so the public headline and broader
technical capacity can be checked independently.

## 9. GHG Protocol Calculation Rule

GHG Protocol is included as voluntary additional technical coverage. It is not
part of the minimum E1 acceptance threshold.

For the GHG Protocol reportable headline:

```text
GHG_reportable_coordinates(group) = public_register_nodes * product(closed_dimension_member_counts)
```

For the broader technical-capacity count:

```text
GHG_technical_coordinates(group) = contract_nodes * product(closed_dimension_member_counts)
```

Typed/open dimensions such as factor source, supplier, activity source, or
company-specific source identifiers are counted as one governed runtime
placeholder at standards level.

### GHG Protocol Reconciliation

| GHG Protocol surface | Coordinates |
|---|---:|
| Public register reportable coordinates | 290 |
| Full calculation-contract technical coordinates | 554 |
| Public register rows | 113 |
| Calculation-contract nodes | 317 |

### GHG Protocol Calculation Examples

Scope 3 category group:

```text
coordinates = nodes * scope3_category_members
coordinates = 1 * 16 = 16
```

Required gas emissions by scope:

```text
coordinates = nodes * ghg_scope_members * ghg_gas_members
coordinates = 1 * 2 * 7 = 14
```

Scope 2 method group:

```text
coordinates = nodes * scope2_calculation_method_members
coordinates = 1 * 2 = 2
```

The complete GHG Protocol grouped arithmetic is in the CSV ledger.

## 10. Open Dimension Treatment

Open or typed dimensions are not flattened into a universal standards-level
indicator count. Examples include:

- `site_id`;
- `supplier_id`;
- `product_id`;
- `route_id`;
- `asset_id`;
- `emission_factor_id`;
- `facility_id`;
- `country` or `region` where the standard does not provide a finite closed
  list in the calculation surface.

For audit counting:

```text
member_count(open_dimension) = 1
```

For operational reporting:

```text
operational_value_rows = standards_coordinate * actual_company_members
```

Example:

```text
Scope 3 category value by supplier_id
```

The standards-level count keeps `supplier_id = 1` placeholder. A company with
200 relevant suppliers would instantiate supplier-level operational rows only
where that datapoint is applicable.

This keeps the standards-level count bounded and auditable while preserving full
runtime granularity.

## 11. Headline Result

| Standard surface | Reportable closed-dimension coordinates | Broader technical coordinates | Notes |
|---|---:|---:|---|
| ESRS | 82,858 | 82,858 | Finite ESRS Set 1 XBRL dimensions expanded; period/perimeter repetitions excluded. |
| GRI | 4,399 | 4,985 | Public register headline plus full calculation-contract capacity. |
| GHG Protocol | 290 | 554 | Voluntary additional technical coverage; public register headline plus full calculation-contract capacity. |
| **Total** | **87,547** | **88,397** | Period, perimeter, location, and company master-data repetitions excluded. |

## 12. Interpretation

The `87,547` reportable-coordinate total is a flat form-field equivalent. It
approximates how many individual value positions a flat questionnaire or
indicator-per-cell implementation would need in order to represent the same
finite standards coverage.

It is not a statement that every reporting company must submit all `87,547`
values. Actual reporting is reduced by materiality, applicability, phase-ins,
alternative disclosures, unavailable activities, and company context.

It is also not a requirement to create `87,547` separate master indicators. SDS
stores canonical datapoints once and applies dimensions, members, gates,
formulas, and runtime value coordinates. This avoids indicator explosion while
preserving auditability and full reporting granularity.

## 13. Verification Procedure

To verify the annex totals:

1. Open `dimension-expanded-value-coordinate-calculation-v2026-06-05.csv`.
2. Sum `reportable_coordinates` where `standard = ESRS`.
3. Sum `reportable_coordinates` where `standard = GRI`.
4. Sum `reportable_coordinates` where `standard = GHG Protocol`.
5. Confirm:

```text
ESRS = 82,858
GRI = 4,399
GHG Protocol = 290
reportable_total = 87,547
```

6. Sum the technical-contract coordinates for GRI and GHG Protocol and combine
   them with the ESRS total:

```text
ESRS_technical = 82,858
GRI_technical = 4,985
GHG_technical = 554
technical_total = 88,397
```

The CSV is intentionally grouped by calculation signature rather than expanded
to one row per final value cell. This keeps the audit file readable while still
making every multiplication and subtotal reproducible.
