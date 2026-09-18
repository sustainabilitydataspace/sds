"""Shared hierarchy fixture for operational value-import gates."""

from __future__ import annotations

from src.api.models import HierarchyConfiguration, HierarchyLevel
from src.services.hierarchy_store import DatabaseHierarchyStore

VALUE_IMPORT_GATE_COMPANY_ID = "sds_public_demo"
VALUE_IMPORT_GATE_HIERARCHY_ID = "sds-public-demo-org"
VALUE_IMPORT_GATE_ENTITY = "sds_public_demo_facility"


def build_value_import_gate_hierarchy() -> HierarchyConfiguration:
    return HierarchyConfiguration(
        id=VALUE_IMPORT_GATE_HIERARCHY_ID,
        company_id=VALUE_IMPORT_GATE_COMPANY_ID,
        hierarchy_type="organizational",
        name="SDS public-demo hierarchy",
        description="Disposable hierarchy used by the public synthetic value-import gates.",
        active=True,
        levels=[
            HierarchyLevel(id="demo_group", name="SDS Demo Group", level=0),
            HierarchyLevel(
                id="demo_region",
                name="SDS Demo Region",
                parent="demo_group",
                level=1,
            ),
            HierarchyLevel(
                id=VALUE_IMPORT_GATE_ENTITY,
                name="Gate Facility",
                parent="demo_region",
                level=2,
                metadata={"entity_type": "synthetic_demo_facility"},
            ),
        ],
    )


def ensure_value_import_gate_hierarchy(db, *, created_by: str) -> dict[str, str]:
    store = DatabaseHierarchyStore(db)
    config = build_value_import_gate_hierarchy()
    existing = store.get(VALUE_IMPORT_GATE_HIERARCHY_ID)
    if existing:
        if existing != config:
            store.update(VALUE_IMPORT_GATE_HIERARCHY_ID, config)
            return {"hierarchy_status": "updated"}
        return {"hierarchy_status": "unchanged"}

    store.create(
        config, config_id=VALUE_IMPORT_GATE_HIERARCHY_ID, created_by=created_by
    )
    return {"hierarchy_status": "created"}
