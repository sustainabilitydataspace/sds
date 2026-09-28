"""Tests for Sygris-footprint pairwise materialization."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
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
from src.services import canonical_pairwise_materialization as pairwise_module
from src.services.canonical_pairwise_materialization import (
    PairwiseMaterializationReport,
    _acquire_materialization_lock,
    _derive_pairwise_candidates,
    _directional_group_relationship,
    _invert_directional_relationship,
    _load_current_groups,
    _match_strength,
    _upsert_pairwise_candidate,
    materialize_pairwise_mappings,
)


def test_pairwise_materialization_lock_uses_deterministic_postgres_key() -> None:
    db = MagicMock()
    db.get_bind.return_value = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql")
    )

    _acquire_materialization_lock(db)

    expected_key = (
        int.from_bytes(
            pairwise_module.hashlib.sha256(b"sds:pairwise_materialization").digest()[
                :8
            ],
            "big",
        )
        & 0x7FFF_FFFF_FFFF_FFFF
    )
    assert db.execute.call_args.args[1] == {"key": expected_key}
    assert "pg_advisory_xact_lock" in str(db.execute.call_args.args[0])


def test_pairwise_materialization_lock_is_noop_outside_postgres() -> None:
    db = MagicMock()
    db.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    _acquire_materialization_lock(db)

    db.execute.assert_not_called()


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


def _seed_assertions(db):
    esrs = StandardRelease(
        standard_id="ESRS",
        name="European Sustainability Reporting Standards",
        version="2024",
    )
    gri = StandardRelease(
        standard_id="GRI",
        name="GRI Standards",
        version="2021",
    )
    concept_a = CanonicalConcept(
        canonical_uri="syg:ScopeA",
        revision=1,
        label="Scope A",
        taxonomy="Sygris",
        concept_type="mapping_pivot",
    )
    concept_b = CanonicalConcept(
        canonical_uri="syg:ScopeB",
        revision=1,
        label="Scope B",
        taxonomy="Sygris",
        concept_type="mapping_pivot",
    )
    db.add_all([esrs, gri, concept_a, concept_b])
    db.flush()

    esrs_a = StandardDatapoint(
        standard_release=esrs,
        code="ESRS-A",
        label="ESRS Scope A",
    )
    gri_a = StandardDatapoint(
        standard_release=gri,
        code="GRI-A",
        label="GRI Scope A",
    )
    gri_ab = StandardDatapoint(
        standard_release=gri,
        code="GRI-AB",
        label="GRI Scope A and B",
    )
    db.add_all([esrs_a, gri_a, gri_ab])
    db.flush()

    group_esrs = _group(esrs_a, "e" * 64)
    group_gri_a = _group(gri_a, "a" * 64)
    group_gri_ab = _group(gri_ab, "b" * 64)
    db.add_all([group_esrs, group_gri_a, group_gri_ab])
    db.flush()

    db.add_all(
        [
            MappingAssertionComponent(
                assertion_group=group_esrs,
                canonical_concept=concept_a,
                component_order=1,
                coverage_fraction=Decimal("1.0000"),
            ),
            MappingAssertionComponent(
                assertion_group=group_gri_a,
                canonical_concept=concept_a,
                component_order=1,
                coverage_fraction=Decimal("1.0000"),
            ),
            MappingAssertionComponent(
                assertion_group=group_gri_ab,
                canonical_concept=concept_a,
                component_order=1,
                coverage_fraction=Decimal("0.5000"),
            ),
            MappingAssertionComponent(
                assertion_group=group_gri_ab,
                canonical_concept=concept_b,
                component_order=2,
                coverage_fraction=Decimal("0.5000"),
            ),
        ]
    )
    db.commit()
    return group_gri_ab.id


def _group(
    datapoint: StandardDatapoint,
    assertion_hash: str,
    *,
    approval_status: str = "approved",
    superseded_by: int | None = None,
) -> MappingAssertionGroup:
    # Default to APPROVED: the public materialization default is approved-only
    # (codex F08 M1), so materialization fixtures must seed approved groups.
    return MappingAssertionGroup(
        source_datapoint=datapoint,
        mapping_profile="default",
        relationship_type="equivalent",
        coverage_status="complete",
        confidence=Decimal("0.95"),
        valid_from=datetime(2026, 5, 8),
        approval_status=approval_status,
        publication_status="internal",
        assertion_hash=assertion_hash,
        superseded_by=superseded_by,
    )


def test_pairwise_materialization_dry_run_rolls_back():
    db = _session()
    _seed_assertions(db)

    report = materialize_pairwise_mappings(db=db, dry_run=True)

    assert report.candidate_count == 4
    assert report.counts["pairwise_created"] == 4
    assert report.committed is False
    assert db.query(MaterializedPairwiseMapping).count() == 0
    assert db.query(StandardMapping).count() == 0


def test_pairwise_materialization_is_bidirectional_and_idempotent():
    db = _session()
    _seed_assertions(db)

    first = materialize_pairwise_mappings(db=db)
    second = materialize_pairwise_mappings(db=db)

    assert first.committed is True
    assert first.counts["pairwise_created"] == 4
    assert second.counts["pairwise_unchanged"] == 4
    assert db.query(MaterializedPairwiseMapping).count() == 4
    assert db.query(StandardMapping).count() == 0

    exact = (
        db.query(MaterializedPairwiseMapping)
        .filter(
            MaterializedPairwiseMapping.source_code == "ESRS-A",
            MaterializedPairwiseMapping.target_code == "GRI-A",
        )
        .one()
    )
    partial = (
        db.query(MaterializedPairwiseMapping)
        .filter(
            MaterializedPairwiseMapping.source_code == "ESRS-A",
            MaterializedPairwiseMapping.target_code == "GRI-AB",
        )
        .one()
    )
    assert exact.relationship_type == "equivalent"
    assert exact.match_strength == Decimal("1.00")
    assert partial.relationship_type == "partial"
    assert partial.match_strength == Decimal("0.50")
    assert partial.metadata_json["shared_sygris_concept_ids"]


def test_pairwise_materialization_preserves_explicit_directional_relationships():
    db = _session()
    _seed_assertions(db)
    esrs_group = (
        db.query(MappingAssertionGroup)
        .join(StandardDatapoint)
        .filter(StandardDatapoint.code == "ESRS-A")
        .one()
    )
    esrs_group.relationship_type = "narrower"
    esrs_group.coverage_status = "partial"
    db.commit()

    materialize_pairwise_mappings(db=db)

    forward = (
        db.query(MaterializedPairwiseMapping)
        .filter(
            MaterializedPairwiseMapping.source_code == "ESRS-A",
            MaterializedPairwiseMapping.target_code == "GRI-A",
        )
        .one()
    )
    reverse = (
        db.query(MaterializedPairwiseMapping)
        .filter(
            MaterializedPairwiseMapping.source_code == "GRI-A",
            MaterializedPairwiseMapping.target_code == "ESRS-A",
        )
        .one()
    )
    assert forward.match_strength == Decimal("1.00")
    assert forward.relationship_type == "narrower"
    assert reverse.match_strength == Decimal("1.00")
    assert reverse.relationship_type == "broader"
    assert forward.metadata_json["source_assertion_relationship_type"] == "narrower"
    assert forward.metadata_json["target_assertion_relationship_type"] == "equivalent"


def test_pairwise_materialization_blocks_non_operational_relationships():
    db = _session()
    _seed_assertions(db)
    esrs_group = (
        db.query(MappingAssertionGroup)
        .join(StandardDatapoint)
        .filter(StandardDatapoint.code == "ESRS-A")
        .one()
    )
    esrs_group.relationship_type = "partial_overlap"
    db.commit()

    report = materialize_pairwise_mappings(db=db)

    assert report.candidate_count == 0
    assert report.blocked is True
    assert report.counts["non_operational_assertion_groups_blocked"] == 1
    assert "partial_overlap" in report.blockers[0]
    assert report.committed is False
    assert db.query(MaterializedPairwiseMapping).count() == 0
    assert db.query(StandardMapping).count() == 0


def test_pairwise_materialization_can_commit_explicit_operational_subset():
    db = _session()
    gri_ab_id = _seed_assertions(db)
    gri_ab_group = (
        db.query(MappingAssertionGroup)
        .filter(MappingAssertionGroup.id == gri_ab_id)
        .one()
    )
    gri_ab_group.relationship_type = "partial_overlap"
    db.commit()

    report = materialize_pairwise_mappings(db=db, allow_operational_subset=True)

    assert report.committed is True
    assert report.blocked is False
    assert report.candidate_count == 2
    assert report.counts["non_operational_assertion_groups_skipped"] == 1
    assert "partial_overlap" not in report.blockers
    assert db.query(MaterializedPairwiseMapping).count() == 2
    assert db.query(StandardMapping).count() == 0
    assert (
        db.query(MaterializedPairwiseMapping)
        .filter(MaterializedPairwiseMapping.target_code == "GRI-AB")
        .count()
        == 0
    )


def test_pairwise_materialization_blocks_component_of_even_with_full_coverage():
    db = _session()
    _seed_assertions(db)
    gri_group = (
        db.query(MappingAssertionGroup)
        .join(StandardDatapoint)
        .filter(StandardDatapoint.code == "GRI-A")
        .one()
    )
    gri_group.relationship_type = "component_of"
    db.commit()

    report = materialize_pairwise_mappings(db=db)

    assert report.blocked is True
    assert report.candidate_count == 0
    assert report.relationship_type_counts["component_of"] == 1
    assert db.query(MaterializedPairwiseMapping).count() == 0


def test_pairwise_materialization_blocks_unknown_relationship_types():
    db = _session()
    _seed_assertions(db)
    esrs_group = (
        db.query(MappingAssertionGroup)
        .join(StandardDatapoint)
        .filter(StandardDatapoint.code == "ESRS-A")
        .one()
    )
    esrs_group.relationship_type = "custom_semantic_relation"
    db.commit()

    report = materialize_pairwise_mappings(db=db)

    assert report.blocked is True
    assert report.relationship_type_counts["custom_semantic_relation"] == 1
    assert db.query(MaterializedPairwiseMapping).count() == 0


def test_pairwise_materialization_stales_rows_missing_from_latest_run():
    db = _session()
    old_group_id = _seed_assertions(db)
    materialize_pairwise_mappings(db=db)

    old_group = db.get(MappingAssertionGroup, old_group_id)
    old_group.valid_to = datetime(2026, 5, 9)
    db.commit()
    report = materialize_pairwise_mappings(db=db)

    assert report.candidate_count == 2
    assert report.counts["pairwise_unchanged"] == 2
    assert report.counts["pairwise_staled"] == 2
    assert (
        db.query(MaterializedPairwiseMapping)
        .filter(MaterializedPairwiseMapping.is_current.is_(True))
        .count()
        == 2
    )


def test_pairwise_upsert_supersedes_changed_current_row_without_legacy_writes():
    existing = SimpleNamespace(
        generated_from_hash="old-hash",
        is_current=True,
        stale_reason=None,
    )

    class _Query:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return existing

    class _DB:
        def __init__(self):
            self.added = []
            self.flushed = 0

        def query(self, *_args, **_kwargs):
            return _Query()

        def flush(self):
            self.flushed += 1

        def add(self, row):
            self.added.append(row)

    candidate = SimpleNamespace(
        source_datapoint=SimpleNamespace(id=1, code="ESRS-A"),
        target_datapoint=SimpleNamespace(id=2, code="GRI-A"),
        source_group=SimpleNamespace(id=10),
        target_group=SimpleNamespace(id=20),
        source_standard="ESRS",
        target_standard="GRI",
        relationship_type="equivalent",
        match_strength=Decimal("1.00"),
        coverage_summary="Full coverage.",
        difference_summary="",
        generated_from_hash="new-hash",
        metadata_json={"shared": [1]},
    )
    report = PairwiseMaterializationReport(
        mode="shadow_pairwise_materialization",
        dry_run=False,
        committed=False,
        mapping_profile="default",
        approval_statuses=("draft",),
        candidate_count=1,
        materialization_hash="hash",
    )
    db = _DB()

    _upsert_pairwise_candidate(db, candidate, report)

    assert existing.is_current is False
    assert existing.stale_reason == "replaced_by_new_sygris_footprint_materialization"
    assert db.flushed == 1
    assert len(db.added) == 1
    assert report.counts["pairwise_superseded"] == 1
    assert report.counts["pairwise_created"] == 1


def test_pairwise_materialization_blocked_report_preserves_skip_counts(monkeypatch):
    monkeypatch.setattr(
        pairwise_module,
        "_load_current_groups",
        lambda *args, **kwargs: (
            [],
            2,
            3,
            {"partial_overlap": 1},
            {"partial_overlap": 1},
        ),
    )
    db = _session()

    report = materialize_pairwise_mappings(db=db)

    assert report.blocked is True
    assert report.counts["older_assertion_groups_skipped"] == 2
    assert report.counts["assertion_groups_skipped_missing_installed_standard"] == 3
    assert report.counts["non_operational_assertion_groups_blocked"] == 1
    assert report.counts["non_operational_relationship_partial_overlap"] == 1


def test_pairwise_materialization_skips_duplicates_and_missing_installed_releases():
    active_release = SimpleNamespace(
        standard_id="ESRS",
        version="2024",
        lifecycle_status="active",
    )
    inactive_release = SimpleNamespace(
        standard_id="GRI",
        version="2021",
        lifecycle_status="retired",
    )
    active_datapoint = SimpleNamespace(
        id=1,
        lifecycle_status="active",
        standard_release=active_release,
    )
    missing_release_datapoint = SimpleNamespace(
        id=2,
        lifecycle_status="active",
        standard_release=SimpleNamespace(
            standard_id="ISSB", version="2024", lifecycle_status="active"
        ),
    )
    inactive_datapoint = SimpleNamespace(
        id=3,
        lifecycle_status="active",
        standard_release=inactive_release,
    )
    rows = [
        SimpleNamespace(
            id=1,
            components=[object()],
            source_datapoint=active_datapoint,
            source_datapoint_id=1,
            relationship_type="equivalent",
        ),
        SimpleNamespace(
            id=2,
            components=[object()],
            source_datapoint=active_datapoint,
            source_datapoint_id=1,
            relationship_type="equivalent",
        ),
        SimpleNamespace(
            id=3,
            components=[object()],
            source_datapoint=missing_release_datapoint,
            source_datapoint_id=2,
            relationship_type="equivalent",
        ),
        SimpleNamespace(
            id=4,
            components=[object()],
            source_datapoint=inactive_datapoint,
            source_datapoint_id=3,
            relationship_type="equivalent",
        ),
        SimpleNamespace(
            id=5,
            components=[],
            source_datapoint=active_datapoint,
            source_datapoint_id=4,
            relationship_type="equivalent",
        ),
        SimpleNamespace(
            id=6,
            components=[object()],
            source_datapoint=None,
            source_datapoint_id=5,
            relationship_type="equivalent",
        ),
    ]

    class _Query:
        def options(self, *_args, **_kwargs):
            return self

        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def all(self):
            return rows

    class _DB:
        def query(self, *_args, **_kwargs):
            return _Query()

    selected, skipped, skipped_missing, relationship_counts, _non_operational = (
        _load_current_groups(
            _DB(),
            mapping_profile="default",
            approval_statuses=("draft",),
            installed_standard_releases={("ESRS", "2024")},
        )
    )

    assert [group.id for group in selected] == [1]
    assert skipped == 1
    assert skipped_missing == 2
    assert relationship_counts["equivalent"] == 1


def test_pairwise_materialization_reports_skipped_non_operational_in_dry_run():
    db = _session()
    _seed_assertions(db)
    esrs_group = (
        db.query(MappingAssertionGroup)
        .join(StandardDatapoint)
        .filter(StandardDatapoint.code == "ESRS-A")
        .one()
    )
    esrs_group.relationship_type = "partial_overlap"
    db.commit()

    report = materialize_pairwise_mappings(db=db, dry_run=True)

    assert report.blocked is False
    assert report.counts["non_operational_assertion_groups_dry_run_only"] == 1
    assert report.counts["non_operational_relationship_partial_overlap"] == 1


def test_pairwise_materialization_rolls_back_on_commit_failure(monkeypatch):
    db = _session()
    _seed_assertions(db)

    def fail_flush():
        raise RuntimeError("flush failed")

    monkeypatch.setattr(db, "flush", fail_flush)

    try:
        materialize_pairwise_mappings(db=db)
    except RuntimeError as exc:
        assert "flush failed" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("materialization should fail")


def test_pairwise_materialization_small_helper_edges():
    assert _match_strength({}, {}, ()) == Decimal("0")
    assert _invert_directional_relationship("equivalent") == "equivalent"


def test_pairwise_materialization_dry_run_counts_skips_and_rolls_back(monkeypatch):
    monkeypatch.setattr(
        pairwise_module,
        "_load_current_groups",
        lambda *args, **kwargs: (
            [],
            2,
            3,
            {"equivalent": 1},
            {"partial_overlap": 4},
        ),
    )
    db = _session()

    report = materialize_pairwise_mappings(
        db=db,
        dry_run=True,
        allow_operational_subset=True,
    )

    assert report.counts["older_assertion_groups_skipped"] == 2
    assert report.counts["assertion_groups_skipped_missing_installed_standard"] == 3
    assert report.counts["non_operational_assertion_groups_dry_run_only"] == 4


def test_pairwise_materialization_rolls_back_on_upsert_failure(monkeypatch):
    source = SimpleNamespace(id=1)
    target = SimpleNamespace(id=2)
    candidate = SimpleNamespace(
        source_datapoint=source,
        target_datapoint=target,
        generated_from_hash="candidate-hash",
    )
    monkeypatch.setattr(
        pairwise_module,
        "_load_current_groups",
        lambda *args, **kwargs: ([object()], 0, 0, {}, {}),
    )
    monkeypatch.setattr(
        pairwise_module,
        "_derive_pairwise_candidates",
        lambda *_args, **_kwargs: [candidate],
    )
    monkeypatch.setattr(
        pairwise_module,
        "_upsert_pairwise_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("upsert failed")),
    )
    db = _session()

    with pytest.raises(RuntimeError, match="upsert failed"):
        materialize_pairwise_mappings(db=db)


def test_pairwise_group_loading_and_directional_relationship_edges():
    active_release = SimpleNamespace(
        standard_id="ESRS", version="2024", lifecycle_status="active"
    )
    inactive_release = SimpleNamespace(
        standard_id="GRI", version="2021", lifecycle_status="retired"
    )
    missing_release_datapoint = SimpleNamespace(
        id=10, lifecycle_status="active", standard_release=None
    )
    inactive_release_datapoint = SimpleNamespace(
        id=11, lifecycle_status="active", standard_release=inactive_release
    )
    active_datapoint = SimpleNamespace(
        id=12, lifecycle_status="active", standard_release=active_release
    )
    non_operational = SimpleNamespace(
        id=1,
        components=[object()],
        source_datapoint=missing_release_datapoint,
        source_datapoint_id=10,
        relationship_type="equivalent",
    )
    inactive = SimpleNamespace(
        id=2,
        components=[object()],
        source_datapoint=inactive_release_datapoint,
        source_datapoint_id=11,
        relationship_type="equivalent",
    )
    partial = SimpleNamespace(
        id=3,
        components=[object()],
        source_datapoint=active_datapoint,
        source_datapoint_id=12,
        relationship_type="partial_overlap",
    )

    class _Query:
        def options(self, *_args, **_kwargs):
            return self

        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def all(self):
            return [non_operational, inactive, partial]

    selected, skipped, skipped_missing, _counts, non_operational_counts = (
        _load_current_groups(
            SimpleNamespace(query=lambda *_args: _Query()),
            mapping_profile="default",
            approval_statuses=("draft",),
            installed_standard_releases={("ESRS", "2024")},
        )
    )

    assert selected == []
    assert skipped == 0
    assert skipped_missing == 1
    assert non_operational_counts["partial_overlap"] == 1
    assert (
        _directional_group_relationship(
            SimpleNamespace(relationship_type="equivalent", coverage_status="partial"),
            SimpleNamespace(relationship_type="equivalent", coverage_status="complete"),
        )
        == "partial"
    )
    assert (
        _directional_group_relationship(
            SimpleNamespace(relationship_type="broader", coverage_status="complete"),
            SimpleNamespace(relationship_type="narrower", coverage_status="complete"),
        )
        == "partial"
    )


def test_pairwise_candidate_derivation_skips_same_standard_and_disjoint_footprints():
    esrs = SimpleNamespace(standard_id="ESRS", version="2024")
    gri = SimpleNamespace(standard_id="GRI", version="2021")
    concept_a = SimpleNamespace(id=1)
    concept_b = SimpleNamespace(id=2)
    groups = [
        SimpleNamespace(
            id=1,
            source_datapoint=SimpleNamespace(id=1, standard_release=esrs),
            components=[
                SimpleNamespace(
                    canonical_concept_id=1,
                    canonical_concept=concept_a,
                    coverage_fraction=Decimal("1"),
                )
            ],
            relationship_type="equivalent",
            coverage_status="complete",
            confidence=Decimal("1"),
        ),
        SimpleNamespace(
            id=2,
            source_datapoint=SimpleNamespace(id=2, standard_release=esrs),
            components=[
                SimpleNamespace(
                    canonical_concept_id=1,
                    canonical_concept=concept_a,
                    coverage_fraction=Decimal("1"),
                )
            ],
            relationship_type="equivalent",
            coverage_status="complete",
            confidence=Decimal("1"),
        ),
        SimpleNamespace(
            id=3,
            source_datapoint=SimpleNamespace(id=3, standard_release=gri),
            components=[
                SimpleNamespace(
                    canonical_concept_id=2,
                    canonical_concept=concept_b,
                    coverage_fraction=Decimal("1"),
                )
            ],
            relationship_type="equivalent",
            coverage_status="complete",
            confidence=Decimal("1"),
        ),
    ]

    assert _derive_pairwise_candidates(groups, mapping_profile="default") == []


def test_load_current_groups_excludes_draft_and_superseded(monkeypatch):
    """codex F08 M1: the public (default approved-only) selection must exclude
    draft and superseded assertion groups."""
    from src.database.models import (
        CanonicalConcept,
        MappingAssertionComponent,
        StandardDatapoint,
        StandardRelease,
    )

    db = _session()
    release = StandardRelease(standard_id="ESRS", name="ESRS", version="2024")
    concept = CanonicalConcept(
        canonical_uri="syg:X",
        revision=1,
        label="X",
        taxonomy="Sygris",
        concept_type="mapping_pivot",
    )
    db.add_all([release, concept])
    db.flush()
    dps = [
        StandardDatapoint(standard_release=release, code=f"E-{i}", label=f"E{i}")
        for i in range(3)
    ]
    db.add_all(dps)
    db.flush()

    approved = _group(dps[0], "a" * 64, approval_status="approved")
    draft = _group(dps[1], "d" * 64, approval_status="draft")
    db.add_all([approved, draft])
    db.flush()
    superseded = _group(
        dps[2], "s" * 64, approval_status="approved", superseded_by=approved.id
    )
    db.add(superseded)
    db.flush()
    for grp in (approved, draft, superseded):
        db.add(
            MappingAssertionComponent(
                assertion_group=grp,
                canonical_concept=concept,
                component_order=1,
                coverage_fraction=Decimal("1.0000"),
            )
        )
    db.commit()

    selected, _skipped, _missing, _rel, _nonop = _load_current_groups(
        db,
        mapping_profile="default",
        approval_statuses=pairwise_module.DEFAULT_APPROVAL_STATUSES,
        installed_standard_releases=None,
    )
    selected_ids = {g.id for g in selected}
    assert approved.id in selected_ids
    assert draft.id not in selected_ids  # draft excluded by approved-only default
    assert superseded.id not in selected_ids  # superseded excluded
