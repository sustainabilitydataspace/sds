from datetime import date, datetime, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest
from rdflib import Graph

from src.api.models import ValueBulkImportRequest, ValueCreate
from src.api.routers import values
from src.auth.models import Permission, User, UserRole


def _user() -> User:
    now = datetime.now(timezone.utc)
    return User(
        id="user-1",
        username="user-1",
        email="user-1@example.com",
        full_name=None,
        company_id="tenant-a",
        role=UserRole.DATA_MANAGER,
        is_active=True,
        created_at=now,
        updated_at=now,
        permissions=list(Permission),
    )


def _value_data() -> ValueCreate:
    return ValueCreate(
        concept="syg:Energy_Spend",
        entity="plant-1",
        period=date(2024, 3, 31),
        value=100,
        unit="kWh",
        currency="USD",
        expected_currency="EUR",
    )


def _batch_response():
    return SimpleNamespace(
        committed=True,
        model_dump=lambda **_kwargs: {"committed": True},
    )


class _CsvUpload:
    filename = "values.csv"

    def __init__(self):
        self._stream = BytesIO(
            b"concept,entity,period,value,unit,currency,expected_currency,"
            b"fx_policy_id,value_date\n"
            b"syg:Energy_Spend,plant-1,2024-03-31,100,kWh,USD,EUR,"
            b"monthly-average,2024-03-31\n"
        )

    async def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


def _csv_upload() -> _CsvUpload:
    return _CsvUpload()


@pytest.mark.asyncio
async def test_create_value_builds_request_scoped_conversion_engine(monkeypatch):
    db = object()
    converter = object()
    conversion_engine = object()
    response = SimpleNamespace(model_dump=lambda **_kwargs: {"id": "value-1"})
    factory_calls = []
    ingest_calls = []

    def fake_build_conversion_engine(db_session, unit_converter):
        factory_calls.append((db_session, unit_converter))
        return conversion_engine

    def fake_ingest_value(**kwargs):
        ingest_calls.append(kwargs)
        return response

    monkeypatch.setattr(values, "build_conversion_engine", fake_build_conversion_engine)
    monkeypatch.setattr(values, "ingest_value", fake_ingest_value)

    result = await values.create_value(
        value_data=_value_data(),
        converter=converter,
        store=object(),
        idempotency_store=object(),
        hierarchy_store=object(),
        db=db,
        graph=Graph(),
        current_user=_user(),
        idempotency_key=None,
    )

    assert result is response
    assert factory_calls == [(db, converter)]
    assert len(ingest_calls) == 1
    assert ingest_calls[0]["conversion_engine"] is conversion_engine


@pytest.mark.asyncio
async def test_create_value_log_does_not_include_observation_payload(monkeypatch):
    events = []
    monkeypatch.setattr(
        values,
        "logger",
        SimpleNamespace(info=lambda event, **fields: events.append((event, fields))),
    )
    monkeypatch.setattr(
        values,
        "ingest_value",
        lambda **kwargs: SimpleNamespace(model_dump=lambda **_kw: {"id": "v"}),
    )
    sensitive = "private-observation-canary"
    await values.create_value(
        value_data=_value_data().model_copy(update={"value": sensitive}),
        converter=object(),
        store=object(),
        idempotency_store=object(),
        hierarchy_store=object(),
        db=None,
        graph=Graph(),
        current_user=_user(),
        idempotency_key=None,
    )
    assert events
    assert sensitive not in repr(events)


@pytest.mark.asyncio
async def test_create_value_offline_does_not_build_conversion_engine(monkeypatch):
    response = SimpleNamespace(model_dump=lambda **_kwargs: {"id": "value-1"})
    ingest_calls = []

    def fail_build_conversion_engine(*_args, **_kwargs):
        raise AssertionError("offline create must not build a conversion engine")

    def fake_ingest_value(**kwargs):
        ingest_calls.append(kwargs)
        return response

    monkeypatch.setattr(values, "build_conversion_engine", fail_build_conversion_engine)
    monkeypatch.setattr(values, "ingest_value", fake_ingest_value)

    result = await values.create_value(
        value_data=_value_data(),
        converter=object(),
        store=object(),
        idempotency_store=object(),
        hierarchy_store=object(),
        db=None,
        graph=Graph(),
        current_user=_user(),
        idempotency_key=None,
    )

    assert result is response
    assert len(ingest_calls) == 1
    assert ingest_calls[0]["conversion_engine"] is None


