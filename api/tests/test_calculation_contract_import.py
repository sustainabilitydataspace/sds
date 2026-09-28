from __future__ import annotations

import csv
import importlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Text

from src.calculation.contracts import RuntimeCalculationContractResolver
from src.calculation.engine import CalculationContext, CalculationEngine
from src.calculation.value_provider import ObservationRecord, StaticObservationProvider
from src.database import models as db_models

FIXTURE_PACKAGE_DIR = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "atomizer_packages"
    / "calculation-contract-regression-v1"
)
WEIGHTED_FIXTURE_PACKAGE_DIR = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "atomizer_packages"
    / "calculation-contract-weighted-average-v1"
)


def _service_module():
    try:
        return importlib.import_module("src.services.calculation_contract_import")
    except ModuleNotFoundError as exc:
        pytest.fail(f"calculation contract import service is missing: {exc}")


def _valid_payload() -> dict[str, Any]:
    return {
        "contract_version": "1.0",
        "source_package": {
            "producer": "atomizer",
            "manifest_hash": "a" * 64,
            "generated_at": "2026-05-19T00:00:00Z",
        },
        "register": {
            "path": "sds_dataset_register.csv",
            "sha256": "b" * 64,
        },
        "source": {
            "project": "atomizer",
            "generator": "tests",
            "canonical_model_sha256": "c" * 64,
        },
        "models": [
            {
                "model_id": "GHG_SCOPE3_CATEGORY_15_CALCULATION",
                "standard_id": "GHG Protocol",
                "requirement_id": "scope3-calculation:category-15",
                "source_model": "canonical",
            }
        ],
        "nodes": [
            {
                "node_id": "ghg:scope3_cat15_total",
                "model_id": "GHG_SCOPE3_CATEGORY_15_CALCULATION",
                "indicator_identifier": "urn:sds:reg:ghg:scope3_cat15_total",
                "canonical_datapoint_id": "ghg_scope3_cat15_total",
                "label": "Scope 3 Category 15 total",
                "exposure": "public_register",
                "runtime_status": "executable",
                "role": "primary",
                "value_kind": "numeric",
                "source_kind": "legal_text",
                "unit_name": "tCO2e",
                "unit_type": "GHGEmissions",
                "parent_id": None,
                "relation_to_parent": "",
                "formula": {
                    "kind": "formula",
                    "semantic_expression": "sum(investee_scope12 * equity_share)",
                    "runtime_expression": "sum(investee_scope12 * equity_share)",
                    "component_ids": [
                        "ghg_scope3_cat15_investee_scope12",
                        "input.equity_share",
                    ],
                    "component_refs": [
                        {
                            "component_id": "ghg_scope3_cat15_investee_scope12",
                            "node_id": "ghg:scope3_cat15_investee_scope12",
                            "indicator_identifier": None,
                            "component_scope": "internal_node",
                        },
                        {
                            "component_id": "input.equity_share",
                            "local_variable": "equity_share",
                            "variable_uri": "input.equity_share",
                            "node_id": "",
                            "indicator_identifier": "",
                            "component_scope": "runtime_external",
                        },
                    ],
                    "note": "Sum across equity investments.",
                },
                "aggregation": {
                    "temporal": "SUM",
                    "perimeter": "SUM",
                    "missing_data": "BLOCK",
                    "zero_is_value": True,
                },
                "dimensions": [
                    {
                        "dimension_id": "ghg_scope",
                        "mode": "fixed",
                        "fixed_value": "scope3",
                        "member_values": [],
                        "required": True,
                        "rationale": "Scope 3 disclosure",
                    }
                ],
                "gate_ids": ["gate_cat15_boundary"],
                "support_rule_ids": ["sr_cat15_no_netting"],
                "evidence": [
                    {
                        "source_id": "GHG Protocol Scope 3 Guidance",
                        "anchor": "category-15",
                        "role": "formula_rule",
                    }
                ],
                "provenance": {"test": True},
            },
            {
                "node_id": "ghg:scope3_cat15_investee_scope12",
                "model_id": "GHG_SCOPE3_CATEGORY_15_CALCULATION",
                "canonical_datapoint_id": "ghg_scope3_cat15_investee_scope12",
                "label": "Investee scope 1 and 2 emissions",
                "exposure": "runtime_support",
                "runtime_status": "semantic_only",
                "role": "input",
                "value_kind": "numeric",
                "source_kind": "calculation_input",
                "unit_name": "tCO2e",
                "unit_type": "GHGEmissions",
                "formula": {"kind": "input"},
                "aggregation": {"temporal": "SUM", "perimeter": "SUM"},
                "dimensions": [],
                "gate_ids": [],
                "support_rule_ids": [],
                "evidence": [],
            },
        ],
        "support_rules": [
            {
                "support_rule_id": "sr_cat15_no_netting",
                "title": "No netting",
                "description": "Gross emissions must be reported before offsets.",
            }
        ],
        "gates": [
            {
                "gate_id": "gate_cat15_boundary",
                "title": "Boundary check",
                "description": "Investment boundary must be documented.",
            }
        ],
    }


