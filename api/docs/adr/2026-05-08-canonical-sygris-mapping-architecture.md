# ADR: Canonical Sygris Mapping Knowledge Base

Date: 2026-05-08
Status: Accepted

## Context

SDS currently exposes flat standard-to-standard mappings through `/api/v1/mappings` and persists them in `standard_mappings`. That is enough for the current ESRS/GRI interoperability surface, but it is not a stable long-term source of truth for Sygris-centered mappings because it cannot represent:

- standard release/version identity,
- one-to-many, many-to-one, partial, composite, gap, excluded, or deprecated mappings,
- explicit match and mismatch rationale,
- approval/publication state,
- lineage from generated pairwise rows back to curated Sygris assertions,
- external package schema/version compatibility.

The project already has revisioned `canonical_concepts`, so creating a second internal Sygris identity table would duplicate the concept authority unless a later ADR proves that `canonical_concepts` cannot represent Sygris taxonomy/version semantics.

## Decision

SDS will evolve mappings using an additive canonical mapping knowledge base:

- `canonical_concepts` remains the preferred Sygris/internal canonical concept identity layer.
- `standard_releases` stores external standard releases.
- `standard_datapoints` stores stable datapoint identities inside each release.
- `mapping_assertion_groups` stores curated claims from one external datapoint to a Sygris canonical footprint.
- `mapping_assertion_components` stores the ordered Sygris components of that footprint.
- `materialized_pairwise_mappings` stores generated cross-standard rows for active supported pairs and is now the DB-backed public `/api/v1/mappings` read-model after cutover.
- `standard_mappings` remains legacy/direct evidence for parity and audit checks during migration; it is not a runtime fallback for `/api/v1/mappings`.
- Canonical mapping package validation, import, and materialization are gated by
  the standard releases installed in the target SDS instance. A package may be
  structurally valid while some rows remain inactive/pending because the
  referenced standard release or datapoint is not installed there.

External producers may emit declarative canonical mapping packages, but SDS owns validation, import, snapshots, materialization, API semantics, and gates. SDS must reject a package whose `package_schema_version` is newer than the running SDS code supports.

## Alternatives Considered

- Extend only `standard_mappings`: simplest short-term option, but it keeps direct pairwise rows as the authored truth and does not model partial/composite coverage or Sygris lineage cleanly.
- Create a separate `sygris_concepts` table immediately: clear name, but duplicates the existing `canonical_concepts` direction and adds identity debt before proving the current model is insufficient.
- Use only a graph/ontology store: expressive, but would make the runtime API and validation harder to test and operate for this migration slice.

## Consequences

Positive:
- Sygris becomes the canonical comparison axis.
- Pairwise ESRS/GRI and future cross-standard results can be derived deterministically.
- `/api/v1/mappings` keeps its response contract stable while its backing store can be cut over to the canonical pairwise read-model behind explicit gates.
- The design supports versioning, auditability, confidence, rationale, and gap reporting.

Negative:
- The schema is richer than the current mapping model and needs strict gates to avoid unused complexity.
- Backfilling existing ESRS/GRI mappings cannot be treated as final Sygris truth unless the Sygris footprint is explicit.
- Materialized pairwise rows need drift checks so generated data does not silently diverge from assertions.

Neutral:
- Initial implementation was additive; after cutover, committed pairwise materialization updates the canonical read-model consumed by `/api/v1/mappings`.
- Strict materialization and parity gates remain required before operational mapping changes.

## Verification

The first implementation slice is accepted only when:

- SQLAlchemy models and Alembic migration define the new additive tables.
- Migration `015_create_canonical_mapping_tables` chains after canonical concepts.
- The canonical package manifest preflight rejects unsupported future schema versions.
- The shadow/report-only package validator checks manifest, checksums, CSV headers, row-level references, dates, confidence ranges, and Sygris revisions before any database write exists.
- Existing `/api/v1/mappings` response contract remains stable across the cutover.
- Package import remains separate from materialization: import writes canonical assertion tables, while committed materialization updates the canonical read-model used by `/api/v1/mappings`.
- Internal package APIs expose installed-standard validation jobs, import jobs,
  and materialization previews without turning `/api/v1/mappings` into a write
  surface.
- Materialization cannot emit live pairwise rows for standards outside the
  current installed-standard release set.
