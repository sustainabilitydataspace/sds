from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routers import values

_CSV_HEADER = b"concept,entity,period,value,unit\n"
_CSV_ROW = b"syg:Water_Cooling,,2026-08-31,1.5,m3\n"


class _Upload:
    filename = "values.csv"

    def __init__(self, content: bytes) -> None:
        self._content = content
        self._offset = 0
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        assert 0 < size <= 1024 * 1024
        self.read_sizes.append(size)
        start = self._offset
        self._offset = min(start + size, len(self._content))
        return self._content[start : self._offset]


class _IdempotencyStore:
    def __init__(self, claim_result=None) -> None:
        self.claim_result = claim_result
        self.claim_calls: list[dict] = []
        self.complete_calls: list[dict] = []
        self.abandon_calls: list[dict] = []

    def claim(self, **kwargs):
        self.claim_calls.append(kwargs)
        return self.claim_result

    def complete(self, **kwargs) -> None:
        self.complete_calls.append(kwargs)

    def abandon(self, **kwargs) -> None:
        self.abandon_calls.append(kwargs)


class _JobStore:
    def __init__(self) -> None:
        self.create_calls: list[dict] = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "id": kwargs["job_id"],
                "source_format": kwargs["source_format"],
            }
        )


class _BackgroundTasks:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def add_task(self, *args, **kwargs) -> None:
        self.calls.append((args, kwargs))


class _Database:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def commit(self) -> None:
        self.actions.append("commit")

    def rollback(self) -> None:
        self.actions.append("rollback")


def _user() -> SimpleNamespace:
    return SimpleNamespace(
        id="user-1",
        username="user-1",
        company_id="tenant-a",
    )


async def _call_sync(upload, idempotency_store, *, db=None):
    return await values.import_values_csv(
        file=upload,
        default_entity="fallback-entity",
        converter=object(),
        store=object(),
        idempotency_store=idempotency_store,
        hierarchy_store=object(),
        db=db if db is not None else object(),
        graph=object(),
        current_user=_user(),
        idempotency_key="idem-1",
    )


async def _call_job(upload, idempotency_store, job_store, background_tasks):
    return await values.import_values_csv_job(
        request=SimpleNamespace(app=object()),
        background_tasks=background_tasks,
        file=upload,
        default_entity="fallback-entity",
        job_store=job_store,
        idempotency_store=idempotency_store,
        current_user=_user(),
        idempotency_key="idem-1",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["sync", "job"])
async def test_csv_byte_limit_rejects_before_admission(monkeypatch, endpoint):
    max_bytes = 1024 * 1024 + 7
    monkeypatch.setattr(values.settings, "value_import_max_bytes", max_bytes)
    upload = _Upload(b"x" * (max_bytes + 1))
    idempotency_store = _IdempotencyStore()
    job_store = _JobStore()
    background_tasks = _BackgroundTasks()
    db = _Database()
    monkeypatch.setattr(
        values,
        "execute_value_batch",
        lambda **_kwargs: pytest.fail("rejected upload reached batch execution"),
    )

    with pytest.raises(HTTPException) as exc_info:
        if endpoint == "sync":
            await _call_sync(upload, idempotency_store, db=db)
        else:
            await _call_job(upload, idempotency_store, job_store, background_tasks)

    assert exc_info.value.status_code == 413
    assert upload.read_sizes
    assert max(upload.read_sizes) <= 1024 * 1024
    assert idempotency_store.claim_calls == []
    assert idempotency_store.complete_calls == []
    assert idempotency_store.abandon_calls == []
    assert job_store.create_calls == []
    assert background_tasks.calls == []
    assert db.actions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["sync", "job"])
