from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from rdflib import Graph

from scripts import import_calculation_contracts, import_values_csv


@pytest.mark.parametrize(
    ("factory", "module_name", "class_name"),
    [
        (
            "DatabaseHierarchyStore",
            "src.services.hierarchy_store",
            "DatabaseHierarchyStore",
        ),
        ("IndicatorStore", "src.services.indicator_store", "IndicatorStore"),
        ("DatabaseValueStore", "src.services.value_store", "DatabaseValueStore"),
    ],
)
def test_values_import_store_factory_defers_module_import(
    monkeypatch, factory, module_name, class_name
):
    marker = object()

    def fake_class(*args, **kwargs):
        return args, kwargs

    monkeypatch.setitem(
        sys.modules, module_name, SimpleNamespace(**{class_name: fake_class})
    )

    assert getattr(import_values_csv, factory)(marker, tenant_id="tenant") == (
        (marker,),
        {"tenant_id": "tenant"},
    )


class _FakeSession:
    def close(self):
        pass


class _HierarchyStore:
    def list(self, **_kwargs):
        return [SimpleNamespace(levels=[SimpleNamespace(id="madrid_plant")])], 1


class _IndicatorStore:
    def get_by_identifier(self, identifier: str):
        return (
            {"identifier": identifier}
            if identifier == "urn:sds:reg:test:water"
            else None
        )

    def get_calculation_value_concept(self, _concept: str):
        return None


class _PassthroughConverter:
    def normalize_unit_symbol(self, symbol: str) -> str:
        return symbol

    def convert(self, *_args, **_kwargs):  # pragma: no cover - should not be reached
        raise AssertionError("unexpected conversion")


def test_calculation_contract_cli_package_dry_run_does_not_initialize_database(
    tmp_path: Path, monkeypatch
) -> None:
    register_csv = tmp_path / "sds_dataset_register.csv"
    contract_json = tmp_path / "sds_calculation_contract.json"
    register_csv.write_text(
        "identifier,title\nurn:sds:reg:test:water,Water\n",
        encoding="utf-8",
    )
    contract_json.write_text(
        (
            '{"contract_version":"1.0","nodes":[{'
            '"node_id":"n1",'
            '"model_id":"m1",'
            '"indicator_identifier":"urn:sds:reg:test:water",'
            '"exposure":"public_register",'
            '"runtime_status":"audit_only"'
            "}]}"
        ),
        encoding="utf-8",
    )

    def fail_create_engine(*_args, **_kwargs):
        raise AssertionError("package dry-run must not create a database engine")

    def fail_init_db(*_args, **_kwargs):
        raise AssertionError(
            "package dry-run must not initialize or migrate the database"
        )

    monkeypatch.setattr(
        import_calculation_contracts, "create_engine", fail_create_engine
    )
    monkeypatch.setattr(
        import_calculation_contracts, "init_db_for_engine", fail_init_db
    )

    result = import_calculation_contracts.main(
        [
            "--json",
            str(contract_json),
            "--dry-run",
            "--known-register-csv",
            str(register_csv),
        ]
    )

    assert result == 0


