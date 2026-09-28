"""Tests for canonical mapping package shadow DB import."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from src.database.models import (
    CanonicalConcept,
    MappingAssertionComponent,
    MappingAssertionGroup,
    MaterializedPairwiseMapping,
    StandardDatapoint,
    StandardMapping,
    StandardRelease,
)
from src.services import canonical_mapping_db_import as cmdbi
from src.services.canonical_mapping_db_import import (
    CanonicalMappingDbImportReport,
    _parse_json_object,
    _retire_absent_assertion_groups,
    import_canonical_mapping_package_to_db,
)
from src.services.canonical_mapping_import import CanonicalMappingPackageSecurityError


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _sha256_for(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_package(
    package_dir: Path,
    *,
    source_code: str = "E1-6_07",
    assertion_hash: str = "a" * 64,
    sygris_uri: str = "syg:GrossScope1GreenhouseGasEmissions",
    relationship_type: str = "equivalent",
) -> None:
    files = {
        "sds_standard_releases.csv": [
            {
                "standard_id": "ESRS",
                "name": "European Sustainability Reporting Standards",
                "version": "2024",
                "release_date": "2024-01-01",
                "source_url": "https://example.invalid/esrs",
                "lifecycle_status": "active",
                "provenance": '{"source":"unit-test"}',
            }
        ],
        "sds_standard_datapoints.csv": [
            {
                "standard_id": "ESRS",
                "standard_version": "2024",
                "code": source_code,
                "label": "Gross Scope 1 greenhouse gas emissions",
                "disclosure_text": "Scope 1 disclosure",
                "datapoint_type": "numeric",
                "unit": "tCO2e",
                "lifecycle_status": "active",
                "metadata_json": '{"source":"unit-test"}',
            }
        ],
        "sds_mapping_assertion_groups.csv": [
            {
                "source_standard_id": "ESRS",
                "source_standard_version": "2024",
                "source_code": source_code,
                "mapping_profile": "default",
                "relationship_type": relationship_type,
                "coverage_status": "complete",
                "confidence": "0.95",
                "rationale": "Same Sygris canonical pivot.",
                "coverage_summary": "Full coverage.",
                "difference_summary": "",
                "valid_from": "2026-05-08T00:00:00Z",
                "valid_to": "",
                "approval_status": "draft",
                "publication_status": "internal",
                "assertion_hash": assertion_hash,
                "provenance": '{"seed":"unit-test"}',
            }
        ],
        "sds_mapping_assertion_components.csv": [
            {
                "source_standard_id": "ESRS",
                "source_standard_version": "2024",
                "source_code": source_code,
                "mapping_profile": "default",
                "component_order": "1",
                "sygris_canonical_uri": sygris_uri,
                "sygris_revision": "1",
                "component_role": "primary",
                "coverage_fraction": "1.0000",
                "match_scope": "Full concept.",
                "mismatch_scope": "",
                "transformation_rule": "",
                "rationale": "Sygris canonical concept is the pivot.",
            }
        ],
    }
    for filename, rows in files.items():
        _write_csv(package_dir / filename, rows)

    manifest = {
        "package_schema_version": "1.0",
        "files": [
            {"filename": filename, "sha256": _sha256_for(package_dir / filename)}
            for filename in files
        ],
    }
    (package_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (package_dir / "MANIFEST.sha256").write_text(
        "\n".join(
            f"{_sha256_for(package_dir / filename)} *{filename}" for filename in files
        )
        + "\n",
        encoding="utf-8",
    )


def _session():
    engine = create_engine("sqlite:///:memory:")
    for table in (
        CanonicalConcept.__table__,
        StandardRelease.__table__,
        StandardDatapoint.__table__,
        MappingAssertionGroup.__table__,
        MappingAssertionComponent.__table__,
        MaterializedPairwiseMapping.__table__,
        StandardMapping.__table__,
    ):
        table.create(engine)
    return sessionmaker(bind=engine)()


def test_canonical_mapping_db_import_dry_run_rolls_back(tmp_path):
    _write_package(tmp_path)
    db = _session()

    report = import_canonical_mapping_package_to_db(
        package_dir=tmp_path,
        db=db,
        dry_run=True,
        created_by="test",
    )

    assert report.valid is True
    assert report.committed is False
    assert report.counts["standard_releases_created"] == 1
    assert report.counts["standard_datapoints_created"] == 1
    assert report.counts["sygris_canonical_concepts_created"] == 1
    assert report.counts["mapping_assertion_groups_created"] == 1
    assert report.counts["mapping_assertion_components_created"] == 1
    assert db.query(StandardRelease).count() == 0
    assert db.query(MappingAssertionGroup).count() == 0


def test_canonical_mapping_db_import_can_leave_writes_in_caller_transaction(tmp_path):
    _write_package(tmp_path)
    db = _session()
    report = import_canonical_mapping_package_to_db(
        package_dir=tmp_path,
        db=db,
        dry_run=False,
        created_by="test",
        commit=False,
    )
    assert report.valid is True
    assert report.committed is False
    assert db.query(StandardRelease).count() == 1
    db.rollback()
    assert db.query(StandardRelease).count() == 0
    assert db.query(MappingAssertionGroup).count() == 0


def test_package_security_refusal_rolls_back_uncommitted_import_rows(
    tmp_path, monkeypatch
):
    _write_package(tmp_path)
    db = _session()

    def refuse_after_flush(**_kwargs):
        db.add(StandardRelease(standard_id="TEST", name="Synthetic", version="2024"))
        db.flush()
        raise CanonicalMappingPackageSecurityError(
            "package_changed_during_read", "synthetic snapshot integrity refusal"
        )

    monkeypatch.setattr(
        cmdbi, "_import_canonical_mapping_package_snapshot_to_db", refuse_after_flush
    )
    report = import_canonical_mapping_package_to_db(
        package_dir=tmp_path, db=db, created_by="test", commit=False
    )
    assert report.valid is False
    assert db.query(StandardRelease).count() == 0


def test_canonical_mapping_db_import_consumes_one_private_snapshot(
    tmp_path, monkeypatch
):
    _write_package(tmp_path)
    source = tmp_path / "sds_mapping_assertion_groups.csv"
    db = _session()
    original_validate = cmdbi._validate_canonical_mapping_package_snapshot

    def validate_then_mutate_external_source(snapshot):
        report = original_validate(snapshot)
        source.write_text("mutated after validation\n", encoding="utf-8")
        return report

    monkeypatch.setattr(
        cmdbi,
        "_validate_canonical_mapping_package_snapshot",
        validate_then_mutate_external_source,
    )

    report = import_canonical_mapping_package_to_db(
        package_dir=tmp_path,
        db=db,
        dry_run=True,
        created_by="test",
    )

    assert report.valid is True
    assert report.counts["mapping_assertion_groups_created"] == 1


def test_canonical_mapping_db_import_invalid_package_returns_validation_report(
    tmp_path,
):
    db = _session()

    report = import_canonical_mapping_package_to_db(
        package_dir=tmp_path,
        db=db,
        dry_run=True,
        created_by="test",
    )

    assert report.valid is False
    assert report.committed is False
    assert db.query(StandardRelease).count() == 0


def test_canonical_mapping_db_import_rolls_back_when_db_write_fails(
    tmp_path, monkeypatch
):
    _write_package(tmp_path)
    db = _session()
    rolled_back = {"value": False}

    def fail_flush():
        raise RuntimeError("flush failed")

    def mark_rollback():
        rolled_back["value"] = True

    monkeypatch.setattr(db, "flush", fail_flush)
    monkeypatch.setattr(db, "rollback", mark_rollback)

    try:
        import_canonical_mapping_package_to_db(
            package_dir=tmp_path,
            db=db,
            created_by="test",
        )
    except RuntimeError as exc:
        assert str(exc) == "flush failed"
    else:
        raise AssertionError("expected import failure")

    assert rolled_back["value"] is True


def test_retire_absent_assertion_groups_noops_without_release_ids():
    db = _session()
    report = CanonicalMappingDbImportReport(
        package_dir="pkg",
        mode="shadow_db_import",
        dry_run=False,
        valid=True,
        committed=False,
        validation=type("Validation", (), {"as_dict": lambda self: {}})(),
    )

    _retire_absent_assertion_groups(
        db,
        [{"assertion_hash": "a" * 64, "mapping_profile": "default"}],
        release_by_key={},
        report=report,
        created_by="test",
    )

    assert report.counts == {}


def test_canonical_mapping_db_import_allows_non_operational_dry_run(tmp_path):
    _write_package(tmp_path, relationship_type="partial_overlap")
    db = _session()

    report = import_canonical_mapping_package_to_db(
        package_dir=tmp_path,
        db=db,
        dry_run=True,
        created_by="test",
    )

    assert report.valid is True
    assert report.blocked is False
    assert report.committed is False
    assert report.relationship_type_counts["partial_overlap"] == 1
    assert report.counts["non_operational_assertion_groups_present"] == 1
    assert db.query(StandardRelease).count() == 0
    assert db.query(MappingAssertionGroup).count() == 0


def test_canonical_mapping_db_import_blocks_non_operational_commit(tmp_path):
    _write_package(tmp_path, relationship_type="component_of")
    db = _session()

    report = import_canonical_mapping_package_to_db(
        package_dir=tmp_path,
        db=db,
        created_by="test",
    )

    assert report.valid is True
    assert report.blocked is True
    assert report.committed is False
    assert report.mode == "shadow_db_import_blocked"
    assert report.relationship_type_counts["component_of"] == 1
    assert report.counts["non_operational_assertion_groups_blocked"] == 1
    assert "component_of" in report.blockers[0]
    assert db.query(StandardRelease).count() == 0
    assert db.query(MappingAssertionGroup).count() == 0


def test_canonical_mapping_db_import_blocks_strict_missing_installed_standard(tmp_path):
    _write_package(tmp_path)
    db = _session()

    report = import_canonical_mapping_package_to_db(
        package_dir=tmp_path,
        db=db,
        created_by="test",
        installed_standard_releases={("ESRS", "2024")},
    )

    assert report.valid is True
    assert report.blocked is True
    assert report.mode == "shadow_db_import_blocked"
    assert report.counts["mapping_assertion_groups_blocked_missing_datapoint"] == 1
    assert report.blockers
    assert db.query(StandardRelease).count() == 0


def test_canonical_mapping_db_import_blocks_unknown_relationship_commit(tmp_path):
    _write_package(tmp_path, relationship_type="custom_semantic_relation")
    db = _session()

    report = import_canonical_mapping_package_to_db(
        package_dir=tmp_path,
        db=db,
        created_by="test",
    )

    assert report.valid is True
    assert report.blocked is True
    assert report.relationship_type_counts["custom_semantic_relation"] == 1
    assert report.counts["non_operational_relationship_custom_semantic_relation"] == 1
    assert db.query(MappingAssertionGroup).count() == 0


def test_canonical_mapping_db_import_commits_idempotently_without_pairwise(tmp_path):
    _write_package(tmp_path)
    db = _session()

    first = import_canonical_mapping_package_to_db(
        package_dir=tmp_path,
        db=db,
        created_by="test",
    )
    second = import_canonical_mapping_package_to_db(
        package_dir=tmp_path,
        db=db,
        created_by="test",
    )

    assert first.committed is True
    assert second.valid is True
    assert second.counts["standard_releases_unchanged"] == 1
    assert second.counts["standard_datapoints_unchanged"] == 1
    assert second.counts["sygris_canonical_concepts_unchanged"] == 1
    assert second.counts["mapping_assertion_groups_unchanged"] == 1
    assert second.counts["mapping_assertion_components_unchanged"] == 1
    assert db.query(StandardRelease).count() == 1
    assert db.query(StandardDatapoint).count() == 1
    assert db.query(CanonicalConcept).count() == 1
    assert db.query(MappingAssertionGroup).count() == 1
    assert db.query(MappingAssertionComponent).count() == 1
    assert db.query(StandardMapping).count() == 0
    assert db.query(MaterializedPairwiseMapping).count() == 0
    assert first.materialized_pairwise_mappings_written == 0
    assert first.legacy_standard_mappings_written == 0


def test_canonical_mapping_db_import_retires_assertions_absent_from_reimport(
    tmp_path,
):
    _write_package(tmp_path, source_code="E1-6_07", assertion_hash="a" * 64)
    db = _session()

    import_canonical_mapping_package_to_db(
        package_dir=tmp_path,
        db=db,
        created_by="test",
    )
    _write_package(
        tmp_path,
        source_code="E1-6_08",
        assertion_hash="b" * 64,
        sygris_uri="syg:Scope1EmissionsFromRegulatedTradingSchemes",
    )
    second = import_canonical_mapping_package_to_db(
        package_dir=tmp_path,
        db=db,
        created_by="test",
    )

    old_group = (
        db.query(MappingAssertionGroup)
        .join(StandardDatapoint)
        .filter(StandardDatapoint.code == "E1-6_07")
        .one()
    )
    new_group = (
        db.query(MappingAssertionGroup)
        .join(StandardDatapoint)
        .filter(StandardDatapoint.code == "E1-6_08")
        .one()
    )

    assert second.counts["mapping_assertion_groups_retired_absent"] == 1
    assert old_group.valid_to is not None
    assert new_group.valid_to is None


def test_canonical_mapping_db_import_updates_existing_group_and_component(tmp_path):
    _write_package(tmp_path, assertion_hash="c" * 64)
    db = _session()

    first = import_canonical_mapping_package_to_db(
        package_dir=tmp_path,
        db=db,
        created_by="test",
    )
    assert first.committed is True

    groups_path = tmp_path / "sds_mapping_assertion_groups.csv"
    group_rows = list(csv.DictReader(groups_path.open(encoding="utf-8")))
    group_rows[0]["rationale"] = "Updated rationale."
    _write_csv(groups_path, group_rows)
    components_path = tmp_path / "sds_mapping_assertion_components.csv"
    component_rows = list(csv.DictReader(components_path.open(encoding="utf-8")))
    component_rows[0]["component_role"] = "supporting"
    _write_csv(components_path, component_rows)
    manifest = {
        "package_schema_version": "1.0",
        "files": [
            {"filename": filename, "sha256": _sha256_for(tmp_path / filename)}
            for filename in [
                "sds_standard_releases.csv",
                "sds_standard_datapoints.csv",
                "sds_mapping_assertion_groups.csv",
                "sds_mapping_assertion_components.csv",
            ]
        ],
    }
    (tmp_path / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (tmp_path / "MANIFEST.sha256").write_text(
        "\n".join(
            f"{_sha256_for(tmp_path / item['filename'])} *{item['filename']}"
            for item in manifest["files"]
        )
        + "\n",
        encoding="utf-8",
    )

    second = import_canonical_mapping_package_to_db(
        package_dir=tmp_path,
        db=db,
        created_by="test",
    )

    assert second.counts["mapping_assertion_groups_updated"] == 1
    assert second.counts["mapping_assertion_components_updated"] == 1
    assert db.query(MappingAssertionGroup).one().rationale == "Updated rationale."
    assert db.query(MappingAssertionComponent).one().component_role == "supporting"


def test_canonical_mapping_db_import_blank_assertion_hash_uses_profile_lookup(tmp_path):
    _write_package(tmp_path, assertion_hash="")
    db = _session()

    first = import_canonical_mapping_package_to_db(
        package_dir=tmp_path,
        db=db,
        created_by="test",
    )
    second = import_canonical_mapping_package_to_db(
        package_dir=tmp_path,
        db=db,
        created_by="test",
    )

    assert first.committed is True
    assert second.counts["mapping_assertion_groups_unchanged"] == 1
    assert db.query(MappingAssertionGroup).count() == 1


def test_canonical_mapping_db_import_helpers_cover_metadata_and_retirement_edges():
    assert _parse_json_object(None) is None
    try:
        _parse_json_object("[]")
    except ValueError as exc:
        assert "metadata fields must be objects" in str(exc)
    else:
        raise AssertionError("non-object JSON metadata must be rejected")

    db = _session()
    dummy_report = type(
        "DummyReport",
        (),
        {"counts": {}, "blockers": [], "relationship_type_counts": {}},
    )()

    _retire_absent_assertion_groups(
        db,
        [],
        release_by_key={},
        report=dummy_report,
        created_by="test",
    )
    assert dummy_report.counts == {}
