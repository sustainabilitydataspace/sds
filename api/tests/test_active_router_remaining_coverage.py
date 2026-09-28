from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from rdflib import Graph

from src.api.models import (
    FXConversionRequest,
    HierarchyConfiguration,
    HierarchyLevel,
    UnitConversionRequest,
)
from src.api.routers import (
    calculations,
    hierarchies,
    mapping_assertions,
    mappings,
    ontology,
    values,
)
from src.auth.models import ROLE_PERMISSIONS, User, UserRole


def _user(role: UserRole = UserRole.ADMIN, company_id: str | None = "tenant-a") -> User:
    now = datetime.now(timezone.utc)
    return User(
        id="user-1",
        username="user",
        email="user@example.com",
        full_name=None,
        company_id=company_id,
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
        last_login=None,
        permissions=ROLE_PERMISSIONS[role],
    )


def test_revision_db_helpers_cover_disabled_and_admin_paths(monkeypatch):
    monkeypatch.setattr(values.settings, "value_revision_api_enabled", False)
    with pytest.raises(HTTPException) as exc:
        values._require_revision_db(object())
    assert exc.value.status_code == 404

    assert (
        values._authorized_revision_tenant_id(
            "tenant-a", _user(UserRole.ADMIN), required=True
        )
        == "tenant-a"
    )


@pytest.mark.asyncio
async def test_list_values_generic_store_failure_returns_500():
    class FailingStore:
        def list(self, **_kwargs):
            raise RuntimeError("store unavailable")

    with pytest.raises(HTTPException) as exc:
        await values.get_values(
            concept=None,
            entity=None,
            period_start=None,
            period_end=None,
            unit=None,
            changed_since=None,
            limit=10,
            offset=0,
            cursor=None,
            store=FailingStore(),
            current_user=_user(),
        )

    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_get_value_context_lineage_returns_authorized_payload(monkeypatch):
    context_payload = {
        "id": 7,
        "tenant_id": "tenant-a",
        "context_hash_recipe_version": "sds-value-context-v1",
        "context_hash": "a" * 64,
        "entity_id": "entity-1",
        "reporting_period_id": "FY2026",
        "period_start": date(2026, 1, 1),
        "period_end": date(2026, 12, 31),
        "period_type": "annual",
        "reporting_boundary_id": "operational_control",
        "indicator_identifier": "csrd:E1_6",
        "standard_release_id": "ESRS-2023",
        "standard_datapoint_id": "E1-6_07",
        "dimensions": {},
        "scenario_basis": "actual",
        "value_kind": "numeric",
    }

    class FakeRevisionStore:
        def __init__(self, _db):
            pass

        def get_context_lineage(self, context_id, *, tenant_id):
            assert context_id == 7
            assert tenant_id == "tenant-a"
            return {
                "context": context_payload,
                "revisions": [],
                "events": [],
                "current_revision_id": None,
            }

    monkeypatch.setattr(values.settings, "value_revision_api_enabled", True)
    monkeypatch.setattr(values, "ValueRevisionStore", FakeRevisionStore)

    response = await values.get_value_context_lineage(
        7, db=object(), current_user=_user()
    )

    assert response.context.id == 7
    assert response.current_revision_id is None


def test_calculation_period_and_type_fallback_branches():
    assert calculations._parse_period(
        "2026-Q4", calculations.TemporalGranularity.QUARTERLY
    ) == (
        date(2026, 10, 1),
        date(2026, 12, 31),
    )
    assert calculations._get_concept_calculation_type("general:index") == "general"


def test_decimal_serializers_emit_json_numbers():
    unit_payload = UnitConversionRequest(
        value="12.50",
        from_unit="kg",
        to_unit="t",
    ).model_dump(mode="json")
    fx_payload = FXConversionRequest(
        value="10.25",
        from_currency=" eur ",
        to_currency="usd",
        value_date=date(2026, 5, 23),
        fx_policy_id="ecb-reference",
    ).model_dump(mode="json")

    assert unit_payload["value"] == 12.5
    assert fx_payload["value"] == 10.25
    assert fx_payload["from_currency"] == "EUR"
    assert fx_payload["to_currency"] == "USD"


@pytest.mark.asyncio
async def test_ontology_db_required_and_missing_concept_paths(monkeypatch):
    monkeypatch.setattr(ontology.settings, "require_database", True)
    with pytest.raises(HTTPException) as exc:
        await ontology.get_equivalences(
            concept=None,
            source_taxonomy=None,
            target_taxonomy=None,
            graph=Graph(),
            concept_service=None,
            current_user=_user(),
        )
    assert exc.value.status_code == 503

    class EmptyConceptService:
        def has_semantic_data(self):
            return True

        def get_concept_by_uri(self, _uri, **_kwargs):
            return None

    with pytest.raises(HTTPException) as exc:
        await ontology.get_concept_details(
            "csrd:missing",
            graph=Graph(),
            concept_service=EmptyConceptService(),
            current_user=_user(),
        )
    assert exc.value.status_code == 404


def _hierarchy_config() -> HierarchyConfiguration:
    return HierarchyConfiguration(
        company_id="tenant-a",
        hierarchy_type="organizational",
        name="Hierarchy",
        levels=[
            HierarchyLevel(id="global", name="Global", parent=None, level=0),
            HierarchyLevel(id="site", name="Site", parent="global", level=1),
        ],
        active=True,
    )


@pytest.mark.asyncio
async def test_hierarchy_missing_update_and_activate_paths():
    store = type(
        "MissingHierarchyStore",
        (),
        {
            # the router pre-fetches for tenant scoping; a missing config -> 404
            "get": lambda self, *_args, **_kwargs: None,
            "update": lambda self, *_args, **_kwargs: None,
            "activate": lambda self, *_args, **_kwargs: False,
        },
    )()

    with pytest.raises(HTTPException) as exc:
        await hierarchies.update_hierarchy(
            "missing", config=_hierarchy_config(), store=store, current_user=_user()
        )
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await hierarchies.activate_hierarchy(
            "missing", store=store, current_user=_user()
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_mapping_export_and_inspection_root_error_paths(monkeypatch):
    class FailingStore:
        def __init__(self, db=None):
            pass

        def get_all(self, **_kwargs):
            raise RuntimeError("mapping store down")

    monkeypatch.setattr(mappings, "StandardMappingStore", FailingStore)
    with pytest.raises(RuntimeError, match="mapping store down"):
        await mappings.export_mappings(
            request=SimpleNamespace(headers={}),
            format="json",
            source_standard=None,
            source_code=None,
            target_standard=None,
            target_code=None,
            dimension=None,
            min_confidence=None,
            changed_since=None,
            limit=10,
            db=None,
            user=_user(),
        )

    monkeypatch.setattr(
        mapping_assertions.settings,
        "canonical_mapping_inspection_roots",
        ["relative-root"],
    )
    monkeypatch.setattr(
        mapping_assertions.Path,
        "resolve",
        lambda self: (_ for _ in ()).throw(OSError("bad path")),
    )
    assert mapping_assertions._allowed_inspection_roots() == ()
