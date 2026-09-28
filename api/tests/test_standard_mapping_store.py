"""Tests for standard mapping store."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.database.repositories.standard_mapping_repository import (
    MappingCandidateLimitExceeded,
)
from src.services import standard_mapping_store as store_module
from src.services.canonical_data import CanonicalDataUnavailableError
from src.services.standard_mapping_store import (
    InMemoryStandardMappingStore,
    StandardMappingData,
    StandardMappingStore,
)
from src.services.value_resolution import find_mapping_routes


def test_standard_mapping_data_from_dict():
    data = {
        "id": 1,
        "source_standard": "ESRS",
        "source_code": "E1-1",
        "source_label": "GHG emissions",
        "target_standard": "GRI",
        "target_code": "305-1",
        "target_label": "GRI 305-1a",
        "esg_dimension": "E",
        "relationship_type": "equivalent",
        "confidence": 0.9,
        "dataset": "e2_crosswalks_esrs_gri.csv",
        "source_row": 5,
    }

    mapping = StandardMappingData(data)

    assert mapping.id == 1
    assert mapping.source_standard == "ESRS"
    assert mapping.source_code == "E1-1"
    assert mapping.source_label == "GHG emissions"
    assert mapping.target_standard == "GRI"
    assert mapping.target_code == "305-1"
    assert mapping.target_label == "GRI 305-1a"
    assert mapping.esg_dimension == "E"
    assert mapping.relationship_type == "equivalent"
    assert mapping.confidence == 0.9
    assert mapping.dataset == "e2_crosswalks_esrs_gri.csv"
    assert mapping.source_row == 5


def test_standard_mapping_data_defaults():
    mapping = StandardMappingData({})
    assert mapping.id == 0
    assert mapping.source_standard == ""
    assert mapping.source_code == ""
    assert mapping.target_standard == ""
    assert mapping.target_code is None
    assert mapping.esg_dimension is None


def test_in_memory_store_loads_from_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        json_path = Path(tmpdir) / "mappings.json"
        test_data = {
            "mappings": [
                {
                    "source_standard": "ESRS",
                    "source_code": "E1-1",
                    "target_standard": "GRI",
                    "target_code": "305-1",
                    "esg_dimension": "E",
                }
            ]
        }
        json_path.write_text(json.dumps(test_data), encoding="utf-8")

        store = InMemoryStandardMappingStore(json_path=json_path)
        assert store.count() == 1


def test_find_by_source_and_target_prefix():
    with tempfile.TemporaryDirectory() as tmpdir:
        json_path = Path(tmpdir) / "mappings.json"
        test_data = {
            "mappings": [
                {
                    "source_standard": "ESRS",
                    "source_code": "E1-1",
                    "target_standard": "GRI",
                    "target_code": "305-1",
                    "esg_dimension": "E",
                },
                {
                    "source_standard": "ESRS",
                    "source_code": "S1-1",
                    "target_standard": "GRI",
                    "target_code": "401-1",
                    "esg_dimension": "S",
                },
            ]
        }
        json_path.write_text(json.dumps(test_data), encoding="utf-8")

        store = InMemoryStandardMappingStore(json_path=json_path)
        assert len(store.find_by_source("ESRS", "E1")) == 1
        assert len(store.find_by_target("GRI", "401")) == 1


def test_store_rejects_canonical_query_failure():
    db = MagicMock()
    repo = MagicMock()
    repo.get_all.side_effect = RuntimeError("db down")

    store = StandardMappingStore(db=db)
    store._get_repo = lambda: repo

    with pytest.raises(CanonicalDataUnavailableError) as raised:
        store.get_all()
    assert isinstance(raised.value.__cause__, RuntimeError)


def test_store_uses_canonical_pairwise_repository(monkeypatch):
    db = MagicMock()

    class FakeCanonicalRepo:
        def __init__(self, db):
            self.db = db

        def find_between_standards(self, source_std, target_std, limit=100):
            assert source_std == "ESRS"
            assert target_std == "GRI"
            return ["canonical-row"]

    monkeypatch.setattr(
        store_module, "CanonicalPairwiseMappingRepository", FakeCanonicalRepo
    )

    store = StandardMappingStore(db=db)

    assert store.find_between_standards("ESRS", "GRI") == ["canonical-row"]


def test_store_never_reads_legacy_mapping_table_when_canonical_model_empty(monkeypatch):
    class FakeCanonicalRepo:
        def __init__(self, db):
            self.db = db

        def search(self, **_kwargs):
            return []

        def find_candidate_routes(self, **_kwargs):
            return []

    monkeypatch.setattr(
        store_module, "CanonicalPairwiseMappingRepository", FakeCanonicalRepo
    )
    store = StandardMappingStore(db=MagicMock())
    monkeypatch.setattr(
        store,
        "_get_legacy_repo",
        lambda: pytest.fail("retired mapping read"),
        raising=False,
    )
    assert store.search(source_standard="ESRS", source_code="E3-4_05") == []
    assert store.find_candidate_routes(target_standards=["GRI"]) == []


def test_candidate_route_canonical_overflow_is_not_converted_to_absence(monkeypatch):
    class OverflowingRepo:
        def find_candidate_routes(self, **_kwargs):
            raise MappingCandidateLimitExceeded(500)

    store = StandardMappingStore(db=MagicMock())
    monkeypatch.setattr(store, "_get_repo_for_operation", lambda _: OverflowingRepo())
    with pytest.raises(MappingCandidateLimitExceeded):
        store.find_candidate_routes(target_standards=["GRI"], target_codes=["303-3"])


def test_candidate_route_internal_query_failure_cannot_become_not_found(monkeypatch):
    class BrokenRepo:
        def find_candidate_routes(self, **_kwargs):
            raise TypeError("internal canonical query failure")

    store = StandardMappingStore(db=MagicMock())
    monkeypatch.setattr(store, "_get_repo_for_operation", lambda _: BrokenRepo())
    with pytest.raises(MappingCandidateLimitExceeded):
        store.find_candidate_routes(target_standards=["GRI"], target_codes=["303-3"])


def test_candidate_route_empty_query_does_not_require_unrelated_count(monkeypatch):
    class BrokenCountRepo:
        def find_candidate_routes(self, **_kwargs):
            return []

        def count(self):
            try:
                raise TypeError("internal count type error")
            except TypeError as exc:
                raise RuntimeError("count unavailable") from exc

    store = StandardMappingStore(db=MagicMock())
    monkeypatch.setattr(store, "_get_repo_for_operation", lambda _: BrokenCountRepo())
    assert (
        store.find_candidate_routes(target_standards=["GRI"], target_codes=["303-3"])
        == []
    )


def test_candidate_route_cannot_use_retired_legacy_rows_beyond_limit(monkeypatch):
    rows = [SimpleNamespace(id=i) for i in range(501)]

    class EmptyCanonical:
        def find_candidate_routes(self, **_kwargs):
            return []

        def count(self):
            return 0

    class LegacyRepo:
        def search(self, **kwargs):
            return rows[: kwargs["limit"]]

    store = StandardMappingStore(db=MagicMock())
    monkeypatch.setattr(store, "_get_repo_for_operation", lambda _: EmptyCanonical())
    monkeypatch.setattr(store, "_get_legacy_repo", lambda: LegacyRepo(), raising=False)
    assert (
        store.find_candidate_routes(target_standards=["GRI"], target_codes=["303-3"])
        == []
    )


def test_candidate_route_does_not_query_retired_legacy_on_canonical_absence(
    monkeypatch,
):
    class EmptyCanonical:
        def find_candidate_routes(self, **_kwargs):
            return []

        def count(self):
            return 0

    class BrokenLegacy:
        def search(self, **_kwargs):
            raise TypeError("internal legacy query failure")

    store = StandardMappingStore(db=MagicMock())
    monkeypatch.setattr(store, "_get_repo_for_operation", lambda _: EmptyCanonical())
    monkeypatch.setattr(
        store, "_get_legacy_repo", lambda: BrokenLegacy(), raising=False
    )
    assert (
        store.find_candidate_routes(target_standards=["GRI"], target_codes=["303-3"])
        == []
    )


def test_unbounded_legacy_search_signature_cannot_authorize_partial_routes():
    class SearchWithoutFilters:
        def search(self):
            return [SimpleNamespace(id=i) for i in range(100)]

    with pytest.raises(MappingCandidateLimitExceeded):
        find_mapping_routes(
            SearchWithoutFilters(),
            source_concept="csrd:E3_5",
            target_concept="gri:303_3",
        )


def test_unbounded_legacy_get_all_signature_cannot_authorize_partial_routes():
    class GetAllWithoutBounds:
        def get_all(self):
            return [SimpleNamespace(id=i) for i in range(100)]

    with pytest.raises(MappingCandidateLimitExceeded):
        find_mapping_routes(
            GetAllWithoutBounds(),
            source_concept="csrd:E3_5",
            target_concept="gri:303_3",
        )


def test_mapping_route_duplicate_id_with_conflicting_semantics_is_rejected():
    class ConflictingSearch:
        def search(self, **_kwargs):
            return [
                SimpleNamespace(
                    id=1,
                    source_standard="ESRS",
                    source_code="E3-5",
                    target_standard="GRI",
                    target_code="303-3",
                    relationship_type=relationship,
                )
                for relationship in ("partial", "equivalent")
            ]

    with pytest.raises(MappingCandidateLimitExceeded):
        find_mapping_routes(
            ConflictingSearch(), source_concept="csrd:E3_5", target_concept="gri:303_3"
        )


def test_retired_legacy_duplicate_ids_cannot_enter_canonical_routes(monkeypatch):
    rows = [
        SimpleNamespace(id=1, relationship_type=relationship)
        for relationship in ("partial", "equivalent")
    ]

    class EmptyCanonical:
        def find_candidate_routes(self, **_kwargs):
            return []

        def count(self):
            return 0

    class LegacyRepo:
        def search(self, **_kwargs):
            return rows

    store = StandardMappingStore(db=MagicMock())
    monkeypatch.setattr(store, "_get_repo_for_operation", lambda _: EmptyCanonical())
    monkeypatch.setattr(store, "_get_legacy_repo", lambda: LegacyRepo(), raising=False)
    assert (
        store.find_candidate_routes(target_standards=["GRI"], target_codes=["303-3"])
        == []
    )


def test_candidate_route_internal_type_error_cannot_fall_back_to_partial_search():
    class BrokenCandidateStore:
        def find_candidate_routes(self, **_kwargs):
            raise TypeError("internal route query failed")

        def search(self, **_kwargs):
            return [
                SimpleNamespace(
                    id=1,
                    source_standard="ESRS",
                    source_code="E3-5",
                    target_standard="GRI",
                    target_code="303-3",
                    relationship_type="equivalent",
                )
            ]

    with pytest.raises(MappingCandidateLimitExceeded):
        find_mapping_routes(
            BrokenCandidateStore(),
            source_concept="csrd:E3_5",
            target_concept="gri:303_3",
        )


def test_sds_register_urns_find_mapping_candidates_by_standard_and_code():
    class CanonicalStore:
        def find_candidate_routes(self, **filters):
            assert "ESRS" in filters["source_standards"]
            assert "GRI" in filters["target_standards"]
            assert "303-3" in filters["target_codes"]
            return [
                SimpleNamespace(
                    id=1,
                    source_standard="ESRS",
                    source_code="E3-5",
                    target_standard="GRI",
                    target_code="303-3",
                    relationship_type="equivalent",
                )
            ]

    routes = find_mapping_routes(
        CanonicalStore(),
        source_concept="urn:sds:reg:esrs:e3_5",
        target_concept="urn:sds:reg:gri:gri_303_3",
    )
    assert len(routes) == 1


def test_esrs_prefixed_code_is_searched_and_matched_for_csrd_alias():
    row = SimpleNamespace(
        id=1,
        source_standard="ESRS",
        source_code="ESRS E3-5",
        target_standard="GRI",
        target_code="303-3",
        relationship_type="partial",
    )

    class CanonicalStore:
        def find_candidate_routes(self, **filters):
            assert "ESRS E3-5" in filters["source_codes"]
            return [row]

    routes = find_mapping_routes(
        CanonicalStore(),
        source_concept="csrd:E3_5",
        target_concept="gri:303_3",
    )
    assert len(routes) == 1


def test_mapping_route_with_missing_standard_cannot_inherit_requested_authority():
    class MissingStandardStore:
        def find_candidate_routes(self, **_filters):
            return [
                SimpleNamespace(
                    id=1,
                    source_standard=None,
                    source_code="E3-5",
                    target_standard="GRI",
                    target_code="303-3",
                    relationship_type="equivalent",
                )
            ]

    with pytest.raises(MappingCandidateLimitExceeded):
        find_mapping_routes(
            MissingStandardStore(),
            source_concept="csrd:E3_5",
            target_concept="gri:303_3",
        )


def test_empty_canonical_candidate_catalog_never_queries_retired_legacy_table(
    monkeypatch,
):
    store = StandardMappingStore(db=MagicMock())
    canonical = MagicMock()
    canonical.find_candidate_routes.return_value = []
    canonical.count.return_value = 0
    monkeypatch.setattr(store, "_get_repo_for_operation", lambda _: canonical)
    monkeypatch.setattr(
        store,
        "_get_legacy_repo",
        lambda: pytest.fail("legacy mapping read forbidden"),
        raising=False,
    )

    assert (
        store.find_candidate_routes(target_standards=["GRI"], target_codes=["303-3"])
        == []
    )
    canonical.find_candidate_routes.assert_called_once()


def test_in_memory_store_filters_search_dimensions_standards_and_changed_since(
    tmp_path,
):
    json_path = tmp_path / "mappings.json"
    json_path.write_text(
        json.dumps(
            {
                "crosswalks": [
                    {
                        "source_standard": "ESRS",
                        "source_code": "E1-1",
                        "target_standard": "GRI",
                        "target_code": "305-1",
                        "esg_dimension": "E",
                        "confidence": 0.95,
                    },
                    {
                        "source_standard": "GRI",
                        "source_code": "401-1",
                        "target_standard": "ESRS",
                        "target_code": "S1-1",
                        "esg_dimension": "S",
                        "confidence": 0.7,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    store = InMemoryStandardMappingStore(json_path=json_path)

    assert len(store.get_all(limit=1, offset=1)) == 1
    assert store.get_all(changed_since=datetime.now(timezone.utc)) == []
    assert store.count(changed_since=datetime.now(timezone.utc)) == 0
    assert [m.source_code for m in store.search_by_dimension("E")] == ["E1-1"]
    assert [m.source_code for m in store.search(source_standard="ESRS")] == ["E1-1"]
    assert [m.source_code for m in store.search(source_code="E1")] == ["E1-1"]
    assert [m.source_code for m in store.search(target_standard="ESRS")] == ["401-1"]
    assert [m.source_code for m in store.search(target_code="S1")] == ["401-1"]
    assert [m.source_code for m in store.search(dimension="S")] == ["401-1"]
    assert [m.source_code for m in store.search(min_confidence=0.9)] == ["E1-1"]
    assert store.search(changed_since=datetime.now(timezone.utc)) == []
    assert [m.source_code for m in store.find_between_standards("ESRS", "GRI")] == [
        "E1-1"
    ]
    assert store.get_supported_standards() == ["ESRS", "GRI"]


def test_in_memory_store_ignores_invalid_json(tmp_path):
    json_path = tmp_path / "mappings.json"
    json_path.write_text("{not-json", encoding="utf-8")

    store = InMemoryStandardMappingStore(json_path=json_path)

    assert store.count() == 0
    assert store.get_all() == []


def test_standard_mapping_store_delegates_all_operations_and_handles_failures(
    monkeypatch,
):
    repo = MagicMock()
    repo.get_all.return_value = ["all"]
    repo.count.return_value = 2
    repo.find_by_source.return_value = ["source"]
    repo.find_by_target.return_value = ["target"]
    repo.find_between_standards.return_value = ["between"]
    repo.search.return_value = ["search"]
    repo.get_supported_standards.return_value = ["ESRS", "GRI"]
    monkeypatch.setattr(
        store_module,
        "CanonicalPairwiseMappingRepository",
        lambda _db: repo,
    )
    monkeypatch.setattr(store_module, "canonical_data_required", lambda: False)

    store = StandardMappingStore(db=MagicMock())

    assert store.is_db_available() is True
    assert store.get_all(limit=1, offset=0) == ["all"]
    assert store.count() == 2
    assert store.find_by_source("ESRS", "E1") == ["source"]
    assert store.find_by_target("GRI", "305") == ["target"]
    assert store.find_between_standards("ESRS", "GRI") == ["between"]
    assert store.search(source_standard="ESRS") == ["search"]
    assert store.search_by_dimension("E") == ["search"]
    assert store.get_supported_standards() == ["ESRS", "GRI"]

    for method_name in (
        "get_all",
        "count",
        "find_by_source",
        "find_by_target",
        "find_between_standards",
        "search",
        "get_supported_standards",
    ):
        getattr(repo, method_name).side_effect = RuntimeError("db down")

    for read in (
        lambda: store.get_all(),
        lambda: store.count(),
        lambda: store.find_by_source("ESRS", "E1"),
        lambda: store.find_by_target("GRI", "305"),
        lambda: store.find_between_standards("ESRS", "GRI"),
        lambda: store.search(source_standard="ESRS"),
        lambda: store.get_supported_standards(),
    ):
        with pytest.raises(CanonicalDataUnavailableError):
            read()
    assert StandardMappingStore().get_all() == []


def test_store_fail_closed_when_db_unavailable_and_strict_mode(monkeypatch):
    """When DB is unavailable and canonical_data_required() is True, the store
    must raise CanonicalDataUnavailableError rather than returning fallback data.
    """
    monkeypatch.setattr(store_module, "canonical_data_required", lambda: True)

    store = StandardMappingStore(db=None)
    assert store.is_db_available() is False

    with pytest.raises(
        CanonicalDataUnavailableError,
        match="StandardMappingStore requires canonical PostgreSQL data",
    ):
        store.get_all()
    assert store_module.get_standard_mapping_store(db=MagicMock()).is_db_available()
