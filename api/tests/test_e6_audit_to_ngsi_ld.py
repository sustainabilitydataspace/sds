from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "e6_audit_to_ngsi_ld.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("e6_audit_to_ngsi_ld", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_e6_audit_ngsi_context_uses_canonical_sds_namespace(tmp_path, monkeypatch):
    module = _load_module()

    source = tmp_path / "events.json"
    out = tmp_path / "events.jsonld"
    source.write_text(
        json.dumps(
            {
                "eventId": "evt-1",
                "eventType": "policy.decision",
                "timestamp": "2026-05-22T00:00:00Z",
                "correlationIds": {"policyId": "policy-reporting"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "e6_audit_to_ngsi_ld.py",
            "--input",
            str(source),
            "--out",
            str(out),
        ],
    )

    assert module.main() == 0

    payload = json.loads(out.read_text(encoding="utf-8"))
    local_context = payload["@context"][1]
    assert local_context["AuditEvent"] == f"{module.SDS_ONTOLOGY_NAMESPACE}AuditEvent"
    assert local_context["eventId"] == f"{module.SDS_ONTOLOGY_NAMESPACE}eventId"
    assert local_context["hasPolicy"] == {"@id": "odrl:hasPolicy", "@type": "@id"}
    assert "https://sds.gob.es/ontology#" not in json.dumps(payload)
