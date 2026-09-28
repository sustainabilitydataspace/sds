from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.models import (
    CalculationRequest,
    CalculationResponse,
    HierarchyConfiguration,
    HierarchyLevel,
    TemporalGranularity,
    ValueResolutionStatus,
    ValueResolveResponse,
)
from src.api.routers import calculations
from src.calculation.contracts import (
    CalculationContract,
    CalculationContractInput,
    CalculationContractNotFoundError,
    ContractResolutionError,
    ContractResolutionMetadata,
)
from src.services.value_store import InMemoryValueStore


@pytest.mark.parametrize(
    ("status", "relationships", "precondition", "coverage"),
    [
        (
            ValueResolutionStatus.NOT_TRANSFORMABLE,
            ["partial"],
            "equivalent_relationship_required",
            "not_transformable",
        ),
        (
            ValueResolutionStatus.NOT_TRANSFORMABLE,
            ["equivalent"],
            "not_transformable",
            "not_transformable",
        ),
        (
            ValueResolutionStatus.AMBIGUOUS_MAPPING,
            [],
            "single_eligible_route_required",
            "ambiguous",
        ),
        (
            ValueResolutionStatus.INCOMPLETE_COVERAGE,
            [],
            "source_value_completeness",
            "incomplete",
        ),
        (
            ValueResolutionStatus.UNSUPPORTED_CONVERSION,
            [],
            "unit_conversion_policy",
            "incomplete",
        ),
    ],
)
def test_calculation_refusal_preserves_mapping_and_conversion_cause(
    status, relationships, precondition, coverage
):
    request = CalculationRequest(concept="gri:302-1.e", entity="plant-a", period="2024")
    resolution = ValueResolveResponse(
        status=status,
        target_concept=request.concept,
        entity=request.entity,
        period=request.period,
        granularity=TemporalGranularity.ANNUAL,
        trace={"reason": "the source is not authorized to calculate the target"},
    )
    assert resolution.value is None
    assert calculations._failed_preconditions(resolution, relationships) == [
        precondition
    ]
    assert calculations._coverage_status(resolution) == coverage
    assert (
        calculations._calculation_refusal_message(request, resolution, relationships)
        == "the source is not authorized to calculate the target"
    )


class FakeResolver:
    def __init__(self, contract):
        self.contract = contract
        self.resolved: list[str] = []

    def resolve(self, concept: str):
        self.resolved.append(concept)
        return self.contract


class FakeConversionEngine:
    def __init__(self):
        self.requests = []

    def normalize(self, request):
        self.requests.append(request)
        value = Decimal(str(request.value))
        trace = []
        if request.currency != request.expected_currency:
            value *= Decimal("1.2")
            trace.append(
                {
                    "step_type": "fx",
                    "from_currency": request.currency,
                    "to_currency": request.expected_currency,
                    "rate_observation_id": "fx-router",
                }
            )
        return SimpleNamespace(
            value=value,
            unit=request.expected_unit or request.unit,
            currency=request.expected_currency or request.currency,
            trace=trace,
        )


class FakeHierarchyStore:
    def __init__(self, configs=None):
        self.configs = list(configs or [])
        self.calls = []

    def list(
        self,
        *,
        company_id,
        hierarchy_type,
        active,
        limit,
        offset,
    ):
        self.calls.append(
            {
                "company_id": company_id,
                "hierarchy_type": hierarchy_type,
                "active": active,
                "limit": limit,
                "offset": offset,
            }
        )
        items = [
            config
            for config in self.configs
            if (company_id is None or config.company_id == company_id)
            and (hierarchy_type is None or config.hierarchy_type == hierarchy_type)
            and (active is None or config.active is active)
        ]
        return items, len(items)


@dataclass(frozen=True)
class FakeMapping:
    id: int
    source_standard: str
    source_code: str
    target_standard: str
    target_code: str
    relationship_type: str
    confidence: float = 1.0
    dataset: str = "canonical_pairwise_mappings"


class FakeMappingStore:
    def __init__(self, rows: list[FakeMapping]):
        self.rows = rows

    def search(self, **_kwargs):
        return self.rows


@dataclass
class ExplodingGraph:
    used: bool = False

    def objects(self, *_args, **_kwargs):
        self.used = True
        raise AssertionError("ontology graph must not be used in contract runtime")


def _contract() -> CalculationContract:
    return CalculationContract(
        contract_id="contract-1",
        concept="urn:sds:disclosure:csrd:e3-5",
        contract_version="2026.05",
        contract_hash="sha256:router",
        runtime_status="executable",
        formula="cooling + industrial",
        result_unit="m3",
        inputs=(
            CalculationContractInput("cooling", "syg:Water_Cooling", unit="m3"),
            CalculationContractInput("industrial", "syg:Water_Industrial", unit="m3"),
        ),
        resolution=ContractResolutionMetadata(source="canonical_db"),
    )


def _currency_contract() -> CalculationContract:
    return CalculationContract(
        contract_id="contract-fx-router",
        concept="finance:revenue",
        contract_version="2026.05",
        contract_hash="sha256:router-fx",
        runtime_status="executable",
        formula="revenue",
        result_unit="EURm",
        result_currency="EUR",
        inputs=(
            CalculationContractInput(
                "revenue",
                "finance:revenue_input",
                unit="EURm",
                expected_currency="EUR",
                fx_policy_id="ecb-monthly",
            ),
        ),
        resolution=ContractResolutionMetadata(source="canonical_db"),
    )


def _gri_energy_contract() -> CalculationContract:
    return CalculationContract(
        contract_id="contract-gri-energy",
        concept="gri:302-1.a",
        contract_version="2026.05",
        contract_hash="sha256:gri-energy",
        runtime_status="executable",
        formula="fuel_energy",
        result_unit="MWh",
        inputs=(
            CalculationContractInput(
                "fuel_energy",
                "gri:302-1.a_input",
                unit="MWh",
            ),
        ),
        resolution=ContractResolutionMetadata(source="canonical_db"),
    )


