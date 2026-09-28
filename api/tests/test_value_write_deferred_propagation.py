from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from rdflib import Graph

from src.api.models import ValueCreate
from src.services.value_batch import execute_value_batch
from src.services.value_ingest import ingest_value

_DEFAULT = object()


def _value(*, external_key: str | None) -> ValueCreate:
    return ValueCreate(
        concept="urn:sds:test:comment",
        entity="entity-1",
        period=date(2026, 8, 31),
        external_key=external_key,
        value="verified",
        value_type="text",
        unit="status",
    )


def _commit_kwargs(commit):
    return {} if commit is _DEFAULT else {"commit": commit}


@pytest.mark.parametrize(
    ("commit", "expected"),
    [
        pytest.param(_DEFAULT, True, id="default"),
        pytest.param(False, False, id="deferred"),
    ],
)
def test_ingest_value_propagates_commit_to_save_for_keyed_values(commit, expected):
    calls = []
    persisted = object()

    class _Store:
        def save(self, **kwargs):
            calls.append(("save", kwargs))
            return [persisted]

        def create(self, **_kwargs):  # pragma: no cover - keyed values use save
            raise AssertionError("keyed value should use save")

    result = ingest_value(
        value_id="value-keyed",
        value_data=_value(external_key="source:keyed"),
        converter=object(),
        store=_Store(),
        hierarchy_store=object(),
        graph=Graph(),
        created_by="pytest",
        company_id=None,
        strict=False,
        **_commit_kwargs(commit),
    )

    assert result is persisted
    assert len(calls) == 1
    operation, kwargs = calls[0]
    assert operation == "save"
    assert kwargs["commit"] is expected


@pytest.mark.parametrize(
    ("commit", "expected"),
    [
        pytest.param(_DEFAULT, True, id="default"),
        pytest.param(False, False, id="deferred"),
    ],
)
def test_ingest_value_propagates_commit_to_create_for_unkeyed_values(commit, expected):
    calls = []
    persisted = object()

    class _Store:
        def create(self, **kwargs):
            calls.append(("create", kwargs))
            return persisted

        def save(self, **_kwargs):  # pragma: no cover - unkeyed values use create
            raise AssertionError("unkeyed value should use create")

    result = ingest_value(
        value_id="value-unkeyed",
        value_data=_value(external_key=None),
        converter=object(),
        store=_Store(),
        hierarchy_store=object(),
        graph=Graph(),
        created_by="pytest",
        company_id=None,
        strict=False,
        **_commit_kwargs(commit),
    )

    assert result is persisted
    assert len(calls) == 1
    operation, kwargs = calls[0]
    assert operation == "create"
    assert kwargs["commit"] is expected


@pytest.mark.parametrize(
    ("commit", "expected"),
    [
        pytest.param(_DEFAULT, True, id="default"),
        pytest.param(False, False, id="deferred"),
    ],
)
def test_execute_value_batch_propagates_commit_to_save_for_mixed_values(
    commit, expected
):
    calls = []

    class _Store:
        def save(self, **kwargs):
            calls.append(("save", kwargs))
            return [SimpleNamespace(id=record.value_id) for record in kwargs["records"]]

        def bulk_create(self, **_kwargs):  # pragma: no cover - mixed batch uses save
            raise AssertionError("mixed batch should use save")

    response = execute_value_batch(
        items=[_value(external_key="source:keyed"), _value(external_key=None)],
        converter=object(),
        store=_Store(),
        hierarchy_store=object(),
        graph=Graph(),
        created_by="pytest",
        company_id=None,
        strict=False,
        **_commit_kwargs(commit),
    )

    assert response.accepted_rows == 2
    assert len(calls) == 1
    operation, kwargs = calls[0]
    assert operation == "save"
    assert [record.external_key for record in kwargs["records"]] == [
        "source:keyed",
        None,
    ]
    assert kwargs["commit"] is expected


@pytest.mark.parametrize(
    ("commit", "expected"),
    [
        pytest.param(_DEFAULT, True, id="default"),
        pytest.param(False, False, id="deferred"),
    ],
)
def test_execute_value_batch_propagates_commit_to_bulk_create(commit, expected):
    calls = []

    class _Store:
        def save(self, **_kwargs):  # pragma: no cover - unkeyed batch uses bulk create
            raise AssertionError("unkeyed batch should use bulk_create")

        def bulk_create(self, **kwargs):
            calls.append(("bulk_create", kwargs))
            return [SimpleNamespace(id=record.value_id) for record in kwargs["records"]]

    response = execute_value_batch(
        items=[_value(external_key=None), _value(external_key=None)],
        converter=object(),
        store=_Store(),
        hierarchy_store=object(),
        graph=Graph(),
        created_by="pytest",
        company_id=None,
        strict=False,
        **_commit_kwargs(commit),
    )

    assert response.accepted_rows == 2
    assert len(calls) == 1
    operation, kwargs = calls[0]
    assert operation == "bulk_create"
    assert kwargs["commit"] is expected
