# SDS Semantics Bundle (canonical)

This folder is the **canonical machine‑readable semantics bundle** for Sustainability Data Spaces (SDS).

It contains:
- JSON‑LD contexts used by NGSI‑LD exports and services
- SHACL shapes used as contract/validation artifacts
- JSON Schema used to validate the E1 dataset register structure

**Governance / versioning policy:** managed with the canonical SDS semantic assets and public technical documentation in this repository.

## Structure

- `semantics/context/`
  - `ngsi_ld_context.jsonld` — local/dev context reference (used by scripts in dev mode)
  - `sds/v1.0.jsonld` — publishable, versioned SDS context content (mirrors the publish URL)
- `semantics/shacl/`
  - `shapes_register_shacl.ttl`, `shapes_indicator_shacl.ttl`
- `semantics/schema/`
  - `register_schema.json`
