"""Synthetic fixtures shared by offline and disposable-PG H15 tests."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import MetaData, select

from src.database.models import (
    DatasetSnapshot,
    ESGValue,
    Indicator,
    IndicatorImportJob,
    StandardMapping,
    ValueContext,
    ValueImportJob,
    ValueRevision,
    ValueRevisionEvent,
)
from src.database.repositories.value_import_job_repository import (
    LEGACY_RECONCILIATION_REASON,
)
from src.services.value_revision_store import ValueRevisionInput, ValueRevisionStore
from src.services.value_versioning import ValueContextIdentity

CHANGED_FIELDS = {"status", "error_message", "completed_at", "updated_at"}
OLD_TIME = datetime(2020, 1, 2, 3, 4, 5)
MODELS = (
    ValueImportJob,
    IndicatorImportJob,
    ESGValue,
    Indicator,
    StandardMapping,
    DatasetSnapshot,
    ValueContext,
    ValueRevision,
    ValueRevisionEvent,
)


def seed_evidence(db):
    for status in ("pending", "running", "completed", "failed", "unknown"):
        for committed in (None, False, True):
            suffix = f"{status}-{committed}"
            evidence = dict(
                id=suffix,
                source_format="csv" if committed else "json",
                status=status,
                submitted_by="synthetic-user",
                source_filename="synthetic-source.csv",
                request_metadata={"opaque": ["preserve", {"nested": True}]},
                result_body=(
                    {"rows": [{"error": "synthetic-retained-secret"}]}
                    if committed is not None
                    else None
                ),
                total_rows=7 if committed is not None else None,
                accepted_rows=5 if committed is not None else None,
                rejected_rows=2 if committed is not None else None,
                committed=committed,
                error_message="synthetic-old-error-secret",
                created_at=OLD_TIME,
                started_at=OLD_TIME if status != "pending" else None,
                # Even inconsistent pre-existing evidence must not be normalized.
                completed_at=OLD_TIME,
                updated_at=OLD_TIME,
            )
            db.add(ValueImportJob(**evidence))
            db.add(
                IndicatorImportJob(
                    **evidence,
                    tenant_id=None,
                    owner_user_id=None,
                    ownership_state="quarantined",
                    job_type="import",
                    source_payload="synthetic-payload-secret",
                    source_sha256="a" * 64,
                    source_size_bytes=24,
                )
            )
    db.add(
        ESGValue(
            id="h15-value",
            tenant_id="h15-tenant",
            ownership_state="resolved",
            concept="urn:sds:reg:h15",
            entity="h15-entity",
            period=date(2020, 1, 31),
            value=7,
            unit="kg",
            value_metadata={"retained": True},
        )
    )
    db.add(
        Indicator(
            id="h15-indicator",
            identifier="urn:sds:reg:h15",
            title="Synthetic H15 indicator",
            dimension="E",
        )
    )
    db.add(
        StandardMapping(
            source_standard="H15-A",
            source_code="A",
            target_standard="H15-B",
            target_code="B",
            mapping_metadata={"retained": True},
        )
    )
    db.add(
        DatasetSnapshot(
            dataset="h15-fixture",
            manifest_hash="b" * 64,
            record_count=1,
            contract_version="test",
            item_index={"retained": [1]},
        )
    )
    ValueRevisionStore(db).append_revision(
        revision_input=ValueRevisionInput(
            context_identity=ValueContextIdentity(
                tenant_id="h15-tenant",
                entity_id="h15-entity",
                reporting_period_id="2020-01",
                period_start=date(2020, 1, 1),
                period_end=date(2020, 1, 31),
                period_close_date=date(2020, 1, 31),
                period_type="monthly",
                reporting_boundary_id="h15-boundary",
                sds_indicator_id=None,
                indicator_identifier="urn:sds:reg:h15",
                standard_release_id="h15-release",
                standard_datapoint_id="h15-datapoint",
                dimensions={},
                expected_unit="kg",
                expected_currency=None,
                value_kind="numeric",
            ),
            value=Decimal("7"),
            value_kind="numeric",
            unit="kg",
            state="draft",
            created_by="h15-fixture",
        ),
    )


def snapshot(engine):
    """Compare every column of every real table, including migration-only tables."""
    metadata = MetaData()
    metadata.reflect(bind=engine, resolve_fks=False)
    with engine.connect() as connection:
        return {
            name: sorted(
                [dict(row) for row in connection.execute(select(table)).mappings()],
                key=repr,
            )
            for name, table in metadata.tables.items()
        }


def assert_reconciled(before, after):
    assert set(before) == set(after)
    for name in before:
        if name != "value_import_jobs":
            assert after[name] == before[name], name
    old_jobs = {row["id"]: row for row in before["value_import_jobs"]}
    new_jobs = {row["id"]: row for row in after["value_import_jobs"]}
    assert old_jobs.keys() == new_jobs.keys()
    for job_id, old in old_jobs.items():
        new = new_jobs[job_id]
        if old["status"] not in ("pending", "running"):
            assert new == old
            continue
        assert new["status"] == "failed"
        assert new["error_message"] == LEGACY_RECONCILIATION_REASON
        assert "historical execution effects are indeterminate" in new["error_message"]
        assert new["completed_at"] == new["updated_at"]
        assert new["completed_at"] > OLD_TIME
        assert {k: v for k, v in new.items() if k not in CHANGED_FIELDS} == {
            k: v for k, v in old.items() if k not in CHANGED_FIELDS
        }
