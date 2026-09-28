# SDS ↔ Atomizer translation package contract

Status: `v1` (`package_contract_version = sds-translations-v1`)

This is the contract between **Atomizer** (produces translations) and **SDS** (validates + serves
them). It implements the SDS API localization V1 package contract. SDS never auto-translates at
runtime; Atomizer owns production and packaging, SDS owns the import gate and the read/serve path.

## Responsibility split

| Step | Owner | Artifact |
|---|---|---|
| 1. Emit the **worklist** — what to translate, with the locked source hash | **SDS** | `sds_translation_worklist.<kind>.csv/json` + `worklist_manifest.json` (`scripts/export_translation_worklist.py`) |
| 2. **Produce** target text (official / human-reviewed / AI `machine_draft`) + **package** it | **Atomizer** | `sds_translations.csv` (+ optional `.json`) + `manifest.json` |
| 3. **Import → validate → serve** | **SDS** | `localization_service.import_translations` + the `?lang` read path |

## The locked hash

`source_hash` and `translation_hash` are **SHA-256 over NFC-normalized UTF-8** of the exact field
text (`src/services/localization.py::text_hash`). This is fixed for v1 and MUST NOT change without a
new `package_contract_version`. Atomizer MUST translate against the `source_hash` SDS emits; if the
SDS source text later changes, that translation becomes **stale** and is excluded from public
display until re-reviewed against the new source.

## Stable subject identity (never a DB id)

Translations are keyed by canonical subject identity, never a DB primary key:

| subject_kind | subject_uri | localizable fields (v1) |
|---|---|---|
| `concept` | compact concept URI (e.g. `csrd:E5-5_09`) | `label`, `description` |
| `indicator` | public indicator `identifier` (e.g. `E1-1`) | `title`, `indicator_name`, `description` |

(Other subject_kinds — standard_datapoint, unit, equivalence, mapping_assertion,
calculation_contract, hierarchy, api_*, error_code — are reserved in the store/registry and added in
later rollout slices.) Identifiers, ESRS/GRI/GHG/Sygris codes, enum values, unit symbols and
formulas are **never** translated.

## 1. Worklist (SDS → Atomizer)

Columns of `sds_translation_worklist.<kind>.csv`:

| column | meaning |
|---|---|
| `subject_kind` | `concept` / `indicator` |
| `subject_uri` | stable subject identity (above) |
| `field` | one of the localizable fields for that kind |
| `source_language` | declared source language (normally `en`) |
| `source_text` | the exact canonical text to translate |
| `source_hash` | SHA-256/NFC of `source_text` — bind the translation to THIS |
| `target_language` | requested target (when `--target-language` given) |
| `status` | current store status: `approved_current` / `stale` / `draft_only` / `missing` |

Atomizer should translate `missing` + `stale` rows and may skip `approved_current`.
`worklist_manifest.json` carries `package_contract_version`, `hash_algorithm`, and per-kind
`row_count` + `source_catalog_hash` (a snapshot id over all `(subject_uri, field, source_hash)`).

## 2. Translation package (Atomizer → SDS)

Each row of `sds_translations.csv` (consumed by `import_translations`):

| column | required | rule |
|---|---|---|
| `subject_kind` | yes | must equal the importer's kind |
| `subject_uri` | yes | must already exist in SDS (no auto-install) |
| `field` | yes | from the registered field set |
| `language` | yes | BCP-47 (normalized to lowercase for lookup) |
| `text` | yes | the translated text |
| `status` | yes | `approved` is public-eligible; `machine_draft`/`reviewed`/`draft` are preview-only |
| `source_language` | yes | declared source language |
| `source_hash` | yes | SHA-256/NFC of the source text the translation was made against |
| `translation_hash` | yes | SHA-256/NFC of `text` (importer re-verifies) |
| `source_type` | for approved | `official` / `human_reviewed` / `machine_assisted` / `customer_override` |
| `source_ref` | for approved | package/evidence pointer |
| `reviewer_ref` | for approved | reviewer/approval evidence |

`manifest.json` should carry package id/version, source catalog snapshot (the worklist
`source_catalog_hash`), source/target language, reviewer metadata, generation method, and checksums.

## Import gate (enforced by SDS, regardless of producer)

`import_translations` is idempotent and fail-closed:

- rejects rows whose `subject_kind` ≠ the import kind, and any unsupported `subject_kind`;
- rejects an unknown `subject_uri` against the **public-subject allowlist**: the caller's explicit
  `known_subject_uris`, or — by default — the set of subjects the API actually serves for that kind
  (`localization_worklist.public_subject_uris`). A translation is never stored for a subject the read
  path would not serve;
- rejects a `field` that is not in the registered set **for that `subject_kind`**
  (`concept={label,description}`, `indicator={title,indicator_name,description}`) — a concept row
  carrying an indicator-only field such as `title` is rejected, not silently stored;
- rejects `approved` rows without `source_ref` + `reviewer_ref`;
- treats empty evidence columns as absent: `source_type` / `source_ref` / `reviewer_ref` arriving as
  an empty string (a `machine_draft` row from the package CSV always carries the columns) are stored
  as `NULL`, so the row satisfies the DB CHECK constraints instead of inserting `''`;
- re-verifies `translation_hash == text_hash(text)`;
- rejects a duplicate active `approved` row for the same `(subject_uri, field, language)` — within
  the package and against the active DB row;
- supersedes a changed `approved` row by closing the prior effective range (append-only audit);
- is a no-op for an identical re-import;
- never auto-installs subjects or fabricates translations.

## Public serve rules

The `?lang` read path serves a field only from an `approved` row whose `source_hash` still matches
the current source text and whose effective interval is active; otherwise it falls back (stale →
source, missing → source) and reports it in the response `localization` metadata. AI-generated text
may be stored only as `machine_draft` and is never served publicly by default.
