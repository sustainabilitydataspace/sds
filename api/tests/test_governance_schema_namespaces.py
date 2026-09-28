from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_active_governance_schema_ids_use_canonical_sds_domain():
    schema_paths = [
        REPO_ROOT / "governance" / "policies" / "policy_registry_schema.json",
        REPO_ROOT / "docs" / "policies" / "policy_registry_schema.json",
        REPO_ROOT / "governance" / "audit" / "audit_event_schema.json",
        REPO_ROOT / "docs" / "governance" / "audit_event_schema.json",
        REPO_ROOT / "docs" / "governance" / "trust_issuers_schema.json",
    ]

    for path in schema_paths:
        doc = json.loads(path.read_text(encoding="utf-8"))
        schema_id = doc.get("$id", "")
        assert schema_id.startswith("https://sustainabilitydataspace.com/schema/sds/")
        assert "sds.gob.es" not in schema_id
