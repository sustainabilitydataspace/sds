from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from rdflib import Graph

from src.api.models import (
    ValueBulkImportRequest,
    ValueBulkImportResponse,
    ValueBulkImportRowResult,
    ValueCreate,
    ValueImportStatus,
)
from src.api.routers import values
from src.auth.models import Permission, User, UserRole


class _DatabaseSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.events: list[str] = []

    def commit(self) -> None:
        self.commits += 1
        self.events.append("commit")

    def rollback(self) -> None:
        self.rollbacks += 1
        self.events.append("rollback")


class _DatabaseStore:
    def __init__(self, db: _DatabaseSession) -> None:
        self._db = db


class _IdempotencyStore:
    def __init__(
        self,
        db: _DatabaseSession | None = None,
        *,
        fail_completion: bool = False,
    ) -> None:
        if db is not None:
            self._db = db
        self.fail_completion = fail_completion
        self.claim_calls: list[dict] = []
        self.complete_calls: list[dict] = []
        self.abandon_calls: list[dict] = []

    def claim(self, **kwargs):
        self.claim_calls.append(kwargs)
        if hasattr(self, "_db"):
            self._db.events.append("claim")
        return values.IdempotencyClaim(record_id=17)

    def complete(self, **kwargs) -> None:
        self.complete_calls.append(kwargs)
        if hasattr(self, "_db"):
            self._db.events.append("complete")
        if self.fail_completion:
            raise RuntimeError("completion failed")

    def abandon(self, **kwargs) -> None:
        self.abandon_calls.append(kwargs)
        if hasattr(self, "_db"):
            self._db.events.append("abandon")


class _CsvUpload:
    filename = "values.csv"

    def __init__(self) -> None:
        self._content = (
            b"concept,entity,period,value,unit\n"
            b"syg:Water_Cooling,plant-1,2026-08-31,1.5,m3\n"
        )
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise AssertionError("CSV upload fake requires bounded read(size)")
        if size == 0:
            return b""
        end = min(self._offset + size, len(self._content))
        chunk = self._content[self._offset : end]
        self._offset = end
        return chunk


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


def _value() -> ValueCreate:
    return ValueCreate(
        concept="syg:Water_Cooling",
        entity="plant-1",
        period=date(2026, 8, 31),
        value=1.5,
        unit="m3",
    )


def _batch_response(*, committed: bool = True) -> ValueBulkImportResponse:
    status = ValueImportStatus.ACCEPTED if committed else ValueImportStatus.REJECTED
    return ValueBulkImportResponse(
        total_rows=1,
        accepted_rows=int(committed),
        rejected_rows=int(not committed),
        committed=committed,
        items=[
            ValueBulkImportRowResult(
                row_number=1,
                status=status,
                concept="syg:Water_Cooling",
                entity="plant-1",
                period=date(2026, 8, 31),
                unit="m3",
                message="persisted" if committed else "rejected",
            )
        ],
    )


async def _call_endpoint(
    endpoint: str,
    *,
    db,
    store,
    idempotency_store,
    idempotency_key: str | None,
):
    common = {
        "converter": object(),
        "store": store,
        "idempotency_store": idempotency_store,
        "hierarchy_store": object(),
        "db": db,
        "graph": Graph(),
        "current_user": _user(),
        "idempotency_key": idempotency_key,
    }
    if endpoint == "create":
        return await values.create_value(value_data=_value(), **common)
    if endpoint == "json":
        return await values.import_values(
            import_data=ValueBulkImportRequest(items=[_value()]), **common
        )
    return await values.import_values_csv(
        file=_CsvUpload(), default_entity=None, **common
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["create", "json", "csv"])
async def test_database_keyed_success_is_one_endpoint_owned_transaction(
    monkeypatch, endpoint
):
    db = _DatabaseSession()
    store = _DatabaseStore(db)
    idempotency_store = _IdempotencyStore(db)
    mutation_calls = []

    monkeypatch.setattr(values, "build_conversion_engine", lambda *_args: object())
    monkeypatch.setattr(
        values,
        "load_values_from_handle",
        lambda *_args, **_kwargs: [_value()],
    )

    if endpoint == "create":
        response = SimpleNamespace(model_dump=lambda **_kwargs: {"id": "value-1"})

        def mutate(**kwargs):
            mutation_calls.append(kwargs)
            db.events.append("mutation")
            return response

        monkeypatch.setattr(values, "ingest_value", mutate)
    else:
        response = _batch_response()

        def mutate(**kwargs):
            mutation_calls.append(kwargs)
            db.events.append("mutation")
            return response

        monkeypatch.setattr(values, "execute_value_batch", mutate)

    result = await _call_endpoint(
        endpoint,
        db=db,
        store=store,
        idempotency_store=idempotency_store,
        idempotency_key="idem-1",
    )

    assert result is response
    assert idempotency_store.claim_calls[0]["commit"] is False
    assert mutation_calls[0]["commit"] is False
    assert idempotency_store.complete_calls[0]["commit"] is False
    assert db.commits == 1
    assert db.rollbacks == 0
    assert db.events == ["claim", "mutation", "complete", "commit"]
    assert idempotency_store.abandon_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["create", "json", "csv"])
