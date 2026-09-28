from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.database.semantic_models import SemanticAxis, SemanticTerm
from src.services.semantic_catalog import SemanticCatalogRepository


class _FakeQuery:
    def __init__(self, rows=None, *, scalar_value=None):
        self.rows = list(rows or [])
        self.scalar_value = scalar_value
        self.offset_value = 0
        self.limit_value = None

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def offset(self, value):
        self.offset_value = value
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def all(self):
        start = self.offset_value
        stop = None if self.limit_value is None else start + self.limit_value
        return self.rows[start:stop]

    def scalar(self):
        return self.scalar_value


class _FakeDb:
    def __init__(self):
        self.axes = [
            SimpleNamespace(
                id="ax-1",
                axis_key="hazard_class",
                label="Hazard class",
                description="Hazardous or non-hazardous",
                axis_hash="hash-1",
            ),
            SimpleNamespace(
                id="ax-2",
                axis_key="treatment",
                label="Treatment",
                description="Waste treatment",
                axis_hash="hash-2",
            ),
        ]
        self.terms = [
            SimpleNamespace(
                id="term-1",
                term_key="hazardous",
                label="Hazardous",
                description="Hazardous waste",
            ),
            SimpleNamespace(
                id="term-2",
                term_key="non_hazardous",
                label="Non-hazardous",
                description="Non-hazardous waste",
            ),
        ]

    def query(self, model):
        if model is SemanticAxis:
            return _FakeQuery(self.axes)
        if model is SemanticTerm:
            return _FakeQuery(self.terms)
        return _FakeQuery(scalar_value=len(self.axes))


def test_semantic_catalog_repository_projects_axes_and_terms_from_query_rows():
    repo = SemanticCatalogRepository(_FakeDb())
    read_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    assert repo.count_published_axes(valid_as_of=read_time, decision_commit_id=10) == 2
    assert repo.list_published_axes(
        valid_as_of=read_time,
        decision_commit_id=10,
        limit=1,
        offset=1,
    ) == [
        {
            "axis_key": "treatment",
            "label": "Treatment",
            "description": "Waste treatment",
            "axis_hash": "hash-2",
        }
    ]
    assert repo.list_published_terms(
        axis_id="ax-1",
        valid_as_of=read_time,
        decision_commit_id=10,
    ) == [
        {
            "term_key": "hazardous",
            "label": "Hazardous",
            "description": "Hazardous waste",
        },
        {
            "term_key": "non_hazardous",
            "label": "Non-hazardous",
            "description": "Non-hazardous waste",
        },
    ]