def _gri_total_energy_bridge_contract() -> CalculationContract:
    return CalculationContract(
        contract_id="bridge-gri-302-1e-total-energy",
        concept="gri:302-1.e",
        contract_version="2026.05",
        contract_hash="sha256:gri-302-1e-bridge",
        runtime_status="executable",
        formula="fossil_energy + renewable_energy",
        result_unit="MWh",
        inputs=(
            CalculationContractInput(
                "fossil_energy",
                "gri:302-1.e:fossil_component",
                unit="MWh",
                allowed_mapping_relationships=("narrower",),
            ),
            CalculationContractInput(
                "renewable_energy",
                "gri:302-1.e:renewable_component",
                unit="MWh",
                allowed_mapping_relationships=("narrower",),
            ),
        ),
        resolution=ContractResolutionMetadata(source="canonical_db"),
    )


def _store() -> InMemoryValueStore:
    store = InMemoryValueStore()
    now = datetime.now(timezone.utc)
    store.create(
        value_id="cooling-1",
        concept="syg:Water_Cooling",
        entity="madrid",
        period=date(2024, 1, 1),
        value=Decimal("0.05"),
        unit="m3",
        original_unit=None,
        conversion_applied=False,
        metadata={"loaded_at": now.isoformat()},
    )
    store.create(
        value_id="industrial-1",
        concept="syg:Water_Industrial",
        entity="madrid",
        period=date(2024, 1, 1),
        value=Decimal("0.07"),
        unit="m3",
        original_unit=None,
        conversion_applied=False,
        metadata={"loaded_at": now.isoformat()},
    )
    return store


def _energy_store() -> InMemoryValueStore:
    store = InMemoryValueStore()
    now = datetime.now(timezone.utc)
    store.create(
        value_id="sygris-fuel-energy",
        concept="syg:Energy_Fuel",
        entity="nh_group",
        period=date(2024, 1, 1),
        value=Decimal("832.964"),
        unit="MWh",
        original_unit=None,
        conversion_applied=False,
        metadata={"loaded_at": now.isoformat()},
    )
    return store


def _esrs_energy_component_store() -> InMemoryValueStore:
    store = InMemoryValueStore()
    now = datetime.now(timezone.utc)
    for value_id, concept, value in [
        ("esrs-fossil-energy", "csrd:E1-5_02", Decimal("832.964")),
        ("esrs-renewable-energy", "csrd:E1-5_03", Decimal("117.036")),
    ]:
        store.create(
            value_id=value_id,
            concept=concept,
            entity="nh_group",
            period=date(2024, 1, 1),
            value=value,
            unit="MWh",
            original_unit=None,
            conversion_applied=False,
            metadata={"loaded_at": now.isoformat()},
        )
    return store


def _currency_store() -> InMemoryValueStore:
    store = InMemoryValueStore()
    now = datetime.now(timezone.utc)
    store.create(
        value_id="revenue-gbp",
        concept="finance:revenue_input",
        entity="madrid",
        period=date(2024, 1, 1),
        value=Decimal("10"),
        unit="EURm",
        original_unit=None,
        conversion_applied=False,
        currency="GBP",
        value_date=date(2024, 1, 1),
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        metadata={"loaded_at": now.isoformat()},
    )
    return store


def _contract_for(concept: str, *, exposure: str | None = "public_register"):
    contract = _contract()
    return CalculationContract(
        contract_id=contract.contract_id,
        concept=concept,
        contract_version=contract.contract_version,
        contract_hash=contract.contract_hash,
        runtime_status=contract.runtime_status,
        formula=contract.formula,
        result_unit=contract.result_unit,
        inputs=contract.inputs,
        exposure=exposure,
        resolution=contract.resolution,
    )


class RoutingResolver:
    def __init__(self, contracts: dict[str, CalculationContract]):
        self.contracts = contracts
        self.resolved: list[str] = []

    def resolve(self, concept: str):
        self.resolved.append(concept)
        try:
            return self.contracts[concept]
        except KeyError as exc:
            raise CalculationContractNotFoundError(concept) from exc


def _hierarchy_config() -> HierarchyConfiguration:
    return HierarchyConfiguration(
        id="hierarchy-1",
        company_id="company-a",
        hierarchy_type="organizational",
        name="Operational hierarchy",
        levels=[
            HierarchyLevel(id="group", name="Group", parent=None, level=0),
            HierarchyLevel(id="madrid", name="Madrid", parent="group", level=2),
        ],
        active=True,
    )


def test_authenticated_value_store_scopes_non_admin_legacy_db_reads(monkeypatch):
    monkeypatch.setattr(calculations.settings, "require_database", True)
    monkeypatch.setattr(
        calculations.settings, "value_revision_primary_read_path", "legacy"
    )
    monkeypatch.setattr(
        calculations.settings, "value_revision_dual_write_enabled", False
    )

    captured = {}
    expected_store = object()

    def fake_get_value_store(*, request, db, tenant_id):
        captured["request"] = request
        captured["db"] = db
        captured["tenant_id"] = tenant_id
        return expected_store

    monkeypatch.setattr(calculations, "get_value_store", fake_get_value_store)

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    db = object()
    current_user = SimpleNamespace(company_id="company-a", role="analyst")

    store = calculations.get_authenticated_value_store(
        request=request,
        db=db,
        current_user=current_user,
    )

    assert store is expected_store
    assert captured == {
        "request": request,
        "db": db,
        "tenant_id": "company-a",
    }


def test_entity_level_comes_from_active_hierarchy_configuration():
    store = FakeHierarchyStore([_hierarchy_config()])

    assert (
        calculations._get_entity_level(
            "madrid",
            hierarchy_store=store,
            company_id="company-a",
        )
        == 2
    )
    assert store.calls == [
        {
            "company_id": "company-a",
            "hierarchy_type": "organizational",
            "active": True,
            "limit": 1000,
            "offset": 0,
        }
    ]