@pytest.mark.asyncio
async def test_import_values_builds_one_request_scoped_conversion_engine(monkeypatch):
    db = object()
    converter = object()
    conversion_engine = object()
    response = _batch_response()
    factory_calls = []
    batch_calls = []

    def fake_build_conversion_engine(db_session, unit_converter):
        factory_calls.append((db_session, unit_converter))
        return conversion_engine

    def fake_execute_value_batch(**kwargs):
        batch_calls.append(kwargs)
        return response

    monkeypatch.setattr(values, "build_conversion_engine", fake_build_conversion_engine)
    monkeypatch.setattr(values, "execute_value_batch", fake_execute_value_batch)

    result = await values.import_values(
        import_data=ValueBulkImportRequest(items=[_value_data()]),
        converter=converter,
        store=object(),
        idempotency_store=object(),
        hierarchy_store=object(),
        db=db,
        graph=Graph(),
        current_user=_user(),
        idempotency_key=None,
    )

    assert result is response
    assert factory_calls == [(db, converter)]
    assert len(batch_calls) == 1
    assert batch_calls[0]["conversion_engine"] is conversion_engine


@pytest.mark.asyncio
async def test_import_values_offline_does_not_build_conversion_engine(monkeypatch):
    response = _batch_response()
    batch_calls = []

    def fail_build_conversion_engine(*_args, **_kwargs):
        raise AssertionError("offline JSON import must not build a conversion engine")

    def fake_execute_value_batch(**kwargs):
        batch_calls.append(kwargs)
        return response

    monkeypatch.setattr(values, "build_conversion_engine", fail_build_conversion_engine)
    monkeypatch.setattr(values, "execute_value_batch", fake_execute_value_batch)

    result = await values.import_values(
        import_data=ValueBulkImportRequest(items=[_value_data()]),
        converter=object(),
        store=object(),
        idempotency_store=object(),
        hierarchy_store=object(),
        db=None,
        graph=Graph(),
        current_user=_user(),
        idempotency_key=None,
    )

    assert result is response
    assert len(batch_calls) == 1
    assert batch_calls[0]["conversion_engine"] is None


@pytest.mark.asyncio
async def test_import_values_csv_builds_one_request_scoped_conversion_engine(
    monkeypatch,
):
    db = object()
    converter = object()
    conversion_engine = object()
    response = _batch_response()
    factory_calls = []
    batch_calls = []

    def fake_build_conversion_engine(db_session, unit_converter):
        factory_calls.append((db_session, unit_converter))
        return conversion_engine

    def fake_execute_value_batch(**kwargs):
        batch_calls.append(kwargs)
        return response

    monkeypatch.setattr(values, "build_conversion_engine", fake_build_conversion_engine)
    monkeypatch.setattr(values, "execute_value_batch", fake_execute_value_batch)

    result = await values.import_values_csv(
        file=_csv_upload(),
        default_entity=None,
        converter=converter,
        store=object(),
        idempotency_store=object(),
        hierarchy_store=object(),
        db=db,
        graph=Graph(),
        current_user=_user(),
        idempotency_key=None,
    )

    assert result is response
    assert factory_calls == [(db, converter)]
    assert len(batch_calls) == 1
    assert batch_calls[0]["conversion_engine"] is conversion_engine


@pytest.mark.asyncio
async def test_import_values_csv_offline_does_not_build_conversion_engine(monkeypatch):
    response = _batch_response()
    batch_calls = []

    def fail_build_conversion_engine(*_args, **_kwargs):
        raise AssertionError("offline CSV import must not build a conversion engine")

    def fake_execute_value_batch(**kwargs):
        batch_calls.append(kwargs)
        return response

    monkeypatch.setattr(values, "build_conversion_engine", fail_build_conversion_engine)
    monkeypatch.setattr(values, "execute_value_batch", fake_execute_value_batch)

    result = await values.import_values_csv(
        file=_csv_upload(),
        default_entity=None,
        converter=object(),
        store=object(),
        idempotency_store=object(),
        hierarchy_store=object(),
        db=None,
        graph=Graph(),
        current_user=_user(),
        idempotency_key=None,
    )

    assert result is response
    assert len(batch_calls) == 1
    assert batch_calls[0]["conversion_engine"] is None
