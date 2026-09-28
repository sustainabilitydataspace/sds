# Usage Control Examples (ODRL/EDC Policy Snippets)

These examples express common constraints and duties as ODRL-like policies and as Eclipse Dataspace Components (EDC) policy JSON. Adapt attributes to your deployment (purpose, retention, regions, endpoints).

## 1) Allow for reporting purpose with audit and retention (365 days)

ODRL-style (illustrative)
```
policy:
  permission:
    action: use
    constraint:
      - leftOperand: purpose
        operator: eq
        rightOperand: reporting
  duty:
    - action: logUse
      constraint:
        - leftOperand: eventType
          operator: eq
          rightOperand: access
    - action: deleteAfter
      constraint:
        - leftOperand: retention
          operator: lte
          rightOperand: P365D
```

EDC policy (JSON)
```
{
  "@type": "Policy",
  "permissions": [
    {
      "edctype": "dataspaceconnector:permission",
      "target": "urn:sds:dataproduct:example",
      "action": { "type": "USE" },
      "constraints": [
        {
          "edctype": "AtomicConstraint",
          "leftExpression": { "edctype": "LiteralExpression", "value": "purpose" },
          "operator": "EQ",
          "rightExpression": { "edctype": "LiteralExpression", "value": "reporting" }
        }
      ],
      "duties": [
        {
          "edctype": "dataspaceconnector:duty",
          "action": { "type": "LOG" },
          "constraints": [
            {
              "edctype": "AtomicConstraint",
              "leftExpression": { "edctype": "LiteralExpression", "value": "retention" },
              "operator": "LTE",
              "rightExpression": { "edctype": "LiteralExpression", "value": "P365D" }
            }
          ]
        }
      ]
    }
  ]
}
```

## 2) Geofence: EU-only access

ODRL-style
```
permission:
  action: use
  constraint:
    - leftOperand: region
      operator: in
      rightOperand: [EU]
```

EDC policy
```
{
  "@type": "Policy",
  "permissions": [
    {
      "edctype": "dataspaceconnector:permission",
      "action": { "type": "USE" },
      "constraints": [
        {
          "edctype": "AtomicConstraint",
          "leftExpression": { "edctype": "LiteralExpression", "value": "region" },
          "operator": "IN",
          "rightExpression": { "edctype": "LiteralExpression", "value": "EU" }
        }
      ]
    }
  ]
}
```

## 3) Deny analytics usage

ODRL-style
```
prohibition:
  action: analyze
```

EDC policy
```
{
  "@type": "Policy",
  "prohibitions": [
    {
      "edctype": "dataspaceconnector:prohibition",
      "action": { "type": "ANON_ANALYZE" }
    }
  ]
}
```

Notes
- Align attribute names (purpose, retention, region) to your policy registry. Keep a catalog of allowed purpose values.
- Log obligations must be connected to your audit pipeline; ensure timestamps, subject, purpose, and contract IDs are recorded.
- Combine multiple constraints/duties as needed; test in pre‑prod connectors before rollout.
