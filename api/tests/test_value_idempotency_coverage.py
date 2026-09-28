from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import TypedDict
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from src.database.models import ValueIdempotencyKey
from src.database.repositories.value_idempotency_repository import (
    ValueIdempotencyRepository,
)
from src.services.value_idempotency_store import (
    DatabaseValueIdempotencyStore,
    IdempotencyClaim,
    IdempotencyReplay,
    InMemoryValueIdempotencyStore,
    build_request_hash,
    get_value_idempotency_store,
)


class _ClaimIdentity(TypedDict):
    tenant_id: str
    user_id: str
    scope: str
    idempotency_key: str
    record_id: int


def _query_returning(result):
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = result
    return query


def test_in_memory_value_idempotency_claim_replay_conflict_and_abandon() -> None:
    store = InMemoryValueIdempotencyStore()
    request_hash = build_request_hash({"value": 1, "unit": "L"})

    assert (
        store.claim(
            tenant_id="tenant-a",
            user_id="user-1",
            scope="values:create",
            idempotency_key=None,
            request_hash=request_hash,
        )
        is None
    )

    claim = store.claim(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        request_hash=request_hash,
    )
    assert isinstance(claim, IdempotencyClaim)

    with pytest.raises(HTTPException) as in_progress:
        store.claim(
            tenant_id="tenant-a",
            user_id="user-1",
            scope="values:create",
            idempotency_key="key-1",
            request_hash=request_hash,
        )
    assert in_progress.value.status_code == 409

    store.complete(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        record_id=claim.record_id,
        response_status=201,
        response_body={"id": "value-1"},
    )
    replay = store.claim(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        request_hash=request_hash,
    )
    assert replay == IdempotencyReplay(status_code=201, body={"id": "value-1"})

    with pytest.raises(HTTPException) as conflict:
        store.claim(
            tenant_id="tenant-a",
            user_id="user-1",
            scope="values:create",
            idempotency_key="key-1",
            request_hash=build_request_hash({"value": 2}),
        )
    assert conflict.value.status_code == 409

    store.complete(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="missing",
        record_id=claim.record_id,
        response_status=200,
        response_body={},
    )
    store.abandon(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        record_id=claim.record_id,
    )
    assert store.claim(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        request_hash=request_hash,
    ) == IdempotencyReplay(status_code=201, body={"id": "value-1"})


def test_inmemory_idempotency_store_rejects_blank_authority() -> None:
    store = InMemoryValueIdempotencyStore()

    with pytest.raises(ValueError, match="tenant_id"):
        store.claim(
            tenant_id=" ",
            user_id="user-1",
            scope="values:create",
            idempotency_key="key-1",
            request_hash="hash",
        )


def test_in_memory_value_idempotency_expires_old_claims() -> None:
    store = InMemoryValueIdempotencyStore()
    first = store.claim(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="short",
        request_hash="hash",
        ttl_seconds=-1,
    )
    second = store.claim(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="short",
        request_hash="hash",
    )

    assert isinstance(first, IdempotencyClaim)
    assert isinstance(second, IdempotencyClaim)
    assert second.record_id == 2


def test_in_memory_completed_replay_cannot_be_abandoned_or_overwritten() -> None:
    store = InMemoryValueIdempotencyStore()
    identity: dict[str, str] = dict(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
    )
    claim = store.claim(**identity, request_hash="hash")
    assert isinstance(claim, IdempotencyClaim)
    store.complete(
        **identity,
        record_id=claim.record_id,
        response_status=201,
        response_body={"id": "first"},
    )
    store.complete(
        **identity,
        record_id=claim.record_id,
        response_status=200,
        response_body={"id": "overwritten"},
    )
    store.abandon(**identity, record_id=claim.record_id)
    assert store.claim(**identity, request_hash="hash") == IdempotencyReplay(
        status_code=201, body={"id": "first"}
    )


def test_in_memory_idempotency_identity_is_tenant_bound() -> None:
    store = InMemoryValueIdempotencyStore()

    first = store.claim(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="shared-key",
        request_hash="hash-a",
    )
    second = store.claim(
        tenant_id="tenant-b",
        user_id="user-1",
        scope="values:create",
        idempotency_key="shared-key",
        request_hash="hash-b",
    )

    assert isinstance(first, IdempotencyClaim)
    assert isinstance(second, IdempotencyClaim)
    assert first.record_id != second.record_id