def test_entity_level_rejects_unknown_entity_when_active_hierarchy_exists():
    store = FakeHierarchyStore([_hierarchy_config()])

    with pytest.raises(ValueError, match="Unknown entity: nh_group"):
        calculations._get_entity_level(
            "nh_group",
            hierarchy_store=store,
            company_id="company-a",
        )


def test_entity_level_uses_generic_fallback_without_active_hierarchy():
    store = FakeHierarchyStore([])

    assert (
        calculations._get_entity_level(
            "any_entity",
            hierarchy_store=store,
            company_id="company-a",
        )
        == 0
    )


@pytest.mark.asyncio
async def test_router_uses_contract_runtime_and_does_not_touch_ontology_graph():
    graph = ExplodingGraph()

    response = await calculations._run_calculation(
        CalculationRequest(
            concept="urn:sds:disclosure:csrd:e3-5",
            entity="madrid",
            period="2024",
            granularity=TemporalGranularity.ANNUAL,
            include_trace=True,
        ),
        graph=graph,
        store=_store(),
        contract_resolver=FakeResolver(_contract()),
        allow_ontology_fallback=False,
    )

    assert response.value == Decimal("0.12")
    assert response.unit == "m3"
    assert graph.used is False
    assert response.trace is not None
    assert "contract_id=contract-1" in response.trace.steps
    assert "contract_hash=sha256:router" in response.trace.steps
    assert "resolver_source=canonical_db" in response.trace.steps
    assert set(response.trace.dependencies_resolved) == {
        "syg:Water_Cooling",
        "syg:Water_Industrial",
    }
    assert response.trace.aggregations_applied == [
        "cooling: temporal=sum;perimeter=sum;entities=self",
        "industrial: temporal=sum;perimeter=sum;entities=self",
    ]


@pytest.mark.asyncio
async def test_router_reports_missing_required_observations_as_422():
    store = InMemoryValueStore()
    now = datetime.now(timezone.utc)
    store.create(
        value_id="cooling-only",
        concept="syg:Water_Cooling",
        entity="madrid",
        period=date(2024, 1, 1),
        value=Decimal("0.05"),
        unit="m3",
        original_unit=None,
        conversion_applied=False,
        metadata={"loaded_at": now.isoformat()},
    )

    with pytest.raises(HTTPException) as exc_info:
        await calculations._run_calculation(
            CalculationRequest(
                concept="urn:sds:disclosure:csrd:e3-5",
                entity="madrid",
                period="2024",
                granularity=TemporalGranularity.ANNUAL,
                include_trace=True,
            ),
            graph=ExplodingGraph(),
            store=store,
            contract_resolver=FakeResolver(_contract()),
            allow_ontology_fallback=False,
        )

    assert exc_info.value.status_code == 422
    assert "Missing required observations: industrial" in exc_info.value.detail


@pytest.mark.asyncio
async def test_router_uses_equivalent_mapping_when_requested_contract_is_missing():
    resolver = RoutingResolver({"csrd:E3_5": _contract_for("csrd:E3_5")})

    response = await calculations._run_calculation(
        CalculationRequest(
            concept="gri:303_3",
            entity="madrid",
            period="2024",
            granularity=TemporalGranularity.ANNUAL,
            include_trace=True,
        ),
        graph=ExplodingGraph(),
        store=_store(),
        contract_resolver=resolver,
        allow_ontology_fallback=False,
        mapping_store=FakeMappingStore(
            [
                FakeMapping(
                    id=5001,
                    source_standard="CSRD",
                    source_code="E3_5",
                    target_standard="GRI",
                    target_code="303_3",
                    relationship_type="equivalent",
                )
            ]
        ),
    )

    assert response.concept == "gri:303_3"
    assert response.value == Decimal("0.12")
    assert response.requested_concept == "gri:303_3"
    assert response.executed_concept == "csrd:E3_5"
    assert response.execution_authority == "equivalent_mapping"
    assert response.mapping_relationship_type == "equivalent"
    assert response.coverage_status == "complete"
    assert response.source_value_ids == ["cooling-1", "industrial-1"]
    assert response.trace is not None
    assert "execution_authority=equivalent_mapping" in response.trace.steps
    assert "executed_concept=csrd:E3_5" in response.trace.steps


@pytest.mark.asyncio
async def test_router_refuses_present_invalid_target_before_equivalent_mapping(
    monkeypatch,
):
    class BrokenTargetResolver(RoutingResolver):
        def resolve(self, concept):
            if concept == "gri:303_3":
                raise ContractResolutionError(
                    "present target aggregation_policy is malformed"
                )
            return super().resolve(concept)

    resolver = BrokenTargetResolver({"csrd:E3_5": _contract_for("csrd:E3_5")})

    async def forbidden_fallback(*args, **kwargs):
        raise AssertionError("mapping fallback must not run for an invalid target")

    monkeypatch.setattr(
        calculations, "_try_cross_standard_calculation", forbidden_fallback
    )
    with pytest.raises(HTTPException) as exc_info:
        await calculations._run_calculation(
            CalculationRequest(
                concept="gri:303_3",
                entity="madrid",
                period="2024",
                granularity=TemporalGranularity.ANNUAL,
            ),
            graph=ExplodingGraph(),
            store=_store(),
            contract_resolver=resolver,
            allow_ontology_fallback=False,
            mapping_store=FakeMappingStore(
                [
                    FakeMapping(
                        id=5001,
                        source_standard="CSRD",
                        source_code="E3_5",
                        target_standard="GRI",
                        target_code="303_3",
                        relationship_type="equivalent",
                    )
                ]
            ),
        )
    assert exc_info.value.status_code == 404
    assert "malformed" in exc_info.value.detail
    assert resolver.resolved == []


