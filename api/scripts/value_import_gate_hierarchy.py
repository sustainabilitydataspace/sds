"""Shared hierarchy fixture for operational value-import gates."""

from __future__ import annotations

from src.api.models import HierarchyConfiguration, HierarchyLevel

VALUE_IMPORT_GATE_COMPANY_ID = "value_import_gate"
VALUE_IMPORT_GATE_HIERARCHY_ID = "value-import-gate-org"
VALUE_IMPORT_GATE_ENTITY = "gate_facility"
VALUE_IMPORT_GATE_CONCEPTS = (
    "urn:sds:reg:esrs:e3_4_01",
    "urn:sds:reg:esrs:e3_4_02",
)


def build_value_import_gate_hierarchy() -> HierarchyConfiguration:
    return HierarchyConfiguration(
        id=VALUE_IMPORT_GATE_HIERARCHY_ID,
        company_id=VALUE_IMPORT_GATE_COMPANY_ID,
        hierarchy_type="organizational",
        name="Value import gate hierarchy",
        description="Disposable hierarchy used by operational value-import gates.",
        active=True,
        levels=[
            HierarchyLevel(id="gate_group", name="Gate Group", level=0),
            HierarchyLevel(
                id="gate_spain",
                name="Gate Spain",
                parent="gate_group",
                level=1,
            ),
            HierarchyLevel(
                id=VALUE_IMPORT_GATE_ENTITY,
                name="Gate Facility",
                parent="gate_spain",
                level=2,
                metadata={"country": "ES", "entity_type": "facility"},
            ),
        ],
    )


def ensure_value_import_gate_hierarchy(db, *, created_by: str) -> dict[str, str]:
    from src.services.hierarchy_store import DatabaseHierarchyStore

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
