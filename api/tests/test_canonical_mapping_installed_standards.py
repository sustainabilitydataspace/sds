"""Installed-standard guards for canonical mapping package activation."""

from __future__ import annotations

import csv
import hashlib
import json
from contextlib import nullcontext
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from src.api.main import app
from src.config.settings import settings
from src.database.models import (
    CanonicalConcept,
    MappingAssertionComponent,
    MappingAssertionGroup,
    MaterializedPairwiseMapping,
    StandardDatapoint,
    StandardMapping,
    StandardRelease,
)
from src.database.session import get_db_optional
from src.services import canonical_mapping_installed_standards as cms
from src.services.canonical_mapping_db_import import (
    import_canonical_mapping_package_to_db,
)
from src.services.canonical_mapping_import import (
    CanonicalMappingImportReport,
    CanonicalMappingPackageRows,
)
from src.services.canonical_mapping_installed_standards import (
    CanonicalMappingInstalledStandardIssue,
    CanonicalMappingInstalledStandardReport,
    active_standard_release_keys_from_db,
    filter_rows_for_installed_standard_compatibility,
    validate_canonical_mapping_installed_standard_compatibility,
)
from src.services.canonical_pairwise_materialization import (
    materialize_pairwise_mappings,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _sha256_for(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _assertion_group_row(
    *,
    standard_id: str,
    version: str,
    code: str,
    assertion_hash: str,
) -> dict[str, str]:
    return {
        "source_standard_id": standard_id,
        "source_standard_version": version,
        "source_code": code,
        "mapping_profile": "default",
        "relationship_type": "equivalent",
        "coverage_status": "complete",
        "confidence": "1.0",
        "rationale": "Same Sygris canonical pivot.",
        "coverage_summary": "Full coverage.",
        "difference_summary": "",
        "valid_from": "2026-05-20T00:00:00Z",
        "valid_to": "",
        "approval_status": "approved",
        "publication_status": "internal",
        "assertion_hash": assertion_hash,
    }


def _component_row(*, standard_id: str, version: str, code: str) -> dict[str, str]:
    return {
        "source_standard_id": standard_id,
        "source_standard_version": version,
        "source_code": code,
        "mapping_profile": "default",
        "component_order": "1",
        "sygris_canonical_uri": "syg:ScopeA",
        "sygris_revision": "1",
        "component_role": "primary",
        "coverage_fraction": "1.0000",
        "match_scope": "Full concept.",
        "mismatch_scope": "",
        "transformation_rule": "",
        "rationale": "Same canonical concept.",
    }


def _write_two_standard_package(package_dir: Path) -> None:
    files = {
        "sds_standard_releases.csv": [
            {
                "standard_id": "ESRS",
                "name": "European Sustainability Reporting Standards",
                "version": "2024",
                "release_date": "2024-01-01",
                "source_url": "https://example.invalid/esrs",
                "lifecycle_status": "active",
            },
            {
                "standard_id": "GRI",
                "name": "GRI Standards",
                "version": "2021",
                "release_date": "2021-10-05",
                "source_url": "https://example.invalid/gri",
                "lifecycle_status": "active",
            },
        ],
        "sds_standard_datapoints.csv": [
            {
                "standard_id": "ESRS",
                "standard_version": "2024",
                "code": "ESRS-A",
                "label": "ESRS Scope A",
                "disclosure_text": "ESRS Scope A disclosure",
                "datapoint_type": "numeric",
                "unit": "tCO2e",
                "lifecycle_status": "active",
            },
            {
                "standard_id": "GRI",
                "standard_version": "2021",
                "code": "GRI-A",
                "label": "GRI Scope A",
                "disclosure_text": "GRI Scope A disclosure",
                "datapoint_type": "numeric",
                "unit": "tCO2e",
                "lifecycle_status": "active",
            },
        ],
        "sds_mapping_assertion_groups.csv": [
            _assertion_group_row(
                standard_id="ESRS",
                version="2024",
                code="ESRS-A",
                assertion_hash="e" * 64,
            ),
            _assertion_group_row(
                standard_id="GRI",
                version="2021",
                code="GRI-A",
                assertion_hash="g" * 64,
            ),
        ],
        "sds_mapping_assertion_components.csv": [
            _component_row(standard_id="ESRS", version="2024", code="ESRS-A"),
            _component_row(standard_id="GRI", version="2021", code="GRI-A"),
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


def _seed_installed_esrs(db):
    release = StandardRelease(
        standard_id="ESRS",
        name="European Sustainability Reporting Standards",
        version="2024",
        lifecycle_status="active",
    )
    db.add(release)
    db.flush()
    db.add(
        StandardDatapoint(
            standard_release=release,
            code="ESRS-A",
            label="ESRS Scope A",
            lifecycle_status="active",
        )
    )
    db.commit()


def _seed_two_live_assertions(db):
    esrs = StandardRelease(
        standard_id="ESRS",
        name="European Sustainability Reporting Standards",
        version="2024",
        lifecycle_status="active",
    )
    gri = StandardRelease(
        standard_id="GRI",
        name="GRI Standards",
        version="2021",
        lifecycle_status="active",
    )
    concept = CanonicalConcept(
        canonical_uri="syg:ScopeA",
        revision=1,
        label="Scope A",
        taxonomy="Sygris",
        concept_type="mapping_pivot",
    )
    db.add_all([esrs, gri, concept])
    db.flush()
    esrs_datapoint = StandardDatapoint(
        standard_release=esrs,
        code="ESRS-A",
        label="ESRS Scope A",
        lifecycle_status="active",
    )
    gri_datapoint = StandardDatapoint(
        standard_release=gri,
        code="GRI-A",
        label="GRI Scope A",
        lifecycle_status="active",
    )
    db.add_all([esrs_datapoint, gri_datapoint])
    db.flush()
    group_esrs = _group(esrs_datapoint, "e" * 64)
    group_gri = _group(gri_datapoint, "g" * 64)
    db.add_all([group_esrs, group_gri])
    db.flush()
    db.add_all(
        [
            MappingAssertionComponent(
                assertion_group=group_esrs,
                canonical_concept=concept,
                component_order=1,
                coverage_fraction=Decimal("1.0000"),
            ),
            MappingAssertionComponent(
                assertion_group=group_gri,
                canonical_concept=concept,
                component_order=1,
                coverage_fraction=Decimal("1.0000"),
            ),
        ]
    )
    db.commit()


def _group(datapoint: StandardDatapoint, assertion_hash: str) -> MappingAssertionGroup:
    return MappingAssertionGroup(
        source_datapoint=datapoint,
        mapping_profile="default",
        relationship_type="equivalent",
        coverage_status="complete",
        confidence=Decimal("1.00"),
        valid_from=datetime(2026, 5, 20),
        approval_status="approved",
        publication_status="internal",
        assertion_hash=assertion_hash,
    )


def test_installed_standard_validation_marks_missing_gri_pending(tmp_path):
    _write_two_standard_package(tmp_path)
    db = _session()
    _seed_installed_esrs(db)

    report = validate_canonical_mapping_installed_standard_compatibility(
        package_dir=tmp_path,
        db=db,
        installed_standard_releases=active_standard_release_keys_from_db(db),
        compatibility_mode="partial",
    )

    payload = report.as_dict()
    assert report.valid is True
    assert report.operational_eligible is True
    assert payload["status_counts"] == {
        "installable_now": 1,
        "pending_missing_standard": 1,
    }
    assert payload["importable_assertion_group_count"] == 1
    assert payload["pending_assertion_group_count"] == 1
    assert payload["issues"][0]["code"] == "missing_standard_release"
    assert payload["issues"][0]["standard_id"] == "GRI"


def test_installed_standard_report_properties_and_invalid_mode(tmp_path):
    valid_base = CanonicalMappingImportReport(
        package_dir=str(tmp_path),
        package_schema_version="1.0",
        mode="shadow",
        valid=True,
        file_counts={},
        manifest_hash=None,
        checksum_count=0,
        operational_eligible=True,
    )
    invalid = CanonicalMappingInstalledStandardReport(
        package_dir=str(tmp_path),
        compatibility_mode="strict",
        validation=CanonicalMappingImportReport(
            package_dir=str(tmp_path),
            package_schema_version=None,
            mode="shadow",
            valid=False,
            file_counts={},
            manifest_hash=None,
            checksum_count=0,
        ),
        installed_standard_releases=(),
    )
    assert invalid.operational_eligible is False
    assert invalid.blockers == ["canonical mapping package validation failed"]

    blocked = CanonicalMappingInstalledStandardReport(
        package_dir=str(tmp_path),
        compatibility_mode="partial",
        validation=valid_base,
        installed_standard_releases=(),
        blocked_assertion_group_keys=(("ESRS", "2024", "E1-1", "default"),),
        issues=(
            CanonicalMappingInstalledStandardIssue(
                code="blocked_missing_datapoint",
                message="blocked row",
            ),
        ),
    )
    assert blocked.operational_eligible is False
    assert blocked.blockers == ["blocked row"]

    empty = CanonicalMappingInstalledStandardReport(
        package_dir=str(tmp_path),
        compatibility_mode="partial",
        validation=valid_base,
        installed_standard_releases=(),
    )
    assert empty.blockers == [
        "no assertion groups are compatible with installed standards"
    ]

    try:
        validate_canonical_mapping_installed_standard_compatibility(
            package_dir=tmp_path,
            installed_standard_releases=[],
            compatibility_mode="loose",
        )
    except ValueError as exc:
        assert "strict" in str(exc)
    else:
        raise AssertionError("invalid compatibility_mode should fail")


def test_installed_standard_validation_invalid_package_short_circuits(tmp_path):
    report = validate_canonical_mapping_installed_standard_compatibility(
        package_dir=tmp_path,
        installed_standard_releases=[],
        compatibility_mode="strict",
    )

    assert report.valid is False
    assert report.status_counts == {}
    assert report.importable_assertion_group_keys == ()


def test_installed_standard_missing_release_from_assertion_row(monkeypatch, tmp_path):
    validation = CanonicalMappingImportReport(
        package_dir=str(tmp_path),
        package_schema_version="1.0",
        mode="shadow",
        valid=True,
        file_counts={},
        manifest_hash=None,
        checksum_count=0,
        operational_eligible=True,
    )
    rows = CanonicalMappingPackageRows(
        releases=[],
        datapoints=[],
        assertion_groups=[
            {
                "source_standard_id": "GRI",
                "source_standard_version": "2021",
                "source_code": "GRI-A",
                "mapping_profile": "default",
            }
        ],
        assertion_components=[],
    )
    observed_package_dirs: list[Path] = []
    private_snapshot = tmp_path / "private-snapshot"

    def validate(package_dir):
        observed_package_dirs.append(package_dir)
        return validation

    def load(package_dir):
        observed_package_dirs.append(package_dir)
        return rows

    monkeypatch.setattr(cms, "validate_canonical_mapping_package", validate)
    monkeypatch.setattr(cms, "load_canonical_mapping_package_rows", load)
    monkeypatch.setattr(
        cms,
        "canonical_mapping_package_snapshot",
        lambda _package_dir: nullcontext(private_snapshot),
    )

    report = validate_canonical_mapping_installed_standard_compatibility(
        package_dir=tmp_path,
        installed_standard_releases=[],
        compatibility_mode="partial",
        require_installed_datapoints=False,
    )

    assert report.pending_assertion_group_count == 1
    assert report.issues[0].source_code == "GRI-A"
    assert observed_package_dirs[0] == observed_package_dirs[1]
    assert observed_package_dirs[0] == private_snapshot


def test_installed_standard_validation_rejects_symlink_package_dir(tmp_path):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    _write_two_standard_package(package_dir)
    package_link = tmp_path / "package-link"
    package_link.symlink_to(package_dir, target_is_directory=True)

    report = validate_canonical_mapping_installed_standard_compatibility(
        package_dir=package_link,
        installed_standard_releases={("ESRS", "2024"), ("GRI", "2021")},
        compatibility_mode="strict",
        require_installed_datapoints=False,
    )

    assert report.valid is False


def test_filter_rows_for_installed_standard_compatibility_keeps_only_importable(
    tmp_path,
):
    validation = CanonicalMappingImportReport(
        package_dir=str(tmp_path),
        package_schema_version="1.0",
        mode="shadow",
        valid=True,
        file_counts={},
        manifest_hash=None,
        checksum_count=0,
        operational_eligible=True,
    )
    report = CanonicalMappingInstalledStandardReport(
        package_dir=str(tmp_path),
        compatibility_mode="partial",
        validation=validation,
        installed_standard_releases=(("ESRS", "2024"),),
        importable_assertion_group_keys=(("ESRS", "2024", "E1-1", "default"),),
    )
    rows = CanonicalMappingPackageRows(
        releases=[
            {"standard_id": "ESRS", "version": "2024"},
            {"standard_id": "GRI", "version": "2021"},
        ],
        datapoints=[
            {"standard_id": "ESRS", "standard_version": "2024", "code": "E1-1"},
            {"standard_id": "GRI", "standard_version": "2021", "code": "GRI-A"},
        ],
        assertion_groups=[
            {
                "source_standard_id": "ESRS",
                "source_standard_version": "2024",
                "source_code": "E1-1",
                "mapping_profile": "default",
            },
            {
                "source_standard_id": "GRI",
                "source_standard_version": "2021",
                "source_code": "GRI-A",
                "mapping_profile": "default",
            },
        ],
        assertion_components=[
            {
                "source_standard_id": "ESRS",
                "source_standard_version": "2024",
                "source_code": "E1-1",
                "mapping_profile": "default",
            },
            {
                "source_standard_id": "GRI",
                "source_standard_version": "2021",
                "source_code": "GRI-A",
                "mapping_profile": "default",
            },
        ],
    )

    filtered = filter_rows_for_installed_standard_compatibility(rows, report)

    assert filtered.releases == [{"standard_id": "ESRS", "version": "2024"}]
    assert filtered.datapoints[0]["code"] == "E1-1"
    assert filtered.assertion_groups[0]["source_code"] == "E1-1"
    assert filtered.assertion_components[0]["source_code"] == "E1-1"


def test_partial_db_import_skips_missing_standard_rows_without_legacy_writes(tmp_path):
    _write_two_standard_package(tmp_path)
    db = _session()
    _seed_installed_esrs(db)

    report = import_canonical_mapping_package_to_db(
        package_dir=tmp_path,
        db=db,
        created_by="test",
        installed_standard_releases=active_standard_release_keys_from_db(db),
        allow_partial_installed_standards=True,
    )

    assert report.valid is True
    assert report.committed is True
    assert report.compatibility["status_counts"] == {
        "installable_now": 1,
        "pending_missing_standard": 1,
    }
    assert report.counts["mapping_assertion_groups_created"] == 1
    assert report.counts["mapping_assertion_groups_pending_missing_standard"] == 1
    assert (
        db.query(StandardRelease).filter(StandardRelease.standard_id == "GRI").count()
        == 0
    )
    assert db.query(MappingAssertionGroup).count() == 1
    assert db.query(MaterializedPairwiseMapping).count() == 0
    assert db.query(StandardMapping).count() == 0


def test_materialization_respects_installed_standard_filter():
    db = _session()
    _seed_two_live_assertions(db)

    esrs_only = materialize_pairwise_mappings(
        db=db,
        installed_standard_releases={("ESRS", "2024")},
    )

    assert esrs_only.candidate_count == 0
    assert esrs_only.counts["assertion_groups_skipped_missing_installed_standard"] == 1
    assert db.query(MaterializedPairwiseMapping).count() == 0

    both = materialize_pairwise_mappings(
        db=db,
        installed_standard_releases={("ESRS", "2024"), ("GRI", "2021")},
    )

    assert both.candidate_count == 2
    assert both.counts["pairwise_created"] == 2
    assert db.query(MaterializedPairwiseMapping).count() == 2

    esrs_only_after_full = materialize_pairwise_mappings(
        db=db,
        installed_standard_releases={("ESRS", "2024")},
    )

    assert esrs_only_after_full.candidate_count == 0
    assert "pairwise_staled" not in esrs_only_after_full.counts
    assert (
        db.query(MaterializedPairwiseMapping)
        .filter(MaterializedPairwiseMapping.is_current.is_(True))
        .count()
        == 2
    )


def test_internal_mapping_package_validation_endpoint_reports_installed_slice(
    client,
    admin_token,
    monkeypatch,
    tmp_path,
):
    package_root = tmp_path / "packages"
    package_id = "esrs-gri"
    _write_two_standard_package(package_root / package_id)
    monkeypatch.setattr(
        settings, "canonical_mapping_inspection_roots", [str(package_root)]
    )

    response = client.post(
        f"/api/v1/internal/canonical-mapping-packages/{package_id}/validations",
        headers=_auth(admin_token),
        json={
            "compatibility_mode": "partial",
            "installed_standard_releases": [{"standard_id": "ESRS", "version": "2024"}],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["job_type"] == "validation"
    assert payload["status"] == "completed"
    compatibility = payload["result_body"]["compatibility"]
    assert compatibility["status_counts"] == {
        "installable_now": 1,
        "pending_missing_standard": 1,
    }
    assert compatibility["issues"][0]["code"] == "missing_standard_release"


def test_internal_mapping_package_import_endpoint_commits_installed_slice(
    client,
    admin_token,
    monkeypatch,
    tmp_path,
):
    package_root = tmp_path / "packages"
    package_id = "esrs-gri"
    _write_two_standard_package(package_root / package_id)
    monkeypatch.setattr(
        settings, "canonical_mapping_inspection_roots", [str(package_root)]
    )
    db = _session()
    _seed_installed_esrs(db)

    app.dependency_overrides[get_db_optional] = lambda: db
    try:
        response = client.post(
            f"/api/v1/internal/canonical-mapping-packages/{package_id}/import-jobs",
            headers=_auth(admin_token),
            json={
                "compatibility_mode": "partial",
                "installed_standard_releases": [
                    {"standard_id": "ESRS", "version": "2024"}
                ],
            },
        )
    finally:
        app.dependency_overrides.pop(get_db_optional, None)

    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["job_type"] == "import"
    assert payload["status"] == "completed"
    assert payload["committed"] is True
    compatibility = payload["result_body"]["compatibility"]
    assert compatibility["status_counts"] == {
        "installable_now": 1,
        "pending_missing_standard": 1,
    }
    assert (
        db.query(StandardRelease).filter(StandardRelease.standard_id == "GRI").count()
        == 0
    )
    assert db.query(MappingAssertionGroup).count() == 1
    assert db.query(MaterializedPairwiseMapping).count() == 0
    assert db.query(StandardMapping).count() == 0