@pytest.mark.asyncio
async def test_router_refuses_narrower_esrs_mapping_for_gri_total_energy():
    resolver = RoutingResolver({})

    with pytest.raises(HTTPException) as exc_info:
        await calculations._run_calculation(
            CalculationRequest(
                concept="gri:302-1.e",
                entity="nh_group",
                period="2024",
                granularity=TemporalGranularity.ANNUAL,
                include_trace=True,
            ),
            graph=ExplodingGraph(),
            store=_store(),
            contract_resolver=resolver,
            allow_ontology_fallback=False,
            mapping_store=FakeMappingStore(
                [
                    FakeMapping(
                        id=3021,
                        source_standard="ESRS",
                        source_code="E1-5_02",
                        target_standard="GRI",
                        target_code="GRI 302-1.e",
                        relationship_type="narrower",
                    )
                ]
            ),
        )

    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert detail["code"] == "NOT_TRANSFORMABLE"
    assert detail["requested_concept"] == "gri:302-1.e"
    assert detail["execution_authority"] == "mapping_refusal"
    assert detail["coverage_status"] == "not_transformable"
    assert detail["mapping_relationship_types"] == ["narrower"]
    assert detail["failed_preconditions"] == ["equivalent_relationship_required"]
    assert detail["required_bridge_status"] == "missing"
    assert "GRI 302-1.e" in detail["message"]
    assert "ESRS E1-5_02" in detail["message"]


