# Policy Registry (v2025-11-12)

Canonical list of purposes, regions, retention envelopes, and policy IDs referenced by role VCs, usage control clauses, and data product terms.

## Purposes (controlled vocabulary)
| Purpose | Description | Legal basis | Default policyId |
|---------|-------------|-------------|------------------|
| reporting | ESRS/GRI disclosures for consolidated filings | GDPR Art. 6(1)(c) + Data Act obligations | policy-reporting-365d-retention |
| supervisory | Bilateral regulator requests (CNMV, EFRAG pilots) | GDPR Art. 6(1)(e) public interest | policy-supervisory-730d |
| audit | Internal/external audit, assurance, traceability | GDPR Art. 6(1)(f) legitimate interest | policy-audit-540d-trace |
| research | Aggregated/non-identifying analytics aligned to contract | GDPR Art. 6(1)(f) + contract | policy-research-180d |
| interop_testing | Connector conformance and latency testing | Contract (B2B) | policy-interop-30d |

Additions require SDS Governance Board approval and alignment with VC schema scopes.

## Regions / Geofences
| Region code | Description | Notes |
|-------------|-------------|-------|
| EU | EEA/GDPR scope (default) | Use for datasets containing personal/derived data; aligns to EU Data Act |
| ES | Spain-only handling (AEPD guidance) | Subset of EU; use for pilot municipal datasets |
| LATAM | Mexico, Chile, Brazil pilot nodes | Only for aggregated indicators without personal data |
| GLOBAL-ALLOWLIST | EU + trusted partners (Canada, Japan) | Requires explicit contractual clause referencing adequacy |

## Retention Options
| Code | Duration | Usage guidance |
|------|----------|----------------|
| P30D | 30 days | Telemetry, interop testing buffers |
| P90D | 90 days | Short-term research + QA |
| P180D | 180 days | Default for aggregated research exports |
| P365D | 365 days | Reporting baseline; aligns to annual filings |
| P540D | 540 days | Audit trail retention; requires audit purpose |
| P730D | 730 days | Supervisory archives; regulator mandate only |

## Policy IDs
| Policy ID | Constraints & duties | Applies to |
|-----------|---------------------|-----------|
| policy-reporting-365d-retention | purpose==reporting; region IN {EU}; duty: LOG; retention<=P365D | ESRS filings, E1 baseline exports |
| policy-supervisory-730d | purpose==supervisory; region IN {EU, ES}; duty: LOG+NOTIFY regulator; retention<=P730D | Regulator data calls |
| policy-audit-540d-trace | purpose==audit; duty: LOG hash chain + store provenance; retention<=P540D | Internal/external assurance |
| policy-research-180d | purpose==research; region IN {EU, LATAM}; duty: AGGREGATE_ONLY; retention<=P180D | De-identified research shares |
| policy-interop-30d | purpose==interop_testing; region==GLOBAL-ALLOWLIST; duty: DELETE after retention; retention<=P30D | Connector conformance |
| policy-geofence-eu | region IN {EU}; prohibition: transfer outside EU unless GLOBAL-ALLOWLIST + SCC | Attach alongside other policies |
| policy-no-analytics | prohibition: ANALYZE; enforcement trigger for datasets restricted to reporting-only pipelines | Sensitive KPIs |

## Notes
- Keep policy IDs stable; reference them verbatim in VCs and contract artifacts.
- Validate every policy in pre‑production EDC/IDS environments before rollout; attach duty logs to `data/extracted/analysis/` when activated.

## Templates (ODRL-style)

- Purpose restriction
  - Action: use
  - Constraint: purpose IN {"reporting", "audit"}
  - Duty: log_access WITH fields {subject, time, purpose}
  - Legal basis: GDPR Art. 6(1)(c) legal obligation (when applicable)

- Retention limit
  - Action: store
  - Constraint: retention_days <= 365
  - Duty: delete_after(retention_days)
  - Legal basis: GDPR Art. 5(1)(e)

- Geofence (EU-only processing)
  - Action: process
  - Constraint: processing_location IN {EU}
  - Duty: record_location; block_if(outside_geofence)

Example (JSON)
```
{
  "policyId": "sds:policy:use-regulatory-eu",
  "rules": [
    { "action": "use", "constraints": { "purpose": ["reporting"] }, "duties": ["log_access"] },
    { "action": "store", "constraints": { "retention_days": 365 }, "duties": ["delete_after"] },
    { "action": "process", "constraints": { "processing_location": ["EU"] }, "duties": ["record_location", "block_if_outside"] }
  ]
}
```
