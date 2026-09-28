from __future__ import annotations

import builtins
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.calculation.contracts import (
    CalculationContractInput,
    ContractResolutionError,
    DerivedCalculationNode,
    RuntimeCalculationContractResolver,
    _as_sequence,
    _concept_uri_candidates,
    _DBCalculationContractRepository,
    _derived_nodes_from_canonical_row,
    _field,
    _json_loads,
    _normalize_conversion_policy,
    _normalize_currency,
    _runtime_formula_from_canonical_row,
    _semantic_formula_contract_from_concept_row,
    contract_from_row,
    validate_contract_shape,
)
from src.database import models as db_models


@dataclass
class FakeContractRow:
    contract_id: str = "contract-1"
    concept: str = "csrd:E3_5"
    contract_version: str = "2026.05"
    contract_hash: str = "sha256:abc"
    runtime_status: str = "executable"
    formula: str = "cooling + industrial"
    result_unit: str = "m3"
    inputs: list[dict] | None = None

    def __post_init__(self):
        if self.inputs is None:
            self.inputs = [
                {
                    "local_variable": "cooling",
                    "concept": "syg:Water_Cooling",
                    "unit": "m3",
                },
                {
                    "local_variable": "industrial",
                    "concept": "syg:Water_Industrial",
                    "unit": "m3",
                },
            ]


class FakeRepository:
    def __init__(self, row):
        self.row = row
        self.requested: list[str] = []

    def get_contract_for_concept(self, concept: str):
        self.requested.append(concept)
        return self.row


class FakeDBQuery:
    def __init__(self, row):
        self.row = row
        self.filters: list[object] = []
        self.ordering: list[object] = []

    def filter(self, *conditions):
        self.filters.extend(conditions)
        return self

    def order_by(self, *ordering):
        self.ordering.extend(ordering)
        return self

    def first(self):
        return self.row


class FakeDBSession:
    def __init__(self, row):
        self.row = row
        self.queried_models: list[type] = []

    def query(self, model):
        self.queried_models.append(model)
        return FakeDBQuery(self.row)


class FakeSemanticDBQuery:
    def __init__(self, model, session):
        self.model = model
        self.session = session
        self.with_options = False

    def options(self, *_options):
        self.with_options = True
        return self

    def filter(self, *_conditions):
        return self

    def order_by(self, *_ordering):
        return self

    def first(self):
        if self.model is db_models.CanonicalCalculationContract:
            return None
        if self.model is db_models.Concept:
            return self.session.concepts.pop(0)
        return None

    def all(self):
        if self.model is db_models.CanonicalCalculationContract:
            return []
        if self.model is db_models.Concept and self.with_options:
            return list(self.session.concepts)
        row = self.first()
        return [row] if row is not None else []


class FakeSemanticDBSession:
    def __init__(self):
        self.concepts = [
            SimpleNamespace(
                id=2,
                uri="https://data.efrag.org/esrs#E3_5",
                unit="sds:CubicMeter",
                formulas=[
                    SimpleNamespace(
                        id=1,
                        version=1,
                        expression="SUM(syg:Water_Industrial, syg:Water_Cooling)",
                        is_active=True,
                    )
                ],
                variables=[
                    SimpleNamespace(
                        variable_uri="syg:Water_Industrial",
                        ordering=0,
                        aggregation_method="SUM",
                    ),
                    SimpleNamespace(
                        variable_uri="syg:Water_Cooling",
                        ordering=1,
                        aggregation_method="SUM",
                    ),
                ],
            ),
            SimpleNamespace(unit="sds:CubicMeter"),
            SimpleNamespace(unit="sds:CubicMeter"),
        ]

    def query(self, model):
        return FakeSemanticDBQuery(model, self)