@pytest.mark.asyncio
async def test_cross_standard_calculation_does_not_return_direct_stored_target_value():
    store = _store()
    store.create(
        value_id="direct-gri-value",
        concept="gri:303_3",
        entity="madrid",
        period=date(2024, 1, 1),
        value=Decimal("99"),
        unit="m3",
        original_unit=None,
        conversion_applied=False,
        metadata={"source": "direct-test"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await calculations._run_calculation(
            CalculationRequest(
                concept="gri:303_3",
                entity="madrid",
                period="2024",
                granularity=TemporalGranularity.ANNUAL,
                include_trace=True,
            ),
            graph=ExplodingGraph(),
            store=store,
            contract_resolver=RoutingResolver({}),
            allow_ontology_fallback=False,
            mapping_store=FakeMappingStore([]),
        )

    assert exc_info.value.status_code == 404
    assert "No calculation contract" in exc_info.value.detail


@pytest.mark.asyncio
async def test_cross_standard_calculation_does_not_return_mapped_direct_source_value():
    store = _store()
    store.create(
        value_id="direct-csrd-value",
        concept="csrd:E3_5",
        entity="madrid",
        period=date(2024, 1, 1),
        value=Decimal("99"),
        unit="m3",
        original_unit=None,
        conversion_applied=False,
        metadata={"source": "direct-test"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await calculations._run_calculation(
            CalculationRequest(
                concept="gri:303_3",
                entity="madrid",
                period="2024",
                granularity=TemporalGranularity.ANNUAL,
                include_trace=True,
            ),
            graph=ExplodingGraph(),
            store=store,
            contract_resolver=RoutingResolver({}),
            allow_ontology_fallback=False,
            mapping_store=FakeMappingStore(
                [
                    FakeMapping(
                        id=5002,
                        source_standard="CSRD",
                        source_code="E3_5",
                        target_standard="GRI",
                        target_code="303_3",
                        relationship_type="equivalent",
                    )
                ]
            ),
        )

    assert exc_info.value.status_code == 404
    assert "No calculation contract" in exc_info.value.detail


@pytest.mark.asyncio
async def test_router_resolves_contract_input_from_equivalent_sygris_value():
    response = await calculations._run_calculation(
        CalculationRequest(
            concept="gri:302-1.a",
            entity="nh_group",
            period="2024",
            granularity=TemporalGranularity.ANNUAL,
            include_trace=True,
        ),
        graph=ExplodingGraph(),
        store=_energy_store(),
        contract_resolver=FakeResolver(_gri_energy_contract()),
        allow_ontology_fallback=False,
        mapping_store=FakeMappingStore(
            [
                FakeMapping(
                    id=302101,
                    source_standard="SYGRIS",
                    source_code="Energy_Fuel",
                    target_standard="GRI",
                    target_code="302-1.a_input",
                    relationship_type="equivalent",
                )
            ]
        ),
    )

    assert response.value == Decimal("832.9640")
    assert response.unit == "MWh"
    assert response.requested_concept == "gri:302-1.a"
    assert response.executed_concept == "gri:302-1.a"
    assert response.execution_authority == "native_contract"
    assert response.coverage_status == "complete"
    assert response.source_value_ids == ["sygris-fuel-energy"]
    assert response.framework_metadata == {
        "requested_concept": "gri:302-1.a",
        "executed_concept": "gri:302-1.a",
        "contract_id": "contract-gri-energy",
        "contract_version": "2026.05",
        "contract_hash": "sha256:gri-energy",
        "resolver_source": "canonical_db",
        "formula": "fuel_energy",
        "result_unit": "MWh",
        "input_concepts": ["gri:302-1.a_input"],
        "input_mappings": [
            {
                "local_variable": "fuel_energy",
                "requested_concept": "gri:302-1.a_input",
                "source_concept": "syg:Energy_Fuel",
                "relationship_type": "equivalent",
                "mapping_row_id": "302101",
                "direction": "forward",
            }
        ],
    }
    assert response.trace is not None
    assert response.trace.dependencies_resolved == ["gri:302-1.a_input"]
    assert response.trace.variables_used == ["fuel_energy"]
    assert "source_value_ids=sygris-fuel-energy" in response.trace.steps
    assert (
        "input_mapping=fuel_energy:gri:302-1.a_input<-syg:Energy_Fuel"
        in response.trace.steps
    )


@pytest.mark.asyncio
async def test_router_refuses_non_equivalent_contract_input_mapping():
    with pytest.raises(HTTPException) as exc_info:
        await calculations._run_calculation(
            CalculationRequest(
                concept="gri:302-1.a",
                entity="nh_group",
                period="2024",
                granularity=TemporalGranularity.ANNUAL,
                include_trace=True,
            ),
            graph=ExplodingGraph(),
            store=_energy_store(),
            contract_resolver=FakeResolver(_gri_energy_contract()),
            allow_ontology_fallback=False,
            mapping_store=FakeMappingStore(
                [
                    FakeMapping(
                        id=302102,
                        source_standard="ESRS",
                        source_code="E1-5_02",
                        target_standard="GRI",
                        target_code="302-1.a_input",
                        relationship_type="narrower",
                    )
                ]
            ),
        )

    assert exc_info.value.status_code == 422
    assert "exact/equivalent relationship is required" in exc_info.value.detail
    assert "narrower" in exc_info.value.detail


@pytest.mark.asyncio
async def test_router_refuses_ambiguous_equivalent_contract_input_mappings():
    with pytest.raises(HTTPException) as exc_info:
        await calculations._run_calculation(
            CalculationRequest(
                concept="gri:302-1.a",
                entity="nh_group",
                period="2024",
                granularity=TemporalGranularity.ANNUAL,
                include_trace=True,
            ),
            graph=ExplodingGraph(),
            store=_energy_store(),
            contract_resolver=FakeResolver(_gri_energy_contract()),
            allow_ontology_fallback=False,
            mapping_store=FakeMappingStore(
                [
                    FakeMapping(
                        id=302103,
                        source_standard="SYGRIS",
                        source_code="Energy_Fuel",
                        target_standard="GRI",
                        target_code="302-1.a_input",
                        relationship_type="equivalent",
                    ),
                    FakeMapping(
                        id=302104,
                        source_standard="SYGRIS",
                        source_code="Energy_Fuel_Alternative",
                        target_standard="GRI",
                        target_code="302-1.a_input",
                        relationship_type="equivalent",
                    ),
                ]
            ),
        )

    assert exc_info.value.status_code == 422
    assert "ambiguous equivalent mappings" in exc_info.value.detail
    assert "syg:Energy_Fuel" in exc_info.value.detail
    assert "syg:Energy_Fuel_Alternative" in exc_info.value.detail


@pytest.mark.asyncio
async def test_router_refuses_conflicting_authority_for_same_contract_input_pair():
    with pytest.raises(HTTPException) as exc_info:
        await calculations._run_calculation(
            CalculationRequest(
                concept="gri:302-1.a",
                entity="nh_group",
                period="2024",
                granularity=TemporalGranularity.ANNUAL,
            ),
            graph=ExplodingGraph(),
            store=_energy_store(),
            contract_resolver=FakeResolver(_gri_energy_contract()),
            allow_ontology_fallback=False,
            mapping_store=FakeMappingStore(
                [
                    FakeMapping(
                        302105,
                        "SYGRIS",
                        "Energy_Fuel",
                        "GRI",
                        "302-1.a_input",
                        "equivalent",
                    ),
                    FakeMapping(
                        302106,
                        "SYGRIS",
                        "Energy_Fuel",
                        "GRI",
                        "302-1.a_input",
                        "partial",
                    ),
                ]
            ),
        )

    assert exc_info.value.status_code == 422
    assert "contradictory" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_router_calculates_gri_total_from_authorized_narrower_esrs_components():
    response = await calculations._run_calculation(
        CalculationRequest(
            concept="gri:302-1.e",
            entity="nh_group",
            period="2024",
            granularity=TemporalGranularity.ANNUAL,
            include_trace=True,
        ),
        graph=ExplodingGraph(),
        store=_esrs_energy_component_store(),
        contract_resolver=FakeResolver(_gri_total_energy_bridge_contract()),
        allow_ontology_fallback=False,
        mapping_store=FakeMappingStore(
            [
                FakeMapping(
                    id=302105,
                    source_standard="ESRS",
                    source_code="E1-5_02",
                    target_standard="GRI",
                    target_code="302-1.e:fossil_component",
                    relationship_type="narrower",
                ),
                FakeMapping(
                    id=302106,
                    source_standard="ESRS",
                    source_code="E1-5_03",
                    target_standard="GRI",
                    target_code="302-1.e:renewable_component",
                    relationship_type="narrower",
                ),
            ]
        ),
    )

    assert response.value == Decimal("950.0000")
    assert response.unit == "MWh"
    assert response.execution_authority == "certified_bridge"
    assert response.bridge_id == "bridge-gri-302-1e-total-energy"
    assert response.source_value_ids == ["esrs-fossil-energy", "esrs-renewable-energy"]
    assert response.framework_metadata["input_mappings"] == [
        {
            "local_variable": "fossil_energy",
            "requested_concept": "gri:302-1.e:fossil_component",
            "source_concept": "csrd:E1-5_02",
            "relationship_type": "narrower",
            "mapping_row_id": "302105",
            "direction": "forward",
        },
        {
            "local_variable": "renewable_energy",
            "requested_concept": "gri:302-1.e:renewable_component",
            "source_concept": "csrd:E1-5_03",
            "relationship_type": "narrower",
            "mapping_row_id": "302106",
            "direction": "forward",
        },
    ]


@pytest.mark.asyncio
async def test_router_refuses_inverse_narrower_mapping_for_contract_input():
    with pytest.raises(HTTPException) as exc_info:
        await calculations._run_calculation(
            CalculationRequest(
                concept="gri:302-1.e",
                entity="nh_group",
                period="2024",
                granularity=TemporalGranularity.ANNUAL,
            ),
            graph=ExplodingGraph(),
            store=_esrs_energy_component_store(),
            contract_resolver=FakeResolver(_gri_total_energy_bridge_contract()),
            allow_ontology_fallback=False,
            mapping_store=FakeMappingStore(
                [
                    FakeMapping(
                        302105,
                        "GRI",
                        "302-1.e:fossil_component",
                        "ESRS",
                        "E1-5_02",
                        "narrower",
                    ),
                    FakeMapping(
                        302106,
                        "ESRS",
                        "E1-5_03",
                        "GRI",
                        "302-1.e:renewable_component",
                        "narrower",
                    ),
                ]
            ),
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_router_passes_conversion_engine_to_contract_runtime():
    conversion_engine = FakeConversionEngine()

    response = await calculations._run_calculation(
        CalculationRequest(
            concept="finance:revenue",
            entity="madrid",
            period="2024",
            granularity=TemporalGranularity.ANNUAL,
            include_trace=True,
        ),
        graph=ExplodingGraph(),
        store=_currency_store(),
        contract_resolver=FakeResolver(_currency_contract()),
        allow_ontology_fallback=False,
        conversion_engine=conversion_engine,
    )

    assert response.value == Decimal("12.0000")
    assert conversion_engine.requests[0].currency == "GBP"
    assert conversion_engine.requests[0].expected_currency == "EUR"
    assert response.trace is not None
    assert response.trace.conversions_applied == [
        {
            "variable": "revenue",
            "value_id": "revenue-gbp",
            "step_type": "fx",
            "from_currency": "GBP",
            "to_currency": "EUR",
            "rate_observation_id": "fx-router",
        }
    ]


@pytest.mark.asyncio
async def test_calculate_endpoint_builds_request_scoped_conversion_engine(monkeypatch):
    captured = {}
    factory_calls = []

    async def fake_run_calculation(request, **kwargs):
        captured.update(kwargs)
        return CalculationResponse(
            concept=request.concept,
            entity=request.entity,
            period=request.period,
            value=Decimal("1"),
            unit="m3",
            formula_used="x",
            confidence=1.0,
            trace=None,
            calculated_at=datetime.now(timezone.utc),
        )

    def fake_build_conversion_engine(db_session, converter):
        factory_calls.append((db_session, converter))
        return request_conversion_engine

    db = object()
    unit_converter = object()
    request_conversion_engine = object()
    global_conversion_engine = object()
    hierarchy_store = object()
    monkeypatch.setattr(calculations, "_run_calculation", fake_run_calculation)
    monkeypatch.setattr(
        calculations,
        "build_conversion_engine",
        fake_build_conversion_engine,
        raising=False,
    )

    await calculations.calculate_indicator(
        CalculationRequest(
            concept="urn:sds:disclosure:csrd:e3-5",
            entity="madrid",
            period="2024",
        ),
        http_request=SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    unit_converter=unit_converter,
                    conversion_engine=global_conversion_engine,
                )
            )
        ),
        graph=ExplodingGraph(),
        store=_store(),
        hierarchy_store=hierarchy_store,
        db=db,
        current_user=object(),
    )

    assert len(factory_calls) == 1
    assert factory_calls[0][0] is db
    assert factory_calls[0][1] is unit_converter
    assert captured["unit_normalizer"] is unit_converter
    assert captured["conversion_engine"] is request_conversion_engine
    assert captured["hierarchy_store"] is hierarchy_store


@pytest.mark.asyncio
async def test_calculate_endpoint_without_db_does_not_build_conversion_engine(
    monkeypatch,
):
    captured = {}
    factory_calls = []

    async def fake_run_calculation(request, **kwargs):
        captured.update(kwargs)
        return CalculationResponse(
            concept=request.concept,
            entity=request.entity,
            period=request.period,
            value=Decimal("1"),
            unit="m3",
            formula_used="x",
            confidence=1.0,
            trace=None,
            calculated_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(calculations, "_run_calculation", fake_run_calculation)
    monkeypatch.setattr(
        calculations,
        "build_conversion_engine",
        lambda *_args: factory_calls.append(_args),
        raising=False,
    )

    await calculations.calculate_indicator(
        CalculationRequest(
            concept="urn:sds:disclosure:csrd:e3-5",
            entity="madrid",
            period="2024",
        ),
        http_request=SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    unit_converter=object(),
                    conversion_engine=object(),
                )
            )
        ),
        graph=ExplodingGraph(),
        store=_store(),
        hierarchy_store=object(),
        db=None,
        current_user=object(),
    )

    assert factory_calls == []
    assert captured["conversion_engine"] is None