def test_database_value_idempotency_store_delegates_and_replays(monkeypatch) -> None:
    repo = MagicMock()
    record = SimpleNamespace(id=7)
    repo.get.return_value = None
    repo.get_quarantined.return_value = None
    repo.create_claim.return_value = record
    monkeypatch.setattr(
        "src.services.value_idempotency_store.ValueIdempotencyRepository",
        lambda _db: repo,
    )
    store = DatabaseValueIdempotencyStore(MagicMock())

    assert (
        store.claim(
            tenant_id="tenant-a",
            user_id="user-1",
            scope="values:create",
            idempotency_key=None,
            request_hash="hash",
        )
        is None
    )
    claim = store.claim(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        request_hash="hash",
    )
    assert claim == IdempotencyClaim(record_id=7)

    repo.get.return_value = SimpleNamespace(
        id=8,
        request_hash="hash",
        state="completed",
        response_status=200,
        response_body={},
        expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).replace(
            tzinfo=None
        ),
    )
    assert store.claim(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-2",
        request_hash="hash",
    ) == IdempotencyReplay(status_code=200, body={})

    repo.get.return_value = SimpleNamespace(
        id=9,
        request_hash="hash",
        state="completed",
        response_status=201,
        response_body={"ok": True},
        expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).replace(
            tzinfo=None
        ),
    )
    expired_claim = store.claim(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-3",
        request_hash="hash",
    )
    assert expired_claim == IdempotencyClaim(record_id=7)
    repo.delete.assert_called_with(record_id=9, commit=True)

    store.complete(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        record_id=7,
        response_status=201,
        response_body={"id": "value-1"},
    )
    repo.complete.assert_called_with(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        record_id=7,
        response_status=201,
        response_body={"id": "value-1"},
        commit=True,
    )
    store.abandon(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        record_id=7,
    )
    repo.abandon.assert_called_with(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        record_id=7,
        commit=True,
    )


def test_database_value_idempotency_store_propagates_deferred_commit(
    monkeypatch,
) -> None:
    repo = MagicMock()
    repo.get.side_effect = [
        SimpleNamespace(
            id=9,
            request_hash="old-hash",
            state="completed",
            response_status=201,
            response_body={"id": "old-value"},
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ),
        None,
    ]
    repo.get_quarantined.return_value = None
    repo.create_claim.return_value = SimpleNamespace(id=17)
    monkeypatch.setattr(
        "src.services.value_idempotency_store.ValueIdempotencyRepository",
        lambda _db: repo,
    )
    store = DatabaseValueIdempotencyStore(MagicMock())

    claim = store.claim(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        request_hash="hash",
        commit=False,
    )
    assert claim == IdempotencyClaim(record_id=17)
    repo.delete.assert_called_once_with(record_id=9, commit=False)
    repo.create_claim.assert_called_once_with(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        request_hash="hash",
        ttl_seconds=86400,
        commit=False,
    )

    store.complete(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        record_id=17,
        response_status=201,
        response_body={"id": "value-1"},
        commit=False,
    )
    repo.complete.assert_called_once_with(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        record_id=17,
        response_status=201,
        response_body={"id": "value-1"},
        commit=False,
    )

    store.abandon(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        record_id=17,
        commit=False,
    )
    repo.abandon.assert_called_once_with(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        record_id=17,
        commit=False,
    )


