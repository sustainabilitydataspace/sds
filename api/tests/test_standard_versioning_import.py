from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import src.services.standard_versioning_import as standard_versioning_import
from scripts import import_standard_versioning as standard_versioning_cli
from src.database.models import StandardDatapoint, StandardRelease
from src.services.standard_versioning_import import (
    StandardVersioningImportError,
    StandardVersioningImportReport,
    _lineage_rows,
    _load_register_rows,
    _load_standard_versioning_payload,
    _object,
    import_standard_versioning_package,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


def _session():
    engine = create_engine("sqlite:///:memory:")
    StandardRelease.__table__.create(engine)
    StandardDatapoint.__table__.create(engine)
    return sessionmaker(bind=engine)()


def _write_package(package_dir: Path, *, release_name: str = "ESRS Set 1") -> None:
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "sds_dataset_register.csv").write_text(
        (
            "identifier,title,description,unitName,valueType\n"
            "urn:sds:reg:esrs:e1_6_07,Gross Scope 1 emissions,"
            "Gross Scope 1 greenhouse gas emissions,tCO2e,numeric\n"
        ),
        encoding="utf-8",
        newline="",
    )
    manifest = {
        "standard_versioning": {
            "schema_version": "standard-versioning-v1",
            "release_manifest": {
                "standard_id": "ESRS",
                "release_id": "ESRS_SET1_2023_12_22",
                "official_name": release_name,
                "publisher": "European Commission",
                "publication_date": "2023-12-22",
                "effective_date": "2024-01-01",
                "release_state": "active",
                "source_hash": "a" * 64,
                "source_hash_set": {"framework_datapoints": "b" * 64},
                "trust_envelope_id": "trust:test",
            },
            "delta_manifest": {"transition_kind": "initial_snapshot"},
            "affected_scope_plan": {"scope_states": {"current": 1}},
            "datapoint_lineage_map": [
                {
                    "sds_identifier": "urn:sds:reg:esrs:e1_6_07",
                    "standard_id": "ESRS",
                    "release_id": "ESRS_SET1_2023_12_22",
                    "datapoint_id": "E1-6_07",
                    "concept_id": "ESRS:E1-6_07",
                    "relationship": "maps_to_sds_identifier",
                }
            ],
            "tenant_activation_policy": {
                "dry_run_required": True,
                "real_import_requires_explicit_approval": True,
            },
        }
    }
    (package_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def test_standard_versioning_import_dry_run_rolls_back(tmp_path: Path) -> None:
    _write_package(tmp_path)
    db = _session()

    report = import_standard_versioning_package(
        package_dir=tmp_path,
        register_csv=tmp_path / "sds_dataset_register.csv",
        db=db,
        dry_run=True,
        created_by="test",
    )

    assert report.valid is True
    assert report.committed is False
    assert report.counts["standard_releases_created"] == 1
    assert report.counts["standard_datapoints_created"] == 1
    assert db.query(StandardRelease).count() == 0
    assert db.query(StandardDatapoint).count() == 0


def test_standard_versioning_cli_dry_run_does_not_initialize_database(
    tmp_path: Path, monkeypatch
) -> None:
    _write_package(tmp_path)

    def fail_create_engine(*_args, **_kwargs):
        raise AssertionError("dry-run must not create a database engine")

    def fail_init_db(*_args, **_kwargs):
        raise AssertionError("dry-run must not initialize or migrate the database")

    monkeypatch.setattr(standard_versioning_cli, "create_engine", fail_create_engine)
    monkeypatch.setattr(standard_versioning_cli, "init_db_for_engine", fail_init_db)

    result = standard_versioning_cli.main(
        [
            "--package-dir",
            str(tmp_path),
            "--register-csv",
            str(tmp_path / "sds_dataset_register.csv"),
            "--dry-run",
        ]
    )

    assert result == 0


def test_standard_versioning_import_commits_idempotently(tmp_path: Path) -> None:
    _write_package(tmp_path)
    db = _session()

    first = import_standard_versioning_package(
        package_dir=tmp_path,
        register_csv=tmp_path / "sds_dataset_register.csv",
        db=db,
        created_by="test",
    )
    second = import_standard_versioning_package(
        package_dir=tmp_path,
        register_csv=tmp_path / "sds_dataset_register.csv",
        db=db,
        created_by="test",
    )

    release = db.query(StandardRelease).one()
    datapoint = db.query(StandardDatapoint).one()

    assert first.committed is True
    assert second.counts["standard_releases_unchanged"] == 1
    assert second.counts["standard_datapoints_unchanged"] == 1
    assert release.standard_id == "ESRS"
    assert release.version == "ESRS_SET1_2023_12_22"
    assert release.release_date.isoformat() == "2023-12-22"
    assert release.provenance["standard_versioning"]["source_hash"] == "a" * 64
    assert datapoint.code == "E1-6_07"
    assert datapoint.label == "Gross Scope 1 emissions"
    assert datapoint.metadata_json["sds_identifier"] == "urn:sds:reg:esrs:e1_6_07"


def test_standard_versioning_import_updates_existing_release(tmp_path: Path) -> None:
    _write_package(tmp_path, release_name="ESRS Set 1")
    db = _session()

    import_standard_versioning_package(
        package_dir=tmp_path,
        register_csv=tmp_path / "sds_dataset_register.csv",
        db=db,
        created_by="test",
    )
    _write_package(tmp_path, release_name="ESRS Set 1 delegated act")
    report = import_standard_versioning_package(
        package_dir=tmp_path,
        register_csv=tmp_path / "sds_dataset_register.csv",
        db=db,
        created_by="test",
    )

    assert report.counts["standard_releases_updated"] == 1
    assert db.query(StandardRelease).one().name == "ESRS Set 1 delegated act"


def test_register_row_lookup_is_rfc4180_compatible(tmp_path: Path) -> None:
    _write_package(tmp_path)
    with (tmp_path / "sds_dataset_register.csv").open(
        "a",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "urn:sds:reg:esrs:e1_6_08",
                "Scope 2, location-based",
                "Comma in title stays parseable",
                "tCO2e",
                "numeric",
            ]
        )

    db = _session()
    report = import_standard_versioning_package(
        package_dir=tmp_path,
        register_csv=tmp_path / "sds_dataset_register.csv",
        db=db,
        dry_run=True,
        created_by="test",
    )

    assert report.valid is True


