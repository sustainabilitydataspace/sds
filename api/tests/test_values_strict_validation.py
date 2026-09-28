"""Strict /values validation tests (concept/entity/unit)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from rdflib import Graph

from src.api.main import app
from src.api.models import HierarchyConfiguration, HierarchyLevel, ValueCreate
from src.auth.dependencies import get_current_active_user
from src.auth.models import Permission, User, UserRole
from src.config.settings import settings
from src.services.hierarchy_store import InMemoryHierarchyStore, get_hierarchy_store
from src.services.value_ingest import ValueIngestError, prepare_value_record
from src.services.value_store import InMemoryValueStore, get_value_store
from src.services.value_versioning import (
    CONTEXT_HASH_RECIPE_VERSION_V2,
    SDS_CANONICAL_OPERATIONAL_RELEASE_ID,
    SOURCE_OBSERVATION_CANONICAL_OPERATIONAL,
)

TEST_COMPANY_ID = "tenant_test_001"
KNOWN_NUMERIC_CONCEPT = (
    "urn:sds:reg:ghg:cat12_waste_treatment_emission_factor_kgco2e_per_kg"
)


def _override_user() -> User:
    now = datetime.now(timezone.utc)
    return User(
        id="test-user",
        username="test_user",
        email="test_user@example.com",
        full_name="Test User",
        company_id=TEST_COMPANY_ID,
        role=UserRole.DATA_MANAGER,
        is_active=True,
        created_at=now,
        updated_at=now,
        last_login=None,
        permissions=[
            Permission.CREATE_VALUES,
            Permission.READ_VALUES,
            Permission.MANAGE_HIERARCHIES,
        ],
    )


class _FakeHierarchyStore:
    def list(self, **_kwargs):
        config = SimpleNamespace(levels=[SimpleNamespace(id="test_madrid_plant")])
        return [config], 1


class _FakeIndicatorStore:
    def __init__(
        self,
        identifiers: set[str],
        calculation_value_concepts: set[str] | None = None,
        esrs_codes: set[str] | None = None,
        gri_codes: set[str] | None = None,
    ):
        self.identifiers = identifiers
        self.calculation_value_concepts = calculation_value_concepts or set()
        self.esrs_codes = esrs_codes or set()
        self.gri_codes = gri_codes or set()

    def get_by_identifier(self, identifier: str):
        if identifier in self.identifiers:
            return SimpleNamespace(identifier=identifier)
        return None

    def get_calculation_value_concept(self, concept: str):
        if concept in self.calculation_value_concepts:
            return SimpleNamespace(canonical_datapoint_id=concept)
        return None

    def search_by_esrs(self, code: str, limit: int = 100):
        return [
            SimpleNamespace(code_esrs=candidate)
            for candidate in sorted(self.esrs_codes)
            if candidate.lower().startswith(code.lower())
        ][:limit]

    def search_by_gri(self, code: str, limit: int = 100):
        return [
            SimpleNamespace(code_gri=candidate, code_gri_expanded=None)
            for candidate in sorted(self.gri_codes)
            if candidate.lower().startswith(code.lower())
        ][:limit]


class _FakeCanonicalConceptStore:
    def __init__(self, concepts: dict[str, SimpleNamespace]):
        self.concepts = concepts
        self.calls: list[str] = []

    def get_current_by_uri(self, canonical_uri: str):
        self.calls.append(canonical_uri)
        return self.concepts.get(canonical_uri)


def test_strict_value_ingest_accepts_registered_catalog_indicator_identifier():
    record = prepare_value_record(
        value_id="value-1",
        value_data=ValueCreate(
            concept="urn:sds:reg:esrs:bp_1_01",
            entity="test_madrid_plant",
            period="2024-12-31",
            value="The company applies the SDS example disclosure boundary.",
            value_type="narrative",
            unit="Text",
        ),
        converter=object(),
        hierarchy_store=_FakeHierarchyStore(),
        indicator_store=_FakeIndicatorStore({"urn:sds:reg:esrs:bp_1_01"}),
        graph=Graph(),
        company_id=None,
        strict=True,
    )

    assert record.concept == "urn:sds:reg:esrs:bp_1_01"
    assert record.value_type == "narrative"


def test_strict_value_ingest_rejects_unregistered_catalog_indicator_identifier():
    with pytest.raises(ValueIngestError, match="Unknown concept"):
        prepare_value_record(
            value_id="value-1",
            value_data=ValueCreate(
                concept="urn:sds:reg:missing",
                entity="test_madrid_plant",
                period="2024-12-31",
                value=True,
                value_type="boolean",
                unit="Boolean",
            ),
            converter=object(),
            hierarchy_store=_FakeHierarchyStore(),
            indicator_store=_FakeIndicatorStore(set()),
            graph=Graph(),
            company_id=None,
            strict=True,
        )


def test_strict_value_ingest_accepts_current_canonical_sygris_concept():
    canonical_uri = "syg:TotalEnergyConsumptionWithinOrganization"
    canonical_store = _FakeCanonicalConceptStore(
        {
            canonical_uri: SimpleNamespace(
                id=101,
                canonical_uri=canonical_uri,
                taxonomy="Sygris",
                concept_type="mapping_pivot",
                effective_to=None,
                superseded_by=None,
            )
        }
    )

    record = prepare_value_record(
        value_id="value-1",
        value_data=ValueCreate(
            concept=canonical_uri,
            entity="test_madrid_plant",
            period="2024-12-31",
            value=832.964,
            value_type="numeric",
            unit="MWh",
            metadata={
                "source_standard": "ESRS",
                "source_standard_release_id": "ESRS_SET1_2023_12_22",
                "source_standard_datapoint_id": "E1-5_02",
            },
        ),
        converter=object(),
        hierarchy_store=_FakeHierarchyStore(),
        indicator_store=_FakeIndicatorStore(set()),
        canonical_concept_store=canonical_store,
        graph=Graph(),
        company_id=None,
        strict=True,
    )

    assert record.concept == canonical_uri
    assert canonical_store.calls == [canonical_uri]
    assert record.metadata is not None
    assert record.metadata["canonical_uri"] == canonical_uri
    assert record.metadata["canonical_concept_id"] == 101
    assert record.metadata["source_observation_type"] == (
        SOURCE_OBSERVATION_CANONICAL_OPERATIONAL
    )
    assert record.metadata["context_hash_recipe_version"] == (
        CONTEXT_HASH_RECIPE_VERSION_V2
    )
    assert record.metadata["indicator_identifier"] == canonical_uri
    assert record.metadata["standard_release_id"] == (
        SDS_CANONICAL_OPERATIONAL_RELEASE_ID
    )
    assert record.metadata["standard_datapoint_id"] == canonical_uri
    assert record.metadata["source_standard_datapoint_id"] == "E1-5_02"


def test_strict_value_ingest_rejects_unknown_canonical_sygris_concept():
    with pytest.raises(ValueIngestError, match="Unknown concept"):
        prepare_value_record(
            value_id="value-1",
            value_data=ValueCreate(
                concept="syg:DoesNotExist",
                entity="test_madrid_plant",
                period="2024-12-31",
                value=10,
                value_type="numeric",
                unit="MWh",
            ),
            converter=object(),
            hierarchy_store=_FakeHierarchyStore(),
            indicator_store=_FakeIndicatorStore(set()),
            canonical_concept_store=_FakeCanonicalConceptStore({}),
            graph=Graph(),
            company_id=None,
            strict=True,
        )


def test_strict_value_ingest_accepts_imported_esrs_code_curie():
    record = prepare_value_record(
        value_id="value-1",
        value_data=ValueCreate(
            concept="csrd:E3-4_05",
            entity="test_madrid_plant",
            period="2024-12-31",
            value=25,
            value_type="numeric",
            unit="m3",
        ),
        converter=object(),
        hierarchy_store=_FakeHierarchyStore(),
        indicator_store=_FakeIndicatorStore(set(), esrs_codes={"E3-4_05"}),
        graph=Graph(),
        company_id=None,
        strict=True,
    )

    assert record.concept == "csrd:E3-4_05"
    assert record.value_type == "numeric"


def test_strict_value_ingest_accepts_imported_gri_code_curie():
    record = prepare_value_record(
        value_id="value-1",
        value_data=ValueCreate(
            concept="gri:303-5.c",
            entity="test_madrid_plant",
            period="2024-12-31",
            value=25,
            value_type="numeric",
            unit="m3",
        ),
        converter=object(),
        hierarchy_store=_FakeHierarchyStore(),
        indicator_store=_FakeIndicatorStore(set(), gri_codes={"GRI 303-5.c"}),
        graph=Graph(),
        company_id=None,
        strict=True,
    )

    assert record.concept == "gri:303-5.c"
    assert record.value_type == "numeric"


def test_strict_value_ingest_accepts_imported_calculation_support_concept():
    record = prepare_value_record(
        value_id="value-1",
        value_data=ValueCreate(
            concept="ghg_scope3_cat15_investee_scope12_emissions_tco2e",
            entity="test_madrid_plant",
            period="2024-12-31",
            value=100,
            value_type="numeric",
            unit="tCO2e",
        ),
        converter=object(),
        hierarchy_store=_FakeHierarchyStore(),
        indicator_store=_FakeIndicatorStore(
            set(),
            {
                "ghg_scope3_cat15_investee_scope12_emissions_tco2e",
            },
        ),
        graph=Graph(),
        company_id=None,
        strict=True,
    )

    assert record.concept == "ghg_scope3_cat15_investee_scope12_emissions_tco2e"
    assert record.value_type == "numeric"


def test_strict_value_ingest_rejects_truthy_unmatched_calculation_lookup():
    class LeakyIndicatorStore:
        def get_by_identifier(self, _identifier: str):
            return None

        def get_calculation_value_concept(self, _concept: str):
            return MagicMock()

    with pytest.raises(ValueIngestError, match="Unknown concept"):
        prepare_value_record(
            value_id="value-1",
            value_data=ValueCreate(
                concept="syg:DoesNotExist",
                entity="test_madrid_plant",
                period="2024-12-31",
                value=100,
                value_type="numeric",
                unit="m3",
            ),
            converter=object(),
            hierarchy_store=_FakeHierarchyStore(),
            indicator_store=LeakyIndicatorStore(),
            graph=Graph(),
            company_id=None,
            strict=True,
        )


def test_values_strict_mode_rejects_unknown_concept(client, monkeypatch):
    monkeypatch.setattr(settings, "require_database", True)

    user = _override_user()
    value_store = InMemoryValueStore()
    hierarchy_store = InMemoryHierarchyStore()

    async def _current_user_override() -> User:
        return user

    def _value_store_override():
        return value_store

    def _hierarchy_store_override():
        return hierarchy_store

    app.dependency_overrides[get_current_active_user] = _current_user_override
    app.dependency_overrides[get_value_store] = _value_store_override
    app.dependency_overrides[get_hierarchy_store] = _hierarchy_store_override
    try:
        response = client.post(
            "/api/v1/values",
            json={
                "concept": "syg:DoesNotExist",
                "entity": "madrid_plant",
                "period": "2024-01-15",
                "value": 1,
                "unit": "L",
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_active_user, None)
        app.dependency_overrides.pop(get_value_store, None)
        app.dependency_overrides.pop(get_hierarchy_store, None)

    assert response.status_code == 400
    assert "concept" in response.json()["error"]["message"].lower()


def test_values_strict_mode_rejects_unknown_entity(client, monkeypatch):
    monkeypatch.setattr(settings, "require_database", True)

    user = _override_user()
    value_store = InMemoryValueStore()
    hierarchy_store = InMemoryHierarchyStore()

    hierarchy_store.create(
        HierarchyConfiguration(
            company_id=TEST_COMPANY_ID,
            hierarchy_type="organizational",
            name="Test",
            description=None,
            levels=[
                HierarchyLevel(
                    id="global", name="Global", parent=None, level=0, metadata={}
                ),
                HierarchyLevel(
                    id="madrid_plant",
                    name="Madrid",
                    parent="global",
                    level=1,
                    metadata={},
                ),
            ],
            active=True,
        ),
        config_id="cfg-1",
        created_by=user.username,
    )

    async def _current_user_override() -> User:
        return user

    def _value_store_override():
        return value_store

    def _hierarchy_store_override():
        return hierarchy_store

    app.dependency_overrides[get_current_active_user] = _current_user_override
    app.dependency_overrides[get_value_store] = _value_store_override
    app.dependency_overrides[get_hierarchy_store] = _hierarchy_store_override
    try:
        response = client.post(
            "/api/v1/values",
            json={
                "concept": KNOWN_NUMERIC_CONCEPT,
                "entity": "missing_entity",
                "period": "2024-01-15",
                "value": 1,
                "unit": "kg",
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_active_user, None)
        app.dependency_overrides.pop(get_value_store, None)
        app.dependency_overrides.pop(get_hierarchy_store, None)

    assert response.status_code == 400
    assert "entity" in response.json()["error"]["message"].lower()


def test_values_strict_mode_rejects_incompatible_unit(client, monkeypatch):
    monkeypatch.setattr(settings, "require_database", True)

    user = _override_user()
    value_store = InMemoryValueStore()
    hierarchy_store = InMemoryHierarchyStore()

    hierarchy_store.create(
        HierarchyConfiguration(
            company_id=TEST_COMPANY_ID,
            hierarchy_type="organizational",
            name="Test",
            description=None,
            levels=[
                HierarchyLevel(
                    id="global", name="Global", parent=None, level=0, metadata={}
                ),
                HierarchyLevel(
                    id="madrid_plant",
                    name="Madrid",
                    parent="global",
                    level=1,
                    metadata={},
                ),
            ],
            active=True,
        ),
        config_id="cfg-1",
        created_by=user.username,
    )

    async def _current_user_override() -> User:
        return user

    def _value_store_override():
        return value_store

    def _hierarchy_store_override():
        return hierarchy_store

    app.dependency_overrides[get_current_active_user] = _current_user_override
    app.dependency_overrides[get_value_store] = _value_store_override
    app.dependency_overrides[get_hierarchy_store] = _hierarchy_store_override
    try:
        response = client.post(
            "/api/v1/values",
            json={
                "concept": KNOWN_NUMERIC_CONCEPT,
                "entity": "madrid_plant",
                "period": "2024-01-15",
                "value": 1,
                "unit": "L",
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_active_user, None)
        app.dependency_overrides.pop(get_value_store, None)
        app.dependency_overrides.pop(get_hierarchy_store, None)

    assert response.status_code == 400
    assert "unit" in response.json()["error"]["message"].lower()
