from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "api" / "scripts" / "import_indicators.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "import_indicators_script", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


import_indicators = _load_module()


def test_default_csv_path_points_to_repo_root_processed_register():
    expected = REPO_ROOT / "data" / "processed" / "e1_dataset_register.csv"
    assert import_indicators.DEFAULT_CSV_PATH == expected


def test_resolve_csv_input_path_supports_repo_relative_register_path(
    tmp_path, monkeypatch
):
    register_csv = tmp_path / "data" / "external" / "sds_dataset_register.csv"
    register_csv.parent.mkdir(parents=True)
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    monkeypatch.setattr(import_indicators, "REPO_ROOT", tmp_path)

    resolved = import_indicators.resolve_csv_input_path(
        Path("data/external/sds_dataset_register.csv")
    )

    assert resolved == register_csv.resolve()


def test_normalize_indicator_row_canonicalizes_legacy_gri_urn():
    analysis = import_indicators.normalize_indicator_row(
        {
            "identifier": "urn:sds:reg:gri:gri 101_1_a",
            "title": "Biodiversity policy",
            "dimension": "E",
            "codeGRI": "GRI 101-1.a",
        }
    )

    assert analysis["original_identifier"] == "urn:sds:reg:gri:gri 101_1_a"
    assert analysis["canonical_identifier"] == "urn:sds:reg:gri:gri_101_1_a"
    assert analysis["record"]["identifier"] == "urn:sds:reg:gri:gri_101_1_a"
    assert analysis["normalized"] is True
    assert analysis["valid"] is True


def test_dry_run_validates_invalid_rows_after_sample_window(tmp_path):
    csv_path = tmp_path / "register.csv"
    header = (
        "identifier,title,indicator,description,dimension,unitName,unitType,periodicity,periodType,"
        "sourceRef,codeESRS,codeGRI,codeGRI_expanded,evidencePath,sourceRow,owner,accessRights,"
        "validationMethod,doubleMateriality,valueType\n"
    )
    rows = [
        "urn:sds:reg:test:1,One,One,,E,kWh,Energy,annual,fy,src,,,,,1,ESG,internal,documentary,double,numeric\n",
        "urn:sds:reg:test:2,Two,Two,,E,kWh,Energy,annual,fy,src,,,,,2,ESG,internal,documentary,double,numeric\n",
        "urn:sds:reg:test:3,Three,Three,,E,kWh,Energy,annual,fy,src,,,,,3,ESG,internal,documentary,double,numeric\n",
        "not-a-urn,,,,E,kWh,Energy,annual,fy,src,,,,,4,ESG,internal,documentary,double,numeric\n",
    ]
    csv_path.write_text(header + "".join(rows), encoding="utf-8")

    try:
        import_indicators.seed_indicators(
            csv_path=csv_path,
            db_url="postgresql://unused",
            dry_run=True,
        )
    except ValueError as error:
        assert "at least one URN remains invalid" in str(error)
    else:
        raise AssertionError("Expected dry-run to reject invalid rows beyond row 3")


def test_indicator_unit_type_column_accepts_official_atomizer_unit_labels():
    unit_type = import_indicators.Indicator.__table__.c.unit_type.type

    assert unit_type.length >= len(
        "Metric tons of CFC-11 (trichlorofluoromethane) equivalent"
    )


def test_indicator_period_columns_accept_official_atomizer_labels():
    table = import_indicators.Indicator.__table__

    assert table.c.periodicity.type.length >= len("sustainability reporting frequency")
    assert table.c.period_type.type.length >= len(
        "sustainability_reporting_period_start"
    )


def test_indicator_code_esrs_accepts_compiled_child_datapoint_codes():
    code_esrs = import_indicators.Indicator.__table__.c.code_esrs.type

    assert code_esrs.length >= len(
        "E4-4_05.other_biodiversity_and_ecosystem_related_national_policies_and_legislation"
    )


