from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from rdflib import Graph

import src.services.value_batch as value_batch_service
from scripts import import_values_csv as importer
from src.api.models import ValueCreate
from src.config.settings import settings
from src.database.models import ESGValue
from src.services.value_batch import execute_value_batch
from src.services.value_ingest import (
    PreparedValueRecord,
    ValueIngestError,
    prepare_value_record,
    prepare_value_records,
)
from src.services.value_store import DatabaseValueStore


class _CountingIndicatorStore:
    def __init__(self, known: set[str]):
        self.known = known
        self.calls = 0

    def get_by_identifier(self, identifier: str):
        self.calls += 1
        return {"identifier": identifier} if identifier in self.known else None


class _CountingHierarchyStore:
    def __init__(self, entity_id: str = "madrid_plant"):
        self.calls = 0
        self._configs = [SimpleNamespace(levels=[SimpleNamespace(id=entity_id)])]

    def list(self, **_kwargs):
        self.calls += 1
        return self._configs, len(self._configs)


class _PassthroughConverter:
    def get_physical_converter(self):
        return self

    def normalize_unit_symbol(self, symbol: str) -> str:
        return symbol

    def convert(self, *_args, **_kwargs):  # pragma: no cover - should not be reached
        raise AssertionError("unexpected conversion")


def _value(
    *,
    external_key: str | None,
    value=Decimal("1.5"),
    concept: str = "urn:sds:reg:test:water",
) -> ValueCreate:
    return ValueCreate(
        concept=concept,
        entity="madrid_plant",
        period=date(2026, 12, 31),
        external_key=external_key,
        value=value,
        unit="m3",
        value_type="numeric",
        metadata={
            "standard_release_id": "TEST",
            "standard_datapoint_id": concept,
            "reporting_period_id": "FY2026",
            "period_type": "annual",
            "reporting_boundary_id": "operational_control",
        },
    )


def test_prepare_value_records_matches_row_by_row_and_caches_strict_lookups():
    rows = [_value(external_key=f"erp:water:{idx}") for idx in range(3)]
    ids = [f"value-{idx}" for idx in range(3)]

    row_by_row = [
        prepare_value_record(
            value_id=value_id,
            value_data=row,
            converter=_PassthroughConverter(),
            hierarchy_store=_CountingHierarchyStore(),
            indicator_store=_CountingIndicatorStore({"urn:sds:reg:test:water"}),
            graph=Graph(),
            company_id="company_001",
            strict=True,
        )
        for value_id, row in zip(ids, rows)
    ]

    hierarchy_store = _CountingHierarchyStore()
    indicator_store = _CountingIndicatorStore({"urn:sds:reg:test:water"})
    batch = prepare_value_records(
        rows=list(zip(ids, rows)),
        row_numbers=[2, 3, 4],
        converter=_PassthroughConverter(),
        hierarchy_store=hierarchy_store,
        indicator_store=indicator_store,
        graph=Graph(),
        company_id="company_001",
        strict=True,
    )

    assert batch == row_by_row
    assert hierarchy_store.calls == 1
    assert indicator_store.calls == 1


def test_prepare_value_records_reports_csv_row_and_external_key_on_failure():
    valid = _value(external_key="erp:water:ok")
    invalid = _value(external_key="erp:water:bad", value="not-a-number")

    with pytest.raises(ValueIngestError) as error:
        prepare_value_records(
            rows=[("value-ok", valid), ("value-bad", invalid)],
            row_numbers=[2, 3],
            converter=_PassthroughConverter(),
            hierarchy_store=_CountingHierarchyStore(),
            indicator_store=_CountingIndicatorStore({"urn:sds:reg:test:water"}),
            graph=Graph(),
            company_id="company_001",
            strict=True,
        )

    message = str(error.value)
    assert "CSV row 3" in message
    assert "external_key=erp:water:bad" in message
    assert "Numeric values must use a numeric JSON/CSV value" in message