def _write_payload(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def test_load_payload_rejects_duplicate_json_keys_at_any_depth(tmp_path):
    service = _service_module()
    path = tmp_path / "calculation_contract.json"
    path.write_text(
        '{"contract_version":"1.0","source_package":{"producer":"a",'
        '"producer":"b"},"nodes":[]}',
        encoding="utf-8",
    )

    with pytest.raises(
        service.CalculationContractImportError, match="duplicate JSON key"
    ):
        service.load_calculation_contract_payload(path)


class _FakeQuery:
    def __init__(self, rows: list[Any]):
        self.rows = rows

    def all(self) -> list[Any]:
        return list(self.rows)

    def first(self) -> Any | None:
        return self.rows[0] if self.rows else None

    def filter(self, *args: Any, **kwargs: Any) -> "_FakeQuery":
        return self

    def filter_by(self, **kwargs: Any) -> "_FakeQuery":
        self.rows = [
            row
            for row in self.rows
            if all(getattr(row, key, None) == value for key, value in kwargs.items())
        ]
        return self


class _FakeSession:
    def __init__(self, indicators: list[Any] | None = None):
        self.rows: dict[type[Any], list[Any]] = defaultdict(list)
        self.rows[db_models.Indicator] = indicators or []
        self.committed = False
        self.rolled_back = False
        self._next_id = 1

    def query(self, model: type[Any]) -> _FakeQuery:
        return _FakeQuery(self.rows[model])

    def add(self, row: Any) -> None:
        if getattr(row, "id", None) is None:
            row.id = self._next_id
            self._next_id += 1
        self.rows[type(row)].append(row)

    def flush(self) -> None:
        return None

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class _ImportedContractRepository:
    def __init__(self, rows: list[Any]):
        self.rows = rows

    def get_contract_for_concept(self, concept: str):
        for row in self.rows:
            if getattr(row, "is_active", True) is False:
                continue
            if concept in {
                getattr(row, "indicator_identifier", None),
                getattr(row, "canonical_datapoint_id", None),
                getattr(row, "node_id", None),
                getattr(row, "indicator_id", None),
            }:
                return row
        return None


@dataclass
class _FixtureUnitNormalizer:
    factors: dict[tuple[str, str], Decimal]

    def normalize(self, value: Decimal, from_unit: str, to_unit: str):
        factor = self.factors[(from_unit, to_unit)]
        return value * factor, {
            "from_unit": from_unit,
            "to_unit": to_unit,
            "factor": str(factor),
        }


def _parse_fixture_period(value: str) -> date:
    if len(value) == 4:
        return date(int(value), 1, 1)
    if len(value) == 7:
        year, month = value.split("-")
        return date(int(year), int(month), 1)
    return date.fromisoformat(value)


def _load_fixture_observations(
    package_dir: Path = FIXTURE_PACKAGE_DIR,
) -> list[ObservationRecord]:
    observations: list[ObservationRecord] = []
    with (package_dir / "sds_values.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        for row in csv.DictReader(handle):
            observations.append(
                ObservationRecord(
                    value_id=row["external_key"],
                    concept=row["concept"],
                    entity=row["entity"],
                    period=_parse_fixture_period(row["period"]),
                    value=Decimal(row["value"]),
                    unit=row["unit"],
                    value_type="numeric",
                    updated_at=datetime.now(timezone.utc),
                )
            )
    return observations


def _import_fixture_package_into_fake_session(
    package_dir: Path = FIXTURE_PACKAGE_DIR,
) -> tuple[_FakeSession, list[Any]]:
    service = _service_module()
    indicators: list[Any] = []
    with (package_dir / "sds_dataset_register.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        for row in csv.DictReader(handle):
            indicators.append(
                db_models.Indicator(
                    id=row["identifier"],
                    identifier=row["identifier"],
                    title=row["title"],
                    dimension=row["dimension"],
                    concept_state=db_models.ConceptState.CATALOGUED,
                )
            )

    db = _FakeSession(indicators)
    service.import_calculation_contract_file(
        contract_path=package_dir / "sds_calculation_contract.json",
        db=db,
        dry_run=False,
    )
    contracts = db.rows[db_models.CanonicalCalculationContract]
    components = db.rows[db_models.CanonicalCalculationComponent]
    for contract in contracts:
        contract.components = [
            component
            for component in components
            if component.contract_id == contract.id
        ]
    return db, contracts


def test_models_expose_calculation_contract_tables():
    assert hasattr(db_models, "AtomizerPackageImport")
    assert hasattr(db_models, "CanonicalCalculationContract")
    assert hasattr(db_models, "CanonicalCalculationComponent")
    assert hasattr(db_models, "CanonicalCalculationDimension")
    assert hasattr(db_models, "CanonicalCalculationGate")
    assert hasattr(db_models, "CanonicalCalculationSupportRule")

    package_cols = set(db_models.AtomizerPackageImport.__table__.columns.keys())
    contract_cols = set(db_models.CanonicalCalculationContract.__table__.columns.keys())
    component_cols = set(
        db_models.CanonicalCalculationComponent.__table__.columns.keys()
    )

    assert {
        "id",
        "package_hash",
        "manifest_hash",
        "calculation_contract_sha256",
        "status",
        "result_json",
    }.issubset(package_cols)
    assert {
        "id",
        "package_import_id",
        "node_id",
        "canonical_datapoint_id",
        "indicator_id",
        "exposure",
        "runtime_status",
        "formula_kind",
        "semantic_expression",
        "runtime_expression",
        "contract_hash",
        "is_active",
    }.issubset(contract_cols)
    assert {
        "contract_id",
        "component_id",
        "component_node_id",
        "indicator_identifier",
        "component_scope",
        "required",
    }.issubset(component_cols)
    assert {"result_currency", "conversion_policy"}.issubset(contract_cols)
    assert {
        "currency",
        "expected_currency",
        "conversion_policy",
        "fx_policy_id",
    }.issubset(component_cols)


def test_contract_version_column_accepts_atomizer_contract_version_name():
    column = db_models.CanonicalCalculationContract.__table__.columns[
        "contract_version"
    ]

    assert column.type.length >= len("atomizer-sds-calculation-contract-v1")


def test_contract_datapoint_identity_is_model_scoped_not_global():
    indexes = {
        index.name: index
        for index in db_models.CanonicalCalculationContract.__table__.indexes
    }

    assert "ix_canonical_calc_contracts_active_datapoint" not in indexes
    assert (
        indexes["ix_canonical_calc_contracts_active_datapoint_lookup"].unique
        is not True
    )
    assert indexes["ix_canonical_calc_contracts_active_model_datapoint"].unique is True


def test_validate_executable_formula_requires_component_refs():
    service = _service_module()
    payload = _valid_payload()
    payload["nodes"][0]["formula"].pop("component_refs")

    with pytest.raises(service.CalculationContractImportError, match="component_refs"):
        service.validate_calculation_contract_payload(
            payload,
            known_indicator_identifiers={"urn:sds:reg:ghg:scope3_cat15_total"},
        )


def test_validate_rejects_invalid_runtime_expression_syntax():
    service = _service_module()
    payload = _valid_payload()
    payload["nodes"][0]["formula"]["runtime_expression"] = "investee_scope12 +"

    with pytest.raises(
        service.CalculationContractImportError, match="runtime_expression"
    ):
        service.validate_calculation_contract_payload(
            payload,
            known_indicator_identifiers={"urn:sds:reg:ghg:scope3_cat15_total"},
        )


def test_validate_rejects_unsafe_runtime_expression():
    service = _service_module()
    payload = _valid_payload()
    payload["nodes"][0]["formula"]["runtime_expression"] = "__import__('os')"

    with pytest.raises(service.CalculationContractImportError, match="Unsafe"):
        service.validate_calculation_contract_payload(
            payload,
            known_indicator_identifiers={"urn:sds:reg:ghg:scope3_cat15_total"},
        )


def test_validate_rejects_unbound_explicit_runtime_variable():
    service = _service_module()
    payload = _valid_payload()
    formula = payload["nodes"][0]["formula"]
    formula["runtime_expression"] = "known + missing"
    formula["component_refs"][0]["local_variable"] = "known"

    with pytest.raises(service.CalculationContractImportError, match="unbound"):
        service.validate_calculation_contract_payload(
            payload,
            known_indicator_identifiers={"urn:sds:reg:ghg:scope3_cat15_total"},
        )


def test_validate_rejects_unbound_positional_runtime_variable():
    service = _service_module()
    payload = _valid_payload()
    payload["nodes"][0]["formula"][
        "runtime_expression"
    ] = "investee_scope12 + equity_share + missing"

    with pytest.raises(service.CalculationContractImportError, match="unbound"):
        service.validate_calculation_contract_payload(
            payload,
            known_indicator_identifiers={"urn:sds:reg:ghg:scope3_cat15_total"},
        )


def test_validate_rejects_unsafe_derived_runtime_expression():
    service = _service_module()
    payload = _valid_payload()
    formula = payload["nodes"][0]["formula"]
    formula["runtime_expression"] = "derived_total"
    formula["derived_nodes"] = [
        {
            "local_variable": "derived_total",
            "expression": "__import__('os')",
        }
    ]

    with pytest.raises(service.CalculationContractImportError, match="derived_nodes"):
        service.validate_calculation_contract_payload(
            payload,
            known_indicator_identifiers={"urn:sds:reg:ghg:scope3_cat15_total"},
        )


def test_validate_rejects_unbound_derived_runtime_expression():
    service = _service_module()
    payload = _valid_payload()
    formula = payload["nodes"][0]["formula"]
    formula["runtime_expression"] = "derived_total"
    formula["derived_nodes"] = [
        {
            "local_variable": "derived_total",
            "expression": "investee_scope12 + missing",
        }
    ]

    with pytest.raises(service.CalculationContractImportError, match="unbound"):
        service.validate_calculation_contract_payload(
            payload,
            known_indicator_identifiers={"urn:sds:reg:ghg:scope3_cat15_total"},
        )


def test_validate_rejects_unbound_explicit_derived_dependency():
    service = _service_module()
    payload = _valid_payload()
    formula = payload["nodes"][0]["formula"]
    formula["runtime_expression"] = "derived_total"
    formula["derived_nodes"] = [
        {
            "local_variable": "derived_total",
            "expression": "ghg_scope3_cat15_investee_scope12 + equity_share",
            "dependencies": ["ghg_scope3_cat15_investee_scope12", "missing"],
        }
    ]

    with pytest.raises(service.CalculationContractImportError, match="unbound"):
        service.validate_calculation_contract_payload(
            payload,
            known_indicator_identifiers={"urn:sds:reg:ghg:scope3_cat15_total"},
        )


def test_load_rejects_invalid_json_before_writes(tmp_path):
    service = _service_module()
    path = tmp_path / "sds_calculation_contract.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(service.CalculationContractImportError, match="invalid JSON"):
        service.load_calculation_contract_payload(path)


def test_validate_rejects_unknown_public_register_indicator():
    service = _service_module()
    payload = _valid_payload()

    with pytest.raises(
        service.CalculationContractImportError, match="unknown indicator"
    ):
        service.validate_calculation_contract_payload(
            payload,
            known_indicator_identifiers={"urn:sds:reg:other"},
        )


def test_validate_rejects_public_register_when_known_set_empty():
    """codex F04 M3: public_register must fail closed when the catalog is empty.

    An empty/absent known set means "unknown", not "permissive" — a public contract
    must never be accepted against an empty/unchecked catalog (which would persist a
    NULL indicator_id public contract).
    """
    service = _service_module()
    payload = _valid_payload()

    with pytest.raises(
        service.CalculationContractImportError, match="unknown indicator"
    ):
        service.validate_calculation_contract_payload(payload)

    with pytest.raises(
        service.CalculationContractImportError, match="unknown indicator"
    ):
        service.validate_calculation_contract_payload(
            payload, known_indicator_identifiers=set()
        )


def test_validate_allows_public_register_when_indicator_known():
    service = _service_module()
    payload = _valid_payload()
    validation = service.validate_calculation_contract_payload(
        payload,
        known_indicator_identifiers={"urn:sds:reg:ghg:scope3_cat15_total"},
    )
    assert validation is not None


def test_validate_rejects_orphan_component_refs():
    service = _service_module()
    payload = _valid_payload()
    payload["nodes"][0]["formula"]["component_refs"][0][
        "node_id"
    ] = "ghg:missing_component"

    with pytest.raises(service.CalculationContractImportError, match="orphan"):
        service.validate_calculation_contract_payload(
            payload,
            known_indicator_identifiers={"urn:sds:reg:ghg:scope3_cat15_total"},
        )


def test_validate_allows_runtime_external_value_component_refs():
    service = _service_module()
    payload = _valid_payload()
    formula = payload["nodes"][0]["formula"]
    formula["runtime_expression"] = "investee_scope12 / net_revenue_million_eur"
    formula["component_ids"] = [
        "ghg_scope3_cat15_investee_scope12",
        "input.net_revenue_million_eur",
    ]
    formula["component_refs"].append(
        {
            "component_id": "input.net_revenue_million_eur",
            "local_variable": "net_revenue_million_eur",
            "variable_uri": "input.net_revenue_million_eur",
            "node_id": "",
            "indicator_identifier": "",
            "component_scope": "runtime_external",
        }
    )

    validation = service.validate_calculation_contract_payload(
        payload,
        known_indicator_identifiers={"urn:sds:reg:ghg:scope3_cat15_total"},
    )

    assert validation.component_count == 3


def test_import_dry_run_writes_nothing(tmp_path):
    service = _service_module()
    indicator = db_models.Indicator(
        id="indicator-1",
        identifier="urn:sds:reg:ghg:scope3_cat15_total",
        title="Scope 3 Category 15 total",
        dimension="E",
        concept_state=db_models.ConceptState.CATALOGUED,
    )
    db = _FakeSession([indicator])
    contract_path = _write_payload(
        tmp_path / "sds_calculation_contract.json", _valid_payload()
    )

    report = service.import_calculation_contract_file(
        contract_path=contract_path,
        db=db,
        dry_run=True,
    )

    assert report.valid is True
    assert report.dry_run is True
    assert report.committed is False
    assert db.committed is False
    assert db.rows[db_models.AtomizerPackageImport] == []
    assert db.rows[db_models.CanonicalCalculationContract] == []
    assert indicator.concept_state == db_models.ConceptState.CATALOGUED


def test_import_dry_run_accepts_package_register_identifier_without_db_row(tmp_path):
    service = _service_module()
    db = _FakeSession([])
    contract_path = _write_payload(
        tmp_path / "sds_calculation_contract.json", _valid_payload()
    )

    report = service.import_calculation_contract_file(
        contract_path=contract_path,
        db=db,
        dry_run=True,
        known_indicator_identifiers={"urn:sds:reg:ghg:scope3_cat15_total"},
    )

    assert report.valid is True
    assert report.dry_run is True
    assert report.committed is False
    assert db.committed is False


def test_import_links_executable_indicator_and_runtime_support_node(tmp_path):
    service = _service_module()
    indicator = db_models.Indicator(
        id="indicator-1",
        identifier="urn:sds:reg:ghg:scope3_cat15_total",
        title="Scope 3 Category 15 total",
        dimension="E",
        concept_state=db_models.ConceptState.CATALOGUED,
    )
    db = _FakeSession([indicator])
    contract_path = _write_payload(
        tmp_path / "sds_calculation_contract.json", _valid_payload()
    )

    report = service.import_calculation_contract_file(
        contract_path=contract_path,
        db=db,
        dry_run=False,
    )

    contracts = db.rows[db_models.CanonicalCalculationContract]
    components = db.rows[db_models.CanonicalCalculationComponent]
    package_import = db.rows[db_models.AtomizerPackageImport][0]

    assert report.valid is True
    assert report.committed is True
    assert report.status == "completed"
    assert db.committed is True
    assert report.counts["contracts_created"] == 2
    assert len(contracts) == 2
    assert len(components) == 2
    assert contracts[0].indicator_id == "indicator-1"
    assert contracts[0].runtime_status == "executable"
    assert contracts[1].indicator_id is None
    assert contracts[1].runtime_status == "semantic_only"
    assert components[0].component_node_id == "ghg:scope3_cat15_investee_scope12"
    assert components[0].unit_name == "tCO2e"
    assert components[0].unit_type == "GHGEmissions"
    assert indicator.concept_state == db_models.ConceptState.CALCULABLE
    assert package_import.result_json["status"] == "completed"
    assert package_import.result_json["committed"] is True


def test_relation_to_parent_accepts_explanatory_text(tmp_path):
    service = _service_module()
    payload = _valid_payload()
    long_relation = (
        "Separable duty within paragraph 13(a): the specification of critical "
        "and strategic raw materials contained in each key material."
    )
    assert len(long_relation) > 100
    payload["nodes"][0]["relation_to_parent"] = long_relation
    db = _FakeSession(
        [
            db_models.Indicator(
                id="indicator-1",
                identifier="urn:sds:reg:ghg:scope3_cat15_total",
                title="Scope 3 Category 15 total",
                dimension="E",
            )
        ]
    )
    contract_path = _write_payload(tmp_path / "sds_calculation_contract.json", payload)

    service.import_calculation_contract_file(
        contract_path=contract_path, db=db, dry_run=False
    )

    contract_row = db.rows[db_models.CanonicalCalculationContract][0]
    assert contract_row.relation_to_parent == long_relation
    assert isinstance(
        db_models.CanonicalCalculationContract.__table__.c.relation_to_parent.type,
        Text,
    )


def test_contract_input_carries_currency_and_fx_policy(tmp_path):
    service = _service_module()
    payload = _valid_payload()
    node = payload["nodes"][0]
    node["result_currency"] = " eur "
    node["conversion_policy"] = "FAIL_CLOSED"
    component = node["formula"]["component_refs"][0]
    component["currency"] = "gbp"
    component["expected_currency"] = " EUR "
    component["fx_policy_id"] = "ecb-monthly"
    component["conversion_policy"] = "FAIL_CLOSED"
    db = _FakeSession(
        [
            db_models.Indicator(
                id="indicator-1",
                identifier="urn:sds:reg:ghg:scope3_cat15_total",
                title="Scope 3 Category 15 total",
                dimension="E",
            )
        ]
    )
    contract_path = _write_payload(tmp_path / "sds_calculation_contract.json", payload)

    service.import_calculation_contract_file(
        contract_path=contract_path, db=db, dry_run=False
    )

    contract_row = db.rows[db_models.CanonicalCalculationContract][0]
    component_row = db.rows[db_models.CanonicalCalculationComponent][0]
    assert contract_row.result_currency == "EUR"
    assert contract_row.conversion_policy == "fail_closed"
    assert component_row.currency == "GBP"
    assert component_row.expected_currency == "EUR"
    assert component_row.fx_policy_id == "ecb-monthly"
    assert component_row.conversion_policy == "fail_closed"


@pytest.mark.parametrize(
    ("component_updates", "match"),
    [
        ({"expected_currency": "EUR"}, "expected_currency requires fx_policy_id"),
        ({"fx_policy_id": "ecb-monthly"}, "fx_policy_id requires expected_currency"),
        ({"conversion_policy": "latest_available"}, "unsupported conversion_policy"),
        (
            {"currency": "EURO", "expected_currency": "EUR", "fx_policy_id": "ecb"},
            "invalid currency",
        ),
    ],
)
def test_validate_rejects_unsafe_component_fx_policy_shape(component_updates, match):
    service = _service_module()
    payload = _valid_payload()
    payload["nodes"][0]["formula"]["component_refs"][0].update(component_updates)

    with pytest.raises(service.CalculationContractImportError, match=match):
        service.validate_calculation_contract_payload(
            payload,
            known_indicator_identifiers={"urn:sds:reg:ghg:scope3_cat15_total"},
        )


def test_validate_rejects_unsupported_component_mapping_relationship_policy():
    service = _service_module()
    payload = _valid_payload()
    payload["nodes"][0]["formula"]["component_refs"][0][
        "allowed_mapping_relationships"
    ] = ["narrower", "unsupported_relation"]

    with pytest.raises(
        service.CalculationContractImportError,
        match="unsupported allowed_mapping_relationships",
    ):
        service.validate_calculation_contract_payload(
            payload,
            known_indicator_identifiers={"urn:sds:reg:ghg:scope3_cat15_total"},
        )


def test_imported_component_mapping_relationship_policy_resolves_to_runtime_input(
    tmp_path,
):
    service = _service_module()
    payload = _valid_payload()
    payload["nodes"][0]["formula"]["component_refs"][0][
        "allowed_mapping_relationships"
    ] = ["narrow_match"]
    indicator = db_models.Indicator(
        id="indicator-1",
        identifier="urn:sds:reg:ghg:scope3_cat15_total",
        title="Scope 3 Category 15 total",
        dimension="E",
        concept_state=db_models.ConceptState.CATALOGUED,
    )
    db = _FakeSession([indicator])
    contract_path = _write_payload(tmp_path / "sds_calculation_contract.json", payload)

    service.import_calculation_contract_file(
        contract_path=contract_path,
        db=db,
        dry_run=False,
    )
    resolver = RuntimeCalculationContractResolver(
        repository=_ImportedContractRepository(
            db.rows[db_models.CanonicalCalculationContract]
        )
    )

    contract = resolver.resolve("urn:sds:reg:ghg:scope3_cat15_total")

    assert contract.inputs[0].allowed_mapping_relationships == ("narrower",)


def test_validate_rejects_unsupported_contract_conversion_policy():
    service = _service_module()
    payload = _valid_payload()
    payload["nodes"][0]["conversion_policy"] = "latest_available"

    with pytest.raises(
        service.CalculationContractImportError, match="unsupported conversion_policy"
    ):
        service.validate_calculation_contract_payload(
            payload,
            known_indicator_identifiers={"urn:sds:reg:ghg:scope3_cat15_total"},
        )


def test_import_is_idempotent_for_same_package_hash(tmp_path):
    service = _service_module()
    indicator = db_models.Indicator(
        id="indicator-1",
        identifier="urn:sds:reg:ghg:scope3_cat15_total",
        title="Scope 3 Category 15 total",
        dimension="E",
        concept_state=db_models.ConceptState.CATALOGUED,
    )
    db = _FakeSession([indicator])
    contract_path = _write_payload(
        tmp_path / "sds_calculation_contract.json", _valid_payload()
    )

    first = service.import_calculation_contract_file(
        contract_path=contract_path,
        db=db,
        dry_run=False,
    )
    second = service.import_calculation_contract_file(
        contract_path=contract_path,
        db=db,
        dry_run=False,
    )

    assert first.status == "completed"
    assert second.status == "already_imported"
    assert second.committed is False
    assert len(db.rows[db_models.AtomizerPackageImport]) == 1
    assert len(db.rows[db_models.CanonicalCalculationContract]) == 2


def test_package_hash_binds_the_declared_manifest_to_contract_bytes():
    service = _service_module()
    payload = _valid_payload()

    first = service.package_hash_for_payload(payload, "1" * 64)
    second = service.package_hash_for_payload(payload, "2" * 64)

    assert first != second
    assert first != payload["source_package"]["manifest_hash"]


def test_import_preserves_and_clears_optional_input_control_payload(tmp_path):
    service = _service_module()
    indicator = db_models.Indicator(
        id="indicator-1",
        identifier="urn:sds:reg:ghg:scope3_cat15_total",
        title="Scope 3 Category 15 total",
        dimension="E",
        concept_state=db_models.ConceptState.CATALOGUED,
    )
    db = _FakeSession([indicator])
    first_payload = _valid_payload()
    first_payload["contract_version"] = "1.1"
    first_payload["nodes"][0]["input_control"] = {
        "schema_version": "sds-input-controls-v1",
        "control_type": "computed",
        "enforcement": {"enabled": False},
    }
    first_path = _write_payload(
        tmp_path / "sds_calculation_contract_v11_first.json", first_payload
    )

    first = service.import_calculation_contract_file(
        contract_path=first_path,
        db=db,
        dry_run=False,
    )

    assert first.status == "completed"
    first_contract = next(
        row
        for row in db.rows[db_models.CanonicalCalculationContract]
        if row.node_id == "ghg:scope3_cat15_total"
    )
    assert first_contract.source_payload["input_control"]["control_type"] == "computed"

    second_payload = _valid_payload()
    second_payload["contract_version"] = "1.1"
    second_payload["source_package"]["manifest_hash"] = "d" * 64
    second_path = _write_payload(
        tmp_path / "sds_calculation_contract_v11_second.json", second_payload
    )

    second = service.import_calculation_contract_file(
        contract_path=second_path,
        db=db,
        dry_run=False,
    )

    assert second.status == "completed"
    assert first_contract.is_active is False
    active_contract = next(
        row
        for row in db.rows[db_models.CanonicalCalculationContract]
        if row.node_id == "ghg:scope3_cat15_total"
        and getattr(row, "is_active", True) is not False
    )
    assert "input_control" not in active_contract.source_payload


def test_import_keeps_same_datapoint_active_in_different_models(tmp_path):
    service = _service_module()
    payload = _valid_payload()
    payload["nodes"] = [
        {
            "node_id": "ghg:scope3:gwp_values_source",
            "model_id": "GHG_SCOPE3_REPORTING",
            "indicator_identifier": "urn:sds:reg:ghg:scope3_gwp_values_source",
            "canonical_datapoint_id": "gwp_values_source",
            "label": "Scope 3 GWP values source",
            "exposure": "public_register",
            "runtime_status": "semantic_only",
            "role": "input",
            "value_kind": "text",
            "source_kind": "legal_text",
            "formula": {"kind": "input"},
        }
    ]
    incoming_indicator = db_models.Indicator(
        id="indicator-incoming",
        identifier="urn:sds:reg:ghg:scope3_gwp_values_source",
        title="Scope 3 GWP values source",
        dimension="E",
        concept_state=db_models.ConceptState.CATALOGUED,
    )
    existing_contract = db_models.CanonicalCalculationContract(
        id=100,
        package_import_id=99,
        contract_version="1.0",
        model_id="GHG_SCOPE2_REPORTING",
        node_id="ghg:scope2:gwp_values_source",
        canonical_datapoint_id="gwp_values_source",
        indicator_identifier="urn:sds:reg:ghg:scope2_gwp_values_source",
        label="Scope 2 GWP values source",
        exposure="public_register",
        runtime_status="semantic_only",
        contract_hash="d" * 64,
        is_active=True,
    )
    db = _FakeSession([incoming_indicator])
    db.rows[db_models.CanonicalCalculationContract].append(existing_contract)
    contract_path = _write_payload(tmp_path / "sds_calculation_contract.json", payload)

    report = service.import_calculation_contract_file(
        contract_path=contract_path,
        db=db,
        dry_run=False,
    )

    active_shared_datapoints = [
        contract
        for contract in db.rows[db_models.CanonicalCalculationContract]
        if contract.canonical_datapoint_id == "gwp_values_source"
        and getattr(contract, "is_active", True) is not False
    ]
    assert report.status == "completed"
    assert existing_contract.is_active is True
    assert {contract.model_id for contract in active_shared_datapoints} == {
        "GHG_SCOPE2_REPORTING",
        "GHG_SCOPE3_REPORTING",
    }


def test_imported_fixture_contracts_execute_runtime_calculations_end_to_end():
    _db, contracts = _import_fixture_package_into_fake_session()
    resolver = RuntimeCalculationContractResolver(
        repository=_ImportedContractRepository(contracts)
    )
    provider = StaticObservationProvider(_load_fixture_observations())
    engine = CalculationEngine(precision=4)
    normalizer = _FixtureUnitNormalizer({("kgCO2e", "tCO2e"): Decimal("0.001")})
    parent_context = CalculationContext(
        entity_id="fixture_entity_parent",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        temporal_granularity="annual",
        organizational_level=1,
    )

    total = engine.calculate_contract(
        resolver.resolve("urn:sds:reg:test:scope1_total_emissions"),
        parent_context,
        provider,
        unit_normalizer=normalizer,
        contract_resolver=resolver,
    )
    intensity = engine.calculate_contract(
        resolver.resolve("urn:sds:reg:test:emissions_intensity"),
        parent_context,
        provider,
        unit_normalizer=normalizer,
        contract_resolver=resolver,
    )
    headcount = engine.calculate_contract(
        resolver.resolve("urn:sds:reg:test:average_headcount"),
        parent_context,
        provider,
        unit_normalizer=normalizer,
        contract_resolver=resolver,
    )
    year_end = engine.calculate_contract(
        resolver.resolve("urn:sds:reg:test:year_end_employees"),
        parent_context,
        provider,
        unit_normalizer=normalizer,
        contract_resolver=resolver,
    )

    assert total.value == Decimal("2.0000")
    assert intensity.value == Decimal("0.0800")
    assert intensity.input_values["scope1_total_emissions_tco2e"] == Decimal("2.0000")
    assert headcount.value == Decimal("13.3333")
    assert year_end.value == Decimal("12.0000")

    zero_context = CalculationContext(
        entity_id="fixture_entity_zero",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        temporal_granularity="annual",
        organizational_level=1,
    )
    zero_total = engine.calculate_contract(
        resolver.resolve("urn:sds:reg:test:scope1_total_emissions"),
        zero_context,
        provider,
        unit_normalizer=normalizer,
        contract_resolver=resolver,
    )
    assert zero_total.value == Decimal("0.0000")

    missing_context = CalculationContext(
        entity_id="fixture_entity_missing",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        temporal_granularity="annual",
        organizational_level=1,
    )
    with pytest.raises(Exception, match="Missing required observations: mobile_tco2e"):
        engine.calculate_contract(
            resolver.resolve("urn:sds:reg:test:scope1_total_emissions"),
            missing_context,
            provider,
            unit_normalizer=normalizer,
            contract_resolver=resolver,
        )


def test_imported_weighted_average_fixture_executes_end_to_end():
    db, contracts = _import_fixture_package_into_fake_session(
        WEIGHTED_FIXTURE_PACKAGE_DIR
    )
    resolver = RuntimeCalculationContractResolver(
        repository=_ImportedContractRepository(contracts)
    )
    provider = StaticObservationProvider(
        _load_fixture_observations(WEIGHTED_FIXTURE_PACKAGE_DIR)
    )
    engine = CalculationEngine(precision=4)
    context = CalculationContext(
        entity_id="madrid_plant",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        temporal_granularity="annual",
        organizational_level=1,
    )

    result = engine.calculate_contract(
        resolver.resolve("urn:sds:reg:test:weighted_training_score"),
        context,
        provider,
        contract_resolver=resolver,
    )

    assert db.committed is True
    assert result.value == Decimal("68.0000")
    assert result.unit == "points"
    assert (
        result.formula_used
        == "weighted_average(training_score_points, training_employee_count)"
    )
    assert result.input_values["training_employee_count"] == Decimal("100")
    assert (
        "Weighted average: training_score_points weighted by training_employee_count"
        in result.trace.formula_steps
    )


def test_alembic_018_declares_calculation_contract_tables():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "018_create_calculation_contract_tables.py"
    )
    source = migration_path.read_text(encoding="utf-8")

    assert 'revision = "018_create_calculation_contract_tables"' in source
    assert 'down_revision = "017_widen_indicator_code_esrs"' in source
    assert '"atomizer_package_imports"' in source
    assert '"canonical_calculation_contracts"' in source
    assert '"canonical_calculation_components"' in source


def test_load_calculation_contract_payload_rejects_missing_bad_json_and_non_object(
    tmp_path,
):
    service = _service_module()

    with pytest.raises(service.CalculationContractImportError, match="not found"):
        service.load_calculation_contract_payload(tmp_path / "missing.json")

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{", encoding="utf-8")
    with pytest.raises(service.CalculationContractImportError, match="invalid JSON"):
        service.load_calculation_contract_payload(bad_json)

    non_object = tmp_path / "list.json"
    non_object.write_text("[]", encoding="utf-8")
    with pytest.raises(
        service.CalculationContractImportError, match="must be an object"
    ):
        service.load_calculation_contract_payload(non_object)


@pytest.mark.parametrize(
    "mutator, message",
    [
        (lambda payload: payload.pop("contract_version"), "contract_version"),
        (lambda payload: payload.__setitem__("nodes", "bad"), "nodes must be a list"),
        (lambda payload: payload["nodes"].__setitem__(0, "bad"), "must be an object"),
        (
            lambda payload: payload["nodes"][0].__setitem__(
                "node_id", payload["nodes"][1]["node_id"]
            ),
            "duplicate node_id",
        ),
        (
            lambda payload: payload["nodes"][0].__setitem__("exposure", "external"),
            "unsupported exposure",
        ),
        (
            lambda payload: payload["nodes"][0].__setitem__(
                "runtime_status", "planned"
            ),
            "unsupported runtime_status",
        ),
        (
            lambda payload: payload["nodes"][0].pop("indicator_identifier"),
            "public_register but has no indicator_identifier",
        ),
        (
            lambda payload: payload["nodes"][0].__setitem__(
                "formula", {"kind": "formula", "component_refs": []}
            ),
            "executable formula requires component_refs",
        ),
        (
            lambda payload: payload["nodes"][0].__setitem__(
                "formula",
                {
                    "kind": "formula",
                    "component_refs": [{"component_id": "c1", "node_id": "missing"}],
                },
            ),
            "requires runtime_expression",
        ),
        (
            lambda payload: payload["nodes"][0].__setitem__(
                "formula",
                {"kind": "formula", "runtime_expression": "a", "component_refs": "bad"},
            ),
            "component_refs must be a list",
        ),
        (
            lambda payload: payload["nodes"][0].__setitem__(
                "formula",
                {
                    "kind": "formula",
                    "runtime_expression": "a",
                    "component_refs": ["bad"],
                },
            ),
            "component_refs\\[0\\] must be an object",
        ),
        (
            lambda payload: payload["nodes"][0].__setitem__(
                "formula",
                {
                    "kind": "formula",
                    "runtime_expression": "a",
                    "component_refs": [{"component_id": "c1"}],
                },
            ),
            "has no node, indicator, or variable_uri",
        ),
        (
            lambda payload: payload["nodes"][0].__setitem__(
                "formula",
                {
                    "kind": "formula",
                    "runtime_expression": "a",
                    "component_refs": [{"component_id": "c1", "node_id": "missing"}],
                },
            ),
            "orphan component_ref",
        ),
        (
            lambda payload: payload["nodes"][0].__setitem__(
                "formula",
                {
                    "kind": "formula",
                    "runtime_expression": "a",
                    "component_ids": ["expected"],
                    "component_refs": [
                        {
                            "component_id": "actual",
                            "node_id": "ghg:scope3_cat15_investee_scope12",
                        }
                    ],
                },
            ),
            "missing component_refs",
        ),
    ],
)
def test_validate_calculation_contract_payload_rejects_shape_errors(mutator, message):
    service = _service_module()
    payload = _valid_payload()
    mutator(payload)

    with pytest.raises(service.CalculationContractImportError, match=message):
        service.validate_calculation_contract_payload(
            payload,
            known_indicator_identifiers={"urn:sds:reg:ghg:scope3_cat15_total"},
        )


def test_validate_calculation_contract_payload_rejects_non_object_payload():
    service = _service_module()

    with pytest.raises(
        service.CalculationContractImportError, match="must be an object"
    ):
        service.validate_calculation_contract_payload([])


def test_validate_component_ref_rejects_unknown_indicator_identifier():
    service = _service_module()
    payload = _valid_payload()
    component_ref = payload["nodes"][0]["formula"]["component_refs"][0]
    component_ref.pop("node_id")
    component_ref["indicator_identifier"] = "urn:sds:reg:ghg:missing"

    with pytest.raises(
        service.CalculationContractImportError, match="unknown indicator"
    ):
        service.validate_calculation_contract_payload(
            payload,
            known_indicator_identifiers={"urn:sds:reg:ghg:scope3_cat15_total"},
        )


def test_calculation_contract_import_rolls_back_on_write_failure(
    tmp_path,
    monkeypatch,
):
    service = _service_module()
    db = _FakeSession(
        [
            db_models.Indicator(
                id="indicator-1",
                identifier="urn:sds:reg:ghg:scope3_cat15_total",
                title="Scope 3 Category 15 total",
                dimension="E",
            )
        ]
    )
    contract_path = _write_payload(
        tmp_path / "sds_calculation_contract.json", _valid_payload()
    )

    monkeypatch.setattr(
        service,
        "_create_contract_row",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("write failed")),
    )

    with pytest.raises(RuntimeError, match="write failed"):
        service.import_calculation_contract_file(
            contract_path=contract_path,
            db=db,
            dry_run=False,
        )

    assert db.rolled_back is True


def test_calculation_contract_import_helper_rows_and_enrichment():
    service = _service_module()
    db = _FakeSession(
        indicators=[db_models.Indicator(id="ind-1", identifier="urn:sds:reg:x:calc")]
    )
    package_import = db_models.AtomizerPackageImport(
        id=11, package_hash="hash", status="validated"
    )
    report = service.CalculationContractImportReport(
        contract_path="contract.json",
        package_hash="hash",
        contract_sha256="sha",
        dry_run=False,
        valid=True,
        committed=False,
        status="validated",
    )

    service._add_global_gate_rows(
        db,
        payload={"gates": ["skip", {"id": ""}, {"id": "gate-1", "title": "Gate"}]},
        package_import=package_import,
        contract_by_node_id={},
        report=report,
    )
    service._add_global_support_rule_rows(
        db,
        payload={"support_rules": ["skip", {"id": ""}, {"support_rule_id": "rule-1"}]},
        package_import=package_import,
        contract_by_node_id={},
        report=report,
    )

    assert report.counts["gates_created"] == 1
    assert report.counts["support_rules_created"] == 1
    assert len(db.rows[db_models.CanonicalCalculationGate]) == 1
    assert len(db.rows[db_models.CanonicalCalculationSupportRule]) == 1

    referenced = {
        "node-a": {
            "unit_name": "tCO2e",
            "unit_type": "GHGEmissions",
            "value_kind": "numeric",
            "aggregation": {"temporal": "SUM", "perimeter": "SUM"},
        }
    }
    components = service._component_refs_for_node(
        {
            "formula": {
                "component_refs": [
                    "skip",
                    {"component_id": "c1", "node_id": "node-a"},
                ]
            }
        },
        nodes_by_id=referenced,
    )
    assert components == [
        {
            "component_id": "c1",
            "node_id": "node-a",
            "unit_name": "tCO2e",
            "unit_type": "GHGEmissions",
            "value_kind": "numeric",
            "aggregation_method": "SUM",
            "perimeter_aggregation": "SUM",
        }
    ]

    indicator = db_models.Indicator(id="indicator-1", identifier="urn:sds:reg:x:calc")
    concept = db_models.Concept(indicator_id="indicator-1")
    canonical = db_models.CanonicalConcept(indicator_id="indicator-1")
    db.rows[db_models.Concept] = [concept]
    db.rows[db_models.CanonicalConcept] = [canonical]
    contract = db_models.CanonicalCalculationContract(
        runtime_status=service.EXECUTABLE_STATUS,
        indicator_identifier="urn:sds:reg:x:calc",
    )
    service._mark_semantic_state_if_calculable(
        db, contract, {"urn:sds:reg:x:calc": indicator}
    )

    assert indicator.concept_state == db_models.ConceptState.CALCULABLE
    assert concept.concept_state == db_models.ConceptState.CALCULABLE
    assert canonical.concept_state == db_models.ConceptState.CALCULABLE


def test_calculation_contract_private_helpers_cover_retirement_and_errors():
    service = _service_module()
    inactive = db_models.Indicator(
        id="inactive",
        identifier="urn:sds:reg:x:inactive",
        is_active=False,
    )
    active = db_models.Indicator(
        id="active",
        identifier="urn:sds:reg:x:active",
        is_active=True,
    )
    db = _FakeSession([inactive, active])

    assert service._active_indicator_lookup(db) == {"urn:sds:reg:x:active": active}

    existing = db_models.CanonicalCalculationContract(
        model_id="MODEL",
        node_id="node-old",
        canonical_datapoint_id="dp",
        indicator_identifier="urn:sds:reg:x:old",
        is_active=True,
    )
    inactive_existing = db_models.CanonicalCalculationContract(
        model_id="MODEL",
        node_id="node-inactive",
        canonical_datapoint_id="other",
        indicator_identifier="urn:sds:reg:x:inactive",
        is_active=False,
    )
    db.rows[db_models.CanonicalCalculationContract] = [existing, inactive_existing]

    service._retire_current_contracts(
        db,
        incoming_nodes=[
            {
                "model_id": "MODEL",
                "node_id": "node-new",
                "canonical_datapoint_id": "dp",
                "indicator_identifier": "urn:sds:reg:x:new",
            }
        ],
    )

    assert existing.is_active is False
    assert existing.effective_to is not None
    assert inactive_existing.is_active is False

    service._mark_semantic_state_if_calculable(
        db,
        db_models.CanonicalCalculationContract(
            runtime_status="semantic_only",
            indicator_identifier="urn:sds:reg:x:active",
        ),
        {"urn:sds:reg:x:active": active},
    )
    service._mark_semantic_state_if_calculable(
        db,
        db_models.CanonicalCalculationContract(
            runtime_status=service.EXECUTABLE_STATUS,
            indicator_identifier="urn:sds:reg:x:missing",
        ),
        {"urn:sds:reg:x:active": active},
    )

    with pytest.raises(service.CalculationContractImportError, match="requires key"):
        service._required_str({}, "key", "context")
    with pytest.raises(service.CalculationContractImportError, match="expected object"):
        service._object_or_empty([])
    with pytest.raises(service.CalculationContractImportError, match="expected list"):
        service._list_or_empty({})


def test_contract_retirement_scope_incoming_models_retires_removed_model_nodes():
    service = _service_module()
    removed = db_models.CanonicalCalculationContract(
        model_id="MODEL",
        node_id="node-removed",
        canonical_datapoint_id="dp-removed",
        indicator_identifier="urn:sds:reg:x:removed",
        is_active=True,
    )
    unchanged_other_model = db_models.CanonicalCalculationContract(
        model_id="OTHER_MODEL",
        node_id="node-other",
        canonical_datapoint_id="dp-other",
        indicator_identifier="urn:sds:reg:x:other",
        is_active=True,
    )
    db = _FakeSession()
    db.rows[db_models.CanonicalCalculationContract] = [
        removed,
        unchanged_other_model,
    ]

    default_retired = service._retire_current_contracts(
        db,
        incoming_nodes=[
            {
                "model_id": "MODEL",
                "node_id": "node-new",
                "canonical_datapoint_id": "dp-new",
                "indicator_identifier": "urn:sds:reg:x:new",
            }
        ],
    )

    assert default_retired == 0
    assert removed.is_active is True

    scoped_retired = service._retire_current_contracts(
        db,
        incoming_nodes=[
            {
                "model_id": "MODEL",
                "node_id": "node-new",
                "canonical_datapoint_id": "dp-new",
                "indicator_identifier": "urn:sds:reg:x:new",
            }
        ],
        retirement_scope="incoming_models",
    )

    assert scoped_retired == 1
    assert removed.is_active is False
    assert removed.effective_to is not None
    assert unchanged_other_model.is_active is True


def test_contract_retirement_scope_rejects_unknown_value():
    service = _service_module()

    with pytest.raises(
        service.CalculationContractImportError, match="retirement_scope"
    ):
        service._retire_current_contracts(
            _FakeSession(),
            incoming_nodes=[],
            retirement_scope="whole_database",
        )