def test_migrate_legacy_identifiers_renames_existing_records_without_duplication():
    indicator_table = import_indicators.Indicator.__table__
    engine = create_engine("sqlite:///:memory:")
    indicator_table.create(engine)
    session = sessionmaker(bind=engine)()

    session.add(
        import_indicators.Indicator(
            id="urn:sds:reg:gri:gri 101_1_a",
            identifier="urn:sds:reg:gri:gri 101_1_a",
            title="Biodiversity policy",
            dimension="E",
        )
    )
    session.commit()

    migrated, removed = import_indicators.migrate_legacy_identifiers(
        session,
        {"urn:sds:reg:gri:gri 101_1_a": "urn:sds:reg:gri:gri_101_1_a"},
    )

    assert migrated == 1
    assert removed == 0
    rows = session.query(import_indicators.Indicator).all()
    assert len(rows) == 1
    assert rows[0].id == "urn:sds:reg:gri:gri_101_1_a"
    assert rows[0].identifier == "urn:sds:reg:gri:gri_101_1_a"


def test_migrate_legacy_identifiers_removes_stale_legacy_rows_when_canonical_exists():
    indicator_table = import_indicators.Indicator.__table__
    engine = create_engine("sqlite:///:memory:")
    indicator_table.create(engine)
    session = sessionmaker(bind=engine)()

    session.add_all(
        [
            import_indicators.Indicator(
                id="urn:sds:reg:gri:gri 101_1_a",
                identifier="urn:sds:reg:gri:gri 101_1_a",
                title="Legacy",
                dimension="E",
            ),
            import_indicators.Indicator(
                id="urn:sds:reg:gri:gri_101_1_a",
                identifier="urn:sds:reg:gri:gri_101_1_a",
                title="Canonical",
                dimension="E",
            ),
        ]
    )
    session.commit()

    migrated, removed = import_indicators.migrate_legacy_identifiers(
        session,
        {"urn:sds:reg:gri:gri 101_1_a": "urn:sds:reg:gri:gri_101_1_a"},
    )

    assert migrated == 0
    assert removed == 1
    rows = (
        session.query(import_indicators.Indicator)
        .order_by(import_indicators.Indicator.identifier)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].identifier == "urn:sds:reg:gri:gri_101_1_a"


def test_retire_missing_indicators_by_prefix_only_deactivates_closed_scope():
    indicator_table = import_indicators.Indicator.__table__
    engine = create_engine("sqlite:///:memory:")
    indicator_table.create(engine)
    session = sessionmaker(bind=engine)()

    session.add_all(
        [
            import_indicators.Indicator(
                id="urn:sds:reg:gri:gri_101_1_a",
                identifier="urn:sds:reg:gri:gri_101_1_a",
                title="Current GRI",
                dimension="E",
                is_active=True,
            ),
            import_indicators.Indicator(
                id="urn:sds:reg:gri:gri_999_1_a",
                identifier="urn:sds:reg:gri:gri_999_1_a",
                title="Stale GRI",
                dimension="E",
                is_active=True,
            ),
            import_indicators.Indicator(
                id="urn:sds:reg:esrs:e1_01",
                identifier="urn:sds:reg:esrs:e1_01",
                title="ESRS untouched",
                dimension="E",
                is_active=True,
            ),
        ]
    )
    session.commit()

    retired = import_indicators.retire_missing_indicators_by_prefix(
        session,
        [{"identifier": "urn:sds:reg:gri:gri_101_1_a"}],
        ["urn:sds:reg:gri:"],
    )

    assert retired == 1
    rows = {
        row.identifier: row.is_active
        for row in session.query(import_indicators.Indicator).all()
    }
    assert rows == {
        "urn:sds:reg:gri:gri_101_1_a": True,
        "urn:sds:reg:gri:gri_999_1_a": False,
        "urn:sds:reg:esrs:e1_01": True,
    }


def test_validate_retirement_approval_counts_require_exact_prefix_and_count():
    plan = {"urn:sds:reg:ghg:": 247}

    try:
        import_indicators.validate_retirement_approval_counts(plan, {})
    except ValueError as exc:
        message = str(exc)
        assert "urn:sds:reg:ghg:=247" in message
        assert "--approve-retirement-impact" in message
    else:
        raise AssertionError("retirement proceeded without explicit impact approval")

    try:
        import_indicators.validate_retirement_approval_counts(
            plan, {"urn:sds:reg:ghg:": 113}
        )
    except ValueError as exc:
        message = str(exc)
        assert "approved 113" in message
        assert "planned 247" in message
    else:
        raise AssertionError("retirement proceeded with the wrong approved count")

    import_indicators.validate_retirement_approval_counts(
        plan, {"urn:sds:reg:ghg:": 247}
    )
