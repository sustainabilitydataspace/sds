# Official Deliverables

This directory is the canonical public surface for SDS official project
deliverables and publishable evidence.

The repository has two durable public surfaces:

- `api/` - API product code, API tests, deployment assets, and API documentation.
- `deliverables/` - official project deliverables, publishable evidence, and the deliverable register.

Do not use this directory for private mappings, raw source material, local AI
workspaces, rehearsal demos, or temporary review artifacts. Deliverable drafts
may live under the relevant deliverable folder's `draft/` directory when they
are intentionally promoted for repo-local review and clearly marked as not
final evidence.

## Register

The canonical index is `deliverables-register.csv`.

Each row records:

- deliverable id and title
- status
- canonical public path
- version/date
- SHA-256 checksum for the public file
- privacy classification
- sanitized private source reference

The `private_source_ref` field must use a sanitized ID such as
`SOURCE_E01_PRIVATE_VAULT`, not a local path or personal storage URL.

## Folder Policy

Each deliverable folder uses:

- `README.md` - status and contents summary
- `draft/` - repo-local drafts that are not final evidence
- `final/` - final or published Markdown/PDF/package artifacts
- `evidence/` - public evidence that supports the deliverable

If a deliverable is complete externally but not imported here, keep only a
status README and mark the register row as `external-not-imported`.

## Citation System

All deliverables inherit the shared citation rules in
`deliverables/citation-system.md`.

- Markdown files keep source citations as `[@CITATION_KEY]` markers and include
  `## References` when external authorities are cited.
- DOCX/PDF renderings must transform the same keys into Chicago-style notes and
  a bibliography.
- Internal repository evidence uses relative paths, not citation keys.

## Current Status

- Published locally: `E01`, `E02`, `E03`, `E04`, `E05`, `E06`, `E08`, `E09`, `E10`
- Official URL evidence: `E11`
- Draft locally, pending final evidence: `E12`
- Candidate for review, not registered final evidence: `E13`
- External or pending import: `E07`

Latest checked public gate evidence, refreshed on 2026-06-09:

- `E01`: `make e1-gate` validates the dimension-expanded value-coordinate
  evidence with `87,547` reportable coordinates and `88,397` broader technical
  coordinates.
- `E02`: topic metrics are source-derived from the atomized ESRS/GRI crosswalk:
  Energy `80 / 85`, GHG `146 / 154`, and Water `43 / 53` completeness.
- `E03`: the model gate derives the summary from the current register,
  NGSI-LD graph, semantics manifest, ontology projection summary, and gap list.
- `E04`: the prototype gate processes the `87,547` reportable-coordinate
  dimension ledger under the 30-second threshold.
- `E05`: the API CI coverage evidence records `2,621` passing tests,
  `56` skipped tests, and exact coverage `97.51163540850652%`.
- `E06`: the governance gate records `32` conformance tests plus the strict
  governance checker.
- `E11`: the official URL remains recorded; HEAD and GET checks on 2026-06-23
  returned HTTP 200, and a 2026-07-22 maintenance pass corrected legal-page
  duplicate-header risk and body-text weight. These observations prove dated
  reachability and visual/legibility maintenance, not content acceptance,
  analytics, or E12 impact closure.

Base-language publication note: `E01`-`E06` now use Spanish (Spain) Markdown
sources dated 2026-06-09 as their canonical public paths in
`deliverables-register.csv`, with matching DOCX renderings stored next to each
source in the corresponding `final/` folder. E01 also includes a strict
Word-native design-system DOCX rendering at
`E01-mapeo-datos-normativas/final/e01-inventario-datos-clave-variables-brutas-v2026-06-09-diseno-claude.docx`.

`web/sds_website/` was a local test artifact and is not official E11 evidence.
The official E11 communication website is recorded under `E11-website/`.
