from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from src.config.settings import settings
from src.database.models import ESGValue, HierarchyConfiguration
from src.services.value_store import DatabaseValueStore


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


def _session():
    engine = create_engine("sqlite:///:memory:")
    for table in (HierarchyConfiguration.__table__, ESGValue.__table__):
        table.create(engine)
    return sessionmaker(bind=engine)()


def _hierarchy(*, config_id: str, company_id: str, entity_id: str):
    return HierarchyConfiguration(
        id=config_id,
        company_id=company_id,
        hierarchy_type="organizational",
        name=f"{company_id} hierarchy",
        configuration=json.dumps(
            {
                "id": config_id,
                "company_id": company_id,
                "hierarchy_type": "organizational",
                "name": f"{company_id} hierarchy",
                "active": True,
                "levels": [
                    {"id": f"{company_id}-root", "name": "Root", "level": 0},
                    {
                        "id": entity_id,
                        "name": entity_id,
                        "parent": f"{company_id}-root",
                        "level": 1,
                    },
                ],
            }
        ),
        is_active=True,
    )


def _value(*, value_id: str, entity: str, amount: str):
    return ESGValue(
        id=value_id,
        tenant_id=None,
        ownership_state="quarantined",
        concept="syg:Water_Cooling",
        entity=entity,
        period=date(2026, 12, 31),
        external_key=f"erp:{value_id}",
        value=Decimal(amount),
        value_type="numeric",
        unit="m3",
        value_metadata={"source": "tenant-isolation-test"},
    )


def test_db_backed_legacy_paths_fail_closed_even_with_company_hierarchies(
    monkeypatch,
):
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "legacy")
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", False)

    db = _session()
    db.add_all(
        [
            _hierarchy(
                config_id="company-a-hierarchy",
                company_id="company-a",
                entity_id="plant-a",
            ),
            _hierarchy(
                config_id="company-b-hierarchy",
                company_id="company-b",
                entity_id="plant-b",
            ),
            _value(value_id="value-a", entity="plant-a", amount="1.0"),
            _value(value_id="value-b", entity="plant-b", amount="2.0"),
        ]
    )
    db.commit()

    store = DatabaseValueStore(db, tenant_id="company-a")

    filters = {
        "concept": None,
        "entity": None,
        "period_start": None,
        "period_end": None,
        "unit": None,
        "changed_since": None,
    }
    operations = [
        lambda: store.list(**filters, limit=10, offset=0),
        lambda: store.list_cursor(**filters, limit=10, cursor=None),
        lambda: store.list_changes(**filters, limit=10, cursor=None),
        lambda: list(store.iterate(**filters)),
    ]
    # Neither same-company nor foreign-company legacy rows are a fallback for
    # governed revision reads. Hierarchy membership cannot authorize that path.
    for value_id, entity in (("value-a", "plant-a"), ("value-b", "plant-b")):
        operations.extend(
            [
                lambda value_id=value_id: store.get(value_id),
                lambda value_id=value_id: store.delete(value_id),
                lambda entity=entity: store.latest(
                    concept="syg:Water_Cooling",
                    entity=entity,
                    period_start=date(2026, 12, 31),
                    period_end=date(2026, 12, 31),
                ),
            ]
        )
    try:
        for operation in operations:
            with pytest.raises(HTTPException) as unavailable:
                operation()
            assert unavailable.value.status_code == 503
            assert unavailable.value.detail == "Value service unavailable"
        assert {row.id for row in db.query(ESGValue).all()} == {"value-a", "value-b"}
    finally:
        db.close()
