from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from src.database.models import (
    CurrentValuePointer,
    ESGValue,
    ReportedValuePointer,
    ValueContext,
    ValueRevision,
    ValueRevisionEvent,
)
from src.services.value_revision_backfill import (
    ValueRevisionBackfillReport,
    _date_or_none,
    _required_text,
    _text_or_none,
    _value_for_kind,
    backfill_esg_values_to_revisions,
    revision_input_for_esg_value,
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
        ESGValue.__table__,
        ValueContext.__table__,
        ValueRevision.__table__,
        ValueRevisionEvent.__table__,
        CurrentValuePointer.__table__,
        ReportedValuePointer.__table__,
    ):
        table.create(engine)
    return sessionmaker(bind=engine)()


def _legacy_value(**overrides):
    values = {
        "id": "value-1",
        "tenant_id": "tenant-a",
        "ownership_state": "resolved",
        "concept": "csrd:E3_5",
        "entity": "madrid_plant",
        "period": date(2026, 12, 31),
        "external_key": "erp:value-1",
        "value": Decimal("12.3000"),
        "original_value": Decimal("12.3000"),
        "value_type": "numeric",
        "unit": "m3",
        "original_unit": "m3",
        "currency": None,
        "original_currency": None,
        "value_metadata": {
            "standard_release_id": "ESRS_SET1_2023_12_22",
            "standard_datapoint_id": "E3-5_01",
            "indicator_identifier": "urn:sds:reg:esrs:e3_5_01",
            "dimensions": {"water_type": "total"},
            "reporting_period_id": "FY2026",
            "period_type": "annual",
            "reporting_boundary_id": "operational_control",
        },
    }
    values.update(overrides)
    return ESGValue(**values)


def test_backfill_value_revisions_dry_run_rolls_back() -> None:
    db = _session()
    db.add(_legacy_value())
    db.commit()

    report = backfill_esg_values_to_revisions(
        db=db,
        tenant_id="tenant-a",
        dry_run=True,
        created_by="tester",
    )

    assert report.scanned == 1
    assert report.created == 1
    assert report.committed is False
    assert db.query(ValueRevision).count() == 0
    assert db.query(CurrentValuePointer).count() == 0


def test_backfill_scans_only_rows_owned_by_requested_tenant() -> None:
    db = _session()
    db.add_all(
        [
            _legacy_value(
                id="tenant-a-value",
                external_key="erp:tenant-a",
                tenant_id="tenant-a",
            ),
            _legacy_value(
                id="tenant-b-value",
                external_key="erp:tenant-b",
                tenant_id="tenant-b",
            ),
        ]
    )
    db.commit()

    report = backfill_esg_values_to_revisions(
        db=db,
        tenant_id="tenant-a",
        dry_run=False,
        created_by="tester",
    )

    assert report.scanned == 1
    revisions = db.query(ValueRevision).all()
    assert [revision.external_key for revision in revisions] == ["erp:tenant-a"]


def test_backfill_value_revisions_is_idempotent_when_applied() -> None:
    db = _session()
    db.add(_legacy_value())
    db.commit()

    first = backfill_esg_values_to_revisions(
        db=db,
        tenant_id="tenant-a",
        dry_run=False,
        created_by="tester",
    )
    second = backfill_esg_values_to_revisions(
        db=db,
        tenant_id="tenant-a",
        dry_run=False,
        created_by="tester",
    )

    revision = db.query(ValueRevision).one()
    context = db.query(ValueContext).one()

    assert first.committed is True
    assert first.created == 1
    assert second.created == 0
    assert second.skipped_existing == 1
    assert revision.state == "legacy_current"
    assert revision.canonical_value == "12.3"
    assert revision.external_key == "erp:value-1"
    assert context.sds_indicator_id is None
    assert context.standard_release_id == "ESRS_SET1_2023_12_22"
    assert db.query(CurrentValuePointer).one().revision_id == revision.id


