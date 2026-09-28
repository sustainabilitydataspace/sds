"""Unit tests for E2 standard mapping integration (repository + store)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.database.repositories.standard_mapping_repository import (
    StandardMappingRepository,
)
from src.services import standard_mapping_store as store_module
from src.services.canonical_data import CanonicalDataUnavailableError
from src.services.standard_mapping_store import (
    InMemoryStandardMappingStore,
    StandardMappingData,
    StandardMappingStore,
)

# =============================================================================
# StandardMappingData Tests
# =============================================================================


class TestStandardMappingData:
    """Tests for StandardMappingData read-only structure."""

    def test_standard_mapping_data_from_dict(self):
        """Test StandardMappingData initialization from dictionary."""
        data = {
            "id": 1,
            "source_standard": "ESRS",
            "source_code": "E1-1",
            "source_label": "total_emissions",
            "target_standard": "GRI",
            "target_code": "305-1",
            "target_label": "GRI 305-1a",
            "esg_dimension": "E",
            "dataset": "ghg_emissions",
            "relationship_type": "equivalent",
            "confidence": 0.95,
            "source_row": 10,
        }
        mapping = StandardMappingData(data)

        assert mapping.id == 1
        assert mapping.source_standard == "ESRS"
        assert mapping.source_code == "E1-1"
        assert mapping.source_label == "total_emissions"
        assert mapping.target_standard == "GRI"
        assert mapping.target_code == "305-1"
        assert mapping.target_label == "GRI 305-1a"
        assert mapping.esg_dimension == "E"
        assert mapping.dataset == "ghg_emissions"
        assert mapping.relationship_type == "equivalent"
        assert mapping.confidence == 0.95
        assert mapping.source_row == 10

    def test_standard_mapping_data_defaults(self):
        """Test StandardMappingData handles missing fields with defaults."""
        mapping = StandardMappingData({})

        assert mapping.id == 0
        assert mapping.source_standard == ""
        assert mapping.source_code == ""
        assert mapping.target_standard == ""
        assert mapping.target_code is None
        assert mapping.esg_dimension is None
        assert mapping.dataset is None
        assert mapping.source_label is None
        assert mapping.relationship_type == "equivalent"
        assert mapping.source_row is None


# =============================================================================
# InMemoryStandardMappingStore Tests
# =============================================================================


class TestInMemoryStandardMappingStore:
    """Tests for JSON-based mapping store fallback."""

    def test_lazy_loading(self):
        """Test that mappings are lazily loaded from JSON."""
        store = InMemoryStandardMappingStore()
        assert store._loaded is False

        # First access triggers loading
        store._ensure_loaded()
        assert store._loaded is True

    def test_load_from_json_file(self):
        """Test loading mappings from a JSON file."""
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
                        "source_code": "E1-2",
                        "target_standard": "GRI",
                        "target_code": "305-2",
                        "esg_dimension": "E",
                    },
                ]
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            store = InMemoryStandardMappingStore(json_path=json_path)
            assert store.count() == 2

    def test_get_all_pagination(self):
        """Test pagination in get_all."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "mappings.json"
            test_data = {
                "mappings": [
                    {
                        "source_standard": "ESRS",
                        "source_code": f"E{i}",
                        "target_standard": "GRI",
                        "target_code": f"30{i}-1",
                        "esg_dimension": "E",
                    }
                    for i in range(10)
                ]
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            store = InMemoryStandardMappingStore(json_path=json_path)

            # Get first page
            page1 = store.get_all(limit=3, offset=0)
            assert len(page1) == 3
            assert page1[0].source_code == "E0"

            # Get second page
            page2 = store.get_all(limit=3, offset=3)
            assert len(page2) == 3
            assert page2[0].source_code == "E3"

    def test_find_by_source(self):
        """Test finding mappings by source standard/code prefix."""
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
                        "source_code": "E1-2",
                        "target_standard": "GRI",
                        "target_code": "305-2",
                        "esg_dimension": "E",
                    },
                    {
                        "source_standard": "ESRS",
                        "source_code": "E3-1",
                        "target_standard": "GRI",
                        "target_code": "303-1",
                        "esg_dimension": "E",
                    },
                ]
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            store = InMemoryStandardMappingStore(json_path=json_path)
            results = store.find_by_source("ESRS", "E1")

            assert len(results) == 2
            assert all(r.source_code.startswith("E1") for r in results)

    def test_find_by_target(self):
        """Test finding mappings by target standard/code prefix."""
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
                        "source_code": "E1-2",
                        "target_standard": "GRI",
                        "target_code": "305-2",
                        "esg_dimension": "E",
                    },
                    {
                        "source_standard": "ESRS",
                        "source_code": "E3-1",
                        "target_standard": "GRI",
                        "target_code": "303-1",
                        "esg_dimension": "E",
                    },
                ]
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            store = InMemoryStandardMappingStore(json_path=json_path)
            results = store.find_by_target("GRI", "305")

            assert len(results) == 2
            assert all(r.target_code.startswith("305") for r in results)

    def test_search_by_dimension(self):
        """Test searching mappings by ESG dimension."""
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
                    {
                        "source_standard": "ESRS",
                        "source_code": "E3-1",
                        "target_standard": "GRI",
                        "target_code": "303-1",
                        "esg_dimension": "E",
                    },
                ]
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            store = InMemoryStandardMappingStore(json_path=json_path)
            results = store.search_by_dimension("E")

            assert len(results) == 2
            assert all(r.esg_dimension == "E" for r in results)

    def test_nonexistent_file_handled_gracefully(self):
        """Test that missing JSON file is handled gracefully."""
        store = InMemoryStandardMappingStore(json_path=Path("/nonexistent/path.json"))
        store._ensure_loaded()

        assert store._loaded is True
        assert store.count() == 0

    def test_find_by_source_case_insensitive(self):
        """Test that source standard search is case-insensitive."""
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
                ]
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            store = InMemoryStandardMappingStore(json_path=json_path)

            # Lowercase search
            results = store.find_by_source("esrs", "e1")
            assert len(results) == 1

    def test_find_by_target_case_insensitive(self):
        """Test that target standard search is case-insensitive."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "mappings.json"
            test_data = {
                "mappings": [
                    {
                        "source_standard": "ESRS",
                        "source_code": "E1-1",
                        "target_standard": "GRI",
                        "target_code": "GRI305-1",
                        "esg_dimension": "E",
                    },
                ]
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            store = InMemoryStandardMappingStore(json_path=json_path)

            # Lowercase search
            results = store.find_by_target("gri", "gri305")
            assert len(results) == 1


# =============================================================================
# StandardMappingRepository Tests
# =============================================================================


class TestStandardMappingRepository:
    """Tests for StandardMappingRepository database operations."""

    def test_get_all_builds_query(self):
        """Test get_all constructs proper query chain."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.order_by.return_value = query
        query.offset.return_value = query
        query.limit.return_value = query
        query.all.return_value = []

        repo = StandardMappingRepository(db)
        result = repo.get_all(limit=50, offset=10)

        assert result == []
        query.filter.assert_called()
        query.order_by.assert_called()
        query.offset.assert_called_with(10)
        query.limit.assert_called_with(50)

    def test_get_all_inactive_included(self):
        """Test get_all without active_only filter."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.order_by.return_value = query
        query.offset.return_value = query
        query.limit.return_value = query
        query.all.return_value = []

        repo = StandardMappingRepository(db)
        repo.get_all(active_only=False)

        # filter should not be called when active_only=False
        query.filter.assert_not_called()

    def test_count_active_only(self):
        """Test count with active_only filter."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.count.return_value = 1501

        repo = StandardMappingRepository(db)
        result = repo.count(active_only=True)

        assert result == 1501
        query.filter.assert_called()

    def test_get_by_id(self):
        """Test get_by_id lookup."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        mock_mapping = MagicMock()
        query.first.return_value = mock_mapping

        repo = StandardMappingRepository(db)
        result = repo.get_by_id(1)

        assert result is mock_mapping

    def test_find_by_source(self):
        """Test find_by_source filtering."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.order_by.return_value = query
        query.limit.return_value = query
        query.all.return_value = ["cw1", "cw2"]

        repo = StandardMappingRepository(db)
        result = repo.find_by_source("ESRS", "E1", limit=50)

        assert result == ["cw1", "cw2"]
        query.limit.assert_called_with(50)

    def test_find_by_target(self):
        """Test find_by_target filtering."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.order_by.return_value = query
        query.limit.return_value = query
        query.all.return_value = ["cw1"]

        repo = StandardMappingRepository(db)
        result = repo.find_by_target("GRI", "305", limit=100)

        assert result == ["cw1"]

    def test_search_by_dimension(self):
        """Test search_by_dimension filtering."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.order_by.return_value = query
        query.limit.return_value = query
        query.all.return_value = ["cw1", "cw2", "cw3"]

        repo = StandardMappingRepository(db)
        result = repo.search(dimension="E", limit=100)

        assert len(result) == 3

    def test_search_multiple_filters(self):
        """Test search with multiple filters."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.order_by.return_value = query
        query.limit.return_value = query
        query.all.return_value = []

        repo = StandardMappingRepository(db)
        result = repo.search(
            source_standard="ESRS",
            source_code="E1",
            target_standard="GRI",
            target_code="305",
            dimension="E",
            limit=50,
        )

        assert result == []
        # Multiple filter calls expected
        assert query.filter.call_count >= 1

    def test_create_mapping(self):
        """Test create adds and commits mapping."""
        db = MagicMock()
        repo = StandardMappingRepository(db)

        data = {
            "source_standard": "ESRS",
            "source_code": "E1-1",
            "target_standard": "GRI",
            "target_code": "305-1",
            "esg_dimension": "E",
            "relationship_type": "equivalent",
        }

        result = repo.create(data)

        db.add.assert_called()
        db.commit.assert_called()
        db.refresh.assert_called()
        assert result is not None

    def test_bulk_upsert_new_records(self):
        """Test bulk_upsert inserts new records."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.first.return_value = None  # No existing records

        repo = StandardMappingRepository(db)
        records = [
            {
                "source_standard": "ESRS",
                "source_code": "E1-1",
                "target_standard": "GRI",
                "target_code": "305-1",
                "esg_dimension": "E",
            },
            {
                "source_standard": "ESRS",
                "source_code": "E1-2",
                "target_standard": "GRI",
                "target_code": "305-2",
                "esg_dimension": "E",
            },
        ]

        count = repo.bulk_upsert(records)

        assert count == 2
        assert db.add.call_count == 2
        db.commit.assert_called()

    def test_bulk_upsert_updates_existing(self):
        """Test bulk_upsert updates existing records."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query

        mock_existing = MagicMock()
        mock_existing.esg_dimension = "E"
        query.first.return_value = mock_existing

        repo = StandardMappingRepository(db)
        records = [
            {
                "source_standard": "ESRS",
                "source_code": "E1-1",
                "target_standard": "GRI",
                "target_code": "305-1",
                "esg_dimension": "E",
            }
        ]

        count = repo.bulk_upsert(records)

        assert count == 1
        db.commit.assert_called()

    def test_bulk_upsert_skips_missing_source_standard(self):
        """Test bulk_upsert skips records without source_standard."""
        db = MagicMock()
        repo = StandardMappingRepository(db)

        records = [
            {"target_code": "305-1", "esg_dimension": "E"},  # No source_standard
            {
                "source_standard": "ESRS",
                "source_code": "E1-1",
                "target_standard": "GRI",
                "target_code": "305-1",
                "esg_dimension": "E",
            },
        ]

        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.first.return_value = None

        count = repo.bulk_upsert(records)

        # Only one record should be processed
        assert count == 1

    def test_delete_soft_deletes(self):
        """Test delete performs soft delete."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query

        mock_mapping = MagicMock()
        mock_mapping.is_active = True
        query.first.return_value = mock_mapping

        repo = StandardMappingRepository(db)
        result = repo.delete(1)

        assert result is True
        assert mock_mapping.is_active is False
        db.commit.assert_called()

    def test_delete_returns_false_not_found(self):
        """Test delete returns False for missing mapping."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.first.return_value = None

        repo = StandardMappingRepository(db)
        result = repo.delete(999)

        assert result is False


# =============================================================================
# StandardMappingStore Tests (Canonical DB Surface)
# =============================================================================


class TestStandardMappingStore:
    """Tests for the canonical StandardMappingStore."""

    def test_uses_db_when_available(self):
        """Test store uses database when session provided."""
        db = MagicMock()
        store = StandardMappingStore(db=db)

        assert store.is_db_available() is True
        assert store._get_repo() is not None

    def test_uses_fallback_when_no_db(self):
        """Test store uses fallback when no database session."""
        store = StandardMappingStore(db=None)

        assert store.is_db_available() is False
        assert store._get_repo() is None

    def test_get_all_db_success(self, monkeypatch):
        """Test get_all succeeds via canonical database repository."""
        db = MagicMock()

        class FakeCanonicalRepo:
            def __init__(self, db):
                self.db = db

            def get_all(self, limit=100, offset=0, changed_since=None):
                assert limit == 10
                return ["cw1", "cw2"]

        monkeypatch.setattr(
            store_module, "CanonicalPairwiseMappingRepository", FakeCanonicalRepo
        )

        store = StandardMappingStore(db=db)
        result = store.get_all(limit=10)

        assert result == ["cw1", "cw2"]

    def test_canonical_surface_uses_materialized_pairwise_repository(self, monkeypatch):
        """Configured canonical mode should read the generated pairwise view."""
        db = MagicMock()
        canonical_row = StandardMappingData(
            {
                "id": 9,
                "source_standard": "ESRS",
                "source_code": "E3-4_11",
                "target_standard": "GRI",
                "target_code": "GRI 303-3.a",
                "relationship_type": "partial",
                "confidence": 1.0,
                "dataset": "canonical_pairwise_mappings",
            }
        )

        class FakeCanonicalRepo:
            def __init__(self, db):
                self.db = db

            def find_by_source(self, standard, code, limit=100):
                assert standard == "ESRS"
                assert code == "E3-4_11"
                assert limit == 100
                return [canonical_row]

        monkeypatch.setattr(
            store_module, "CanonicalPairwiseMappingRepository", FakeCanonicalRepo
        )

        store = StandardMappingStore(db=db)
        result = store.find_by_source("ESRS", "E3-4_11")

        assert result == [canonical_row]

    def test_get_all_db_failure_rejects_instead_of_serving_legacy(self):
        """DB errors must not masquerade as an empty canonical catalog."""
        db = MagicMock()
        db.query.side_effect = Exception("DB connection failed")

        store = StandardMappingStore(db=db)
        with pytest.raises(CanonicalDataUnavailableError):
            store.get_all(limit=10)

    def test_count_db_success(self, monkeypatch):
        """Test count succeeds via canonical database repository."""
        db = MagicMock()

        class FakeCanonicalRepo:
            def __init__(self, db):
                self.db = db

            def count(self, changed_since=None):
                return 1501

        monkeypatch.setattr(
            store_module, "CanonicalPairwiseMappingRepository", FakeCanonicalRepo
        )

        store = StandardMappingStore(db=db)
        result = store.count()

        assert result == 1501

    def test_count_db_failure_rejects_instead_of_returning_zero(self):
        """DB errors must not masquerade as a zero canonical count."""
        db = MagicMock()
        db.query.side_effect = Exception("DB error")

        store = StandardMappingStore(db=db)
        with pytest.raises(CanonicalDataUnavailableError):
            store.count()

    def test_find_by_source_db_success(self, monkeypatch):
        """Test find_by_source via canonical database repository."""
        db = MagicMock()

        class FakeCanonicalRepo:
            def __init__(self, db):
                self.db = db

            def find_by_source(self, standard, code, limit=100):
                assert standard == "ESRS"
                assert code == "E1"
                return ["cw1"]

        monkeypatch.setattr(
            store_module, "CanonicalPairwiseMappingRepository", FakeCanonicalRepo
        )

        store = StandardMappingStore(db=db)
        result = store.find_by_source("ESRS", "E1")

        assert result == ["cw1"]

    def test_find_by_source_db_failure_rejects_instead_of_serving_legacy(self):
        """A failed canonical source lookup cannot authorize a legacy row."""
        db = MagicMock()
        db.query.side_effect = Exception("DB error")

        store = StandardMappingStore(db=db)
        with pytest.raises(CanonicalDataUnavailableError):
            store.find_by_source("ESRS", "E1")

    def test_find_by_target_db_failure_rejects_instead_of_serving_legacy(self):
        """A failed canonical target lookup cannot authorize a legacy row."""
        db = MagicMock()
        db.query.side_effect = Exception("DB error")

        store = StandardMappingStore(db=db)
        with pytest.raises(CanonicalDataUnavailableError):
            store.find_by_target("GRI", "305")

    def test_search_by_dimension_db_failure_rejects_instead_of_serving_legacy(self):
        """A failed canonical dimension search cannot authorize a legacy row."""
        db = MagicMock()
        db.query.side_effect = Exception("DB error")

        store = StandardMappingStore(db=db)
        with pytest.raises(CanonicalDataUnavailableError):
            store.search_by_dimension("E")