def test_values_cli_dry_run_does_not_initialize_or_migrate_database(
    tmp_path: Path, monkeypatch
) -> None:
    values_csv = tmp_path / "sds_values.csv"
    values_csv.write_text(
        "concept,entity,period,external_key,value,unit,value_type\n"
        "urn:sds:reg:test:water,madrid_plant,2026-12-31,erp:one,1,m3,numeric\n",
        encoding="utf-8",
    )
    fake_store = MagicMock()
    fake_db = _FakeSession()
    converter = _PassthroughConverter()
    conversion_engine = object()
    factory_calls = []
    prepare_calls = []

    def fail_init_db(*_args, **_kwargs):
        raise AssertionError("dry-run must not initialize or migrate the database")

    def capture_unit_converter(*, db_session=None):
        assert db_session is fake_db
        return converter

    def capture_build_conversion_engine(db_session, unit_converter):
        factory_calls.append((db_session, unit_converter))
        return conversion_engine

    def capture_prepare_value_records(*_args, **kwargs):
        prepare_calls.append(kwargs)
        return [SimpleNamespace(id="prepared-value")]

    monkeypatch.setattr(
        import_values_csv, "create_engine", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(import_values_csv, "init_db_for_engine", fail_init_db)
    monkeypatch.setattr(
        import_values_csv,
        "sessionmaker",
        lambda **_kwargs: lambda: fake_db,
    )
    monkeypatch.setattr(
        import_values_csv, "load_ontology_graph", lambda: (Graph(), None)
    )
    monkeypatch.setattr(
        import_values_csv, "DatabaseHierarchyStore", lambda _db: _HierarchyStore()
    )
    monkeypatch.setattr(
        import_values_csv, "IndicatorStore", lambda db=None: _IndicatorStore()
    )
    monkeypatch.setattr(import_values_csv, "DatabaseValueStore", lambda _db: fake_store)
    monkeypatch.setattr(
        import_values_csv,
        "UnitConverter",
        capture_unit_converter,
    )
    monkeypatch.setattr(
        import_values_csv,
        "build_conversion_engine",
        capture_build_conversion_engine,
        raising=False,
    )
    monkeypatch.setattr(
        import_values_csv, "prepare_value_records", capture_prepare_value_records
    )

    result = import_values_csv.main(
        [
            "--csv",
            str(values_csv),
            "--dry-run",
            "--db-url",
            "sqlite://",
        ]
    )

    assert result == 0
    assert factory_calls == [(fake_db, converter)]
    assert len(prepare_calls) == 1
    assert prepare_calls[0]["conversion_engine"] is conversion_engine
    assert prepare_calls[0]["company_id"] is None
    fake_store.save.assert_not_called()


def test_values_cli_real_import_missing_tenant_aborts_before_create_engine(
    tmp_path: Path, monkeypatch
) -> None:
    values_csv = tmp_path / "sds_values.csv"
    values_csv.write_text(
        "concept,entity,period,external_key,value,unit,value_type\n"
        "urn:sds:reg:test:water,madrid_plant,2026-12-31,erp:one,1,m3,numeric\n",
        encoding="utf-8",
    )
    create_engine_called = False

    def fail_create_engine(*_args, **_kwargs):
        nonlocal create_engine_called
        create_engine_called = True
        raise AssertionError("missing tenant must abort before create_engine")

    monkeypatch.setattr(import_values_csv, "create_engine", fail_create_engine)

    result = import_values_csv.main(
        [
            "--csv",
            str(values_csv),
        ]
    )

    assert result == 1
    assert create_engine_called is False


def test_values_cli_real_import_placeholder_tenant_aborts_before_create_engine(
    tmp_path: Path, monkeypatch
) -> None:
    values_csv = tmp_path / "sds_values.csv"
    values_csv.write_text(
        "concept,entity,period,external_key,value,unit,value_type\n"
        "urn:sds:reg:test:water,madrid_plant,2026-12-31,erp:one,1,m3,numeric\n",
        encoding="utf-8",
    )
    create_engine_called = False

    def fail_create_engine(*_args, **_kwargs):
        nonlocal create_engine_called
        create_engine_called = True
        raise AssertionError("placeholder tenant must abort before create_engine")

    monkeypatch.setattr(import_values_csv, "create_engine", fail_create_engine)

    result = import_values_csv.main(
        [
            "--csv",
            str(values_csv),
            "--tenant-id",
            "string",
        ]
    )

    assert result == 1
    assert create_engine_called is False


def test_values_cli_real_import_valid_tenant_reaches_store_and_preparation(
    tmp_path: Path, monkeypatch
) -> None:
    values_csv = tmp_path / "sds_values.csv"
    values_csv.write_text(
        "concept,entity,period,external_key,value,unit,value_type\n"
        "urn:sds:reg:test:water,madrid_plant,2026-12-31,erp:one,1,m3,numeric\n",
        encoding="utf-8",
    )
    fake_store = MagicMock()
    fake_db = _FakeSession()
    converter = _PassthroughConverter()
    conversion_engine = object()
    factory_calls = []
    captured_store_tenant = None
    captured_prepare_company = None
    captured_prepare_engine = None

    def capture_value_store(_db, *, tenant_id=None):
        nonlocal captured_store_tenant
        assert _db is fake_db
        captured_store_tenant = tenant_id
        return fake_store

    def capture_unit_converter(*, db_session=None):
        assert db_session is fake_db
        return converter

    def capture_build_conversion_engine(db_session, unit_converter):
        factory_calls.append((db_session, unit_converter))
        return conversion_engine

    def capture_prepare_value_records(*_args, **kwargs):
        nonlocal captured_prepare_company, captured_prepare_engine
        captured_prepare_company = kwargs["company_id"]
        captured_prepare_engine = kwargs["conversion_engine"]
        return [SimpleNamespace(id="prepared-value")]

    monkeypatch.setattr(
        import_values_csv, "create_engine", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(import_values_csv, "init_db_for_engine", lambda _engine: None)
    monkeypatch.setattr(
        import_values_csv,
        "sessionmaker",
        lambda **_kwargs: lambda: fake_db,
    )
    monkeypatch.setattr(
        import_values_csv, "load_ontology_graph", lambda: (Graph(), None)
    )
    monkeypatch.setattr(
        import_values_csv, "DatabaseHierarchyStore", lambda _db: _HierarchyStore()
    )
    monkeypatch.setattr(
        import_values_csv, "IndicatorStore", lambda db=None: _IndicatorStore()
    )
    monkeypatch.setattr(import_values_csv, "DatabaseValueStore", capture_value_store)
    monkeypatch.setattr(
        import_values_csv,
        "UnitConverter",
        capture_unit_converter,
    )
    monkeypatch.setattr(
        import_values_csv,
        "build_conversion_engine",
        capture_build_conversion_engine,
        raising=False,
    )
    monkeypatch.setattr(
        import_values_csv, "prepare_value_records", capture_prepare_value_records
    )

    result = import_values_csv.main(
        [
            "--csv",
            str(values_csv),
            "--tenant-id",
            "tenant-explicit",
            "--db-url",
            "sqlite://",
        ]
    )

    assert result == 0
    assert factory_calls == [(fake_db, converter)]
    assert captured_store_tenant == "tenant-explicit"
    assert captured_prepare_company == "tenant-explicit"
    assert captured_prepare_engine is conversion_engine
    fake_store.save.assert_called_once()