async def test_csv_row_limit_rejects_before_admission(monkeypatch, endpoint):
    monkeypatch.setattr(values.settings, "value_import_max_bytes", 4096)
    monkeypatch.setattr(values.settings, "value_import_max_rows", 1)
    upload = _Upload(_CSV_HEADER + _CSV_ROW + _CSV_ROW)
    idempotency_store = _IdempotencyStore()
    job_store = _JobStore()
    background_tasks = _BackgroundTasks()
    db = _Database()
    monkeypatch.setattr(
        values,
        "execute_value_batch",
        lambda **_kwargs: pytest.fail("rejected CSV reached batch execution"),
    )

    with pytest.raises(HTTPException) as exc_info:
        if endpoint == "sync":
            await _call_sync(upload, idempotency_store, db=db)
        else:
            await _call_job(upload, idempotency_store, job_store, background_tasks)

    assert exc_info.value.status_code == 400
    assert "configured row limit of 1" in exc_info.value.detail
    assert idempotency_store.claim_calls == []
    assert idempotency_store.complete_calls == []
    assert idempotency_store.abandon_calls == []
    assert job_store.create_calls == []
    assert background_tasks.calls == []
    assert db.actions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["sync", "job"])
async def test_invalid_utf8_is_400_before_admission(monkeypatch, endpoint):
    monkeypatch.setattr(values.settings, "value_import_max_bytes", 4096)
    upload = _Upload(b"\xff\xfe")
    idempotency_store = _IdempotencyStore()
    job_store = _JobStore()
    background_tasks = _BackgroundTasks()
    db = _Database()

    with pytest.raises(HTTPException) as exc_info:
        if endpoint == "sync":
            await _call_sync(upload, idempotency_store, db=db)
        else:
            await _call_job(upload, idempotency_store, job_store, background_tasks)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Values CSV must be valid UTF-8 text"
    assert idempotency_store.claim_calls == []
    assert idempotency_store.complete_calls == []
    assert idempotency_store.abandon_calls == []
    assert job_store.create_calls == []
    assert background_tasks.calls == []
    assert db.actions == []


@pytest.mark.asyncio
async def test_bounded_valid_sync_csv_reaches_existing_claim_flow(monkeypatch):
    csv_content = _CSV_HEADER + _CSV_ROW
    monkeypatch.setattr(values.settings, "value_import_max_bytes", len(csv_content))
    monkeypatch.setattr(values.settings, "value_import_max_rows", 1)
    replay = values.IdempotencyReplay(status_code=202, body={"replayed": True})
    idempotency_store = _IdempotencyStore(replay)
    upload = _Upload(csv_content)

    response = await _call_sync(upload, idempotency_store)

    assert response.status_code == 202
    assert len(idempotency_store.claim_calls) == 1
    expected_payload = {
        "csv_sha256": values.build_request_hash(csv_content.decode("utf-8")),
        "default_entity": "fallback-entity",
    }
    assert idempotency_store.claim_calls[0]["request_hash"] == (
        values.build_request_hash(expected_payload)
    )


@pytest.mark.asyncio
async def test_bounded_valid_csv_job_reaches_existing_job_flow(monkeypatch):
    csv_content = _CSV_HEADER + _CSV_ROW
    monkeypatch.setattr(values.settings, "value_import_max_bytes", len(csv_content))
    monkeypatch.setattr(values.settings, "value_import_max_rows", 1)
    idempotency_store = _IdempotencyStore(values.IdempotencyClaim(record_id=17))
    job_store = _JobStore()
    background_tasks = _BackgroundTasks()

    response = await _call_job(
        _Upload(csv_content),
        idempotency_store,
        job_store,
        background_tasks,
    )

    assert response is not None
    assert len(idempotency_store.claim_calls) == 1
    assert len(idempotency_store.complete_calls) == 1
    assert len(job_store.create_calls) == 1
    assert job_store.create_calls[0]["request_metadata"] == {
        "row_count": 1,
        "default_entity": "fallback-entity",
    }
    assert len(background_tasks.calls) == 1
    assert background_tasks.calls[0][1]["items"][0].entity == "fallback-entity"
