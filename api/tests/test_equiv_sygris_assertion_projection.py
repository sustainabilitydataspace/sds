"""PROBLEMA 2 (EQUIV-SYGRIS) contract tests — external->Sygris assertion projection.

Disposable-PG tests proving that ``GET /api/v1/equivalences`` (via the service) now surfaces the
curated external->Sygris mapping-assertion footprint (mapping_assertion_groups + components ->
canonical_concepts), merged with the legacy concept_equivalences, with the right identity,
relationship bucketing, public gating, dedup, and filters. Gated on the disposable PG env vars.

Covers codex P2 discovery findings: M1 (full public gate incl lifecycle + canonical revisioning),
M2 (relationship-alias normalization), M3 (metadata-preserving dedup across mapping_profile /
assertion_group / component), M4 (concept + taxonomy filter normalization).
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.database import models as m
from src.services.concept_service import ConceptService
from src.services.semantic_concept_projector import _source_datapoint_payload

SYG_TARGET = "syg:HazardousWasteDirectedToDisposalByDisposalOperation"


def _disposable_session() -> Session:
    database_url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    from src.database.init_db import init_db_for_engine

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    init_db_for_engine(engine)
    return Session(bind=engine)


def _seed_release_datapoint(
    db: Session,
    *,
    standard_id: str = "ESRS",
    version: str = "2024",
    code: str = "E5-5_09",
    release_status: str = "active",
    datapoint_status: str = "active",
) -> tuple[m.StandardRelease, m.StandardDatapoint]:
    release = m.StandardRelease(
        standard_id=standard_id,
        name=f"{standard_id} {version}",
        version=version,
        lifecycle_status=release_status,
    )
    db.add(release)
    db.flush()
    datapoint = m.StandardDatapoint(
        standard_release_id=release.id,
        code=code,
        label=f"{code} hazardous waste to disposal",
        lifecycle_status=datapoint_status,
    )
    db.add(datapoint)
    db.flush()
    return release, datapoint


def _seed_canonical(
    db: Session,
    *,
    canonical_uri: str = SYG_TARGET,
    effective_to=None,
    superseded_by=None,
) -> m.CanonicalConcept:
    canonical = m.CanonicalConcept(
        canonical_uri=canonical_uri,
        label="Hazardous waste directed to disposal",
        taxonomy="ESRS",
        concept_type="metric",
        effective_to=effective_to,
        superseded_by=superseded_by,
    )
    db.add(canonical)
    db.flush()
    return canonical


def _seed_assertion(
    db: Session,
    *,
    datapoint: m.StandardDatapoint,
    canonical: m.CanonicalConcept,
    relationship_type: str = "broader",
    coverage_status: str = "partial",
    mapping_profile: str = "default",
    approval_status: str = "approved",
    valid_to=None,
    superseded_by=None,
    component_role: str = "primary",
    coverage_fraction: float = 0.5,
) -> m.MappingAssertionGroup:
    group = m.MappingAssertionGroup(
        source_datapoint_id=datapoint.id,
        mapping_profile=mapping_profile,
        relationship_type=relationship_type,
        coverage_status=coverage_status,
        approval_status=approval_status,
        valid_to=valid_to,
        superseded_by=superseded_by,
        confidence=0.9,
    )
    db.add(group)
    db.flush()
    db.add(
        m.MappingAssertionComponent(
            assertion_group_id=group.id,
            canonical_concept_id=canonical.id,
            component_order=0,
            component_role=component_role,
            coverage_fraction=coverage_fraction,
        )
    )
    db.flush()
    return group


def test_basic_external_to_sygris_equivalence_is_surfaced():
    db = _disposable_session()
    try:
        release, datapoint = _seed_release_datapoint(db)
        canonical = _seed_canonical(db)
        _seed_assertion(db, datapoint=datapoint, canonical=canonical)
        db.commit()

        expected_source = _source_datapoint_payload(release, datapoint)["uri"]
        rows = ConceptService(db=db).list_equivalences()
        assert len(rows) == 1
        row = rows[0]
        assert row["source_concept"] == expected_source
        assert row["target_concept"] == SYG_TARGET
        assert row["equivalence_type"] == "partial"  # broader -> partial
        assert row["confidence"] == pytest.approx(0.9)
        meta = row["metadata"]
        assert meta["relationship_type"] == "broader"
        assert meta["coverage_status"] == "partial"
        assert meta["mapping_profile"] == "default"
        assert meta["assertion_group_id"] is not None
        assert meta["component_role"] == "primary"
        assert meta["coverage_fraction"] == pytest.approx(0.5)
        assert meta["source_standard_id"] == "ESRS"
        assert meta["source_standard_version"] == "2024"
        assert meta["source_code"] == "E5-5_09"
        assert meta["target_taxonomy"] == "SYGRIS"
    finally:
        db.close()


@pytest.mark.parametrize(
    "relationship_type,expected_bucket",
    [
        ("equivalent", "exact"),
        ("exact", "exact"),
        ("same_as", "exact"),
        ("overlap", "partial"),
        ("broad_match", "partial"),
        ("narrow_match", "partial"),
        ("partial", "partial"),
    ],
)
def test_relationship_aliases_bucket_correctly(relationship_type, expected_bucket):
    db = _disposable_session()
    try:
        _, datapoint = _seed_release_datapoint(db)
        canonical = _seed_canonical(db)
        _seed_assertion(
            db,
            datapoint=datapoint,
            canonical=canonical,
            relationship_type=relationship_type,
        )
        db.commit()

        rows = ConceptService(db=db).list_equivalences()
        assert len(rows) == 1
        assert rows[0]["equivalence_type"] == expected_bucket
    finally:
        db.close()


def test_public_gate_excludes_non_current_or_inactive_or_unapproved():
    db = _disposable_session()
    try:
        # inactive release
        _, dp_inactive_rel = _seed_release_datapoint(
            db, code="E5-5_01", release_status="retired"
        )
        c1 = _seed_canonical(db, canonical_uri="syg:A")
        _seed_assertion(db, datapoint=dp_inactive_rel, canonical=c1)
        # inactive datapoint
        _, dp_inactive_dp = _seed_release_datapoint(
            db, version="2025", code="E5-5_02", datapoint_status="retired"
        )
        c2 = _seed_canonical(db, canonical_uri="syg:B")
        _seed_assertion(db, datapoint=dp_inactive_dp, canonical=c2)
        # superseded canonical concept
        _, dp_superseded = _seed_release_datapoint(db, version="2026", code="E5-5_03")
        c3 = _seed_canonical(
            db, canonical_uri="syg:C", effective_to=None, superseded_by=None
        )
        c3.effective_to = c3.effective_from  # retire the revision
        db.flush()
        _seed_assertion(db, datapoint=dp_superseded, canonical=c3)
        # draft assertion group
        _, dp_draft = _seed_release_datapoint(db, version="2027", code="E5-5_04")
        c4 = _seed_canonical(db, canonical_uri="syg:D")
        _seed_assertion(db, datapoint=dp_draft, canonical=c4, approval_status="draft")
        # superseded assertion group
        _, dp_sup_group = _seed_release_datapoint(db, version="2028", code="E5-5_05")
        c5 = _seed_canonical(db, canonical_uri="syg:E")
        grp = _seed_assertion(db, datapoint=dp_sup_group, canonical=c5)
        grp.superseded_by = grp.id  # mark superseded
        db.flush()
        db.commit()

        rows = ConceptService(db=db).list_equivalences()
        assert rows == []
    finally:
        db.close()


def test_distinct_mapping_profiles_are_not_collapsed():
    """codex P2 M3: same source/target/type but different mapping_profile must coexist."""
    db = _disposable_session()
    try:
        _, datapoint = _seed_release_datapoint(db)
        canonical = _seed_canonical(db)
        _seed_assertion(
            db, datapoint=datapoint, canonical=canonical, mapping_profile="default"
        )
        _seed_assertion(
            db, datapoint=datapoint, canonical=canonical, mapping_profile="strict"
        )
        db.commit()

        rows = ConceptService(db=db).list_equivalences()
        profiles = sorted(r["metadata"]["mapping_profile"] for r in rows)
        assert profiles == ["default", "strict"]
    finally:
        db.close()


def test_concept_equivalences_still_returned_and_take_precedence():
    db = _disposable_session()
    try:
        # legacy concept_equivalences row
        src = m.Concept(
            uri="csrd:E1-1",
            label="GHG",
            taxonomy="CSRD",
            concept_type="Indicator",
            projection_source="indicator_catalog",
        )
        db.add(src)
        db.flush()
        db.add(
            m.ConceptEquivalence(
                source_concept_id=src.id,
                target_uri="gri:305-1",
                target_taxonomy="GRI",
                relationship_type="equivalent",
                confidence=1.0,
            )
        )
        # plus an assertion projection
        _, datapoint = _seed_release_datapoint(db)
        canonical = _seed_canonical(db)
        _seed_assertion(db, datapoint=datapoint, canonical=canonical)
        db.commit()

        rows = ConceptService(db=db).list_equivalences()
        targets = {r["target_concept"] for r in rows}
        assert "gri:305-1" in targets
        assert SYG_TARGET in targets
    finally:
        db.close()


def test_taxonomy_and_concept_filters_apply_to_assertion_rows():
    db = _disposable_session()
    try:
        release, datapoint = _seed_release_datapoint(db)
        canonical = _seed_canonical(db)
        _seed_assertion(db, datapoint=datapoint, canonical=canonical)
        db.commit()

        svc = ConceptService(db=db)
        expected_source = _source_datapoint_payload(release, datapoint)["uri"]

        # source_taxonomy CSRD selects (ESRS -> CSRD)
        assert len(svc.list_equivalences(source_taxonomy="CSRD")) == 1
        # wrong source taxonomy filters it out
        assert svc.list_equivalences(source_taxonomy="GRI") == []
        # target_taxonomy Sygris selects
        assert len(svc.list_equivalences(target_taxonomy="Sygris")) == 1
        assert svc.list_equivalences(target_taxonomy="GRI") == []
        # concept filter by the syg: target CURIE
        assert len(svc.list_equivalences(concept=SYG_TARGET)) == 1
        # concept filter by the source datapoint URI
        assert len(svc.list_equivalences(concept=expected_source)) == 1
        # unrelated concept filters everything out
        assert svc.list_equivalences(concept="syg:SomethingElse") == []
    finally:
        db.close()


def test_public_equivalences_excludes_non_public_source_concept():
    """codex F09 M2: a source concept outside the public catalog (projection_source
    not in the catalog sources) must not leak its equivalences via /equivalences."""
    db = _disposable_session()
    try:
        public_src = m.Concept(
            uri="csrd:PUBLIC_SRC",
            label="Public",
            taxonomy="CSRD",
            concept_type="Indicator",
            projection_source="indicator_catalog",
        )
        internal_src = m.Concept(
            uri="csrd:INTERNAL_SRC",
            label="Internal",
            taxonomy="CSRD",
            concept_type="Indicator",
            projection_source=None,  # not a public-catalog projection source
        )
        db.add_all([public_src, internal_src])
        db.flush()
        db.add_all(
            [
                m.ConceptEquivalence(
                    source_concept_id=public_src.id,
                    target_uri="gri:1",
                    target_taxonomy="GRI",
                    relationship_type="equivalent",
                    confidence=1.0,
                ),
                m.ConceptEquivalence(
                    source_concept_id=internal_src.id,
                    target_uri="gri:2",
                    target_taxonomy="GRI",
                    relationship_type="equivalent",
                    confidence=1.0,
                ),
            ]
        )
        db.commit()

        rows = ConceptService(db=db).list_equivalences()
        targets = {r["target_concept"] for r in rows}
        assert "gri:1" in targets  # public source surfaces
        assert "gri:2" not in targets  # internal source is gated out
    finally:
        db.close()