def test_backfill_value_revisions_preserves_text_values() -> None:
    db = _session()
    db.add(
        _legacy_value(
            id="value-text",
            external_key=None,
            value=None,
            original_value=None,
            value_type="narrative",
            text_value="Reported methodology",
            unit="n/a",
            value_metadata={},
        )
    )
    db.commit()

    report = backfill_esg_values_to_revisions(
        db=db,
        tenant_id="tenant-a",
        dry_run=False,
        created_by="tester",
    )

    revision = db.query(ValueRevision).one()

    assert report.created == 1
    assert revision.external_key == "legacy-esg-value:value-text"
    assert revision.value_kind == "narrative"
    assert revision.canonical_value == "Reported methodology"


def test_backfill_report_and_limit_zero_are_explicit() -> None:
    db = _session()
    db.add(_legacy_value())
    db.commit()

    report = backfill_esg_values_to_revisions(
        db=db,
        tenant_id="tenant-a",
        dry_run=False,
        limit=0,
    )

    assert report.as_dict() == {
        "dry_run": False,
        "tenant_id": "tenant-a",
        "scanned": 0,
        "created": 0,
        "skipped_existing": 0,
        "failed": 0,
        "committed": True,
        "errors": [],
    }
    assert (
        ValueRevisionBackfillReport(dry_run=True, tenant_id="t").as_dict()["errors"]
        == []
    )


def test_backfill_records_invalid_legacy_rows_as_failures() -> None:
    db = _session()
    db.add(_legacy_value(entity=""))
    db.commit()

    report = backfill_esg_values_to_revisions(
        db=db,
        tenant_id="tenant-a",
        dry_run=False,
    )

    assert report.scanned == 1
    assert report.created == 0
    assert report.failed == 1
    assert report.errors[0]["value_id"] == "value-1"
    assert "entity_id is required" in report.errors[0]["error"]


def test_revision_input_helpers_cover_non_numeric_and_optional_shapes() -> None:
    row = _legacy_value(
        id="value-bool",
        value=None,
        boolean_value=True,
        value_type="boolean",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        value_metadata={
            "dimensions_json": {"scope": "scope2"},
            "period_close_date": "2026-02-15",
            "materiality_metadata": {"assurance": "limited"},
            "sds_indicator_id": "  indicator-db-id  ",
            "evidence_hash": "  ev-hash  ",
            "canonical_uri": "syg:TotalEnergyConsumptionWithinOrganization",
            "source_observation_type": "canonical_operational",
            "source_standard": "ESRS",
            "source_standard_datapoint_id": "E1-5_02",
        },
    )

    revision_input = revision_input_for_esg_value(
        row,
        tenant_id="tenant-a",
        external_key=None,
        use_legacy_external_key_default=False,
        state="draft",
        source_system="manual",
        revision_provenance=None,
        created_by="tester",
    )

    assert revision_input.value is True
    assert revision_input.external_key == "erp:value-1"
    assert revision_input.context_identity.dimensions == {"scope": "scope2"}
    assert revision_input.context_identity.period_close_date == date(2026, 2, 15)
    assert revision_input.context_identity.sds_indicator_id == "indicator-db-id"
    assert revision_input.evidence_hash == "ev-hash"
    assert revision_input.materiality_metadata["assurance"] == "limited"
    assert (
        revision_input.materiality_metadata["canonical_uri"]
        == "syg:TotalEnergyConsumptionWithinOrganization"
    )
    assert (
        revision_input.materiality_metadata["source_observation_type"]
        == "canonical_operational"
    )
    assert revision_input.materiality_metadata["source_standard"] == "ESRS"
    assert (
        revision_input.materiality_metadata["source_standard_datapoint_id"] == "E1-5_02"
    )

    narrative = _legacy_value(
        value=None,
        text_value="free text",
        value_type="semi-narrative",
        value_metadata={"dimensions": "not-a-mapping"},
    )
    assert _value_for_kind(narrative, "semi-narrative") == "free text"
    assert _date_or_none("") is None
    assert _date_or_none(date(2026, 1, 1)) == date(2026, 1, 1)
    assert _date_or_none("2026-01-02") == date(2026, 1, 2)
    assert _text_or_none("   ") is None
    with pytest.raises(ValueError, match="tenant_id is required"):
        _required_text("", "tenant_id")
