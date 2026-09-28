from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "e4_gate.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("e4_gate", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


e4_gate = _load_module()


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def _make_framework_rows(
    total: int, *, family_split: tuple[int, int, int] | None = None
) -> list[list[str]]:
    if family_split is None:
        family_split = (total // 3, total // 3, total - (2 * (total // 3)))
    environmental, social, governance = family_split
    rows: list[list[str]] = []

    for index in range(environmental):
        rows.append(
            [
                "ESRS",
                "v1",
                f"E-{index:04d}",
                f"Environmental {index}",
                "Climate emission disclosure",
                "Numeric",
                "tCO2e",
                "Climate Change",
                "BP-1",
            ]
        )
    for index in range(social):
        rows.append(
            [
                "GRI",
                "v1",
                f"S-{index:04d}",
                f"Social {index}",
                "Workforce disclosure",
                "Numeric",
                "FTE",
                "Own workforce",
                "BP-1",
            ]
        )
    for index in range(governance):
        rows.append(
            [
                "ESRS",
                "v1",
                f"G-{index:04d}",
                f"Governance {index}",
                "Board disclosure",
                "Boolean",
                "Text",
                "Business conduct",
                "BP-1",
            ]
        )
    return rows[:total]


def _make_granulated_rows(total: int) -> list[list[str]]:
    rows: list[list[str]] = []
    for index in range(total):
        rows.append(
            [
                "ESRS" if index % 2 == 0 else "GRI",
                "v1",
                f"ROW-{index:04d}",
                f"A-{index:04d}",
                f"Granulated {index}",
                "Input",
                "Unit",
                "Text",
                "",
                "",
                "TRUE",
                "FALSE",
                "",
                "",
                "",
                "",
            ]
        )
    return rows


def _stub_transform(
    *, framework_path: Path, granulated_path: Path, output_path: Path, verbose: bool
) -> int:
    with framework_path.open("r", encoding="utf-8-sig", newline="") as handle:
        framework_rows = list(csv.DictReader(handle))

    headers = [
        "identifier",
        "title",
        "indicator",
        "description",
        "dimension",
        "unitName",
        "unitType",
        "periodicity",
        "periodType",
        "sourceRef",
        "codeESRS",
        "codeGRI",
        "codeGRI_expanded",
        "evidencePath",
        "sourceRow",
        "owner",
        "accessRights",
        "validationMethod",
        "doubleMateriality",
        "valueType",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for index, row in enumerate(framework_rows, start=1):
            topic = (row.get("Topic") or "").lower()
            if "workforce" in topic or "social" in topic:
                dimension = "S"
            elif "governance" in topic or "business conduct" in topic:
                dimension = "G"
            else:
                dimension = "E"
            writer.writerow(
                [
                    f"urn:sds:reg:{row.get('FrameworkID', '').lower()}:{row.get('DatapointCode', '').lower().replace('-', '_')}",
                    row.get("Label", ""),
                    row.get("Label", ""),
                    row.get("Description", ""),
                    dimension,
                    row.get("DefaultUnit", ""),
                    "",
                    "annual",
                    "fiscal_year",
                    "mock",
                    "",
                    "",
                    "",
                    "",
                    str(index),
                    "system",
                    "Internal",
                    "automated",
                    "",
                    (row.get("DataType") or "numeric").lower(),
                ]
            )
    return len(framework_rows)


def _fake_timer(values: list[float]):
    iterator = iter(values)
    return lambda: next(iterator)


def test_run_gate_passes_and_writes_evidence(tmp_path):
    framework_csv = tmp_path / "framework_datapoints.csv"
    granulated_csv = tmp_path / "granulated_variables.csv"
    report_path = tmp_path / "e4_performance_report.txt"
    summary_path = tmp_path / "e4_performance_summary.csv"

    _write_csv(
        framework_csv,
        [
            "FrameworkID",
            "FrameworkVersion",
            "DatapointCode",
            "Label",
            "Description",
            "DataType",
            "DefaultUnit",
            "Topic",
            "Subtopic",
        ],
        _make_framework_rows(1200),
    )
    _write_csv(
        granulated_csv,
        [
            "FrameworkID",
            "FrameworkVersion",
            "DatapointCode",
            "GranulatedID",
            "Label",
            "Description",
            "UnitName",
            "UnitType",
            "Dimensions",
            "DimensionMembers",
            "IsActivityData",
            "IsCalculated",
            "CalculationLogic",
            "CalculationComponents",
            "XbrlConceptID",
            "AtomizationJustification",
        ],
        _make_granulated_rows(1200),
    )

    result = e4_gate.run_gate(
        framework_path=framework_csv,
        granulated_path=granulated_csv,
        report_path=report_path,
        summary_path=summary_path,
        transform_fn=_stub_transform,
        timer=_fake_timer([10.0, 12.5]),
    )

    assert result.passed
    assert result.transformed_rows == 1200
    assert result.rows_environmental == 400
    assert result.rows_social == 400
    assert result.rows_governance == 400
    assert result.success_rate == 1.0
    assert report_path.exists()
    assert summary_path.exists()

    report = report_path.read_text(encoding="utf-8")
    assert "Gate result: PASS" in report
    assert "Environmental/carbon: 400" in report
    assert "Rows processed: 1200" not in report  # the report uses "Transformed rows"
    assert "Transformed rows:     1200" in report
    assert str(framework_csv) not in report
    assert "external-package:framework_datapoints.csv" in report
    assert "Granulated variables:" in report

    with summary_path.open("r", encoding="utf-8", newline="") as handle:
        summary = list(csv.DictReader(handle))
    assert len(summary) == 1
    row = summary[0]
    assert row["gate_result"] == "PASS"
    assert row["transformed_rows"] == "1200"
    assert row["rows_environmental"] == "400"
    assert row["success_rate"] == "1.0000"


def test_run_gate_fails_on_too_few_rows(tmp_path):
    framework_csv = tmp_path / "framework_datapoints.csv"
    granulated_csv = tmp_path / "granulated_variables.csv"
    report_path = tmp_path / "e4_performance_report.txt"
    summary_path = tmp_path / "e4_performance_summary.csv"

    _write_csv(
        framework_csv,
        [
            "FrameworkID",
            "FrameworkVersion",
            "DatapointCode",
            "Label",
            "Description",
            "DataType",
            "DefaultUnit",
            "Topic",
            "Subtopic",
        ],
        _make_framework_rows(500),
    )
    _write_csv(
        granulated_csv,
        [
            "FrameworkID",
            "FrameworkVersion",
            "DatapointCode",
            "GranulatedID",
            "Label",
            "Description",
            "UnitName",
            "UnitType",
            "Dimensions",
            "DimensionMembers",
            "IsActivityData",
            "IsCalculated",
            "CalculationLogic",
            "CalculationComponents",
            "XbrlConceptID",
            "AtomizationJustification",
        ],
        _make_granulated_rows(500),
    )

    result = e4_gate.run_gate(
        framework_path=framework_csv,
        granulated_path=granulated_csv,
        report_path=report_path,
        summary_path=summary_path,
        transform_fn=_stub_transform,
        timer=_fake_timer([10.0, 12.5]),
    )

    assert not result.passed
    assert any(
        "rows_processed_below_threshold" in reason for reason in result.failure_reasons
    )

    summary = list(csv.DictReader(summary_path.open("r", encoding="utf-8", newline="")))
    assert summary[0]["gate_result"] == "FAIL"


def test_run_gate_fails_on_timeout(tmp_path):
    framework_csv = tmp_path / "framework_datapoints.csv"
    granulated_csv = tmp_path / "granulated_variables.csv"
    report_path = tmp_path / "e4_performance_report.txt"
    summary_path = tmp_path / "e4_performance_summary.csv"

    _write_csv(
        framework_csv,
        [
            "FrameworkID",
            "FrameworkVersion",
            "DatapointCode",
            "Label",
            "Description",
            "DataType",
            "DefaultUnit",
            "Topic",
            "Subtopic",
        ],
        _make_framework_rows(1200),
    )
    _write_csv(
        granulated_csv,
        [
            "FrameworkID",
            "FrameworkVersion",
            "DatapointCode",
            "GranulatedID",
            "Label",
            "Description",
            "UnitName",
            "UnitType",
            "Dimensions",
            "DimensionMembers",
            "IsActivityData",
            "IsCalculated",
            "CalculationLogic",
            "CalculationComponents",
            "XbrlConceptID",
            "AtomizationJustification",
        ],
        _make_granulated_rows(1200),
    )

    result = e4_gate.run_gate(
        framework_path=framework_csv,
        granulated_path=granulated_csv,
        report_path=report_path,
        summary_path=summary_path,
        transform_fn=_stub_transform,
        timer=_fake_timer([10.0, 45.0]),
    )

    assert not result.passed
    assert any(
        "elapsed_seconds_above_threshold" in reason for reason in result.failure_reasons
    )


def test_run_gate_fails_when_required_rule_families_are_missing(tmp_path):
    """The E4 gate requires the three required rule families to be present."""
    framework_csv = tmp_path / "framework_datapoints.csv"
    granulated_csv = tmp_path / "granulated_variables.csv"
    report_path = tmp_path / "e4_performance_report.txt"
    summary_path = tmp_path / "e4_performance_summary.csv"

    _write_csv(
        framework_csv,
        [
            "FrameworkID",
            "FrameworkVersion",
            "DatapointCode",
            "Label",
            "Description",
            "DataType",
            "DefaultUnit",
            "Topic",
            "Subtopic",
        ],
        _make_framework_rows(1200, family_split=(1200, 0, 0)),
    )
    _write_csv(
        granulated_csv,
        [
            "FrameworkID",
            "FrameworkVersion",
            "DatapointCode",
            "GranulatedID",
            "Label",
            "Description",
            "UnitName",
            "UnitType",
            "Dimensions",
            "DimensionMembers",
            "IsActivityData",
            "IsCalculated",
            "CalculationLogic",
            "CalculationComponents",
            "XbrlConceptID",
            "AtomizationJustification",
        ],
        _make_granulated_rows(1200),
    )

    result = e4_gate.run_gate(
        framework_path=framework_csv,
        granulated_path=granulated_csv,
        report_path=report_path,
        summary_path=summary_path,
        transform_fn=_stub_transform,
        timer=_fake_timer([10.0, 12.5]),
    )

    assert not result.passed
    assert result.rows_environmental == 1200
    assert result.rows_social == 0
    assert result.rows_governance == 0
    assert any(
        "missing_rule_family: Social" in reason for reason in result.failure_reasons
    )
    assert any(
        "missing_rule_family: Governance" in reason for reason in result.failure_reasons
    )


def test_run_gate_supports_register_first_mode(tmp_path):
    register_csv = tmp_path / "sds_dataset_register.csv"
    report_path = tmp_path / "e4_performance_report.txt"
    summary_path = tmp_path / "e4_performance_summary.csv"

    _write_csv(
        register_csv,
        [
            "identifier",
            "title",
            "indicator",
            "description",
            "dimension",
            "unitName",
            "unitType",
            "periodicity",
            "periodType",
            "sourceRef",
            "codeESRS",
            "codeGRI",
            "codeGRI_expanded",
            "evidencePath",
            "sourceRow",
            "owner",
            "accessRights",
            "validationMethod",
            "doubleMateriality",
            "valueType",
        ],
        [
            [
                f"urn:sds:reg:esrs:item_{index}",
                f"Indicator {index}",
                f"Indicator {index}",
                "Description",
                "E" if index < 500 else ("S" if index < 900 else "G"),
                "kWh",
                "Energy",
                "annual",
                "fiscal_year",
                "Standards register",
                f"E-{index}",
                "",
                "",
                "",
                str(index),
                "system",
                "Internal",
                "automated",
                "",
                "numeric",
            ]
            for index in range(1200)
        ],
    )

    result = e4_gate.run_gate(
        register_path=register_csv,
        report_path=report_path,
        summary_path=summary_path,
        timer=_fake_timer([20.0, 22.0]),
    )

    assert result.passed
    assert result.source_mode == "register"
    assert result.register_path == register_csv
    assert result.transformed_rows == 1200
    assert result.rows_environmental == 500
    assert result.rows_social == 400
    assert result.rows_governance == 300
    report = report_path.read_text(encoding="utf-8")
    assert "Source mode: register" in report
    assert "Register package:" in report
    assert str(register_csv) not in report
    assert "external-package:sds_dataset_register.csv" in report

    summary = list(csv.DictReader(summary_path.open("r", encoding="utf-8", newline="")))
    assert summary[0]["register_path"] == "external-package:sds_dataset_register.csv"
    assert summary[0]["register_rows"] == "1200"
    assert "framework_path" not in summary[0]
    assert "granulated_path" not in summary[0]


def test_register_rule_family_counter_accepts_full_dimension_labels(tmp_path):
    register_csv = tmp_path / "sds_dataset_register.csv"

    rows = []
    for index, dimension in enumerate(
        ["Environmental"] * 500 + ["Social"] * 400 + ["Governance"] * 300
    ):
        rows.append(
            [
                f"urn:sds:reg:item_{index}",
                "Title",
                "Indicator",
                "Description",
                dimension,
            ]
        )

    _write_csv(
        register_csv,
        ["identifier", "title", "indicator", "description", "dimension"],
        rows,
    )

    counts = e4_gate._count_register_rule_families(register_csv)

    assert counts["Environmental/carbon"] == 500
    assert counts["Social"] == 400
    assert counts["Governance"] == 300
    assert not e4_gate._missing_required_rule_families(counts)


def test_run_gate_supports_dimension_ledger_mode(tmp_path):
    ledger_csv = tmp_path / "dimension-ledger.csv"
    report_path = tmp_path / "e4_performance_report.txt"
    summary_path = tmp_path / "e4_performance_summary.csv"

    _write_csv(
        ledger_csv,
        [
            "standard",
            "standard_area",
            "reportable_coordinates",
            "technical_coordinates",
        ],
        [
            ["ESRS", "E1", "800", "800"],
            ["ESRS", "S1", "300", "300"],
            ["ESRS", "G1", "200", "200"],
            ["ESRS", "ESRS 2", "50", "50"],
            ["GRI", "GRI", "100", "120"],
            ["GHG Protocol", "GHG Protocol", "", "10"],
        ],
    )

    result = e4_gate.run_gate(
        dimension_ledger_path=ledger_csv,
        report_path=report_path,
        summary_path=summary_path,
        timer=_fake_timer([20.0, 22.0]),
    )

    assert result.passed
    assert result.source_mode == "dimension-ledger"
    assert result.transformed_rows == 1450
    assert result.granulated_rows == 6
    assert result.rows_environmental == 800
    assert result.rows_social == 300
    assert result.rows_governance == 200
    assert result.rows_transversal == 150

    report = report_path.read_text(encoding="utf-8")
    assert "Source mode: dimension-ledger" in report
    assert "Dimension ledger:" in report
    assert "Ledger groups:        6" in report
    assert "Reportable coordinates: 1450" in report
    assert "Coordinates summed:   1450" in report
    assert "Transformed rows:" not in report
    assert "Success rate:" not in report
    assert "structural_ledger_nonempty" in report

    summary = list(csv.DictReader(summary_path.open("r", encoding="utf-8", newline="")))
    assert (
        summary[0]["dimension_ledger_path"] == "external-package:dimension-ledger.csv"
    )
    assert summary[0]["ledger_groups"] == "6"
    assert summary[0]["reportable_coordinates"] == "1450"
    assert summary[0]["coordinates_summed"] == "1450"
    assert "transformed_rows" not in summary[0]


def test_dimension_ledger_classifies_gri_rows_from_disclosure_codes():
    cases = {
        "examples: GRI 305-1.a, GRI_302_3_a_energy": "Environmental/carbon",
        "examples: GRI 405-1.a, GRI-SF-11.11.5_405-1-a": "Social",
        "examples: GRI 205-2.c, GRI 201-1.a": "Governance",
        "examples: GRI 2-7.a, GRI_2_7_a_by_gender": "Governance",
        "examples: GRI 3-3.a, GRI 3-3.b": "Transversal",
        "examples: GRI 204-1.a, GRI 401-2.a": "Transversal",
    }

    for scope_or_group, expected in cases.items():
        assert (
            e4_gate._dimension_row_to_rule_family(
                {
                    "standard": "GRI",
                    "standard_area": "GRI",
                    "scope_or_group": scope_or_group,
                }
            )
            == expected
        )


def test_select_register_path_prefers_register_boundary_when_available(tmp_path):
    default_register = tmp_path / "sds_dataset_register.csv"
    default_register.write_text("identifier\nurn:sds:test\n", encoding="utf-8")

    assert (
        e4_gate._select_register_path("auto", None, default_register)
        == default_register
    )
    assert (
        e4_gate._select_register_path("register", None, default_register)
        == default_register
    )
    assert (
        e4_gate._select_register_path("legacy", default_register, default_register)
        is None
    )
    assert (
        e4_gate._select_register_path(
            "auto", tmp_path / "explicit.csv", default_register
        )
        == tmp_path / "explicit.csv"
    )