@pytest.mark.asyncio
async def test_batch_builds_one_request_scoped_engine_for_all_items(monkeypatch):
    factory_calls = []
    calculation_engines = []

    def fake_build_conversion_engine(db_session, converter):
        factory_calls.append((db_session, converter))
        return request_conversion_engine

    async def fake_run_calculation(request, **kwargs):
        calculation_engines.append(kwargs["conversion_engine"])
        return CalculationResponse(
            concept=request.concept,
            entity=request.entity,
            period=request.period,
            value=Decimal("1"),
            unit="m3",
            formula_used="x",
            confidence=1.0,
            trace=None,
            calculated_at=datetime.now(timezone.utc),
        )

    db = object()
    unit_converter = object()
    request_conversion_engine = object()
    monkeypatch.setattr(calculations, "_run_calculation", fake_run_calculation)
    monkeypatch.setattr(
        calculations,
        "build_conversion_engine",
        fake_build_conversion_engine,
        raising=False,
    )
    monkeypatch.setattr(calculations, "StandardMappingStore", lambda db=None: object())

    results = await calculations.batch_calculate_indicators(
        http_request=SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    unit_converter=unit_converter,
                    conversion_engine=object(),
                )
            )
        ),
        entity="madrid",
        period="2024",
        concepts="concept:a,concept:b",
        granularity=TemporalGranularity.ANNUAL,
        graph=ExplodingGraph(),
        store=_store(),
        hierarchy_store=object(),
        db=db,
        current_user=object(),
    )

    assert len(results) == 2
    assert len(factory_calls) == 1
    assert factory_calls[0][0] is db
    assert factory_calls[0][1] is unit_converter
    assert calculation_engines == [request_conversion_engine, request_conversion_engine]