class FakeProjectedThenLegacySemanticDBSession(FakeSemanticDBSession):
    def __init__(self):
        super().__init__()
        self.concepts = [
            SimpleNamespace(
                id=1,
                uri="urn:sds:disclosure:csrd:e3-5",
                unit="sds:CubicMeter",
                formulas=[],
                variables=[],
            ),
            SimpleNamespace(
                id=3,
                uri="csrd:E3_5",
                unit="sds:CubicMeter",
                formulas=[
                    SimpleNamespace(
                        id=1,
                        version=1,
                        expression="SUM(syg:Water_Industrial, syg:Water_Cooling)",
                        is_active=True,
                    )
                ],
                variables=[
                    SimpleNamespace(
                        variable_uri="syg:Water_Industrial",
                        ordering=0,
                        aggregation_method="SUM",
                    ),
                    SimpleNamespace(
                        variable_uri="syg:Water_Cooling",
                        ordering=1,
                        aggregation_method="SUM",
                    ),
                ],
            ),
            SimpleNamespace(unit="sds:CubicMeter"),
            SimpleNamespace(unit="sds:CubicMeter"),
        ]


class FakeSemanticOnlyCanonicalThenSemanticDBQuery(FakeSemanticDBQuery):
    def first(self):
        if self.model is db_models.CanonicalCalculationContract:
            return self.session.canonical_contracts[0]
        if self.model is db_models.Concept and not self.with_options:
            return SimpleNamespace(unit="Currency")
        return super().first()

    def all(self):
        if self.model is db_models.CanonicalCalculationContract:
            return list(self.session.canonical_contracts)
        return super().all()


class FakeSemanticOnlyCanonicalThenSemanticDBSession(FakeSemanticDBSession):
    def __init__(self):
        super().__init__()
        self.canonical_contracts = [
            db_models.CanonicalCalculationContract(
                id=522,
                contract_version="1.0",
                model_id="model_0040",
                node_id="model_0040#E3-5_01",
                canonical_datapoint_id="E3-5_01",
                indicator_identifier="urn:sds:reg:esrs:e3_5_01",
                label="E3-5 semantic-only atomized datapoint",
                exposure="public_register",
                runtime_status="semantic_only",
                role="primary",
                source_kind="atomizer",
                formula_kind="input",
                runtime_expression=None,
                semantic_expression=None,
                value_kind="numeric",
                unit_name="Currency",
                aggregation_policy={},
                source_payload={},
                contract_hash="sem" * 21 + "s",
                is_active=True,
            )
        ]
        self.concepts = [
            SimpleNamespace(
                id=16331,
                uri="urn:sds:disclosure:csrd:e3-5",
                unit=None,
                formulas=[
                    SimpleNamespace(
                        id=2,
                        version=2,
                        expression="SUM(urn:sds:reg:esrs:e3_5_01)",
                        is_active=True,
                    )
                ],
                variables=[
                    SimpleNamespace(
                        variable_uri="urn:sds:reg:esrs:e3_5_01",
                        ordering=0,
                        aggregation_method="SUM",
                    )
                ],
            ),
            SimpleNamespace(unit="Currency"),
        ]

    def query(self, model):
        return FakeSemanticOnlyCanonicalThenSemanticDBQuery(model, self)