def test_standard_versioning_import_rejects_lineage_missing_from_register(
    tmp_path: Path,
) -> None:
    _write_package(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["standard_versioning"]["datapoint_lineage_map"][0][
        "sds_identifier"
    ] = "urn:sds:reg:esrs:missing"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StandardVersioningImportError, match="not present"):
        import_standard_versioning_package(
            package_dir=tmp_path,
            register_csv=tmp_path / "sds_dataset_register.csv",
            db=_session(),
            dry_run=True,
            created_by="test",
        )


def test_standard_versioning_import_rejects_release_identity_mismatch(
    tmp_path: Path,
) -> None:
    _write_package(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["standard_versioning"]["datapoint_lineage_map"][0][
        "release_id"
    ] = "ESRS_SET1_2024_01_01"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StandardVersioningImportError, match="release_id"):
        import_standard_versioning_package(
            package_dir=tmp_path,
            register_csv=tmp_path / "sds_dataset_register.csv",
            db=_session(),
            dry_run=True,
            created_by="test",
        )


def test_standard_versioning_import_rejects_duplicate_datapoint_key(
    tmp_path: Path,
) -> None:
    _write_package(tmp_path)
    with (tmp_path / "sds_dataset_register.csv").open(
        "a",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "urn:sds:reg:esrs:e1_6_07_split",
                "Gross Scope 1 emissions split",
                "Second atomized row for same source datapoint",
                "tCO2e",
                "numeric",
            ]
        )
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    duplicate = dict(manifest["standard_versioning"]["datapoint_lineage_map"][0])
    duplicate["sds_identifier"] = "urn:sds:reg:esrs:e1_6_07_split"
    manifest["standard_versioning"]["datapoint_lineage_map"].append(duplicate)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StandardVersioningImportError, match="duplicate"):
        import_standard_versioning_package(
            package_dir=tmp_path,
            register_csv=tmp_path / "sds_dataset_register.csv",
            db=_session(),
            dry_run=True,
            created_by="test",
        )


