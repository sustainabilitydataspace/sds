"""Unit tests for Wave 4 semantic write protection."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from src.database.models import Concept, ESGValue, Indicator
from src.database.semantic_guard import (
    SemanticIsolationError,
    collect_protected_writes,
    ensure_value_operation_is_isolated,
)


def test_collect_protected_writes_ignores_operational_values():
    session = SimpleNamespace(
        new=[
            ESGValue(
                id="v1",
                concept="c1",
                entity="e1",
                period=date(2024, 1, 1),
                value=1,
                unit="L",
            )
        ],
        dirty=[],
        deleted=[],
    )

    assert collect_protected_writes(session) == []


def test_collect_protected_writes_reports_semantic_models():
    session = SimpleNamespace(
        new=[
            Indicator(
                id="i1", identifier="urn:sds:reg:test:1", title="T", dimension="E"
            )
        ],
        dirty=[
            Concept(
                uri="https://example.com/c",
                label="C",
                taxonomy="SDS",
                concept_type="Disclosure",
            )
        ],
        deleted=[],
    )

    assert collect_protected_writes(session) == ["Concept", "Indicator"]


def test_ensure_value_operation_is_isolated_raises_on_semantic_mutation():
    session = SimpleNamespace(
        new=[],
        dirty=[
            Indicator(
                id="i1", identifier="urn:sds:reg:test:1", title="T", dimension="E"
            )
        ],
        deleted=[],
    )

    with pytest.raises(SemanticIsolationError, match="create_value"):
        ensure_value_operation_is_isolated(session, operation="create_value")
