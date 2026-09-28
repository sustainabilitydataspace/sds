# ADR: Deterministic Unit And FX Conversion Engine

Date: 2026-05-20
Status: Accepted

## Context

SDS already converts units through `UnitConverter` and exposes unit endpoints, but
currency conversion is not modeled as historical policy-driven data. Calculation
contracts already have a runtime seam for unit normalization, so the safest
path is additive architecture rather than a calculation-engine rewrite.

## Decision

SDS will keep physical unit conversion and FX conversion as separate engines
behind a `ConversionEngine` orchestrator. Physical conversions are validated by
dimension vectors and versioned rules. FX conversions are selected by explicit
policy, provider, rate type, date or period, and immutable rate observation ids.

## Alternatives Considered

- Use `UnitCategory.CURRENCY` for FX: rejected because currency depends on
  provider, date, period policy, rate type, redenomination, and licensing.
- Rewrite calculation first: rejected because the existing normalizer seam can
  carry conversion behavior with less blast radius.
- Runtime QUDT dependency: rejected for now; SDS will use a compact dimension
  model with source references.

## Consequences

The design adds tables and a small conversion package, but keeps the calculation
engine stable. All conversion outcomes become auditable. Missing or ambiguous
rules fail closed.

## Verification

Unit, FX, ingest, calculation, API, migration, and package-import tests must pass.
The final gate must prove no latest-rate fallback and no category-only physical
compatibility path remains in calculation runtime.