@pytest.mark.parametrize("failure_stage", ["after_claim", "mutation", "completion"])
async def test_database_keyed_failure_rolls_back_without_abandon(
    monkeypatch, endpoint, failure_stage
):
    db = _DatabaseSession()
    store = _DatabaseStore(db)
    idempotency_store = _IdempotencyStore(
        db, fail_completion=failure_stage == "completion"
    )

    def conversion_engine(*_args):
        if failure_stage == "after_claim":
            raise RuntimeError("after claim failed")
        return object()

    def mutate(**_kwargs):
        if failure_stage == "mutation":
            raise RuntimeError("mutation failed")
        if endpoint == "create":
            return SimpleNamespace(model_dump=lambda **_kwargs: {"id": "value-1"})
        return _batch_response()

    monkeypatch.setattr(values, "build_conversion_engine", conversion_engine)
    monkeypatch.setattr(
        values,
        "load_values_from_handle",
        lambda *_args, **_kwargs: [_value()],
    )
    monkeypatch.setattr(values, "ingest_value", mutate)
    monkeypatch.setattr(values, "execute_value_batch", mutate)

    expected_exception = HTTPException if endpoint == "create" else RuntimeError
    with pytest.raises(expected_exception):
        await _call_endpoint(
            endpoint,
            db=db,
            store=store,
            idempotency_store=idempotency_store,
            idempotency_key="idem-1",
        )

    assert idempotency_store.claim_calls[0]["commit"] is False
    assert db.commits == 0
    assert db.rollbacks == 1
    assert idempotency_store.abandon_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["json", "csv"])
async def test_database_keyed_batch_rejection_rolls_back_claim(monkeypatch, endpoint):
    db = _DatabaseSession()
    store = _DatabaseStore(db)
    idempotency_store = _IdempotencyStore(db)
    mutation_calls = []

    monkeypatch.setattr(values, "build_conversion_engine", lambda *_args: object())
    monkeypatch.setattr(
        values,
        "load_values_from_handle",
        lambda *_args, **_kwargs: [_value()],
    )

    def reject(**kwargs):
        mutation_calls.append(kwargs)
        return _batch_response(committed=False)

    monkeypatch.setattr(values, "execute_value_batch", reject)

    response = await _call_endpoint(
        endpoint,
        db=db,
        store=store,
        idempotency_store=idempotency_store,
        idempotency_key="idem-1",
    )

    assert response.status_code == 400
    assert mutation_calls[0]["commit"] is False
    assert db.commits == 0
    assert db.rollbacks == 1
    assert idempotency_store.complete_calls == []
    assert idempotency_store.abandon_calls == []


@pytest.mark.asyncio
async def test_database_keyed_csv_contract_error_rejects_before_database_claim(
    monkeypatch,
):
    db = _DatabaseSession()
    store = _DatabaseStore(db)
    idempotency_store = _IdempotencyStore(db)

    def reject_csv(*_args, **_kwargs):
        raise values.ValueCsvContractError("invalid CSV contract")

    monkeypatch.setattr(values, "load_values_from_handle", reject_csv)

    with pytest.raises(HTTPException) as exc:
        await _call_endpoint(
            "csv",
            db=db,
            store=store,
            idempotency_store=idempotency_store,
            idempotency_key="idem-1",
        )

    assert exc.value.status_code == 400
    assert idempotency_store.claim_calls == []
    assert idempotency_store.complete_calls == []
    assert idempotency_store.abandon_calls == []
    assert db.commits == 0
    assert db.rollbacks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["create", "json", "csv"])
