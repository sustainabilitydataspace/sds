# Retention Obligation Enforcement (E6-008)

**Version:** 2026-01-27
**Status:** Active
**Legal Basis:** GDPR Art. 5(1)(e) - Storage Limitation

## Overview

This profile defines how retention obligations are enforced in the SDS dataspace. Data must be deleted after the retention period expires, with evidence events logged for compliance.

## Retention Periods

| Code | Duration | ISO 8601 | Use Case |
|------|----------|----------|----------|
| P30D | 30 days | P30D | Interop testing, telemetry |
| P90D | 90 days | P90D | Short-term QA |
| P180D | 180 days | P180D | Research aggregates |
| P365D | 365 days | P365D | Annual reporting |
| P540D | 540 days | P540D | Audit trails |
| P730D | 730 days | P730D | Supervisory archives |

## ODRL Retention Duty

Retention is expressed as a duty with elapsed time constraint:

```json
{
  "action": "delete",
  "constraint": [{
    "leftOperand": "elapsedTime",
    "operator": "lteq",
    "rightOperand": "P365D"
  }]
}
```

## Enforcement Architecture

### 1. Contract Creation

When a contract is agreed, the retention timer is initialized:

```
Contract Signed (T0)
    │
    ├─► Store retention metadata
    │     - contractId
    │     - assetId
    │     - policyId
    │     - retentionPeriod (ISO 8601)
    │     - expiryTimestamp (T0 + period)
    │
    └─► Schedule retention check
```

### 2. Retention Timer

Retention timers run as scheduled jobs:

```python
class RetentionTimer:
    contract_id: str
    asset_id: str
    policy_id: str
    retention_period: str  # ISO 8601 duration
    created_at: datetime
    expires_at: datetime
    status: str  # active, expired, deleted

    def check_expiry(self) -> bool:
        return datetime.utcnow() >= self.expires_at
```

### 3. Deletion Execution

When retention expires:

```
Expiry Detected
    │
    ├─► Verify no active contracts referencing asset
    │
    ├─► Execute deletion
    │     - Remove data from storage
    │     - Invalidate access tokens
    │     - Update asset status
    │
    ├─► Generate evidence event
    │     - eventType: retention.expired
    │     - correlationIds: {contractId, assetId, policyId}
    │     - outcome: {status: success, action: delete}
    │
    └─► Archive evidence for audit
```

## Evidence Events

### Retention Expiry Event

```json
{
  "eventId": "uuid",
  "eventType": "retention.expired",
  "timestamp": "2026-01-27T10:30:00Z",
  "correlationIds": {
    "contractId": "contract-123",
    "assetId": "asset-esrs-e1",
    "policyId": "policy-reporting-365d-retention",
    "obligationId": "duty-delete-p365d"
  },
  "actor": {
    "type": "system",
    "id": "sds-retention-service"
  },
  "action": {
    "type": "delete",
    "target": "urn:sds:asset:esrs-e1",
    "parameters": {
      "retentionPeriod": "P365D",
      "contractCreatedAt": "2025-01-27T10:30:00Z",
      "expiryTimestamp": "2026-01-27T10:30:00Z"
    }
  },
  "outcome": {
    "status": "success",
    "policyDecision": "obligation_fulfilled"
  },
  "integrity": {
    "algorithm": "SHA-256",
    "previousHash": "abc123...",
    "eventHash": "def456..."
  }
}
```

### Deletion Execution Event

```json
{
  "eventId": "uuid",
  "eventType": "deletion.executed",
  "timestamp": "2026-01-27T10:30:01Z",
  "correlationIds": {
    "contractId": "contract-123",
    "assetId": "asset-esrs-e1",
    "retentionEventId": "previous-event-uuid"
  },
  "action": {
    "type": "delete",
    "target": "urn:sds:storage:asset-esrs-e1"
  },
  "outcome": {
    "status": "success",
    "bytesDeleted": 1048576,
    "recordsDeleted": 1500
  }
}
```

## Policy Integration

### Example Policy with Retention

```json
{
  "uid": "policy-reporting-365d-retention",
  "odrl": {
    "permission": [{
      "action": "use",
      "constraint": [{
        "leftOperand": "purpose",
        "operator": "eq",
        "rightOperand": "reporting"
      }],
      "duty": [{
        "action": "delete",
        "constraint": [{
          "leftOperand": "elapsedTime",
          "operator": "lteq",
          "rightOperand": "P365D"
        }]
      }]
    }]
  }
}
```

## Compliance Verification

### KPIs

| KPI | Target | Measurement |
|-----|--------|-------------|
| Retention compliance rate | 100% | Deletion within 24h of expiry |
| Evidence coverage | 100% | All deletions have evidence events |
| Audit trail integrity | 100% | Hash chain verification passes |

### Auditor Queries

1. **Show all data retained beyond policy period**
   ```sql
   SELECT * FROM retention_timers
   WHERE status = 'active' AND expires_at < NOW()
   ```

2. **Verify deletion evidence for contract**
   ```sql
   SELECT * FROM audit_events
   WHERE eventType = 'deletion.executed'
   AND correlationIds->>'contractId' = ?
   ```

3. **Check retention compliance by policy**
   ```sql
   SELECT policyId, COUNT(*) as total,
          SUM(CASE WHEN deleted_on_time THEN 1 ELSE 0 END) as compliant
   FROM retention_timers
   GROUP BY policyId
   ```

## Error Handling

| Scenario | Action | Evidence |
|----------|--------|----------|
| Deletion fails | Retry 3x, then alert | Log failure events |
| Active contract | Defer deletion | Log deferral reason |
| Storage unavailable | Queue for retry | Log infrastructure error |
| Hash chain break | Halt + alert | Incident event |

## GDPR Alignment

- **Art. 5(1)(e)**: Data kept no longer than necessary
- **Art. 17**: Right to erasure (automated via retention)
- **Art. 30**: Records of processing (audit events)
- **Art. 33**: Breach notification (integrity failures)

## References

- Audit Event Schema: `docs/governance/audit_event_schema.json`
- Policy Registry: `docs/policies/policy_registry.json`
- Operating Model: `docs/governance/operating_model_raci.md`