def test_cli_import_rejects_duplicate_external_keys_with_csv_rows():
    rows = [
        _value(external_key="erp:dup"),
        _value(external_key="erp:other"),
        _value(external_key="erp:dup"),
    ]

    with pytest.raises(ValueIngestError) as error:
        importer._validate_unique_external_keys(rows)

    message = str(error.value)
    assert "Duplicate external_key" in message
    assert "external_key=erp:dup" in message
    assert "CSV row 2" in message
    assert "CSV row 4" in message


def test_cli_import_persists_mixed_batch_through_one_store_save(
    tmp_path: Path, monkeypatch
):
    csv_path = tmp_path / "values.csv"
    csv_path.write_text(
        "concept,entity,period,external_key,value,unit,value_type\n"
        "urn:sds:reg:test:water,madrid_plant,2026-12-31,erp:one,1,m3,numeric\n"
        "urn:sds:reg:test:water,madrid_plant,2026-12-31,,2,m3,numeric\n",
        encoding="utf-8",
    )

    class _FakeSession:
        def close(self):
            pass

    fake_store = MagicMock()
    fake_store.save.return_value = []
    fake_store.bulk_create.return_value = []
    captured_store: dict[str, object] = {}

    monkeypatch.setattr(importer, "create_engine", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(importer, "init_db_for_engine", lambda _engine: None)
    monkeypatch.setattr(
        importer, "sessionmaker", lambda **_kwargs: lambda: _FakeSession()
    )
    monkeypatch.setattr(importer, "load_ontology_graph", lambda: (Graph(), None))
    monkeypatch.setattr(
        importer, "DatabaseHierarchyStore", lambda _db: _CountingHierarchyStore()
    )
    monkeypatch.setattr(
        importer,
        "IndicatorStore",
        lambda db=None: _CountingIndicatorStore({"urn:sds:reg:test:water"}),
    )

    def _database_value_store(_db, *, tenant_id=None):
        captured_store["tenant_id"] = tenant_id
        return fake_store

    monkeypatch.setattr(importer, "DatabaseValueStore", _database_value_store)
    monkeypatch.setattr(
        importer, "UnitConverter", lambda db_session=None: _PassthroughConverter()
    )

    imported = importer.import_values_csv(
        csv_path=csv_path,
        db_url="postgresql://example",
        default_entity=None,
        created_by="pytest",
        batch_size=10,
        tenant_id="tenant-explicit",
    )

    assert imported == 2
    assert captured_store == {"tenant_id": "tenant-explicit"}
    fake_store.save.assert_called_once()
    saved_records = fake_store.save.call_args.kwargs["records"]
    assert len(saved_records) == 2
    assert fake_store.save.call_args.kwargs["return_responses"] is False
    fake_store.bulk_create.assert_not_called()


def test_cli_values_dry_run_uses_strict_db_preparation_without_writes(
    tmp_path: Path, monkeypatch
):
    csv_path = tmp_path / "values.csv"
    csv_path.write_text(
        "concept,entity,period,external_key,value,unit,value_type\n"
        "urn:sds:reg:test:water,madrid_plant,2026-12-31,erp:one,1,m3,numeric\n",
        encoding="utf-8",
    )

    class _FakeSession:
        def close(self):
            pass

    fake_store = MagicMock()
    monkeypatch.setattr(importer, "create_engine", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(importer, "init_db_for_engine", lambda _engine: None)
    monkeypatch.setattr(
        importer, "sessionmaker", lambda **_kwargs: lambda: _FakeSession()
    )
    monkeypatch.setattr(importer, "load_ontology_graph", lambda: (Graph(), None))
    monkeypatch.setattr(
        importer, "DatabaseHierarchyStore", lambda _db: _CountingHierarchyStore()
    )
    monkeypatch.setattr(
        importer,
        "IndicatorStore",
        lambda db=None: _CountingIndicatorStore({"urn:sds:reg:test:water"}),
    )
    monkeypatch.setattr(importer, "DatabaseValueStore", lambda _db: fake_store)
    monkeypatch.setattr(
        importer, "UnitConverter", lambda db_session=None: _PassthroughConverter()
    )

    validated = importer.validate_values_csv_import(
        csv_path=csv_path,
        db_url="postgresql://example",
        default_entity=None,
        batch_size=10,
    )

    assert validated == 1
    fake_store.save.assert_not_called()
    fake_store.bulk_create.assert_not_called()


def test_cli_values_dry_run_accepts_same_package_register_overlay(
    tmp_path: Path, monkeypatch
):
    csv_path = tmp_path / "values.csv"
    csv_path.write_text(
        "concept,entity,period,external_key,value,unit,value_type\n"
        "urn:sds:reg:test:package_only,madrid_plant,2026-12-31,erp:one,1,m3,numeric\n",
        encoding="utf-8",
    )

    class _FakeSession:
        def close(self):
            pass

    fake_store = MagicMock()
    monkeypatch.setattr(importer, "create_engine", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(importer, "init_db_for_engine", lambda _engine: None)
    monkeypatch.setattr(
        importer, "sessionmaker", lambda **_kwargs: lambda: _FakeSession()
    )
    monkeypatch.setattr(importer, "load_ontology_graph", lambda: (Graph(), None))
    monkeypatch.setattr(
        importer, "DatabaseHierarchyStore", lambda _db: _CountingHierarchyStore()
    )
    monkeypatch.setattr(
        importer, "IndicatorStore", lambda db=None: _CountingIndicatorStore(set())
    )
    monkeypatch.setattr(importer, "DatabaseValueStore", lambda _db: fake_store)
    monkeypatch.setattr(
        importer, "UnitConverter", lambda db_session=None: _PassthroughConverter()
    )

    validated = importer.validate_values_csv_import(
        csv_path=csv_path,
        db_url="postgresql://example",
        default_entity=None,
        batch_size=10,
        known_register_identifiers={"urn:sds:reg:test:package_only"},
    )

    assert validated == 1
    fake_store.save.assert_not_called()
    fake_store.bulk_create.assert_not_called()


def test_cli_values_dry_run_known_register_overlay_stays_exact_scope(
    tmp_path: Path, monkeypatch
):
    csv_path = tmp_path / "values.csv"
    csv_path.write_text(
        "concept,entity,period,external_key,value,unit,value_type\n"
        "urn:sds:reg:test:missing,madrid_plant,2026-12-31,erp:one,1,m3,numeric\n",
        encoding="utf-8",
    )

    class _FakeSession:
        def close(self):
            pass

    monkeypatch.setattr(importer, "create_engine", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(importer, "init_db_for_engine", lambda _engine: None)
    monkeypatch.setattr(
        importer, "sessionmaker", lambda **_kwargs: lambda: _FakeSession()
    )
    monkeypatch.setattr(importer, "load_ontology_graph", lambda: (Graph(), None))
    monkeypatch.setattr(
        importer, "DatabaseHierarchyStore", lambda _db: _CountingHierarchyStore()
    )
    monkeypatch.setattr(
        importer, "IndicatorStore", lambda db=None: _CountingIndicatorStore(set())
    )
    monkeypatch.setattr(importer, "DatabaseValueStore", lambda _db: MagicMock())
    monkeypatch.setattr(
        importer, "UnitConverter", lambda db_session=None: _PassthroughConverter()
    )

    with pytest.raises(ValueIngestError, match="Unknown concept"):
        importer.validate_values_csv_import(
            csv_path=csv_path,
            db_url="postgresql://example",
            default_entity=None,
            batch_size=10,
            known_register_identifiers={"urn:sds:reg:test:other"},
        )


def test_cli_values_dry_run_accepts_same_package_calculation_value_concept(
    tmp_path: Path, monkeypatch
):
    csv_path = tmp_path / "values.csv"
    csv_path.write_text(
        "concept,entity,period,external_key,value,unit,value_type\n"
        "fixture_scope1_stationary_tco2e,madrid_plant,2026-12-31,erp:one,1,tCO2e,numeric\n",
        encoding="utf-8",
    )

    class _FakeSession:
        def close(self):
            pass

    fake_store = MagicMock()
    monkeypatch.setattr(importer, "create_engine", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(importer, "init_db_for_engine", lambda _engine: None)
    monkeypatch.setattr(
        importer, "sessionmaker", lambda **_kwargs: lambda: _FakeSession()
    )
    monkeypatch.setattr(importer, "load_ontology_graph", lambda: (Graph(), None))
    monkeypatch.setattr(
        importer, "DatabaseHierarchyStore", lambda _db: _CountingHierarchyStore()
    )
    monkeypatch.setattr(
        importer, "IndicatorStore", lambda db=None: _CountingIndicatorStore(set())
    )
    monkeypatch.setattr(importer, "DatabaseValueStore", lambda _db: fake_store)
    monkeypatch.setattr(
        importer, "UnitConverter", lambda db_session=None: _PassthroughConverter()
    )

    validated = importer.validate_values_csv_import(
        csv_path=csv_path,
        db_url="postgresql://example",
        default_entity=None,
        batch_size=10,
        known_calculation_value_concepts={"fixture_scope1_stationary_tco2e"},
    )

    assert validated == 1
    fake_store.save.assert_not_called()


def test_cli_values_dry_run_rejects_missing_indicator_in_strict_path(
    tmp_path: Path, monkeypatch
):
    csv_path = tmp_path / "values.csv"
    csv_path.write_text(
        "concept,entity,period,external_key,value,unit,value_type\n"
        "urn:sds:reg:test:missing,madrid_plant,2026-12-31,erp:one,1,m3,numeric\n",
        encoding="utf-8",
    )

    class _FakeSession:
        def close(self):
            pass

    monkeypatch.setattr(importer, "create_engine", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(importer, "init_db_for_engine", lambda _engine: None)
    monkeypatch.setattr(
        importer, "sessionmaker", lambda **_kwargs: lambda: _FakeSession()
    )
    monkeypatch.setattr(importer, "load_ontology_graph", lambda: (Graph(), None))
    monkeypatch.setattr(
        importer, "DatabaseHierarchyStore", lambda _db: _CountingHierarchyStore()
    )
    monkeypatch.setattr(
        importer, "IndicatorStore", lambda db=None: _CountingIndicatorStore(set())
    )
    monkeypatch.setattr(importer, "DatabaseValueStore", lambda _db: MagicMock())
    monkeypatch.setattr(
        importer, "UnitConverter", lambda db_session=None: _PassthroughConverter()
    )

    with pytest.raises(ValueIngestError):
        importer.validate_values_csv_import(
            csv_path=csv_path,
            db_url="postgresql://example",
            default_entity=None,
            batch_size=10,
        )


def test_execute_value_batch_requests_lightweight_persistence_response():
    calls = []

    class _FakeStore:
        def save(self, **kwargs):
            calls.append(kwargs)
            return [SimpleNamespace(id="persisted-value-1")]

        def bulk_create(self, **kwargs):  # pragma: no cover - not used here
            raise AssertionError("external-key import should use save")

    response = execute_value_batch(
        items=[_value(external_key="erp:batch:one")],
        converter=_PassthroughConverter(),
        store=_FakeStore(),
        hierarchy_store=_CountingHierarchyStore(),
        indicator_store=_CountingIndicatorStore({"urn:sds:reg:test:water"}),
        graph=Graph(),
        created_by="pytest",
        company_id="company_001",
        strict=True,
    )

    assert response.committed is True
    assert response.items[0].value_id == "persisted-value-1"
    assert calls[0]["return_responses"] is False


def test_execute_value_batch_keeps_mixed_persisted_ids_on_original_rows():
    items = [
        _value(external_key="keyed-1", value=Decimal("1")),
        _value(external_key=None, value=Decimal("2")),
        _value(external_key="keyed-3", value=Decimal("3")),
        _value(external_key=None, value=Decimal("4")),
    ]
    expected_ids_by_row = {
        1: "persisted-keyed-1",
        2: "persisted-unkeyed-2",
        3: "persisted-keyed-3",
        4: "persisted-unkeyed-4",
    }

    class _FakeStore:
        def save(self, **kwargs):
            records = kwargs["records"]
            assert [record.external_key for record in records] == [
                "keyed-1",
                None,
                "keyed-3",
                None,
            ]
            assert len({record.value_id for record in records}) == 4
            return [
                SimpleNamespace(id="persisted-keyed-1"),
                SimpleNamespace(id="persisted-unkeyed-2"),
                SimpleNamespace(id="persisted-keyed-3"),
                SimpleNamespace(id="persisted-unkeyed-4"),
            ]

        def bulk_create(self, **_kwargs):  # pragma: no cover - not used here
            raise AssertionError("mixed external-key import should use save")

    response = execute_value_batch(
        items=items,
        converter=_PassthroughConverter(),
        store=_FakeStore(),
        hierarchy_store=_CountingHierarchyStore(),
        indicator_store=_CountingIndicatorStore({"urn:sds:reg:test:water"}),
        graph=Graph(),
        created_by="pytest",
        company_id="company_001",
        strict=True,
    )

    ids_by_row = {item.row_number: item.value_id for item in response.items}
    historical_reordered_ids_by_row = {
        1: "persisted-keyed-1",
        2: "persisted-keyed-3",
        3: "persisted-unkeyed-2",
        4: "persisted-unkeyed-4",
    }

    assert response.committed is True
    assert historical_reordered_ids_by_row != expected_ids_by_row
    assert ids_by_row == expected_ids_by_row
    assert ids_by_row[2] != expected_ids_by_row[3]
    assert ids_by_row[3] != expected_ids_by_row[2]


def test_execute_value_batch_forwards_conversion_engine_identity(monkeypatch):
    explicit_engine = object()
    observed_engines = []

    def fake_prepare_value_record(**kwargs):
        value_data = kwargs["value_data"]
        observed_engines.append(kwargs.get("conversion_engine", "missing"))
        return PreparedValueRecord(
            value_id=kwargs["value_id"],
            concept=value_data.concept,
            entity=value_data.entity,
            period=value_data.period,
            value=value_data.value,
            value_type=value_data.value_type,
            unit=value_data.unit,
            external_key=value_data.external_key,
        )

    class _FakeStore:
        def save(self, **_kwargs):  # pragma: no cover - not used here
            raise AssertionError("batch without external keys should bulk create")

        def bulk_create(self, **kwargs):
            return [SimpleNamespace(id=record.value_id) for record in kwargs["records"]]

    monkeypatch.setattr(
        value_batch_service, "prepare_value_record", fake_prepare_value_record
    )

    execute_value_batch(
        items=[_value(external_key=None), _value(external_key=None)],
        converter=_PassthroughConverter(),
        conversion_engine=explicit_engine,
        store=_FakeStore(),
        hierarchy_store=_CountingHierarchyStore(),
        indicator_store=_CountingIndicatorStore({"urn:sds:reg:test:water"}),
        graph=Graph(),
        created_by="pytest",
        company_id="company_001",
        strict=True,
    )

    execute_value_batch(
        items=[_value(external_key=None)],
        converter=_PassthroughConverter(),
        store=_FakeStore(),
        hierarchy_store=_CountingHierarchyStore(),
        indicator_store=_CountingIndicatorStore({"urn:sds:reg:test:water"}),
        graph=Graph(),
        created_by="pytest",
        company_id="company_001",
        strict=True,
    )

    assert observed_engines[:2] == [explicit_engine, explicit_engine]
    assert observed_engines[2:] == [None]


def test_database_value_store_skips_revision_append_for_unchanged_current_payload(
    monkeypatch,
):
    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.join.return_value = query
    query.filter.return_value = query
    current_revision = SimpleNamespace(
        id="revision-current",
        tenant_id="tenant-a",
        external_key="erp:one",
        source_payload_hash=None,
    )

    store = DatabaseValueStore(db, tenant_id="tenant-a")
    store._revision_store = MagicMock()
    monkeypatch.setattr(
        "src.services.value_store.build_context_hash",
        lambda _identity: "hash-current",
    )

    record = ESGValue(
        id="value-1",
        concept="urn:sds:reg:test:water",
        entity="madrid_plant",
        period=date(2026, 12, 31),
        external_key="erp:one",
        value=Decimal("1.0"),
        value_type="numeric",
        unit="m3",
        value_metadata={
            "standard_release_id": "TEST",
            "standard_datapoint_id": "urn:sds:reg:test:water",
            "reporting_period_id": "FY2026",
            "period_type": "annual",
            "reporting_boundary_id": "operational_control",
        },
    )
    current_revision.source_payload_hash = store._revision_input_for_record(
        record,
        created_by="pytest",
    ).source_payload_hash
    query.all.return_value = [(current_revision, "hash-current")]

    revision = store._append_revision_for_record(record, created_by="pytest")

    assert revision is current_revision
    store._revision_store.append_revision.assert_not_called()


def test_database_value_store_save_batches_current_revision_lookup(monkeypatch):
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", True)
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "legacy")

    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.join.return_value = query
    query.filter.return_value = query
    query.all.return_value = []
    query.first.return_value = None

    store = DatabaseValueStore(db, tenant_id="tenant-a")
    store._repo = MagicMock()
    persisted = [
        ESGValue(
            id="value-1",
            concept="urn:sds:reg:test:water",
            entity="madrid_plant",
            period=date(2026, 12, 31),
            external_key="erp:batch:1",
            value=Decimal("1.0"),
            value_type="numeric",
            unit="m3",
            conversion_applied=False,
            currency_conversion_applied=False,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            value_metadata={
                "standard_release_id": "TEST",
                "standard_datapoint_id": "urn:sds:reg:test:water",
                "reporting_period_id": "FY2026",
                "period_type": "annual",
                "reporting_boundary_id": "operational_control",
            },
        ),
        ESGValue(
            id="value-2",
            concept="urn:sds:reg:test:water",
            entity="madrid_plant",
            period=date(2026, 12, 30),
            external_key="erp:batch:2",
            value=Decimal("2.0"),
            value_type="numeric",
            unit="m3",
            conversion_applied=False,
            currency_conversion_applied=False,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            value_metadata={
                "standard_release_id": "TEST",
                "standard_datapoint_id": "urn:sds:reg:test:water",
                "reporting_period_id": "FY2026-2",
                "period_type": "annual",
                "reporting_boundary_id": "operational_control",
            },
        ),
    ]
    store._repo.save_values.return_value = persisted
    store._revision_store = MagicMock()
    store._revision_store.append_revisions_bulk.return_value = [
        SimpleNamespace(revision=SimpleNamespace(id="revision-1")),
        SimpleNamespace(revision=SimpleNamespace(id="revision-2")),
    ]

    store.save(
        records=[
            PreparedValueRecord(
                value_id="input-1",
                concept="urn:sds:reg:test:water",
                entity="madrid_plant",
                period=date(2026, 12, 31),
                external_key="erp:batch:1",
                value=Decimal("1.0"),
                value_type="numeric",
                unit="m3",
            ),
            PreparedValueRecord(
                value_id="input-2",
                concept="urn:sds:reg:test:water",
                entity="madrid_plant",
                period=date(2026, 12, 30),
                external_key="erp:batch:2",
                value=Decimal("2.0"),
                value_type="numeric",
                unit="m3",
            ),
        ],
        created_by="pytest",
    )

    assert db.query.call_count == 1
    store._revision_store.append_revisions_bulk.assert_called_once()


def test_database_value_store_bulk_create_forwards_refresh_flag(monkeypatch):
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", True)
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "legacy")

    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.join.return_value = query
    query.filter.return_value = query
    query.all.return_value = []

    persisted = [
        ESGValue(
            id="value-1",
            concept="urn:sds:reg:test:water",
            entity="madrid_plant",
            period=date(2026, 12, 31),
            external_key="erp:batch:1",
            value=Decimal("1.0"),
            value_type="numeric",
            unit="m3",
            conversion_applied=False,
            currency_conversion_applied=False,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            value_metadata={
                "standard_release_id": "TEST",
                "standard_datapoint_id": "urn:sds:reg:test:water",
                "reporting_period_id": "FY2026",
                "period_type": "annual",
                "reporting_boundary_id": "operational_control",
            },
        )
    ]
    store = DatabaseValueStore(db, tenant_id="tenant-a")
    store._repo = MagicMock()
    store._repo.create_values.return_value = persisted
    store._revision_store = MagicMock()
    store._revision_store.append_revisions_bulk.return_value = [
        SimpleNamespace(revision=SimpleNamespace(id="revision-1"))
    ]

    store.bulk_create(
        records=[
            PreparedValueRecord(
                value_id="input-1",
                concept="urn:sds:reg:test:water",
                entity="madrid_plant",
                period=date(2026, 12, 31),
                external_key="erp:batch:1",
                value=Decimal("1.0"),
                value_type="numeric",
                unit="m3",
            )
        ],
        created_by="pytest",
        refresh=False,
    )

    store._revision_store.append_revisions_bulk.assert_called_once()
    assert (
        store._revision_store.append_revisions_bulk.call_args.kwargs["refresh"] is False
    )


def test_database_value_store_save_disables_commit_expiration_without_refresh(
    monkeypatch,
):
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", True)
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "legacy")

    db = MagicMock()
    db.expire_on_commit = True
    query = MagicMock()
    db.query.return_value = query
    query.join.return_value = query
    query.filter.return_value = query
    query.all.return_value = []

    commit_expire_flags = []

    def capture_commit():
        commit_expire_flags.append(db.expire_on_commit)

    db.commit.side_effect = capture_commit

    persisted = [
        ESGValue(
            id="value-1",
            concept="urn:sds:reg:test:water",
            entity="madrid_plant",
            period=date(2026, 12, 31),
            external_key="erp:batch:1",
            value=Decimal("1.0"),
            value_type="numeric",
            unit="m3",
            conversion_applied=False,
            currency_conversion_applied=False,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            value_metadata={
                "standard_release_id": "TEST",
                "standard_datapoint_id": "urn:sds:reg:test:water",
                "reporting_period_id": "FY2026",
                "period_type": "annual",
                "reporting_boundary_id": "operational_control",
            },
        )
    ]
    store = DatabaseValueStore(db, tenant_id="tenant-a")
    store._repo = MagicMock()
    store._repo.save_values.return_value = persisted
    store._revision_store = MagicMock()
    store._revision_store.append_revisions_bulk.return_value = [
        SimpleNamespace(revision=SimpleNamespace(id="revision-1"))
    ]

    store.save(
        records=[
            PreparedValueRecord(
                value_id="input-1",
                concept="urn:sds:reg:test:water",
                entity="madrid_plant",
                period=date(2026, 12, 31),
                external_key="erp:batch:1",
                value=Decimal("1.0"),
                value_type="numeric",
                unit="m3",
            )
        ],
        created_by="pytest",
        refresh=False,
    )

    assert commit_expire_flags == [False]
    assert db.expire_on_commit is True