async def test_in_memory_keyed_branch_keeps_default_helper_handling(
    monkeypatch, endpoint
):
    idempotency_store = _IdempotencyStore()
    mutation_calls = []

    monkeypatch.setattr(
        values,
        "load_values_from_handle",
        lambda *_args, **_kwargs: [_value()],
    )

    def mutate(**kwargs):
        mutation_calls.append(kwargs)
        if endpoint == "create":
            return SimpleNamespace(model_dump=lambda **_kwargs: {"id": "value-1"})
        return _batch_response()

    monkeypatch.setattr(values, "ingest_value", mutate)
    monkeypatch.setattr(values, "execute_value_batch", mutate)

    await _call_endpoint(
        endpoint,
        db=None,
        store=object(),
        idempotency_store=idempotency_store,
        idempotency_key="idem-1",
    )

    assert "commit" not in idempotency_store.claim_calls[0]
    assert "commit" not in mutation_calls[0]
    assert "commit" not in idempotency_store.complete_calls[0]
    assert idempotency_store.abandon_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["create", "json", "csv"])
async def test_in_memory_keyed_failure_keeps_abandon_handling(monkeypatch, endpoint):
    idempotency_store = _IdempotencyStore()

    monkeypatch.setattr(
        values,
        "load_values_from_handle",
        lambda *_args, **_kwargs: [_value()],
    )

    def fail_mutation(**_kwargs):
        raise RuntimeError("mutation failed")

    monkeypatch.setattr(values, "ingest_value", fail_mutation)
    monkeypatch.setattr(values, "execute_value_batch", fail_mutation)

    expected_exception = HTTPException if endpoint == "create" else RuntimeError
    with pytest.raises(expected_exception):
        await _call_endpoint(
            endpoint,
            db=None,
            store=object(),
            idempotency_store=idempotency_store,
            idempotency_key="idem-1",
        )

    assert "commit" not in idempotency_store.claim_calls[0]
    assert len(idempotency_store.abandon_calls) == 1
    assert "commit" not in idempotency_store.abandon_calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["create", "json", "csv"])
async def test_database_no_key_branch_keeps_default_mutation_handling(
    monkeypatch, endpoint
):
    db = _DatabaseSession()
    store = _DatabaseStore(db)
    idempotency_store = _IdempotencyStore(db)
    mutation_calls = []

    monkeypatch.setattr(values, "build_conversion_engine", lambda *_args: object())
    monkeypatch.setattr(
        values,
        "load_values_from_handle",
        lambda *_args, **_kwargs: [_value()],
    )

    def mutate(**kwargs):
        mutation_calls.append(kwargs)
        if endpoint == "create":
            return SimpleNamespace(model_dump=lambda **_kwargs: {"id": "value-1"})
        return _batch_response()

    monkeypatch.setattr(values, "ingest_value", mutate)
    monkeypatch.setattr(values, "execute_value_batch", mutate)

    await _call_endpoint(
        endpoint,
        db=db,
        store=store,
        idempotency_store=idempotency_store,
        idempotency_key=None,
    )

    assert idempotency_store.claim_calls == []
    assert idempotency_store.complete_calls == []
    assert "commit" not in mutation_calls[0]
    assert db.commits == 0
    assert db.rollbacks == 0


@pytest.mark.asyncio
async def test_csv_decoding_failure_happens_before_database_claim():
    class _InvalidUtf8Upload:
        filename = "invalid.csv"

        def __init__(self) -> None:
            self._content = b"\xff\xfe"
            self._offset = 0

        async def read(self, size: int = -1) -> bytes:
            if size < 0:
                raise AssertionError("CSV upload fake requires bounded read(size)")
            if size == 0:
                return b""
            end = min(self._offset + size, len(self._content))
            chunk = self._content[self._offset : end]
            self._offset = end
            return chunk

    db = _DatabaseSession()
    store = _DatabaseStore(db)
    idempotency_store = _IdempotencyStore(db)

    with pytest.raises(HTTPException) as exc:
        await values.import_values_csv(
            file=_InvalidUtf8Upload(),
            default_entity=None,
            converter=object(),
            store=store,
            idempotency_store=idempotency_store,
            hierarchy_store=object(),
            db=db,
            graph=Graph(),
            current_user=_user(),
            idempotency_key="idem-1",
        )

    assert exc.value.status_code == 400
    assert idempotency_store.claim_calls == []
    assert db.commits == 0
    assert db.rollbacks == 0
