from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from src.database.models import (
    CanonicalConcept,
    CanonicalConceptIndicatorLink,
    Concept,
    ConceptIndicatorLink,
    ConceptState,
    Indicator,
    StandardDatapoint,
    StandardRelease,
)
from src.services.semantic_concept_projector import SemanticConceptProjector

PROJECT_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "api"
    / "scripts"
    / "project_semantic_catalog.py"
)


def test_csrd_parent_code_rejects_long_adversarial_source_ref_in_bounded_time():
    from src.services.semantic_concept_projector import _csrd_parent_code

    source_ref = "ABCD" + "-A" * 10_000 + ":Q"
    started = time.perf_counter()
    result = _csrd_parent_code(source_ref=source_ref, code_esrs="")
    elapsed = time.perf_counter() - started

    assert result is None
    assert elapsed < 0.25


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


def _load_project_script():
    spec = importlib.util.spec_from_file_location(
        "project_semantic_catalog_script", PROJECT_SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _create_projector_tables(engine):
    for table in (
        Indicator.__table__,
        StandardRelease.__table__,
        StandardDatapoint.__table__,
        CanonicalConcept.__table__,
        Concept.__table__,
        CanonicalConceptIndicatorLink.__table__,
        ConceptIndicatorLink.__table__,
    ):
        table.create(engine)


def _session():
    engine = create_engine("sqlite:///:memory:")
    _create_projector_tables(engine)
    return sessionmaker(bind=engine)()


def _indicator(**overrides):
    payload = {
        "id": "urn:sds:reg:esrs:e1_6_01",
        "identifier": "urn:sds:reg:esrs:e1_6_01",
        "title": "Gross Scope 1 greenhouse gas emissions",
        "indicator_name": "Gross Scope 1 greenhouse gas emissions",
        "description": "Disclosure datapoint as defined in IG3.",
        "dimension": "E",
        "unit_name": "tCO2e",
        "unit_type": "Emissions",
        "periodicity": "annual",
        "period_type": "fiscal_year",
        "source_ref": "ESRS Official",
        "code_esrs": "E1-6_01",
        "concept_state": ConceptState.SEMANTICALLY_MODELLED,
        "is_active": True,
    }
    payload.update(overrides)
    return Indicator(**payload)


def _release(**overrides):
    payload = {
        "standard_id": "ESRS",
        "name": "ESRS Set 1",
        "version": "2023-12-22",
        "lifecycle_status": "active",
    }
    payload.update(overrides)
    return StandardRelease(**payload)


def _datapoint(**overrides):
    payload = {
        "code": "E5-5_AR 35",
        "label": "Anticipated financial effects from waste-related impacts",
        "disclosure_text": "Waste-related disclosure datapoint.",
        "datapoint_type": "disclosure",
        "unit": None,
        "lifecycle_status": "active",
        "metadata_json": {},
    }
    payload.update(overrides)
    return StandardDatapoint(**payload)


def test_projector_materializes_active_indicators_as_canonical_and_read_model():
    db = _session()
    db.add(_indicator())
    db.commit()

    result = SemanticConceptProjector(db).project(commit=False)

    assert result.active_indicators == 1
    assert result.canonical_created == 1
    assert result.concepts_created == 1
    assert result.links_created == 2

    canonical = db.query(CanonicalConcept).one()
    assert canonical.canonical_uri == "urn:sds:reg:esrs:e1_6_01"
    assert canonical.revision == 1
    assert canonical.indicator_id == "urn:sds:reg:esrs:e1_6_01"
    assert canonical.taxonomy == "CSRD"
    assert canonical.concept_type == "Indicator"
    assert canonical.projection_source == "indicator_catalog"
    assert canonical.projection_hash
    assert canonical.projection_metadata["code_esrs"] == "E1-6_01"
    assert canonical.projection_metadata["source_family"] == "CSRD"

    concept = db.query(Concept).filter_by(uri="urn:sds:reg:esrs:e1_6_01").one()
    assert concept.uri == "urn:sds:reg:esrs:e1_6_01"
    assert concept.indicator_id == "urn:sds:reg:esrs:e1_6_01"
    assert concept.label == "Gross Scope 1 greenhouse gas emissions"
    assert concept.taxonomy == "CSRD"
    assert concept.concept_type == "Indicator"
    assert concept.projection_source == "indicator_catalog"
    assert concept.projection_hash == canonical.projection_hash

    assert db.query(CanonicalConceptIndicatorLink).count() == 1
    assert db.query(ConceptIndicatorLink).count() == 1
    assert result.disclosure_catalog_created == 1


def test_projector_materializes_gri_standard_datapoints_as_indicators():
    db = _session()
    db.add(
        _indicator(
            id="urn:sds:reg:gri:gri_305_1_a",
            identifier="urn:sds:reg:gri:gri_305_1_a",
            title="Direct Scope 1 GHG emissions",
            indicator_name="Direct Scope 1 GHG emissions",
            description="Disclosure datapoint as defined in GRI masterfile.",
            source_ref="GRI 305: Emissions 2016 GRI_305_1:305-1-a",
            code_esrs="",
            code_gri="305-1.a",
        )
    )
    db.commit()

    SemanticConceptProjector(db).project(commit=False)

    concept = db.query(Concept).filter_by(uri="urn:sds:reg:gri:gri_305_1_a").one()
    assert concept.taxonomy == "GRI"
    assert concept.concept_type == "Indicator"
    assert concept.projection_metadata["source_family"] == "GRI"


def test_projector_materializes_ghg_protocol_datapoints_under_ghg_taxonomy():
    db = _session()
    db.add(
        _indicator(
            id="urn:sds:reg:ghg:scope3_category1_supplier_emissions",
            identifier="urn:sds:reg:ghg:scope3_category1_supplier_emissions",
            title="Scope 3 Category 1 supplier emissions",
            indicator_name="Scope 3 Category 1 supplier emissions",
            description="Supplier-specific purchased goods emissions.",
            source_ref=(
                "GHG Protocol Technical Guidance for Calculating Scope 3 "
                "Emissions 2013 GHG_SCOPE3_CATEGORY_01_CALCULATION"
            ),
            code_esrs="",
            unit_name="tCO2e",
        )
    )
    db.commit()

    SemanticConceptProjector(db).project(commit=False)

    concept = (
        db.query(Concept)
        .filter_by(uri="urn:sds:reg:ghg:scope3_category1_supplier_emissions")
        .one()
    )
    assert concept.taxonomy == "GHG"
    assert concept.concept_type == "Indicator"
    assert concept.projection_metadata["source_family"] == "GHG"


def test_projector_materializes_unlinked_standard_datapoints_as_source_catalog():
    db = _session()
    esrs = _release()
    gri = _release(
        standard_id="GRI",
        name="GRI Standards",
        version="2025-06-23",
    )
    ghg = _release(
        standard_id="GHG",
        name="GHG Protocol Scope 2 Guidance",
        version="2015",
    )
    future = _release(
        standard_id="ISSB",
        name="ISSB future catalog",
        version="future",
    )
    inactive = _release(
        standard_id="ESRS",
        name="Inactive ESRS",
        version="inactive",
        lifecycle_status="retired",
    )
    esrs.datapoints.append(_datapoint())
    esrs.datapoints.append(
        _datapoint(
            code="E1-6_01",
            label="Linked SDS datapoint",
            metadata_json={"sds_identifier": "urn:sds:reg:esrs:e1_6_01"},
        )
    )
    gri.datapoints.append(
        _datapoint(
            code="305-1.a",
            label="Direct Scope 1 GHG emissions",
            disclosure_text="GRI disclosure datapoint.",
        )
    )
    ghg.datapoints.append(
        _datapoint(
            code="GHG_SCOPE2_MARKET_BASED",
            label="Scope 2 market-based emissions",
            disclosure_text="GHG Protocol metric.",
            datapoint_type="metric",
            unit="tCO2e",
        )
    )
    future.datapoints.append(_datapoint(code="ISSB-FUTURE-1", label="Future row"))
    inactive.datapoints.append(_datapoint(code="E1-6_INACTIVE", label="Inactive row"))
    db.add_all([esrs, gri, ghg, future, inactive])
    db.commit()

    result = SemanticConceptProjector(db).project(commit=False)

    rows = (
        db.query(Concept)
        .filter(Concept.projection_source == "standard_datapoint_catalog")
        .order_by(Concept.taxonomy.asc(), Concept.label.asc())
        .all()
    )
    assert result.source_datapoints_considered == 5
    assert result.source_datapoints_projected == 3
    assert [(row.taxonomy, row.concept_type, row.label) for row in rows] == [
        (
            "CSRD",
            "Disclosure",
            "Anticipated financial effects from waste-related impacts",
        ),
        ("GHG", "Indicator", "Scope 2 market-based emissions"),
        ("GRI", "Disclosure", "Direct Scope 1 GHG emissions"),
    ]
    assert all(row.indicator_id is None for row in rows)
    assert rows[0].projection_metadata["has_sds_identifier"] is False


def test_projector_derives_parent_disclosure_catalog_from_standard_datapoints():
    db = _session()
    db.add_all(
        [
            _indicator(
                id="urn:sds:reg:esrs:e1_6_01",
                identifier="urn:sds:reg:esrs:e1_6_01",
                title="Gross Scope 1 greenhouse gas emissions",
                code_esrs="E1-6_01",
                source_ref="ESRS Set 1 OJ 2023-12-22 E1-6:P44",
            ),
            _indicator(
                id="urn:sds:reg:esrs:e1_6_02",
                identifier="urn:sds:reg:esrs:e1_6_02",
                title="Gross Scope 2 greenhouse gas emissions",
                code_esrs="E1-6_02",
                source_ref="ESRS Set 1 OJ 2023-12-22 E1-6:P48",
            ),
            _indicator(
                id="urn:sds:reg:gri:gri_305_1_a",
                identifier="urn:sds:reg:gri:gri_305_1_a",
                title="Direct Scope 1 GHG emissions",
                code_esrs="",
                code_gri="305-1.a",
                source_ref="GRI 305: Emissions 2016 GRI_305_1:305-1-a",
            ),
        ]
    )
    db.commit()

    SemanticConceptProjector(db).project(commit=False)

    parents = (
        db.query(Concept)
        .filter(Concept.projection_source == "disclosure_catalog")
        .order_by(Concept.uri.asc())
        .all()
    )
    assert [
        (parent.taxonomy, parent.uri, parent.concept_type) for parent in parents
    ] == [
        ("CSRD", "urn:sds:disclosure:csrd:e1-6", "Disclosure"),
        ("GRI", "urn:sds:disclosure:gri:305-1", "Disclosure"),
    ]
    assert parents[0].projection_metadata["child_concept_count"] == 2
    assert parents[1].projection_metadata["child_concept_count"] == 1


def test_projector_materializes_operational_fallback_indicators_as_sygris():
    db = _session()
    db.add(
        _indicator(
            id="urn:sds:reg:sygris:site-energy",
            identifier="urn:sds:reg:sygris:site-energy",
            title="Site energy",
            indicator_name="Site energy",
            source_ref="Sygris operational catalog",
            code_esrs="",
        )
    )
    db.commit()

    result = SemanticConceptProjector(db).project(commit=False)

    assert result.active_indicators == 1
    canonical = db.query(CanonicalConcept).one()
    concept = db.query(Concept).one()
    assert canonical.taxonomy == "Sygris"
    assert canonical.concept_type == "Indicator"
    assert concept.taxonomy == "Sygris"
    assert concept.concept_type == "Indicator"


def test_projector_is_idempotent_when_indicator_payload_is_unchanged():
    db = _session()
    db.add(_indicator())
    db.commit()

    first = SemanticConceptProjector(db).project(commit=False)
    second = SemanticConceptProjector(db).project(commit=False)

    assert first.canonical_created == 1
    assert second.canonical_unchanged == 1
    assert second.concepts_unchanged == 1
    assert db.query(CanonicalConcept).count() == 1
    assert (
        db.query(Concept).filter_by(projection_source="indicator_catalog").count() == 1
    )
    assert (
        db.query(Concept).filter_by(projection_source="disclosure_catalog").count() == 1
    )


def test_projector_revisions_canonical_concept_when_indicator_payload_changes():
    db = _session()
    db.add(_indicator())
    db.commit()
    SemanticConceptProjector(db).project(commit=False)

    db.query(Indicator).one().title = "Updated Scope 1 emissions"
    result = SemanticConceptProjector(db).project(commit=False)

    assert result.canonical_revised == 1
    assert result.concepts_updated == 1

    revisions = (
        db.query(CanonicalConcept).order_by(CanonicalConcept.revision.asc()).all()
    )
    assert [revision.revision for revision in revisions] == [1, 2]
    assert revisions[0].effective_to is not None
    assert revisions[0].superseded_by == revisions[1].id
    assert revisions[1].label == "Updated Scope 1 emissions"
    assert (
        db.query(Concept).filter_by(uri="urn:sds:reg:esrs:e1_6_01").one().label
        == "Updated Scope 1 emissions"
    )


def test_projector_does_not_supersede_non_projector_canonical_concept_with_same_uri():
    db = _session()
    indicator = _indicator()
    non_projector = CanonicalConcept(
        canonical_uri=indicator.identifier,
        revision=1,
        effective_from=datetime(2026, 1, 1),
        label="Sygris mapping pivot",
        taxonomy="SDS",
        concept_type="MappingPivot",
        concept_state=ConceptState.CATALOGUED,
        created_by="canonical_mapping_shadow_import",
    )
    db.add_all([indicator, non_projector])
    db.commit()

    result = SemanticConceptProjector(db).project(commit=False)

    assert result.canonical_created == 1
    concepts = (
        db.query(CanonicalConcept).order_by(CanonicalConcept.revision.asc()).all()
    )
    assert [concept.revision for concept in concepts] == [1, 2]

    non_projector = next(
        concept for concept in concepts if concept.projection_source is None
    )
    projector_owned = next(
        concept
        for concept in concepts
        if concept.projection_source == "indicator_catalog"
    )
    assert non_projector.effective_to is None
    assert non_projector.superseded_by is None
    assert projector_owned.revision == 2
    assert projector_owned.created_by == "semantic_concept_projector"

    read_model = db.query(Concept).filter_by(uri=indicator.identifier).one()
    assert read_model.projection_metadata["canonical_concept_id"] == projector_owned.id
    assert read_model.projection_metadata["canonical_revision"] == 2


def test_projection_coverage_reports_missing_active_indicators():
    db = _session()
    db.add(_indicator())
    db.add(
        Concept(
            uri="csrd:E3_5",
            label="Water consumption",
            taxonomy="CSRD",
            concept_type="Disclosure",
        )
    )
    db.commit()

    coverage = SemanticConceptProjector(db).coverage_summary()

    assert coverage["active_indicators"] == 1
    assert coverage["projected_active_indicators"] == 0
    assert coverage["missing_active_indicator_identifiers"] == [
        "urn:sds:reg:esrs:e1_6_01"
    ]
    assert coverage["stale_active_indicator_identifiers"] == []
    assert coverage["ready"] is False


def test_projection_coverage_detects_stale_projection():
    db = _session()
    db.add(_indicator())
    db.commit()

    SemanticConceptProjector(db).project(commit=False)
    coverage_before = SemanticConceptProjector(db).coverage_summary()
    assert coverage_before["ready"] is True
    assert coverage_before["stale_active_indicator_identifiers"] == []

    db.query(Indicator).one().title = "Updated Scope 1 emissions"
    db.commit()

    coverage_after = SemanticConceptProjector(db).coverage_summary()
    assert coverage_after["ready"] is False
    assert coverage_after["stale_active_indicator_identifiers"] == [
        "urn:sds:reg:esrs:e1_6_01"
    ]
    assert coverage_after["missing_active_indicator_identifiers"] == []


def test_projection_coverage_fails_when_disclosure_catalog_is_missing():
    db = _session()
    db.add(_indicator())
    db.commit()

    SemanticConceptProjector(db).project(commit=False)
    db.query(Concept).filter_by(projection_source="disclosure_catalog").delete()
    db.commit()

    coverage = SemanticConceptProjector(db).coverage_summary()

    assert coverage["ready"] is False
    assert coverage["active_disclosures"] == 1
    assert coverage["projected_disclosures"] == 0
    assert coverage["missing_disclosure_uris"] == ["urn:sds:disclosure:csrd:e1-6"]


def test_projection_coverage_fails_when_projected_catalog_rows_are_extra():
    db = _session()
    release = _release()
    release.datapoints.append(_datapoint())
    db.add_all([_indicator(), release])
    db.commit()

    SemanticConceptProjector(db).project(commit=False)
    db.add(
        Concept(
            uri="urn:sds:standard-datapoint:csrd:esrs:old:obsolete",
            label="Obsolete source datapoint",
            taxonomy="CSRD",
            concept_type="Disclosure",
            concept_state=ConceptState.CATALOGUED,
            projection_source="standard_datapoint_catalog",
            projection_hash="obsolete",
            projection_version="standard-datapoint-catalog-v1",
        )
    )
    db.commit()

    coverage = SemanticConceptProjector(db).coverage_summary()

    assert coverage["ready"] is False
    assert coverage["extra_source_datapoint_uris"] == [
        "urn:sds:standard-datapoint:csrd:esrs:old:obsolete"
    ]


def test_projection_coverage_fails_when_uncontrolled_legacy_disclosure_exists():
    db = _session()
    db.add(_indicator())
    db.commit()

    SemanticConceptProjector(db).project(commit=False)
    db.add(
        Concept(
            uri="csrd:UNCONTROLLED_DISCLOSURE",
            label="Uncontrolled disclosure",
            taxonomy="CSRD",
            concept_type="Disclosure",
            concept_state=ConceptState.CATALOGUED,
            projection_source=None,
        )
    )
    db.commit()

    coverage = SemanticConceptProjector(db).coverage_summary()

    assert coverage["ready"] is False
    assert coverage["unsupported_legacy_disclosure_uris"] == [
        "csrd:UNCONTROLLED_DISCLOSURE"
    ]


def test_projection_coverage_fails_when_null_source_legacy_disclosure_exists():
    db = _session()
    db.add(_indicator())
    db.commit()

    SemanticConceptProjector(db).project(commit=False)
    db.add(
        Concept(
            uri="https://data.efrag.org/esrs#E3_5",
            label="Legacy disclosure support row",
            taxonomy="CSRD",
            concept_type="Disclosure",
            concept_state=ConceptState.CATALOGUED,
            projection_source=None,
        )
    )
    db.commit()

    coverage = SemanticConceptProjector(db).coverage_summary()

    assert coverage["ready"] is False
    assert coverage["unsupported_legacy_disclosure_uris"] == [
        "https://data.efrag.org/esrs#E3_5"
    ]


def test_projection_coverage_distinguishes_future_from_unknown_active_standards():
    db = _session()
    future = _release(standard_id="ISSB", name="ISSB future", version="future")
    future.datapoints.append(_datapoint(code="ISSB-FUTURE"))
    unknown = _release(
        standard_id="CUSTOM_UNKNOWN",
        name="Unexpected active catalog",
        version="current",
    )
    unknown.datapoints.append(_datapoint(code="UNKNOWN-1"))
    db.add_all([_indicator(), future, unknown])
    db.commit()

    SemanticConceptProjector(db).project(commit=False)
    coverage = SemanticConceptProjector(db).coverage_summary()

    assert coverage["future_source_standards"] == ["ISSB"]
    assert coverage["unsupported_active_standard_ids"] == ["CUSTOM_UNKNOWN"]
    assert coverage["ready"] is False


def test_project_script_help_works_without_jwt_secret_key():
    env = os.environ.copy()
    env.pop("JWT_SECRET_KEY", None)
    result = subprocess.run(
        [sys.executable, str(PROJECT_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()


@pytest.mark.parametrize("stale", [False, True], ids=["missing", "stale"])
def test_project_script_dry_run_reports_would_be_coverage_before_rollback(
    tmp_path, monkeypatch, stale
):
    db_path = tmp_path / "semantic-projector.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(db_url)
    _create_projector_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    indicator = _indicator()
    session.add(indicator)
    session.commit()
    if stale:
        SemanticConceptProjector(session).project()
        indicator.title = "Updated catalog title"
        indicator.indicator_name = "Updated catalog title"
        session.commit()
    assert SemanticConceptProjector(session).coverage_summary()["ready"] is False
    projection_tables = (
        CanonicalConcept.__table__,
        Concept.__table__,
        CanonicalConceptIndicatorLink.__table__,
        ConceptIndicatorLink.__table__,
    )
    before = {
        table.name: session.execute(table.select().order_by(*table.primary_key)).all()
        for table in projection_tables
    }
    session.close()
    engine.dispose()
    # Isolate projector rollback: the real initializer can independently commit
    # migrations, so this regression does not claim the whole CLI is read-only.
    monkeypatch.setattr("src.database.init_db.init_db_for_engine", lambda _engine: None)

    report = _load_project_script().run_projection(
        db_url=db_url,
        report_path=tmp_path / "projection-report.json",
        dry_run=True,
    )

    assert report["result"]["concepts_created"] == (0 if stale else 1)
    assert report["result"]["concepts_updated"] == (1 if stale else 0)
    assert report["coverage"]["ready"] is True
    assert report["coverage"]["projected_active_indicators"] == 1

    check_engine = create_engine(db_url)
    CheckSession = sessionmaker(bind=check_engine)
    check_session = CheckSession()
    try:
        after = {
            table.name: check_session.execute(
                table.select().order_by(*table.primary_key)
            ).all()
            for table in projection_tables
        }
        assert after == before
        assert (
            SemanticConceptProjector(check_session).coverage_summary()["ready"] is False
        )
    finally:
        check_session.close()
        check_engine.dispose()


def test_project_script_does_not_import_settings_before_argparse():
    script = PROJECT_SCRIPT.read_text(encoding="utf-8")
    module_prefix = script.split("def _get_default_database_url", 1)[0]
    main_body_before_parse = script.split("args = parser.parse_args()", 1)[0]

    assert "from src.config.settings import settings" not in module_prefix
    assert "default=_get_default_database_url()" not in main_body_before_parse