def test_database_value_idempotency_store_conflicts(monkeypatch) -> None:
    repo = MagicMock()
    monkeypatch.setattr(
        "src.services.value_idempotency_store.ValueIdempotencyRepository",
        lambda _db: repo,
    )
    store = DatabaseValueIdempotencyStore(MagicMock())

    repo.get.return_value = SimpleNamespace(
        id=1,
        request_hash="original",
        state="completed",
        response_status=200,
        response_body={},
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    with pytest.raises(HTTPException) as conflict:
        store.claim(
            tenant_id="tenant-a",
            user_id="user-1",
            scope="values:create",
            idempotency_key="key-1",
            request_hash="different",
        )
    assert conflict.value.status_code == 409

    repo.get.return_value = SimpleNamespace(
        id=2,
        request_hash="same",
        state="in_progress",
        response_status=None,
        response_body=None,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    with pytest.raises(HTTPException) as in_progress:
        store.claim(
            tenant_id="tenant-a",
            user_id="user-1",
            scope="values:create",
            idempotency_key="key-2",
            request_hash="same",
        )
    assert in_progress.value.status_code == 409


def test_database_store_blocks_nonexpired_quarantined_legacy_key(monkeypatch) -> None:
    repo = MagicMock()
    repo.get.return_value = None
    repo.get_quarantined.return_value = SimpleNamespace(
        id=9,
        ownership_state="quarantined",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    monkeypatch.setattr(
        "src.services.value_idempotency_store.ValueIdempotencyRepository",
        lambda _db: repo,
    )
    store = DatabaseValueIdempotencyStore(MagicMock())

    with pytest.raises(HTTPException, match="cannot be replayed safely") as conflict:
        store.claim(
            tenant_id="tenant-a",
            user_id="user-1",
            scope="values:create",
            idempotency_key="legacy-key",
            request_hash="hash",
        )

    assert conflict.value.status_code == 409
    repo.create_claim.assert_not_called()


def test_database_value_idempotency_store_handles_unique_claim_race(
    monkeypatch,
) -> None:
    repo = MagicMock()
    existing = SimpleNamespace(
        id=3,
        request_hash="same",
        state="in_progress",
        response_status=None,
        response_body=None,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    repo.get.side_effect = [None, existing]
    repo.get_quarantined.return_value = None
    repo.create_claim.side_effect = IntegrityError(
        "insert value_idempotency_keys", {}, Exception("unique")
    )
    monkeypatch.setattr(
        "src.services.value_idempotency_store.ValueIdempotencyRepository",
        lambda _db: repo,
    )
    store = DatabaseValueIdempotencyStore(MagicMock())

    with pytest.raises(HTTPException) as in_progress:
        store.claim(
            tenant_id="tenant-a",
            user_id="user-1",
            scope="values:create",
            idempotency_key="key-race",
            request_hash="same",
        )

    assert in_progress.value.status_code == 409
    repo.rollback.assert_called_once()


def test_database_value_idempotency_store_defers_unique_race_rollback(
    monkeypatch,
) -> None:
    db = MagicMock()
    repo = MagicMock()
    existing = SimpleNamespace(
        id=3,
        request_hash="same",
        state="in_progress",
        response_status=None,
        response_body=None,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    repo.get.side_effect = [None, existing]
    repo.get_quarantined.return_value = None
    repo.create_claim.side_effect = IntegrityError(
        "insert value_idempotency_keys", {}, Exception("unique")
    )
    monkeypatch.setattr(
        "src.services.value_idempotency_store.ValueIdempotencyRepository",
        lambda _db: repo,
    )
    store = DatabaseValueIdempotencyStore(db)

    with pytest.raises(HTTPException) as in_progress:
        store.claim(
            tenant_id="tenant-a",
            user_id="user-1",
            scope="values:create",
            idempotency_key="key-race",
            request_hash="same",
            commit=False,
        )

    assert in_progress.value.status_code == 409
    db.begin_nested.assert_called_once_with()
    db.rollback.assert_not_called()
    repo.rollback.assert_not_called()


def test_value_idempotency_repository_crud_paths() -> None:
    db = MagicMock()
    repo = ValueIdempotencyRepository(db)
    existing = ValueIdempotencyKey(
        id=3,
        tenant_id="tenant-a",
        ownership_state="resolved",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        request_hash="hash",
        state="in_progress",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    db.query.return_value = _query_returning(existing)
    assert (
        repo.get(
            tenant_id="tenant-a",
            user_id="user-1",
            scope="values:create",
            idempotency_key="key-1",
        )
        is existing
    )

    created = repo.create_claim(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-2",
        request_hash="hash",
        ttl_seconds=60,
    )
    assert created.state == "in_progress"
    db.add.assert_called_with(created)

    db.execute.return_value.rowcount = 1
    completed = repo.complete(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        record_id=3,
        response_status=201,
        response_body={"id": "value-1"},
    )
    assert completed is True
    db.execute.assert_called_once()

    repo.delete(record_id=3)
    db.delete.assert_called_with(existing)

    assert db.commit.call_count == 3
    assert db.refresh.call_count == 1
    db.flush.assert_not_called()

    db.query.return_value = _query_returning(None)
    db.execute.return_value.rowcount = 0
    with pytest.raises(ValueError):
        repo.complete(
            tenant_id="tenant-a",
            user_id="user-1",
            scope="values:create",
            idempotency_key="key-1",
            record_id=999,
            response_status=200,
            response_body={},
        )
    repo.delete(record_id=999)


def test_value_idempotency_repository_requires_resolved_tenant_authority() -> None:
    db = MagicMock()
    repo = ValueIdempotencyRepository(db)

    with pytest.raises(ValueError, match="tenant_id"):
        repo.create_claim(
            tenant_id=" ",
            user_id="user-1",
            scope="values:create",
            idempotency_key="key-1",
            request_hash="hash",
            ttl_seconds=60,
        )

    db.add.assert_not_called()
    created = repo.create_claim(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        request_hash="hash",
        ttl_seconds=60,
    )
    assert created.tenant_id == "tenant-a"
    assert created.ownership_state == "resolved"


def test_value_idempotency_terminal_updates_compare_full_claim_identity() -> None:
    db = MagicMock()
    db.execute.return_value.rowcount = 1
    repo = ValueIdempotencyRepository(db)
    identity = _ClaimIdentity(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        record_id=3,
    )
    assert repo.complete(
        **identity, response_status=201, response_body={"id": "value-1"}
    )
    assert repo.abandon(**identity)
    assert db.execute.call_count == 2
    for call in db.execute.call_args_list:
        statement = call.args[0]
        compiled = statement.compile(dialect=postgresql.dialect())
        where = str(compiled).split(" WHERE ", 1)[1]
        for field in (
            "tenant_id",
            "user_id",
            "scope",
            "idempotency_key",
            "ownership_state",
            "state",
            "id",
        ):
            assert f"value_idempotency_keys.{field} = " in where
        for value in (*identity.values(), "resolved", "in_progress"):
            assert value in compiled.params.values()
    assert db.commit.call_count == 2
    db.refresh.assert_not_called()


def test_value_idempotency_terminal_cas_rejects_stale_claim_without_committing() -> (
    None
):
    db = MagicMock()
    db.execute.return_value.rowcount = 0
    repo = ValueIdempotencyRepository(db)
    identity = _ClaimIdentity(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-1",
        record_id=3,
    )
    with pytest.raises(ValueError, match="not in progress"):
        repo.complete(**identity, response_status=201, response_body={"id": "value-1"})
    assert repo.abandon(**identity) is False
    db.commit.assert_not_called()
    db.flush.assert_not_called()


def test_value_idempotency_repository_deferred_paths_flush_without_commit() -> None:
    db = MagicMock()
    repo = ValueIdempotencyRepository(db)
    pending = []

    db.add.side_effect = pending.append

    def materialize_claim_id() -> None:
        for record in pending:
            if record.id is None:
                record.id = 41

    db.flush.side_effect = materialize_claim_id

    created = repo.create_claim(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-2",
        request_hash="hash",
        ttl_seconds=60,
        commit=False,
    )
    assert created.id == 41

    db.execute.return_value.rowcount = 1
    completed = repo.complete(
        tenant_id="tenant-a",
        user_id="user-1",
        scope="values:create",
        idempotency_key="key-2",
        record_id=41,
        response_status=201,
        response_body={"id": "value-1"},
        commit=False,
    )
    assert completed is True

    repo.delete(record_id=41, commit=False)

    assert db.flush.call_count == 3
    db.commit.assert_not_called()
    db.refresh.assert_not_called()
    db.rollback.assert_not_called()


def test_get_value_idempotency_store_selects_backend(monkeypatch) -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    monkeypatch.setattr(
        "src.services.value_idempotency_store.settings.require_database", False
    )
    first = get_value_idempotency_store(request=request, db=None)
    second = get_value_idempotency_store(request=request, db=None)
    assert first is second

    monkeypatch.setattr(
        "src.services.value_idempotency_store.settings.require_database", True
    )
    with pytest.raises(HTTPException) as unavailable:
        get_value_idempotency_store(request=request, db=None)
    assert unavailable.value.status_code == 503

    db = MagicMock()
    assert isinstance(
        get_value_idempotency_store(request=request, db=db),
        DatabaseValueIdempotencyStore,
    )