@pytest.mark.asyncio
async def test_router_fails_closed_when_contract_runtime_has_no_contract():
    class MissingResolver:
        def resolve(self, concept: str):
            from src.calculation.contracts import ContractResolutionError

            raise ContractResolutionError(f"No calculation contract for {concept}")

    with pytest.raises(HTTPException) as exc_info:
        await calculations._run_calculation(
            CalculationRequest(
                concept="csrd:missing",
                entity="madrid",
                period="2024",
                granularity=TemporalGranularity.ANNUAL,
            ),
            graph=ExplodingGraph(),
            store=_store(),
            contract_resolver=MissingResolver(),
            allow_ontology_fallback=False,
        )

    assert exc_info.value.status_code == 404
    assert "No calculation contract" in exc_info.value.detail


@pytest.mark.asyncio
async def test_dependencies_endpoint_prefers_contract_runtime_when_db_first(
    monkeypatch,
):
    graph = ExplodingGraph()

    class Resolver:
        def __init__(self, db):
            self.db = db

        def resolve(self, concept: str):
            assert concept == "urn:sds:disclosure:csrd:e3-5"
            return _contract()

    monkeypatch.setattr(calculations.settings, "require_database", True)
    monkeypatch.setattr(calculations, "RuntimeCalculationContractResolver", Resolver)

    response = await calculations.get_calculation_dependencies(
        concept="urn:sds:disclosure:csrd:e3-5",
        graph=graph,
        db=object(),
        current_user=object(),
    )

    assert graph.used is False
    assert response["dependency_source"] == "canonical_contract"
    assert response["formula"] == "cooling + industrial"
    assert response["required_variables"] == [
        "syg:Water_Cooling",
        "syg:Water_Industrial",
    ]


@pytest.mark.asyncio
async def test_dependencies_endpoint_exposes_certified_bridge_input_policy(
    monkeypatch,
):
    class Resolver:
        def __init__(self, db):
            pass

        def resolve(self, concept: str):
            assert concept == "gri:302-1.e"
            return _gri_total_energy_bridge_contract()

    monkeypatch.setattr(calculations.settings, "require_database", True)
    monkeypatch.setattr(calculations, "RuntimeCalculationContractResolver", Resolver)

    response = await calculations.get_calculation_dependencies(
        concept="gri:302-1.e",
        graph=ExplodingGraph(),
        db=object(),
        current_user=object(),
    )

    assert response["required_variables"] == [
        "gri:302-1.e:fossil_component",
        "gri:302-1.e:renewable_component",
    ]
    assert response["supports_certified_bridge_inputs"] is True
    assert response["input_bindings"] == [
        {
            "local_variable": "fossil_energy",
            "concept": "gri:302-1.e:fossil_component",
            "unit": "MWh",
            "required": True,
            "entity_scope": "self",
            "temporal_aggregation": "sum",
            "perimeter_aggregation": "sum",
            "allowed_mapping_relationships": ["narrower"],
        },
        {
            "local_variable": "renewable_energy",
            "concept": "gri:302-1.e:renewable_component",
            "unit": "MWh",
            "required": True,
            "entity_scope": "self",
            "temporal_aggregation": "sum",
            "perimeter_aggregation": "sum",
            "allowed_mapping_relationships": ["narrower"],
        },
    ]


@pytest.mark.asyncio
async def test_dependencies_endpoint_does_not_mark_exact_mapping_policy_as_bridge(
    monkeypatch,
):
    contract = CalculationContract(
        contract_id="contract-gri-energy-exact",
        concept="gri:302-1.a",
        contract_version="2026.05",
        contract_hash="sha256:gri-energy-exact",
        runtime_status="executable",
        formula="fuel_energy",
        result_unit="MWh",
        inputs=(
            CalculationContractInput(
                "fuel_energy",
                "gri:302-1.a_input",
                unit="MWh",
                allowed_mapping_relationships=("equivalent",),
            ),
        ),
        resolution=ContractResolutionMetadata(source="canonical_db"),
    )

    class Resolver:
        def __init__(self, db):
            pass

        def resolve(self, concept: str):
            assert concept == "gri:302-1.a"
            return contract

    monkeypatch.setattr(calculations.settings, "require_database", True)
    monkeypatch.setattr(calculations, "RuntimeCalculationContractResolver", Resolver)

    response = await calculations.get_calculation_dependencies(
        concept="gri:302-1.a",
        graph=ExplodingGraph(),
        db=object(),
        current_user=object(),
    )

    assert response["input_bindings"][0]["allowed_mapping_relationships"] == [
        "equivalent"
    ]
    assert response["supports_certified_bridge_inputs"] is False


def _contract_with_exposure(exposure):
    return CalculationContract(
        contract_id="contract-exp",
        concept="urn:sds:disclosure:csrd:e3-5",
        contract_version="2026.05",
        contract_hash="sha256:exp",
        runtime_status="executable",
        formula="cooling + industrial",
        result_unit="m3",
        exposure=exposure,
        inputs=(
            CalculationContractInput("cooling", "syg:Water_Cooling", unit="m3"),
            CalculationContractInput("industrial", "syg:Water_Industrial", unit="m3"),
        ),
        resolution=ContractResolutionMetadata(source="canonical_db"),
    )


@pytest.mark.parametrize("exposure", ["audit_only", "runtime_support", "container"])
@pytest.mark.asyncio
async def test_hidden_contract_targets_are_terminal_generic_403(exposure):
    graph = ExplodingGraph()

    with pytest.raises(HTTPException) as denied:
        await calculations._run_calculation(
            CalculationRequest(
                concept="urn:sds:disclosure:csrd:e3-5",
                entity="madrid",
                period="2024",
                granularity=TemporalGranularity.ANNUAL,
                include_trace=True,
            ),
            graph=graph,
            store=_store(),
            contract_resolver=FakeResolver(_contract_with_exposure(exposure)),
            allow_ontology_fallback=True,
        )

    assert denied.value.status_code == 403
    assert denied.value.detail == "Calculation contract is not public."
    denial_text = str(denied.value.detail)
    for hidden_detail in (
        "contract-exp",
        "sha256:exp",
        "cooling + industrial",
        "syg:Water_Cooling",
        "syg:Water_Industrial",
        exposure,
    ):
        assert hidden_detail not in denial_text
    assert graph.used is False


