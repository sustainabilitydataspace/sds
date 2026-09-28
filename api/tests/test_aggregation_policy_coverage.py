from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.calculation import aggregation_policy
from src.calculation.aggregation_policy import (
    _aggregate_axis,
    _aggregate_perimeter,
    aggregate_observations,
    resolve_entities,
)
from src.calculation.contracts import CalculationContractInput, ContractExecutionError
from src.calculation.value_provider import ObservationRecord


def _input(**overrides) -> CalculationContractInput:
    kwargs = {
        "local_variable": "amount",
        "concept": "sds:amount",
        "unit": "kg",
        "temporal_aggregation": "sum",
        "perimeter_aggregation": "sum",
        "entity_scope": "self",
    }
    kwargs.update(overrides)
    return CalculationContractInput(**kwargs)


def _obs(
    value_id: str, entity: str, value: str, *, unit: str = "kg"
) -> ObservationRecord:
    return ObservationRecord(
        value_id=value_id,
        concept="sds:amount",
        entity=entity,
        period=date(2024, 1, 31),
        value=Decimal(value),
        unit=unit,
    )


def test_resolve_entities_handles_children_and_rejects_unknown_scope():
    context = SimpleNamespace(entity_id="parent")

    assert resolve_entities(
        _input(entity_scope="self_and_children"),
        context,
        {"parent": ["child-1", "child-2"]},
    ) == ["parent", "child-1", "child-2"]

    with pytest.raises(ContractExecutionError, match="no children"):
        resolve_entities(_input(entity_scope="children"), context, {})

    with pytest.raises(ContractExecutionError, match="Unsupported entity scope"):
        resolve_entities(_input(entity_scope="group"), context, {})


def test_aggregate_observations_rejects_empty_and_late_invalid_perimeter(monkeypatch):
    with pytest.raises(ContractExecutionError, match="Cannot aggregate empty"):
        aggregate_observations(_input(), [])

    monkeypatch.setattr(
        aggregation_policy,
        "_aggregate_perimeter",
        lambda temporal_values, perimeter_rule: Decimal("1"),
    )

    with pytest.raises(ContractExecutionError, match="Unsupported perimeter"):
        aggregate_observations(
            _input(perimeter_aggregation="unsupported"),
            [_obs("v1", "entity-1", "1")],
        )


def test_aggregate_policy_unit_normalizer_convert_hook_and_errors():
    class ConvertOnlyNormalizer:
        def convert(self, value, from_unit, to_unit):
            assert (from_unit, to_unit) == ("g", "kg")
            return SimpleNamespace(
                converted_value=Decimal("2"), conversion_factor="0.001"
            )

    outcome = aggregate_observations(
        _input(unit="kg"),
        [_obs("v1", "entity-1", "2000", unit="g")],
        unit_normalizer=ConvertOnlyNormalizer(),
    )

    assert outcome.value == Decimal("2")
    assert outcome.conversions == [
        {
            "variable": "amount",
            "value_id": "v1",
            "from_unit": "g",
            "to_unit": "kg",
            "factor": "0.001",
        }
    ]

    with pytest.raises(ContractExecutionError, match="Mixed units"):
        aggregate_observations(
            _input(unit="kg"), [_obs("v1", "entity-1", "1", unit="g")]
        )

    with pytest.raises(ContractExecutionError, match="Invalid unit normalizer"):
        aggregate_observations(
            _input(unit="kg"),
            [_obs("v1", "entity-1", "1", unit="g")],
            unit_normalizer=object(),
        )


def test_axis_and_perimeter_reject_unknown_rules():
    with pytest.raises(
        ContractExecutionError, match="Unsupported temporal aggregation"
    ):
        _aggregate_axis(
            [(_obs("v1", "entity-1", "1"), Decimal("1"))], "median", axis="temporal"
        )

    with pytest.raises(
        ContractExecutionError, match="Unsupported perimeter aggregation"
    ):
        _aggregate_perimeter([("entity-1", Decimal("1"))], "median")
