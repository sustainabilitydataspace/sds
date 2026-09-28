"""Canonical-only reads distinguish empty results from failed queries."""

from unittest.mock import MagicMock

import pytest

from src.database.repositories.standard_mapping_repository import (
    MappingCandidateLimitExceeded,
)
from src.services import standard_mapping_store as store_module
from src.services.canonical_data import CanonicalDataUnavailableError

READS = [
    ("get_all", {"limit": 3, "offset": 2, "changed_since": None}, "get_all"),
    ("count", {"changed_since": None}, "count"),
    (
        "find_by_source",
        {"standard": "ESRS", "code": "E1", "limit": 3},
        "find_by_source",
    ),
    (
        "find_by_target",
        {"standard": "GRI", "code": "305", "limit": 3},
        "find_by_target",
    ),
    (
        "find_between_standards",
        {"source_std": "ESRS", "target_std": "GRI", "limit": 3},
        "find_between_standards",
    ),
    ("search", {"source_standard": "ESRS", "limit": 3}, "search"),
    ("get_supported_standards", {}, "get_supported_standards"),
    ("find_candidate_routes", {"source_standards": ["ESRS"], "limit": 3}, "search"),
]


@pytest.fixture
def mapping_repositories(monkeypatch):
    canonical = MagicMock(spec=store_module.CanonicalPairwiseMappingRepository)
    canonical.count.return_value = 0
    monkeypatch.setattr(
        store_module, "CanonicalPairwiseMappingRepository", lambda db: canonical
    )
    store = store_module.StandardMappingStore(db=MagicMock())
    legacy_factory = MagicMock(side_effect=AssertionError("retired mapping read"))
    monkeypatch.setattr(store, "_get_legacy_repo", legacy_factory, raising=False)
    return store, canonical, legacy_factory


@pytest.mark.parametrize("method,kwargs,_legacy_method", READS)
def test_canonical_empty_catalog_never_uses_retired_legacy_mapping_table(
    monkeypatch, mapping_repositories, method, kwargs, _legacy_method
):
    store, canonical, legacy_factory = mapping_repositories
    getattr(canonical, method).return_value = 0 if method == "count" else []
    monkeypatch.setattr(store_module, "canonical_data_required", lambda: False)

    result = getattr(store, method)(**kwargs)

    assert result == (0 if method == "count" else [])
    legacy_factory.assert_not_called()


@pytest.mark.parametrize("method,kwargs,_legacy_method", READS)
@pytest.mark.parametrize("strict", [False, True])
def test_canonical_read_failure_never_masquerades_as_empty(
    monkeypatch, mapping_repositories, method, kwargs, _legacy_method, strict
):
    store, canonical, legacy_factory = mapping_repositories
    error = RuntimeError("canonical mapping query failed")
    getattr(canonical, method).side_effect = error
    monkeypatch.setattr(store_module, "canonical_data_required", lambda: strict)

    failure_type = (
        MappingCandidateLimitExceeded
        if method == "find_candidate_routes" and not strict
        else CanonicalDataUnavailableError
    )
    with pytest.raises(failure_type) as raised:
        getattr(store, method)(**kwargs)
    assert raised.value.__cause__ is error
    legacy_factory.assert_not_called()