def test_standard_versioning_report_and_invalid_manifest_branch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = StandardVersioningImportReport(
        package_dir="package",
        dry_run=True,
        valid=False,
        committed=False,
        standard_id="ESRS",
        release_id="SET1",
        counts={"standard_releases_created": 1},
        errors=["broken"],
    )
    assert report.as_dict()["errors"] == ["broken"]

    monkeypatch.setattr(
        standard_versioning_import,
        "validate_standard_versioning_manifest",
        lambda *_args, **_kwargs: type(
            "Validation",
            (),
            {"valid": False, "errors": ("missing manifest",)},
        )(),
    )

    invalid = import_standard_versioning_package(
        package_dir=tmp_path,
        register_csv=tmp_path / "missing.csv",
        db=_session(),
        dry_run=True,
    )

    assert invalid.valid is False
    assert invalid.errors == ["missing manifest"]


def test_standard_versioning_rolls_back_when_db_write_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_package(tmp_path)
    db = _session()
    monkeypatch.setattr(
        standard_versioning_import,
        "_upsert_standard_release",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("db failed")),
    )

    with pytest.raises(RuntimeError, match="db failed"):
        import_standard_versioning_package(
            package_dir=tmp_path,
            register_csv=tmp_path / "sds_dataset_register.csv",
            db=db,
            dry_run=False,
            created_by="test",
        )

    assert db.query(StandardRelease).count() == 0


def test_standard_versioning_helpers_reject_bad_shapes(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("[]", encoding="utf-8")
    with pytest.raises(StandardVersioningImportError, match="manifest.json"):
        _load_standard_versioning_payload(tmp_path)

    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(StandardVersioningImportError, match="standard_versioning"):
        _load_standard_versioning_payload(tmp_path)

    no_identifier = tmp_path / "no_identifier.csv"
    no_identifier.write_text("title\nOnly title\n", encoding="utf-8")
    assert _load_register_rows(no_identifier) == {}

    with pytest.raises(StandardVersioningImportError, match="must be a list"):
        _lineage_rows("not-a-list")
    with pytest.raises(StandardVersioningImportError, match="entries must be objects"):
        _lineage_rows(["not-an-object"])
    with pytest.raises(StandardVersioningImportError, match="expected JSON object"):
        _object([])


def test_standard_versioning_updates_existing_datapoint(tmp_path: Path) -> None:
    _write_package(tmp_path)
    db = _session()
    import_standard_versioning_package(
        package_dir=tmp_path,
        register_csv=tmp_path / "sds_dataset_register.csv",
        db=db,
        created_by="test",
    )

    (tmp_path / "sds_dataset_register.csv").write_text(
        (
            "identifier,title,description,unitName,valueType\n"
            "urn:sds:reg:esrs:e1_6_07,Updated title,"
            "Updated description,kgCO2e,numeric\n"
        ),
        encoding="utf-8",
        newline="",
    )

    report = import_standard_versioning_package(
        package_dir=tmp_path,
        register_csv=tmp_path / "sds_dataset_register.csv",
        db=db,
        created_by="test",
    )

    datapoint = db.query(StandardDatapoint).one()
    assert report.counts["standard_datapoints_updated"] == 1
    assert datapoint.label == "Updated title"
    assert datapoint.unit == "kgCO2e"


def test_standard_versioning_rejects_standard_identity_mismatch(
    tmp_path: Path,
) -> None:
    _write_package(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["standard_versioning"]["datapoint_lineage_map"][0]["standard_id"] = "GRI"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StandardVersioningImportError, match="standard_id"):
        import_standard_versioning_package(
            package_dir=tmp_path,
            register_csv=tmp_path / "sds_dataset_register.csv",
            db=_session(),
            dry_run=True,
            created_by="test",
        )
