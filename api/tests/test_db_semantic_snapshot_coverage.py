from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.calculation import db_semantic_snapshot as snapshot_module
from src.calculation.db_semantic_snapshot import (
    _snapshot_from_concept,
    build_seeded_variable_values,
    evaluate_db_semantic_formula,
    load_db_semantic_snapshots,
)


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def options(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def filter(self, *_args):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows

    def query(self, _model):
        return _Query(self.rows)


def _concept(**overrides):
    data = {
        "uri": "syg:Total",
        "label": "Total",
        "description": None,
        "taxonomy": "Sygris",
        "concept_type": "Disclosure",
        "unit": "sds:CubicMeter",
        "temporal_granularity": "annual",
        "concept_state": "calculable",
        "formulas": [],
        "variables": [],
        "equivalences": [],
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_db_semantic_snapshot_orders_active_formula_variables_and_equivalences():
    concept = _concept(
        formulas=[
            SimpleNamespace(id=1, version=1, is_active=False, expression="old"),
            SimpleNamespace(
                id=2,
                version=1,
                is_active=True,
                expression="a",
                expression_language="sds",
            ),
            SimpleNamespace(
                id=3,
                version=2,
                is_active=True,
                expression="a + b",
                expression_language="sds",
            ),
        ],
        variables=[
            SimpleNamespace(
                id=2,
                variable_uri="syg:B",
                variable_label="B",
                ordering=2,
                aggregation_method="SUM",
                temporal_granularity="annual",
            ),
            SimpleNamespace(
                id=1,
                variable_uri="syg:A",
                variable_label="A",
                ordering=1,
                aggregation_method="SUM",
                temporal_granularity="annual",
            ),
        ],
        equivalences=[
            SimpleNamespace(
                id=2,
                target_uri="gri:303_3",
                target_taxonomy="GRI",
                relationship_type="equivalent",
            ),
            SimpleNamespace(
                id=1,
                target_uri="csrd:E3_5",
                target_taxonomy="CSRD",
                relationship_type="same_as",
            ),
        ],
    )

    snapshot = _snapshot_from_concept(concept)

    assert snapshot.formula_expression == "a + b"
    assert [variable.uri for variable in snapshot.variables] == ["syg:A", "syg:B"]
    assert snapshot.display_unit is not None
    assert snapshot.comparable_formula == "a + b"
    assert [item.relationship_type for item in snapshot.equivalences] == [
        "equivalent",
        "same_as",
    ]


def test_load_db_semantic_snapshots_empty_uri_filter_and_formula_evaluation(
    monkeypatch,
):
    assert load_db_semantic_snapshots(_Session([]), uris=[]) == {}

    concept = _concept(
        formulas=[],
        variables=[
            SimpleNamespace(
                id=1,
                variable_uri="syg:A",
                variable_label="A",
                ordering=1,
                aggregation_method="SUM",
                temporal_granularity=None,
            )
        ],
        equivalences=[],
    )
    snapshots = load_db_semantic_snapshots(_Session([concept]), uris=["syg:Total"])
    snapshot = snapshots["syg:Total"]

    assert build_seeded_variable_values(["syg:A", "plain"]) == {
        "A": Decimal("1"),
        "plain": Decimal("2"),
    }
    value, unit, formula = evaluate_db_semantic_formula(snapshot, {"A": Decimal("3")})
    assert value == Decimal("3")
    assert unit is not None
    assert formula == "A"

    with pytest.raises(ValueError, match="No evaluable formula"):
        evaluate_db_semantic_formula(_snapshot_from_concept(_concept()), {})

    monkeypatch.setattr(
        snapshot_module,
        "CalculationEngine",
        lambda: SimpleNamespace(_evaluate_formula=lambda *_args: 1.5),
    )
    value, _unit, _formula = evaluate_db_semantic_formula(
        _snapshot_from_concept(
            _concept(
                formulas=[
                    SimpleNamespace(
                        id=1,
                        version=1,
                        is_active=True,
                        expression="1.5",
                        expression_language="sds",
                    )
                ]
            )
        ),
        {},
    )
    assert value == Decimal("1.5")
