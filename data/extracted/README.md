# Derived SDS Artifacts

`data/extracted/` stores downstream SDS artifacts produced from local transforms, analysis runs, and deliverable snapshots.

## Boundary

- Public SDS does not vendor upstream operational packages as canonical source
  data. Indicator, value, and mapping packages enter through the documented SDS
  package contracts.
- `data/extracted/` is **non-canonical**. Files here may depend on package
  inputs, SDS transforms, governance logic, or deliverable packaging.
- `data/processed/` is also non-canonical relative to external packages. It
  contains SDS-owned prepared outputs such as the E1 dataset register.

## Typical contents

- `analysis/` acceptance snapshots and evidence files
- `analysis/ngsi_ld/` exported E3 entities
- `analysis/semantics_bundle/` built semantics bundles

If a file under `data/extracted/` needs to become a pinned input, move it through a dedicated contract instead of treating this directory as a source of truth.
