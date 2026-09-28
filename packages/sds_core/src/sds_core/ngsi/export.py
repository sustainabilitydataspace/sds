from __future__ import annotations

import re
from typing import Dict, Tuple

from sds_core.policy.selection import select_policy_for_dataset


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", (text or "").strip())[:96] or "item"


def dataset_entity(
    row: Dict[str, str],
    policy_registry: Dict[str, object],
    default_purpose: str = "reporting",
    default_region: str = "EU",
) -> Tuple[Dict[str, object], str]:
    ident = (row.get("identifier") or "").strip() or slug(row.get("title") or "")
    ds_id_slug = slug(ident.replace("urn:sds:reg:", ""))
    ds_ngsi_id = f"urn:ngsi-ld:Dataset:{ds_id_slug}"
    ind_label = (row.get("indicator") or "").strip()
    ind_id = f"urn:ngsi-ld:Indicator:{slug(ind_label)}" if ind_label else ""
    source_row_raw = (row.get("sourceRow") or "").strip()
    source_row: object = source_row_raw
    if source_row_raw.isdigit():
        source_row = int(source_row_raw)

    purpose = (row.get("purpose") or "").strip() or default_purpose
    region = (row.get("region") or "").strip() or default_region

    explicit_policy_id = (
        row.get("policyId") or row.get("policy_id") or ""
    ).strip() or None
    policy_id, additive = select_policy_for_dataset(
        policy_registry,
        purpose=purpose,
        region=region,
        explicit_policy_id=explicit_policy_id,
    )
    policy_urns = [f"urn:sds:policy:{policy_id}"] + [
        f"urn:sds:policy:{pid}" for pid in additive
    ]
    policy_object: object = policy_urns[0] if len(policy_urns) == 1 else policy_urns

    ent: Dict[str, object] = {
        "id": ds_ngsi_id,
        "type": "Dataset",
        "identifier": {"type": "Property", "value": ident},
        "title": {"type": "Property", "value": row.get("title", "")},
        "description": {"type": "Property", "value": row.get("description", "")},
        "dimension": {"type": "Property", "value": row.get("dimension", "")},
        "codeESRS": {"type": "Property", "value": row.get("codeESRS", "")},
        "codeGRI": {"type": "Property", "value": row.get("codeGRI", "")},
        "owner": {"type": "Property", "value": row.get("owner", "")},
        "accessRights": {"type": "Property", "value": row.get("accessRights", "")},
        "evidencePath": {"type": "Property", "value": row.get("evidencePath", "")},
        "sourceRow": {"type": "Property", "value": source_row},
        # Governance metadata
        "purpose": {"type": "Property", "value": purpose},
        "spatialCoverage": {"type": "Property", "value": region},
        "hasPolicy": {
            "type": "Relationship",
            "object": policy_object,
        },
        "conformsTo": {
            "type": "Property",
            "value": "SDS Governance Framework v1.0",
        },
    }
    if ind_id:
        ent["hasIndicator"] = {"type": "Relationship", "object": ind_id}
    return ent, ind_id


def indicator_entity(ind_id: str, label: str) -> Dict[str, object]:
    return {
        "id": ind_id,
        "type": "Indicator",
        "title": {"type": "Property", "value": label},
    }
