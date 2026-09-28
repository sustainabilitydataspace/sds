# Data Product Terms (Policy Alignment v2026-01-27)

Use this reference when publishing the E1 dataset register under SDS connectors. Align to GDPR/Data Act obligations and attach the policy IDs defined in `policy_registry.md`.

## 1. Product
- Identifier: `urn:sds:dataproduct:e1:dataset-register:v2026-02-20`
- Title: E1 ESRS Dataset Register
- Description: Consolidated inventory of ESRS/GRI indicators with NGSI-LD-ready fields, derived from the controlled E1 inventory deliverable at `deliverables/E01-mapeo-datos-normativas/final/e01-inventario-datos-clave-variables-brutas-v2026-06-09.md`.

## 2. Provider & Contact
- Organization: Programa Espanol de Espacios de Datos Sostenibles (SDS Program Office)
- Contact: SDS Governance Board contact channel (published by the operating body)

## 3. Licensing & Access
- License: https://creativecommons.org/licenses/by/4.0/
- Access rights: restricted under the implemented SDS authentication,
  authorization and product-policy controls; VC issuer verification is not an
  active access path while trust enforcement is held.
- Authentication/authorization: SDS runtime authentication and authorization only. VC presentation (`OrgIdentityCredential` + `SDSRoleCredential`) is a target control and is not activated until the trust custody/provenance hold and fail-closed runtime verifier are completed; see `docs/governance/trust_issuers.md`.

## 4. Permitted Purposes
- reporting (primary) - annual ESRS/GRI filings
- audit - internal/external assurance on reported KPIs
- supervisory - regulator requests

Any additional purpose must be requested through the SDS Governance Board and encoded in a new policy ID before access is granted.

## 5. Usage Control Policies
- Policy ID: `policy-reporting-365d-retention`
  - Purpose == reporting; region IN {EU}; duty: LOG access events; retention <= P365D.
- Policy ID: `policy-audit-540d-trace`
  - Purpose == audit; duty: LOG + STORE provenance hashes; retention <= P540D.
- Policy ID: `policy-supervisory-730d`
  - Purpose == supervisory; duty: LOG + NOTIFY regulator; retention <= P730D.
- Policy ID: `policy-geofence-eu`
  - Region IN {EU}; prohibition: transfer outside EU unless policy `GLOBAL-ALLOWLIST` is also granted.
- Policy ID: `policy-no-analytics`
  - Prohibition: ANALYZE or aggregate beyond reporting scope; enforces read-only usage in calculators.

## 6. Data Protection & Legal
- GDPR lawful basis: Art. 6(1)(c) (legal obligation) for reporting, Art. 6(1)(f) (legitimate interest) for audit, Art. 6(1)(e) (public interest) for supervisory cases.
- Data minimization: dataset contains indicator metadata only (no personal data); provenance is maintained in the controlled E1 inventory deliverable and linked E6 evidence artifacts.
- Retention: default P365D; longer retention requires a supervisory policy and explicit audit-trail justification.

## 7. Audit & Monitoring
- Access logs capture timestamp, connector DID, subject DID, purpose, policy IDs, contract ID, and dataset hash.
- Policy decision logs are retained for >=P365D (reporting) or the highest duty requirement attached to the contract.
- Local audit evidence can be generated on demand with `scripts/e6_generate_evidence.py` when required for review or assurance.

## 8. Versioning
- Terms version: 1.2.0
- Change log:
  - 1.2.0 - align with policy registry v2026-01-27 and the E6 governance evidence set.
  - 1.1.0 - added supervisory clause.
  - 1.0.0 - initial publication.