def _shape_contract(**overrides):
    data = {
        "contract_id": "contract-1",
        "concept": "csrd:E3_5",
        "contract_version": "2026.05",
        "contract_hash": "sha256:abc",
        "runtime_status": "executable",
        "formula": "water",
        "inputs": (CalculationContractInput("water", "syg:water"),),
        "derived_nodes": (),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_contract_input_currency_policy_and_default_value_edges():
    with pytest.raises(ValueError, match="expected_currency requires fx_policy_id"):
        CalculationContractInput(
            "revenue",
            "finance:revenue",
            expected_currency="EUR",
        )

    with pytest.raises(ValueError, match="fx_policy_id requires expected_currency"):
        CalculationContractInput(
            "revenue",
            "finance:revenue",
            fx_policy_id="ecb-monthly",
        )

    input_contract = CalculationContractInput(
        "water",
        "syg:water",
        default_value="1.25",
    )
    assert input_contract.default_value == Decimal("1.25")


def test_contract_shape_and_mapping_helpers_fail_closed():
    with pytest.raises(ContractResolutionError, match="missing fields"):
        validate_contract_shape(_shape_contract(contract_id=""))

    with pytest.raises(ContractResolutionError, match="no inputs"):
        validate_contract_shape(_shape_contract(inputs=()))

    with pytest.raises(ContractResolutionError, match="Invalid local variable"):
        validate_contract_shape(
            _shape_contract(inputs=(SimpleNamespace(local_variable="not valid"),))
        )

    assert _as_sequence(None) == []
    assert _as_sequence('{"a": 1, "b": 2}') == [1, 2]
    assert _as_sequence({"a": 1}) == [1]
    assert _as_sequence(7) == [7]
    assert _json_loads("{bad", default=["fallback"]) == ["fallback"]
    assert _field({}, "missing", default="fallback") == "fallback"
    with pytest.raises(ContractResolutionError, match="missing field"):
        _field({}, "missing")
    with pytest.raises(ValueError, match="invalid currency"):
        _normalize_currency("EURO", "currency")
    with pytest.raises(ValueError, match="unsupported conversion_policy"):
        _normalize_conversion_policy("best_effort")


def test_canonical_formula_payload_and_repository_fallback_edges():
    assert _derived_nodes_from_canonical_row(SimpleNamespace(source_payload=[])) == ()
    assert (
        _derived_nodes_from_canonical_row(
            SimpleNamespace(source_payload={"formula": []})
        )
        == ()
    )
    assert (
        _runtime_formula_from_canonical_row(
            SimpleNamespace(
                runtime_expression=None,
                semantic_expression=None,
                formula=None,
                formula_expression=None,
                formula_kind="constant",
                canonical_datapoint_id="fixture_metric",
                indicator_identifier=None,
                node_id="fixture:metric",
            )
        )
        == "fixture_metric"
    )
    with pytest.raises(ContractResolutionError, match="no runtime expression"):
        _runtime_formula_from_canonical_row(
            SimpleNamespace(
                runtime_expression=None,
                semantic_expression=None,
                formula=None,
                formula_expression=None,
                formula_kind="ratio",
                node_id="fixture:ratio",
            )
        )

    assert (
        _DBCalculationContractRepository(None).get_contract_for_concept("missing")
        is None
    )


def test_contract_row_string_hierarchy_and_non_mapping_payload_edges(monkeypatch):
    row = FakeContractRow()
    row.hierarchy = '{"global": ["site"]}'
    contract = contract_from_row(row, requested_concept="csrd:E3_5")
    assert contract.hierarchy == {"global": ["site"]}

    canonical = SimpleNamespace(
        id=30,
        contract_version="1.0",
        model_id="SDS_CALCULATION_CONTRACT_REGRESSION_V1",
        node_id="fixture:string_hierarchy",
        canonical_datapoint_id="fixture_string_hierarchy",
        indicator_identifier="urn:sds:reg:test:string_hierarchy",
        label="Fixture string hierarchy",
        exposure="public_register",
        runtime_status="executable",
        role="primary",
        source_kind="fixture",
        formula_kind="input",
        runtime_expression="fixture_string_hierarchy",
        value_kind="numeric",
        unit_name="count",
        aggregation_policy='{"temporal": "SUM", "perimeter": "SUM"}',
        hierarchy='{"global": ["site"]}',
        hierarchy_config_id=None,
        source_payload=[],
        contract_hash="str" * 21 + "s",
        is_active=True,
        result_currency=None,
        conversion_policy="fail_closed",
    )
    canonical.components = []
    contract = contract_from_row(
        canonical, requested_concept=canonical.indicator_identifier
    )
    assert contract.hierarchy == {"global": ["site"]}
    assert contract.inputs[0].local_variable == "fixture_string_hierarchy"

    component = db_models.CanonicalCalculationComponent(
        component_order=1,
        component_id="fixture_component",
        component_node_id="fixture:component",
        unit_name="count",
        required=True,
        source_payload=[],
    )
    canonical.components = [component]
    canonical.runtime_expression = "fixture_component"
    contract = contract_from_row(
        canonical, requested_concept=canonical.indicator_identifier
    )
    assert contract.inputs[0].local_variable == "fixture_component"

    assert (
        _derived_nodes_from_canonical_row(
            SimpleNamespace(source_payload={"formula": []})
        )
        == ()
    )

    monkeypatch.setattr(
        _DBCalculationContractRepository, "_model", staticmethod(lambda: None)
    )
    assert (
        _DBCalculationContractRepository(
            SimpleNamespace(query=lambda *_args: None)
        ).get_contract_for_concept("missing")
        is None
    )


def test_db_contract_repository_model_lookup_and_or_import_fallback(monkeypatch):
    original_attrs = {}
    for name in (
        "CanonicalCalculationContract",
        "CalculationContract",
        "CalculationContractRecord",
        "IndicatorCalculationContract",
    ):
        original_attrs[name] = getattr(db_models, name, None)
        monkeypatch.setattr(db_models, name, None, raising=False)
    assert _DBCalculationContractRepository._model() is None

    for name, value in original_attrs.items():
        if value is not None:
            monkeypatch.setattr(db_models, name, value, raising=False)

    class _Column:
        def __eq__(self, _other):
            return ("eq", _other)

        def is_(self, _value):
            return ("is", _value)

        def desc(self):
            return ("desc", self)

    class _CanonicalModel:
        is_active = _Column()
        indicator_identifier = _Column()
        created_at = _Column()

    _CanonicalModel.__name__ = "CanonicalCalculationContract"

    class _Query:
        def __init__(self):
            self.filters = []

        def filter(self, *conditions):
            self.filters.append(conditions)
            return self

        def order_by(self, *_ordering):
            return self

        def first(self):
            return None

    query = _Query()
    db = SimpleNamespace(query=lambda _model: query)
    monkeypatch.setattr(
        _DBCalculationContractRepository,
        "_model",
        staticmethod(lambda: _CanonicalModel),
    )

    real_import = builtins.__import__

    def blocking_import(name, *args, **kwargs):
        if name == "sqlalchemy":
            raise RuntimeError("sqlalchemy unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocking_import)
    assert _DBCalculationContractRepository(db).get_contract_for_concept("x") is None
    assert any(condition for filters in query.filters for condition in filters)


def test_db_repository_non_canonical_lookup_and_model_import_failure(monkeypatch):
    original_model = _DBCalculationContractRepository._model

    class _Column:
        def __eq__(self, _other):
            return ("eq", _other)

        def desc(self):
            return ("desc", self)

    class _LegacyModel:
        concept = _Column()
        created_at = _Column()

    _LegacyModel.__name__ = "CalculationContract"

    class _Query:
        def __init__(self):
            self.filters = []
            self.ordering = []

        def filter(self, *conditions):
            self.filters.append(conditions)
            return self

        def order_by(self, *ordering):
            self.ordering.append(ordering)
            return self

        def first(self):
            return None

    query = _Query()
    monkeypatch.setattr(
        _DBCalculationContractRepository,
        "_model",
        staticmethod(lambda: _LegacyModel),
    )
    assert (
        _DBCalculationContractRepository(
            SimpleNamespace(query=lambda _model: query)
        ).get_contract_for_concept("legacy:concept")
        is None
    )
    assert query.filters
    assert query.ordering

    real_import = builtins.__import__

    def blocking_import(name, *args, **kwargs):
        if name == "src.database":
            raise RuntimeError("models unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocking_import)
    monkeypatch.setattr(
        _DBCalculationContractRepository, "_model", staticmethod(original_model)
    )
    assert _DBCalculationContractRepository._model() is None


def test_canonical_contract_private_payload_fallbacks():
    assert (
        _derived_nodes_from_canonical_row(SimpleNamespace(source_payload=["bad"])) == ()
    )
    assert (
        _derived_nodes_from_canonical_row(
            SimpleNamespace(source_payload={"formula": ["bad"]})
        )
        == ()
    )

    row = SimpleNamespace(
        id=1,
        contract_version="1",
        node_id="node",
        canonical_datapoint_id="concept",
        indicator_identifier=None,
        runtime_status="executable",
        runtime_expression="input",
        formula_kind="formula",
        unit_name="kg",
        aggregation_policy={},
        source_payload=[],
        contract_hash="hash",
        result_currency=None,
        conversion_policy="fail_closed",
        components=[],
    )
    contract = contract_from_row(row, requested_concept="concept")

    assert contract.inputs[0].concept == "concept"

    component = db_models.CanonicalCalculationComponent(
        component_order=1,
        component_id="component_id",
        component_node_id="component:node",
        source_payload=[],
        unit_name="kg",
        required=True,
    )
    row.components = [component]
    contract = contract_from_row(row, requested_concept="concept")
    assert contract.inputs[0].local_variable == "input"

    component.source_payload = ["bad"]
    contract = contract_from_row(row, requested_concept="concept")
    assert contract.inputs[0].local_variable == "input"

    row.aggregation_policy = ["bad"]
    row.source_payload = ["bad"]
    row.components = []
    with pytest.raises(ContractResolutionError, match="aggregation_policy"):
        contract_from_row(row, requested_concept="concept")
    row.aggregation_policy = "{invalid-json"
    with pytest.raises(ContractResolutionError, match="aggregation_policy JSON"):
        contract_from_row(row, requested_concept="concept")
    row.aggregation_policy = "null"
    with pytest.raises(ContractResolutionError, match="aggregation_policy shape"):
        contract_from_row(row, requested_concept="concept")


def test_resolver_wraps_unexpected_contract_row_errors():
    row = FakeContractRow(
        inputs=[
            {
                "local_variable": "revenue",
                "concept": "finance:revenue",
                "expected_currency": "EUR",
            }
        ]
    )
    resolver = RuntimeCalculationContractResolver(repository=FakeRepository(row))

    with pytest.raises(ContractResolutionError, match="Invalid calculation contract"):
        resolver.resolve("csrd:E3_5")


def test_resolver_loads_contract_from_duck_typed_db_row():
    resolver = RuntimeCalculationContractResolver(
        repository=FakeRepository(FakeContractRow())
    )

    contract = resolver.resolve("csrd:E3_5")

    assert contract.concept == "csrd:E3_5"
    assert contract.contract_version == "2026.05"
    assert contract.contract_hash == "sha256:abc"
    assert contract.runtime_status == "executable"
    assert contract.resolver_source == "canonical_db"
    assert contract.inputs == (
        CalculationContractInput(
            local_variable="cooling",
            concept="syg:Water_Cooling",
            unit="m3",
        ),
        CalculationContractInput(
            local_variable="industrial",
            concept="syg:Water_Industrial",
            unit="m3",
        ),
    )


def test_resolver_carries_currency_and_policy_fields_from_duck_typed_row():
    row = FakeContractRow(
        result_unit="EURm",
        inputs=[
            {
                "local_variable": "revenue",
                "concept": "finance:revenue",
                "unit": "EURm",
                "currency": " gbp ",
                "expected_currency": "eur",
                "conversion_policy": "FAIL_CLOSED",
                "fx_policy_id": "ecb-monthly",
            }
        ],
    )
    row.result_currency = " eur "
    row.conversion_policy = "FAIL_CLOSED"
    resolver = RuntimeCalculationContractResolver(repository=FakeRepository(row))

    contract = resolver.resolve("csrd:E3_5")

    assert contract.result_currency == "EUR"
    assert contract.conversion_policy == "fail_closed"
    assert contract.inputs == (
        CalculationContractInput(
            local_variable="revenue",
            concept="finance:revenue",
            unit="EURm",
            currency="GBP",
            expected_currency="EUR",
            conversion_policy="fail_closed",
            fx_policy_id="ecb-monthly",
        ),
    )


def test_db_repository_reads_imported_canonical_calculation_contract_model():
    row = db_models.CanonicalCalculationContract(
        id=17,
        contract_version="1.0",
        model_id="SDS_CALCULATION_CONTRACT_REGRESSION_V1",
        node_id="fixture:scope1_total_emissions",
        canonical_datapoint_id="fixture_scope1_total_emissions",
        indicator_identifier="urn:sds:reg:test:scope1_total_emissions",
        label="Fixture Scope 1 total emissions",
        exposure="public_register",
        runtime_status="executable",
        role="primary",
        source_kind="fixture",
        formula_kind="sum",
        runtime_expression="sum(stationary_tco2e, mobile_tco2e)",
        value_kind="numeric",
        unit_name="tCO2e",
        unit_type="GHGEmissions",
        result_currency="EUR",
        conversion_policy="fail_closed",
        aggregation_policy={"temporal": "SUM", "perimeter": "SUM"},
        contract_hash="abc" * 21 + "a",
        is_active=True,
    )
    row.components = [
        db_models.CanonicalCalculationComponent(
            component_order=1,
            component_id="fixture_scope1_stationary_tco2e",
            component_node_id="fixture:scope1_stationary_tco2e",
            unit_name="tCO2e",
            currency="GBP",
            expected_currency="EUR",
            conversion_policy="fail_closed",
            fx_policy_id="ecb-monthly",
            required=True,
        ),
        db_models.CanonicalCalculationComponent(
            component_order=2,
            component_id="fixture_scope1_mobile_tco2e",
            component_node_id="fixture:scope1_mobile_tco2e",
            unit_name="tCO2e",
            required=True,
        ),
    ]
    db = FakeDBSession(row)
    resolver = RuntimeCalculationContractResolver(db=db)

    contract = resolver.resolve("urn:sds:reg:test:scope1_total_emissions")

    assert db.queried_models == [db_models.CanonicalCalculationContract]
    assert contract.contract_id == "fixture:scope1_total_emissions"
    assert contract.concept == "urn:sds:reg:test:scope1_total_emissions"
    assert contract.formula == "stationary_tco2e + mobile_tco2e"
    assert contract.result_unit == "tCO2e"
    assert contract.result_currency == "EUR"
    assert contract.conversion_policy == "fail_closed"
    assert contract.resolver_source == "canonical_calculation_contracts"
    assert contract.inputs == (
        CalculationContractInput(
            local_variable="stationary_tco2e",
            concept="fixture_scope1_stationary_tco2e",
            unit="tCO2e",
            currency="GBP",
            expected_currency="EUR",
            fx_policy_id="ecb-monthly",
        ),
        CalculationContractInput(
            local_variable="mobile_tco2e",
            concept="fixture_scope1_mobile_tco2e",
            unit="tCO2e",
        ),
    )


def test_db_repository_builds_runtime_contract_from_semantic_formula_when_canonical_missing():
    resolver = RuntimeCalculationContractResolver(db=FakeSemanticDBSession())

    contract = resolver.resolve("csrd:E3_5")

    assert contract.contract_id == "semantic-db:2"
    assert contract.concept == "csrd:E3_5"
    assert contract.formula == "Water_Industrial + Water_Cooling"
    assert contract.result_unit == "m3"
    assert contract.resolver_source == "semantic_db"
    assert contract.inputs == (
        CalculationContractInput(
            local_variable="Water_Industrial",
            concept="syg:Water_Industrial",
            unit="m3",
        ),
        CalculationContractInput(
            local_variable="Water_Cooling",
            concept="syg:Water_Cooling",
            unit="m3",
        ),
    )


def test_semantic_formula_contract_resolves_new_disclosure_uri_through_legacy_alias():
    resolver = RuntimeCalculationContractResolver(db=FakeSemanticDBSession())

    contract = resolver.resolve("urn:sds:disclosure:csrd:e3-5")

    assert _concept_uri_candidates("urn:sds:disclosure:csrd:e3-5") == [
        "urn:sds:disclosure:csrd:e3-5",
        "csrd:E3_5",
        "https://data.efrag.org/esrs#E3_5",
    ]
    assert contract.contract_id == "semantic-db:2"
    assert contract.concept == "urn:sds:disclosure:csrd:e3-5"
    assert contract.formula == "Water_Industrial + Water_Cooling"
    assert contract.result_unit == "m3"
    assert contract.resolver_source == "semantic_db"


def test_semantic_formula_contract_uses_single_input_unit_when_disclosure_unit_missing(
    monkeypatch,
):
    monkeypatch.setattr(
        "src.calculation.contracts._semantic_input_unit",
        lambda _db, _variable, _fallback_unit: "Currency",
    )
    concept_row = SimpleNamespace(
        id=16331,
        uri="urn:sds:disclosure:csrd:e3-5",
        unit=None,
        formulas=[
            SimpleNamespace(
                id=2,
                version=2,
                expression="SUM(urn:sds:reg:esrs:e3_5_01)",
                is_active=True,
            )
        ],
        variables=[
            SimpleNamespace(
                variable_uri="urn:sds:reg:esrs:e3_5_01",
                ordering=0,
                aggregation_method="SUM",
            )
        ],
    )

    row = _semantic_formula_contract_from_concept_row(
        object(), concept_row, "urn:sds:disclosure:csrd:e3-5"
    )
    contract = contract_from_row(row, requested_concept="urn:sds:disclosure:csrd:e3-5")

    assert contract.formula == "e3_5_01"
    assert contract.result_unit == "Currency"
    assert contract.inputs == (
        CalculationContractInput(
            local_variable="e3_5_01",
            concept="urn:sds:reg:esrs:e3_5_01",
            unit="Currency",
        ),
    )


def test_semantic_formula_contract_wins_when_only_canonical_match_is_semantic_only():
    resolver = RuntimeCalculationContractResolver(
        db=FakeSemanticOnlyCanonicalThenSemanticDBSession()
    )

    contract = resolver.resolve("urn:sds:disclosure:csrd:e3-5")

    assert contract.resolver_source == "semantic_db"
    assert contract.runtime_status == "executable"
    assert contract.formula == "e3_5_01"
    assert contract.result_unit == "Currency"
    assert contract.inputs == (
        CalculationContractInput(
            local_variable="e3_5_01",
            concept="urn:sds:reg:esrs:e3_5_01",
            unit="Currency",
        ),
    )


def test_semantic_formula_contract_skips_empty_projected_disclosure_alias():
    resolver = RuntimeCalculationContractResolver(
        db=FakeProjectedThenLegacySemanticDBSession()
    )

    contract = resolver.resolve("urn:sds:disclosure:csrd:e3-5")

    assert contract.contract_id == "semantic-db:3"
    assert contract.concept == "urn:sds:disclosure:csrd:e3-5"
    assert contract.formula == "Water_Industrial + Water_Cooling"
    assert contract.inputs[0].concept == "syg:Water_Industrial"


def test_canonical_weighted_average_function_name_is_not_bound_as_input():
    row = db_models.CanonicalCalculationContract(
        id=20,
        contract_version="1.0",
        model_id="SDS_CALCULATION_CONTRACT_REGRESSION_V1",
        node_id="fixture:weighted_training_score",
        canonical_datapoint_id="fixture_weighted_training_score",
        indicator_identifier="urn:sds:reg:test:weighted_training_score",
        label="Fixture weighted training score",
        exposure="public_register",
        runtime_status="executable",
        role="primary",
        source_kind="fixture",
        formula_kind="weighted_average",
        runtime_expression="weighted_average(training_score_points, training_employee_count)",
        value_kind="numeric",
        unit_name="points",
        unit_type="Score",
        aggregation_policy={
            "temporal": "WEIGHTED_AVERAGE",
            "perimeter": "WEIGHTED_AVERAGE",
        },
        contract_hash="jkl" * 21 + "j",
        is_active=True,
    )
    row.components = [
        db_models.CanonicalCalculationComponent(
            component_order=1,
            component_id="fixture_training_score_points",
            unit_name="points",
            required=True,
        ),
        db_models.CanonicalCalculationComponent(
            component_order=2,
            component_id="fixture_training_employee_count",
            unit_name="FTE",
            required=True,
        ),
    ]
    resolver = RuntimeCalculationContractResolver(db=FakeDBSession(row))

    contract = resolver.resolve("urn:sds:reg:test:weighted_training_score")

    assert contract.inputs == (
        CalculationContractInput(
            local_variable="training_score_points",
            concept="fixture_training_score_points",
            unit="points",
        ),
        CalculationContractInput(
            local_variable="training_employee_count",
            concept="fixture_training_employee_count",
            unit="FTE",
        ),
    )


def test_canonical_input_contract_keeps_non_sum_aggregation_policy():
    row = db_models.CanonicalCalculationContract(
        id=18,
        contract_version="1.0",
        model_id="SDS_CALCULATION_CONTRACT_REGRESSION_V1",
        node_id="fixture:average_headcount",
        canonical_datapoint_id="fixture_average_headcount",
        indicator_identifier="urn:sds:reg:test:average_headcount",
        label="Fixture average headcount",
        exposure="public_register",
        runtime_status="executable",
        role="primary",
        source_kind="fixture",
        formula_kind="input",
        runtime_expression="fixture_average_headcount",
        value_kind="numeric",
        unit_name="FTE",
        unit_type="Workforce",
        aggregation_policy={
            "temporal": "AVERAGE_OVER_PERIOD",
            "perimeter": "AVERAGE",
            "entity_scope": "self_and_children",
        },
        contract_hash="def" * 21 + "d",
        is_active=True,
    )
    row.components = []
    resolver = RuntimeCalculationContractResolver(db=FakeDBSession(row))

    contract = resolver.resolve("urn:sds:reg:test:average_headcount")

    assert contract.formula == "fixture_average_headcount"
    assert contract.inputs == (
        CalculationContractInput(
            local_variable="fixture_average_headcount",
            concept="fixture_average_headcount",
            unit="FTE",
            temporal_aggregation="average",
            perimeter_aggregation="average",
            entity_scope="self_and_children",
        ),
    )


def test_canonical_input_contract_rejects_unknown_aggregation_policy():
    row = db_models.CanonicalCalculationContract(
        id=180,
        contract_version="1.0",
        model_id="SDS_CALCULATION_CONTRACT_REGRESSION_V1",
        node_id="fixture:unknown_aggregation",
        canonical_datapoint_id="fixture_unknown_aggregation",
        indicator_identifier="urn:sds:reg:test:unknown_aggregation",
        label="Fixture unknown aggregation",
        exposure="public_register",
        runtime_status="executable",
        role="primary",
        source_kind="fixture",
        formula_kind="input",
        runtime_expression="fixture_unknown_aggregation",
        value_kind="numeric",
        unit_name="kg",
        unit_type="Mass",
        aggregation_policy={"temporal": "MEDIAN", "perimeter": "SUM"},
        contract_hash="bad" * 21 + "b",
        is_active=True,
    )
    row.components = []
    resolver = RuntimeCalculationContractResolver(db=FakeDBSession(row))

    with pytest.raises(ContractResolutionError, match="unsupported aggregation"):
        resolver.resolve("urn:sds:reg:test:unknown_aggregation")


def test_canonical_contract_reconstructs_derived_nodes_from_source_payload():
    row = db_models.CanonicalCalculationContract(
        id=19,
        contract_version="1.0",
        model_id="SDS_CALCULATION_CONTRACT_REGRESSION_V1",
        node_id="fixture:complex_equation",
        canonical_datapoint_id="fixture_complex_equation",
        indicator_identifier="urn:sds:reg:test:complex_equation",
        label="Fixture complex equation",
        exposure="public_register",
        runtime_status="executable",
        role="primary",
        source_kind="fixture",
        formula_kind="formula",
        runtime_expression="sqrt(weighted_total)",
        value_kind="numeric",
        unit_name="score",
        unit_type="Score",
        aggregation_policy={"temporal": "SUM", "perimeter": "SUM"},
        source_payload={
            "formula": {
                "derived_nodes": [
                    {
                        "local_variable": "weighted_total",
                        "expression": "pow(base, 2) + adjustment",
                        "dependencies": ["base", "adjustment"],
                    }
                ]
            }
        },
        contract_hash="ghi" * 21 + "g",
        is_active=True,
    )
    row.components = [
        db_models.CanonicalCalculationComponent(
            component_order=1,
            component_id="fixture_base",
            unit_name="score",
            required=True,
            source_payload={"local_variable": "base"},
        ),
        db_models.CanonicalCalculationComponent(
            component_order=2,
            component_id="fixture_adjustment",
            unit_name="score",
            required=True,
            source_payload={"local_variable": "adjustment"},
        ),
    ]
    resolver = RuntimeCalculationContractResolver(db=FakeDBSession(row))

    contract = resolver.resolve("urn:sds:reg:test:complex_equation")

    assert contract.derived_nodes == (
        DerivedCalculationNode(
            local_variable="weighted_total",
            expression="pow(base, 2) + adjustment",
            dependencies=("base", "adjustment"),
        ),
    )


def test_resolver_fails_closed_when_contract_is_missing():
    resolver = RuntimeCalculationContractResolver(repository=FakeRepository(None))

    with pytest.raises(ContractResolutionError, match="No calculation contract"):
        resolver.resolve("csrd:missing")


def test_resolver_rejects_duplicate_local_variables():
    row = FakeContractRow(
        inputs=[
            {"local_variable": "water", "concept": "syg:Water_Cooling"},
            {"local_variable": "water", "concept": "syg:Water_Industrial"},
        ]
    )
    resolver = RuntimeCalculationContractResolver(repository=FakeRepository(row))

    with pytest.raises(ContractResolutionError, match="Duplicate local variable"):
        resolver.resolve("csrd:E3_5")


def test_resolver_imports_non_executable_contract_without_promoting_it():
    row = FakeContractRow(runtime_status="draft")
    resolver = RuntimeCalculationContractResolver(repository=FakeRepository(row))

    contract = resolver.resolve("csrd:E3_5")

    assert contract.runtime_status == "draft"
    assert contract.is_executable is False