@pytest.mark.asyncio
async def test_batch_hidden_contract_target_fails_without_metadata_leak(monkeypatch):
    class Resolver:
        def __init__(self, db):
            pass

        def resolve(self, concept):
            return _contract_with_exposure("runtime_support")

    monkeypatch.setattr(calculations.settings, "require_database", True)
    monkeypatch.setattr(calculations, "RuntimeCalculationContractResolver", Resolver)
    monkeypatch.setattr(
        calculations, "StandardMappingStore", lambda db=None: FakeMappingStore([])
    )
    http_request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(unit_converter=None, conversion_engine=None)
        )
    )

    results = await calculations.batch_calculate_indicators(
        http_request=http_request,
        entity="madrid",
        period="2024",
        concepts="urn:sds:disclosure:csrd:e3-5",
        granularity=TemporalGranularity.ANNUAL,
        graph=ExplodingGraph(),
        store=_store(),
        hierarchy_store=FakeHierarchyStore([]),
        db=object(),
        current_user=SimpleNamespace(role="analyst", company_id="company-a"),
    )

    assert len(results) == 1
    assert results[0].status.value == "failed"
    assert results[0].result is None
    assert results[0].error == "Calculation contract is not public."
    for hidden_detail in (
        "contract-exp",
        "sha256:exp",
        "cooling + industrial",
        "syg:Water_Cooling",
        "syg:Water_Industrial",
        "runtime_support",
    ):
        assert hidden_detail not in results[0].error


@pytest.mark.asyncio
async def test_public_parent_uses_nested_support_without_exposing_support_metadata():
    hidden_concept = "internal:runtime-support-total"
    hidden_input = "internal:runtime-support-observation"
    hidden_contract = CalculationContract(
        contract_id="hidden-support-contract",
        concept=hidden_concept,
        contract_version="2026.05",
        contract_hash="sha256:hidden-support",
        runtime_status="executable",
        formula="hidden_observation",
        result_unit="m3",
        exposure="runtime_support",
        inputs=(
            CalculationContractInput(
                "hidden_observation",
                hidden_input,
                unit="m3",
            ),
        ),
        resolution=ContractResolutionMetadata(source="canonical_db"),
    )
    public_contract = CalculationContract(
        contract_id="public-parent-contract",
        concept="urn:sds:disclosure:csrd:e3-5",
        contract_version="2026.05",
        contract_hash="sha256:public-parent",
        runtime_status="executable",
        formula="support_total",
        result_unit="m3",
        exposure="public_register",
        inputs=(CalculationContractInput("support_total", hidden_concept, unit="m3"),),
        resolution=ContractResolutionMetadata(source="canonical_db"),
    )
    store = InMemoryValueStore()
    store.create(
        value_id="support-source-value",
        concept=hidden_input,
        entity="madrid",
        period=date(2024, 1, 1),
        value=Decimal("7"),
        unit="m3",
        original_unit=None,
        conversion_applied=False,
        metadata={"source": "test"},
    )

    response = await calculations._run_calculation(
        CalculationRequest(
            concept=public_contract.concept,
            entity="madrid",
            period="2024",
            granularity=TemporalGranularity.ANNUAL,
            include_trace=True,
        ),
        graph=ExplodingGraph(),
        store=store,
        contract_resolver=RoutingResolver(
            {
                public_contract.concept: public_contract,
                hidden_concept: hidden_contract,
            }
        ),
        allow_ontology_fallback=False,
    )

    assert response.value == Decimal("7")
    payload = response.model_dump_json()
    assert "public-parent-contract" in payload
    assert "sha256:public-parent" in payload
    for hidden_detail in (
        "hidden-support-contract",
        "sha256:hidden-support",
        hidden_concept,
        hidden_input,
        "hidden_observation",
        "runtime_support",
    ):
        assert hidden_detail not in payload


@pytest.mark.parametrize("exposure", ["audit_only", "runtime_support", "container"])
@pytest.mark.asyncio
async def test_dependencies_endpoint_hides_non_public_contracts(monkeypatch, exposure):
    """codex G-AUDIT M2: /calculate/dependencies must not expose a non-public-register
    canonical contract's formula/variables."""

    class Resolver:
        def __init__(self, db):
            pass

        def resolve(self, concept):
            return _contract_with_exposure(exposure)

    monkeypatch.setattr(calculations.settings, "require_database", True)
    monkeypatch.setattr(calculations, "RuntimeCalculationContractResolver", Resolver)

    with pytest.raises(HTTPException) as exc:
        await calculations.get_calculation_dependencies(
            concept="urn:sds:disclosure:csrd:e3-5",
            graph=ExplodingGraph(),
            db=object(),
            current_user=object(),
        )
    assert exc.value.status_code == 404


@pytest.mark.parametrize("exposure", ["public_register", None])
@pytest.mark.asyncio
async def test_dependencies_endpoint_exposes_public_or_projected_contracts(
    monkeypatch, exposure
):
    """public_register canonical contracts and projected (exposure=None) formulas remain
    visible."""

    class Resolver:
        def __init__(self, db):
            pass

        def resolve(self, concept):
            return _contract_with_exposure(exposure)

    monkeypatch.setattr(calculations.settings, "require_database", True)
    monkeypatch.setattr(calculations, "RuntimeCalculationContractResolver", Resolver)

    response = await calculations.get_calculation_dependencies(
        concept="urn:sds:disclosure:csrd:e3-5",
        graph=ExplodingGraph(),
        db=object(),
        current_user=object(),
    )
    assert response["formula"] == "cooling + industrial"
