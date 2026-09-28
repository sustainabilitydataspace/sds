# SDS Governance Operating Model (RACI)

**Version:** 2026-01-27 (E6-001)
**Status:** Active
**Owner:** SDS Governance Board

## Overview

This document defines the operating model for SDS governance, including roles, responsibilities, and decision rights for all governance artifacts.

## Roles

| Role | Description | Typical Holder |
|------|-------------|----------------|
| **DSAu** | Data Space Authority - Ultimate governance authority | SDS Governance Board |
| **DPO** | Data Protection Officer - Privacy compliance | Legal/Compliance Team |
| **SecOps** | Security Operations - Technical security | Platform Team |
| **DevOps** | Development Operations - Deployment & CI/CD | Engineering Team |
| **Auditor** | Internal/External Audit - Compliance verification | Audit Function |
| **Participant** | Dataspace participant organization | Member Organizations |

## RACI Matrix

### Governance Artifacts

| Artifact | Create | Review | Approve | Maintain | Audit |
|----------|--------|--------|---------|----------|-------|
| Policy Registry (`policy_registry.json`) | DevOps | DPO, SecOps | DSAu | DevOps | Auditor |
| Trust Enforcement Status (`trust_issuers.json`) | SecOps | DPO | DSAu | SecOps | Auditor |
| Legal Binding Matrix (`legal_binding_matrix.csv`) | DPO | Legal | DSAu | DPO | Auditor |
| Audit Event Schema (`audit_event_schema.json`) | DevOps | SecOps, Auditor | DSAu | DevOps | Auditor |
| Data Product Terms (`data_product_terms.md`) | DevOps | DPO, Legal | DSAu | DevOps | Auditor |
| EDC Bundle (`configs/edc/*`) | DevOps | SecOps | DevOps | DevOps | Auditor |

**Legend:** R = Responsible, A = Accountable, C = Consulted, I = Informed

| Artifact | DSAu | DPO | SecOps | DevOps | Auditor | Participant |
|----------|------|-----|--------|--------|---------|-------------|
| Policy Registry | A | C | C | R | I | I |
| Trust Enforcement Status | A | C | R | C | I | I |
| Legal Binding Matrix | A | R | C | C | I | I |
| Audit Event Schema | A | C | C | R | C | I |
| Data Product Terms | A | C | I | R | I | I |
| EDC Bundle | I | I | C | R/A | I | I |

### Operational Processes

| Process | DSAu | DPO | SecOps | DevOps | Auditor | Participant |
|---------|------|-----|--------|--------|---------|-------------|
| Participant Onboarding | A | C | R | C | I | R |
| Participant Offboarding | A | C | R | C | I | I |
| Policy Change Request | A | C | C | R | I | C |
| Emergency Policy Change | A | I | R | R | I | I |
| Trust Issuer Addition | A | C | R | C | I | I |
| Trust Issuer Revocation | A | I | R | I | I | I |
| Audit Log Review | I | I | C | C | R | I |
| Incident Response | A | C | R | R | I | I |
| Dispute Resolution | R/A | C | I | I | I | C |
| Conformance Testing | I | I | C | R | C | C |

## Change Control

### Standard Change Process

1. **Request** - Requester submits change request via issue tracker
2. **Triage** - DevOps assesses impact and routes to reviewers
3. **Review** - Consulted parties review within SLA
4. **Approve** - Accountable party approves or rejects
5. **Implement** - Responsible party implements change
6. **Verify** - `make e6-check` passes
7. **Deploy** - Change merged to main branch
8. **Notify** - Informed parties notified

### Emergency Change Process

For security incidents or critical compliance issues:

1. **Detect** - Issue identified by any party
2. **Escalate** - Immediate escalation to SecOps + DSAu
3. **Mitigate** - SecOps implements temporary mitigation
4. **Document** - Change documented post-hoc within 24 hours
5. **Review** - Post-incident review within 5 business days

### Change SLAs

| Change Type | Review SLA | Approval SLA | Implementation SLA |
|-------------|------------|--------------|-------------------|
| Standard | 5 business days | 2 business days | 3 business days |
| Expedited | 2 business days | 1 business day | 1 business day |
| Emergency | Immediate | Immediate | Immediate |

## Versioning

All governance artifacts follow semantic versioning with date stamps:

- **Version format:** `YYYY-MM-DD` for documents, `vX.Y.Z` for schemas
- **Breaking changes:** Require DSAu approval and 30-day notice
- **Non-breaking changes:** Require standard approval flow
- **Patches:** Can be expedited with SecOps approval

### Rollback Procedure

1. Identify rollback target version in git history
2. Create rollback branch
3. Run `make e6-check` to verify rollback state
4. Obtain emergency approval from DSAu or SecOps
5. Merge rollback branch
6. Regenerate EDC bundle
7. Notify affected participants

## Incident Response

### Severity Levels

| Level | Description | Response Time | Escalation |
|-------|-------------|---------------|------------|
| **P0** | Sovereignty breach, data exposure | 15 minutes | DSAu + DPO + SecOps |
| **P1** | Policy bypass, access control failure | 1 hour | SecOps + DevOps |
| **P2** | Compliance gap, audit finding | 4 hours | DPO + DevOps |
| **P3** | Documentation issue, non-critical bug | 1 business day | DevOps |

### Incident Workflow

```
Detect → Triage → Contain → Investigate → Remediate → Review → Close
```

### Evidence Preservation

During incidents:
- Audit logs must be preserved for minimum 730 days
- Policy states must be snapshot before remediation
- All communications must be logged with timestamps

## Dispute Resolution

### Process

1. **Filing** - Participant files dispute via formal channel
2. **Acknowledgment** - DSAu acknowledges within 2 business days
3. **Investigation** - Evidence gathered from all parties
4. **Mediation** - Attempt resolution between parties
5. **Adjudication** - DSAu renders decision if mediation fails
6. **Appeal** - One appeal allowed within 10 business days
7. **Final Decision** - DSAu final decision is binding

### Dispute Categories

| Category | First Resolution | Escalation |
|----------|-----------------|------------|
| Access denial | DevOps | SecOps → DSAu |
| Policy interpretation | DPO | DSAu |
| Data quality | Participant | DevOps → DSAu |
| Contract terms | Legal | DSAu |

## Monitoring & Reporting

### Governance KPIs

| KPI | Target | Measurement |
|-----|--------|-------------|
| Policy compliance rate | ≥99% | Audit log analysis |
| Change request SLA compliance | ≥95% | Issue tracker metrics |
| Incident response SLA compliance | ≥99% | Incident log analysis |
| Audit finding closure rate | 100% within 30 days | Audit tracker |

### Reporting Schedule

| Report | Frequency | Audience | Owner |
|--------|-----------|----------|-------|
| Governance Dashboard | Weekly | DSAu, DPO | DevOps |
| Compliance Summary | Monthly | All stakeholders | DPO |
| Audit Status | Quarterly | DSAu, Auditor | Auditor |
| Annual Governance Review | Annually | All stakeholders | DSAu |

## References

- E6 Deliverable: `deliverables/E06-governance/final/e06-gobernanza-politica-datos-v2026-06-09.md`
- Policy Registry: `docs/policies/policy_registry.json`
- Acceptance Gates: `docs/quality/acceptance_gates.md`
