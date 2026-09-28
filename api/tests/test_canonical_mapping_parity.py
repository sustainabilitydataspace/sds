"""Tests for legacy/canonical mapping parity comparison."""

from __future__ import annotations

import csv
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from src.database.models import MaterializedPairwiseMapping, StandardMapping
from src.services.canonical_mapping_parity import (
    MappingParityKey,
    _decimal_or_none,
    _unique_normalized_keys,
    compare_legacy_to_canonical_mapping_parity,
    write_mapping_parity_worklist_csv,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


def _session():
    engine = create_engine("sqlite:///:memory:")
    StandardMapping.__table__.create(engine)
    MaterializedPairwiseMapping.__table__.create(engine)
    return sessionmaker(bind=engine)()


def _legacy(
    source_code: str,
    target_code: str,
    *,
    relationship_type: str = "equivalent",
    confidence: Decimal = Decimal("1.00"),
) -> StandardMapping:
    return StandardMapping(
        source_standard="ESRS",
        source_code=source_code,
        source_label=source_code,
        target_standard="GRI",
        target_code=target_code,
        target_label=target_code,
        relationship_type=relationship_type,
        confidence=confidence,
        dataset="test",
        is_active=True,
    )


def _canonical(
    source_code: str,
    target_code: str,
    *,
    relationship_type: str = "equivalent",
    match_strength: Decimal = Decimal("1.00"),
    source_datapoint_id: int = 1,
    target_datapoint_id: int = 2,
) -> MaterializedPairwiseMapping:
    return MaterializedPairwiseMapping(
        source_datapoint_id=source_datapoint_id,
        target_datapoint_id=target_datapoint_id,
        source_assertion_group_id=source_datapoint_id,
        target_assertion_group_id=target_datapoint_id,
        source_standard="ESRS",
        source_code=source_code,
        target_standard="GRI",
        target_code=target_code,
        relationship_type=relationship_type,
        match_strength=match_strength,
        generated_from_hash="a" * 64,
        is_current=True,
    )


def test_mapping_parity_passes_when_keys_and_behavior_match():
    db = _session()
    db.add(_legacy("E1-6_07", "305-1"))
    db.add(_canonical("E1-6_07", "305-1"))
    db.commit()

    report = compare_legacy_to_canonical_mapping_parity(
        db=db,
        source_standard="ESRS",
        target_standard="GRI",
    )

    assert report.passed is True
    assert report.legacy_count == 1
    assert report.canonical_count == 1
    assert report.matched_count == 1


def test_mapping_parity_reports_missing_extra_and_field_drift():
    db = _session()
    db.add(_legacy("E1-6_07", "305-1"))
    db.add(_legacy("E1-6_11", "305-3"))
    db.add(
        _canonical(
            "E1-6_07",
            "305-1",
            relationship_type="partial",
            match_strength=Decimal("0.50"),
        )
    )
    db.add(_canonical("E1-6_09", "305-2", source_datapoint_id=3, target_datapoint_id=4))
    db.commit()

    report = compare_legacy_to_canonical_mapping_parity(db=db, sample_limit=1)
    payload = report.as_dict()

    assert report.passed is False
    assert payload["missing_in_canonical_count"] == 1
    assert payload["extra_in_canonical_count"] == 1
    assert payload["relationship_mismatch_count"] == 1
    assert payload["confidence_mismatch_count"] == 1
    assert payload["worklist_count"] == 4
    assert len(payload["samples"]["missing_in_canonical"]) == 1
    assert payload["samples"]["worklist"][0]["status"] == "unclassified"
    assert payload["samples"]["worklist"][0]["suggested_classification"]


def test_mapping_parity_pairs_atomic_gri_code_format_drift_for_review():
    db = _session()
    db.add(_legacy("S1-6_13", "GRI 2-7.c-"))
    db.add(_canonical("S1-6_13", "GRI 2-7.c"))
    db.add(_legacy("S1-6_15", "GRI 2-7.c-ii"))
    db.add(
        _canonical(
            "S1-6_15",
            "GRI 2-7.c.ii",
            source_datapoint_id=3,
            target_datapoint_id=4,
        )
    )
    db.commit()

    report = compare_legacy_to_canonical_mapping_parity(db=db)
    payload = report.as_dict()

    assert report.passed is False
    assert payload["matched_count"] == 2
    assert payload["normalized_code_match_count"] == 2
    assert payload["missing_in_canonical_count"] == 0
    assert payload["extra_in_canonical_count"] == 0
    assert payload["code_mismatch_count"] == 2
    assert payload["samples"]["code_mismatches"][0]["field"] == "target_code"
    assert {
        item["suggested_classification"]
        for item in payload["samples"]["worklist"]
        if item["issue_type"] == "code_mismatch"
    } == {"legacy_code_normalization_review_required"}


def test_mapping_parity_does_not_normalize_composite_gri_codes():
    db = _session()
    db.add(_legacy("S1-6_16", "GRI 2-7.d; e"))
    db.add(_canonical("S1-6_16", "GRI 2-7.d"))
    db.commit()

    report = compare_legacy_to_canonical_mapping_parity(db=db)
    payload = report.as_dict()

    assert payload["matched_count"] == 0
    assert payload["normalized_code_match_count"] == 0
    assert payload["missing_in_canonical_count"] == 1
    assert payload["extra_in_canonical_count"] == 1
    assert payload["code_mismatch_count"] == 0


def test_mapping_parity_reports_duplicate_legacy_keys():
    db = _session()
    db.add(_legacy("E1-6_07", "305-1"))
    db.add(_legacy("E1-6_07", "305-1"))
    db.add(_canonical("E1-6_07", "305-1"))
    db.commit()

    report = compare_legacy_to_canonical_mapping_parity(db=db)

    assert report.passed is False
    assert report.as_dict()["duplicate_legacy_key_count"] == 1


def test_mapping_parity_worklist_csv_is_reviewable(tmp_path):
    db = _session()
    db.add(_legacy("E1-6_07", "305-1"))
    db.commit()

    report = compare_legacy_to_canonical_mapping_parity(db=db)
    output = tmp_path / "worklist.csv"
    write_mapping_parity_worklist_csv(output, report.worklist())

    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert rows == [
        {
            "issue_type": "missing_in_canonical",
            "status": "unclassified",
            "suggested_classification": "canonical_coverage_gap_or_materialization_bug",
            "source_standard": "ESRS",
            "source_code": "E1-6_07",
            "target_standard": "GRI",
            "target_code": "305-1",
            "field": "",
            "legacy_value": "",
            "canonical_value": "",
        }
    ]


def test_mapping_parity_key_helpers_drop_ambiguous_normalized_duplicates():
    key = MappingParityKey("ESRS", "E1-6_07", "GRI", "305-1")
    assert key.as_string() == "ESRS|E1-6_07|GRI|305-1"

    duplicate_a = MappingParityKey("ESRS", "S1-6_13", "GRI", "GRI 2-7.c-")
    duplicate_b = MappingParityKey("ESRS", "S1-6_13", "GRI", "GRI 2-7.c")
    unique = _unique_normalized_keys([duplicate_a, duplicate_b, key])

    assert list(unique.values()) == [key]
    assert _decimal_or_none(None) is None
