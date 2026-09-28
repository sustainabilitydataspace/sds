# EDC Bundle (Generated)

Generated: 2026-06-08T18:19:07.353352+00:00
Policy Registry: docs\policies\policy_registry.json (v2026-01-27)
Deny-by-Default: True

## Security

This bundle enforces **deny-by-default** policy semantics:
- Default deny policy: `policy-deny-all`
- No allow-all policies permitted
- All permissions require explicit constraints

## Files

- `assets.json` - 1805 asset definitions
- `policies.json` - 8 policy definitions
- `contract-definitions.json` - 1805 contract definitions

## Policy Usage

| Policy | Datasets |
|--------|----------|
| `policy-reporting-365d-retention` | 1805 |

## Usage

POST these JSONs to EDC Data Management API in order:
1. `assets.json` -> POST /management/v2/assets
2. `policies.json` -> POST /management/v2/policydefinitions
3. `contract-definitions.json` -> POST /management/v2/contractdefinitions

Ensure consumers provide purpose/region claims compatible with policies.