"""Tests for the canonical mapping shadow workflow orchestrator."""

from __future__ import annotations

import csv
import hashlib
import json
from decimal import Decimal
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
from src.services.canonical_mapping_shadow_workflow import (
    run_canonical_mapping_shadow_workflow,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


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


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _sha256_for(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_two_standard_package(
    package_dir: Path, *, relationship_type: str = "equivalent"
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
                relationship_type=relationship_type,
            ),
            _assertion_group_row(
                standard_id="GRI",
                version="2021",
                code="GRI-A",
                assertion_hash="g" * 64,
                relationship_type=relationship_type,
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


def _assertion_group_row(
    *,
    standard_id: str,
    version: str,
    code: str,
    assertion_hash: str,
    relationship_type: str = "equivalent",
) -> dict[str, str]:
    return {
        "source_standard_id": standard_id,
        "source_standard_version": version,
        "source_code": code,
        "mapping_profile": "default",
        "relationship_type": relationship_type,
        "coverage_status": "complete",
        "confidence": "1.0",
        "rationale": "Same Sygris canonical pivot.",
        "coverage_summary": "Full coverage.",
        "difference_summary": "",
        "valid_from": "2026-05-08T00:00:00Z",
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


def test_canonical_mapping_shadow_workflow_runs_without_legacy_cutover(tmp_path):
    _write_two_standard_package(tmp_path)
    db = _session()
    db.add(
        StandardMapping(
            source_standard="ESRS",
            source_code="ESRS-A",
            source_label="ESRS Scope A",
            target_standard="GRI",
            target_code="GRI-A",
            target_label="GRI Scope A",
            relationship_type="equivalent",
            confidence=Decimal("1.00"),
            dataset="legacy-fixture",
            is_active=True,
        )
    )
    db.commit()

    report = run_canonical_mapping_shadow_workflow(
        db=db,
        package_dir=tmp_path,
        created_by="test",
        source_standard="ESRS",
        target_standard="GRI",
    )
    payload = report.as_dict()

    assert report.passed is True
    assert payload["status"] == "passed"
    assert payload["import"]["committed"] is True
    assert payload["materialization"]["candidate_count"] == 2
    assert payload["parity"]["legacy_count"] == 1
    assert payload["parity"]["canonical_count"] == 1
    assert payload["parity"]["matched_count"] == 1
    assert db.query(StandardMapping).count() == 1
    assert db.query(MaterializedPairwiseMapping).count() == 2


def test_canonical_mapping_shadow_workflow_stops_on_non_operational_package(tmp_path):
    _write_two_standard_package(tmp_path, relationship_type="partial_overlap")
    db = _session()

    report = run_canonical_mapping_shadow_workflow(
        db=db,
        package_dir=tmp_path,
        created_by="test",
        source_standard="ESRS",
        target_standard="GRI",
    )
    payload = report.as_dict()

    assert report.passed is False
    assert payload["status"] == "blocked_non_operational_package"
    assert payload["import"]["valid"] is True
    assert payload["import"]["blocked"] is True
    assert payload["materialization"] is None
    assert payload["parity"] is None
    assert db.query(MappingAssertionGroup).count() == 0
    assert db.query(MaterializedPairwiseMapping).count() == 0


def test_canonical_mapping_shadow_workflow_blocks_existing_non_operational_groups(
    tmp_path,
):
    db = _session()
    release_esrs = StandardRelease(
        standard_id="ESRS",
        name="European Sustainability Reporting Standards",
        version="2024",
    )
    release_gri = StandardRelease(
        standard_id="GRI",
        name="GRI Standards",
        version="2021",
    )
    db.add_all([release_esrs, release_gri])
    db.flush()
    datapoint_esrs = StandardDatapoint(
        standard_release=release_esrs,
        code="ESRS-A",
        label="ESRS Scope A",
    )
    datapoint_gri = StandardDatapoint(
        standard_release=release_gri,
        code="GRI-A",
        label="GRI Scope A",
    )
    concept = CanonicalConcept(
        canonical_uri="syg:ScopeA",
        revision=1,
        label="Scope A",
        taxonomy="Sygris",
        concept_type="mapping_pivot",
    )
    db.add_all([datapoint_esrs, datapoint_gri, concept])
    db.flush()
    group_esrs = MappingAssertionGroup(
        source_datapoint=datapoint_esrs,
        mapping_profile="default",
        relationship_type="partial_overlap",
        coverage_status="complete",
        confidence=Decimal("1.00"),
        approval_status="approved",
        publication_status="internal",
        assertion_hash="e" * 64,
    )
    group_gri = MappingAssertionGroup(
        source_datapoint=datapoint_gri,
        mapping_profile="default",
        relationship_type="equivalent",
        coverage_status="complete",
        confidence=Decimal("1.00"),
        approval_status="approved",
        publication_status="internal",
        assertion_hash="g" * 64,
    )
    db.add_all([group_esrs, group_gri])
    db.flush()
    db.add_all(
        [
            MappingAssertionComponent(
                assertion_group=group_esrs,
                canonical_concept=concept,
                component_order=1,
            ),
            MappingAssertionComponent(
                assertion_group=group_gri,
                canonical_concept=concept,
                component_order=1,
            ),
        ]
    )
    db.commit()

    report = run_canonical_mapping_shadow_workflow(
        db=db,
        package_dir=None,
        skip_import=True,
        created_by="test",
        source_standard="ESRS",
        target_standard="GRI",
    )
    payload = report.as_dict()

    assert payload["status"] == "blocked_non_operational_materialization"
    assert payload["materialization"]["blocked"] is True
    assert payload["parity"] is None
    assert db.query(MaterializedPairwiseMapping).count() == 0
