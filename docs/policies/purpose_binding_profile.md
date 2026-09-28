# Purpose Binding Profile (E6-007)

**Version:** 2026-01-27
**Status:** Active
**Standard:** ODRL 2.2 + SDS Extensions

## Overview

This profile defines how purpose limitation is enforced in the SDS dataspace. All data access requests must include a declared purpose that is validated against the controlled vocabulary and policy constraints.

## Controlled Vocabulary

| Purpose | Description | Allowed Actions | Default Policy |
|---------|-------------|-----------------|----------------|
| `reporting` | ESRS/GRI disclosures for consolidated filings | use, read, aggregate | policy-reporting-365d-retention |
| `supervisory` | Bilateral regulator requests (CNMV, EFRAG pilots) | use, read, aggregate, transfer | policy-supervisory-730d |
| `audit` | Internal/external audit, assurance, traceability | use, read | policy-audit-540d-trace |
| `research` | Aggregated/non-identifying analytics | use, read, aggregate, anonymize | policy-research-180d |
| `interop_testing` | Connector conformance and latency testing | use, read | policy-interop-30d |

## Enforcement Points

### 1. Contract Negotiation

Purpose must be declared in the contract negotiation request:

```json
{
  "@type": "ContractNegotiationRequest",
  "offer": {
    "@type": "Offer",
    "permission": [{
      "action": "use",
      "constraint": [{
        "leftOperand": "purpose",
        "operator": "eq",
        "rightOperand": "reporting"
      }]
    }]
  }
}
```

**Validation Rules:**
- Request MUST include `purpose` constraint
- Purpose value MUST be in controlled vocabulary
- Purpose MUST be allowed by the asset's policy

**Failure Response:**
```json
{
  "@type": "ContractNegotiationError",
  "code": "PURPOSE_REQUIRED",
  "message": "Contract negotiation requires a valid purpose declaration"
}
```

### 2. Access Request

Each data access request includes purpose in the credential or request context:

```json
{
  "@type": "AccessRequest",
  "credentials": [{
    "@type": "VerifiableCredential",
    "credentialSubject": {
      "purpose": "reporting",
      "organization": "did:web:acme.example.com"
    }
  }]
}
```

### 3. Audit Logging

All access events log the declared purpose:

```json
{
  "eventType": "access.granted",
  "action": {
    "type": "use",
    "purpose": "reporting"
  }
}
```

## Policy Integration

### ODRL Constraint Pattern

```json
{
  "leftOperand": "purpose",
  "operator": "eq",
  "rightOperand": "reporting"
}
```

### Multi-Purpose Permission

Some policies allow multiple purposes:

```json
{
  "leftOperand": "purpose",
  "operator": "isAnyOf",
  "rightOperand": ["reporting", "audit"]
}
```

## Target Verification Credential Binding

If VC trust enforcement is separately activated, the declared purpose must match
the scope in the actor's Verifiable Credential. This is not a current runtime
authorization control; see `../governance/trust_issuers.md`.

```json
{
  "@type": "VerifiableCredential",
  "credentialSubject": {
    "id": "did:web:acme.example.com:connector",
    "role": "data_consumer",
    "allowedPurposes": ["reporting", "audit"]
  }
}
```

## Error Codes

| Code | Description | Action |
|------|-------------|--------|
| `PURPOSE_REQUIRED` | No purpose in request | Reject negotiation |
| `PURPOSE_INVALID` | Purpose not in vocabulary | Reject negotiation |
| `PURPOSE_NOT_ALLOWED` | Purpose not permitted by policy | Reject negotiation |
| `PURPOSE_CREDENTIAL_MISMATCH` | Future VC enforcement: purpose not in VC scope | Reject access after VC activation |

## Audit Requirements

All purpose-related decisions must be logged:

1. **Negotiation Initiation** - Log requested purpose
2. **Purpose Validation** - Log validation result
3. **Access Decision** - Log purpose constraint evaluation
4. **Obligation Trigger** - Log purpose-specific duties

## References

- Policy Registry: `docs/policies/policy_registry.json`
- Audit Event Schema: `docs/governance/audit_event_schema.json`
- ODRL 2.2: https://www.w3.org/TR/odrl-model/
