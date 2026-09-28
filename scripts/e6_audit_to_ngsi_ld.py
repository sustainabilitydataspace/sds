#!/usr/bin/env python3
"""
Audit Event → NGSI-LD Export (E6-010)

Converts SDS audit events to NGSI-LD entities for federation
and interoperability with FIWARE-based systems.

Version: 2026-01-27 (E6-010 audit interoperability)
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

SDS_ONTOLOGY_NAMESPACE = "https://sustainabilitydataspace.com/ontology#"


def slug(text: str) -> str:
    """Create URL-safe slug from text."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", (text or "").strip())[:96] or "item"


def sds_term(local_name: str) -> str:
    return f"{SDS_ONTOLOGY_NAMESPACE}{local_name}"


def audit_event_to_ngsi_ld(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert an audit event to NGSI-LD AuditEvent entity.

    Maps audit event schema fields to NGSI-LD properties and relationships.
    """
    event_id = event.get("eventId", "")
    event_type = event.get("eventType", "")
    ngsi_id = f"urn:ngsi-ld:AuditEvent:{slug(event_id)}"

    # Build the entity
    entity: Dict[str, Any] = {
        "id": ngsi_id,
        "type": "AuditEvent",
        "eventId": {"type": "Property", "value": event_id},
        "eventType": {"type": "Property", "value": event_type},
        "timestamp": {
            "type": "Property",
            "value": {
                "@type": "DateTime",
                "@value": event.get("timestamp", ""),
            },
        },
    }

    # Correlation IDs as properties
    correlation_ids = event.get("correlationIds", {})
    if correlation_ids:
        entity["correlationIds"] = {
            "type": "Property",
            "value": correlation_ids,
        }

        # Create relationships for key correlation IDs
        if correlation_ids.get("contractId"):
            entity["hasContract"] = {
                "type": "Relationship",
                "object": f"urn:ngsi-ld:Contract:{slug(correlation_ids['contractId'])}",
            }
        if correlation_ids.get("policyId"):
            entity["hasPolicy"] = {
                "type": "Relationship",
                "object": f"urn:sds:policy:{correlation_ids['policyId']}",
            }
        if correlation_ids.get("assetId"):
            entity["refersToAsset"] = {
                "type": "Relationship",
                "object": f"urn:ngsi-ld:Dataset:{slug(correlation_ids['assetId'])}",
            }

    # Actor as property with nested structure
    actor = event.get("actor", {})
    if actor:
        entity["actor"] = {
            "type": "Property",
            "value": {
                "actorType": actor.get("type", ""),
                "actorId": actor.get("id", ""),
                "organization": actor.get("organization", ""),
                "role": actor.get("role", ""),
            },
        }
        # Create relationship to actor
        if actor.get("id"):
            entity["performedBy"] = {
                "type": "Relationship",
                "object": actor["id"]
                if actor["id"].startswith("urn:")
                else f"urn:ngsi-ld:Agent:{slug(actor['id'])}",
            }

    # Action as property
    action = event.get("action", {})
    if action:
        entity["action"] = {
            "type": "Property",
            "value": {
                "actionType": action.get("type", ""),
                "purpose": action.get("purpose", ""),
                "target": action.get("target", ""),
            },
        }

    # Outcome as property
    outcome = event.get("outcome", {})
    if outcome:
        entity["outcome"] = {
            "type": "Property",
            "value": {
                "status": outcome.get("status", ""),
                "reason": outcome.get("reason", ""),
                "policyDecision": outcome.get("policyDecision", ""),
            },
        }

    # Context as property
    context = event.get("context", {})
    if context:
        entity["eventContext"] = {
            "type": "Property",
            "value": context,
        }
        # GeoProperty for location if available
        if context.get("geoLocation"):
            entity["location"] = {
                "type": "Property",
                "value": context["geoLocation"],
            }

    # Integrity as property (important for audit chain)
    integrity = event.get("integrity", {})
    if integrity:
        entity["integrity"] = {
            "type": "Property",
            "value": {
                "algorithm": integrity.get("algorithm", ""),
                "previousHash": integrity.get("previousHash", ""),
                "eventHash": integrity.get("eventHash", ""),
            },
        }
        # Link to previous event in chain
        if integrity.get("previousHash") and integrity["previousHash"] != "genesis":
            # Note: In practice, you'd need to resolve hash to event ID
            entity["previousEvent"] = {
                "type": "Relationship",
                "object": f"urn:ngsi-ld:AuditEvent:hash-{integrity['previousHash'][:16]}",
            }

    # Metadata as property
    metadata = event.get("metadata", {})
    if metadata:
        entity["metadata"] = {
            "type": "Property",
            "value": metadata,
        }

    return entity


def load_audit_events(input_path: Path) -> List[Dict[str, Any]]:
    """
    Load audit events from file.

    Supports:
    - Single JSON file with one event
    - JSON file with "events" array
    - JSONL file with one event per line
    """
    events = []

    if input_path.suffix == ".jsonl":
        # JSONL format
        with open(input_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    else:
        # JSON format
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                events = data
            elif isinstance(data, dict):
                if "events" in data:
                    events = data["events"]
                else:
                    events = [data]

    return events


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Convert SDS audit events to NGSI-LD entities"
    )
    data_root = os.environ.get("DATA_ROOT", "data")
    extracted = Path(os.environ.get("DATA_EXTRACTED", os.path.join(data_root, "extracted")))

    ap.add_argument(
        "--input",
        required=True,
        help="Input JSON/JSONL file with audit events",
    )
    ap.add_argument(
        "--out",
        default=str(extracted / "analysis/ngsi_ld/audit_events.jsonld"),
        help="Output JSON-LD containing NGSI-LD entities",
    )
    ap.add_argument(
        "--context-url",
        default="https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld",
        help="NGSI-LD core context URL",
    )
    args = ap.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        return 1

    # Load and convert events
    events = load_audit_events(input_path)
    print(f"Loaded {len(events)} audit events from {input_path}")

    entities = []
    event_types: Dict[str, int] = {}

    for event in events:
        entity = audit_event_to_ngsi_ld(event)
        entities.append(entity)

        # Track event types
        evt_type = event.get("eventType", "unknown")
        event_types[evt_type] = event_types.get(evt_type, 0) + 1

    # Build output with context
    payload = {
        "@context": [
            args.context_url,
            {
                "AuditEvent": sds_term("AuditEvent"),
                "eventId": sds_term("eventId"),
                "eventType": sds_term("eventType"),
                "correlationIds": sds_term("correlationIds"),
                "hasContract": {"@id": sds_term("hasContract"), "@type": "@id"},
                "hasPolicy": {"@id": "odrl:hasPolicy", "@type": "@id"},
                "refersToAsset": {"@id": sds_term("refersToAsset"), "@type": "@id"},
                "performedBy": {"@id": sds_term("performedBy"), "@type": "@id"},
                "previousEvent": {"@id": sds_term("previousEvent"), "@type": "@id"},
                "actor": sds_term("actor"),
                "action": sds_term("action"),
                "outcome": sds_term("outcome"),
                "eventContext": sds_term("eventContext"),
                "integrity": sds_term("integrity"),
                "odrl": "http://www.w3.org/ns/odrl/2/",
            },
        ],
        "@graph": entities,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote NGSI-LD entities: {out_path} ({len(entities)} entities)")
    print("Event type distribution:")
    for evt_type, count in sorted(event_types.items(), key=lambda x: -x[1]):
        print(f"  - {evt_type}: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
