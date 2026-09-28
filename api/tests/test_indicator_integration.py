"""Unit tests for E1 indicator integration (repository + store)."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from src.database.repositories.indicator_repository import IndicatorRepository
from src.services.indicator_store import (
    IndicatorData,
    IndicatorStore,
    InMemoryIndicatorStore,
)

# =============================================================================
# IndicatorData Tests
# =============================================================================


class TestIndicatorData:
    """Tests for IndicatorData read-only structure."""

    def test_indicator_data_from_dict(self):
        """Test IndicatorData initialization from dictionary."""
        data = {
            "id": "urn:sds:reg:1",
            "identifier": "urn:sds:reg:1",
            "title": "GHG Emissions",
            "indicator_name": "Total GHG",
            "description": "Total greenhouse gas emissions",
            "dimension": "E",
            "unit_name": "tCO2e",
            "unit_type": "mass",
            "periodicity": "annual",
            "period_type": "fiscal_year",
            "source_ref": "ESRS E1",
            "code_esrs": "E1-1",
            "code_gri": "305-1",
            "code_gri_expanded": "GRI 305-1a",
            "evidence_path": "/data/evidence/e1",
            "source_row": 5,
            "owner": "sustainability_team",
            "access_rights": "internal",
            "validation_method": "audited",
            "double_materiality": "high",
            "value_type": "numeric",
        }
        ind = IndicatorData(data)

        assert ind.id == "urn:sds:reg:1"
        assert ind.identifier == "urn:sds:reg:1"
        assert ind.title == "GHG Emissions"
        assert ind.indicator_name == "Total GHG"
        assert ind.description == "Total greenhouse gas emissions"
        assert ind.dimension == "E"
        assert ind.unit_name == "tCO2e"
        assert ind.unit_type == "mass"
        assert ind.periodicity == "annual"
        assert ind.period_type == "fiscal_year"
        assert ind.source_ref == "ESRS E1"
        assert ind.code_esrs == "E1-1"
        assert ind.code_gri == "305-1"
        assert ind.code_gri_expanded == "GRI 305-1a"
        assert ind.evidence_path == "/data/evidence/e1"
        assert ind.source_row == 5
        assert ind.owner == "sustainability_team"
        assert ind.access_rights == "internal"
        assert ind.validation_method == "audited"
        assert ind.double_materiality == "high"
        assert ind.value_type == "numeric"

    def test_indicator_data_defaults(self):
        """Test IndicatorData handles missing fields with defaults."""
        ind = IndicatorData({})

        assert ind.id == ""
        assert ind.identifier == ""
        assert ind.title == ""
        assert ind.dimension == ""
        assert ind.indicator_name is None
        assert ind.description is None
        assert ind.code_esrs is None
        assert ind.code_gri is None


# =============================================================================
# InMemoryIndicatorStore Tests
# =============================================================================


class TestInMemoryIndicatorStore:
    """Tests for JSON-based indicator store fallback."""

    def test_lazy_loading(self):
        """Test that indicators are lazily loaded from JSON."""
        store = InMemoryIndicatorStore()
        assert store._loaded is False

        # First access triggers loading
        store._ensure_loaded()
        assert store._loaded is True

    def test_load_from_json_file(self):
        """Test loading indicators from a JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "indicators.json"
            test_data = {
                "indicators": [
                    {
                        "identifier": "urn:sds:reg:1",
                        "title": "GHG Emissions",
                        "dimension": "E",
                        "code_esrs": "E1-1",
                    },
                    {
                        "identifier": "urn:sds:reg:2",
                        "title": "Water Usage",
                        "dimension": "E",
                        "code_esrs": "E3-1",
                    },
                ]
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            store = InMemoryIndicatorStore(json_path=json_path)
            assert store.count() == 2

    def test_get_all_pagination(self):
        """Test pagination in get_all."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "indicators.json"
            test_data = {
                "indicators": [
                    {
                        "identifier": f"urn:sds:reg:{i}",
                        "title": f"Indicator {i}",
                        "dimension": "E",
                    }
                    for i in range(10)
                ]
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            store = InMemoryIndicatorStore(json_path=json_path)

            # Get first page
            page1 = store.get_all(limit=3, offset=0)
            assert len(page1) == 3
            assert page1[0].identifier == "urn:sds:reg:0"

            # Get second page
            page2 = store.get_all(limit=3, offset=3)
            assert len(page2) == 3
            assert page2[0].identifier == "urn:sds:reg:3"

    def test_get_by_identifier(self):
        """Test lookup by URN identifier."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "indicators.json"
            test_data = {
                "indicators": [
                    {
                        "identifier": "urn:sds:reg:1",
                        "title": "GHG Emissions",
                        "dimension": "E",
                    },
                ]
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            store = InMemoryIndicatorStore(json_path=json_path)
            result = store.get_by_identifier("urn:sds:reg:1")

            assert result is not None
            assert result.title == "GHG Emissions"

            # Test not found
            assert store.get_by_identifier("urn:sds:reg:999") is None

    def test_search_by_dimension(self):
        """Test search by ESG dimension."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "indicators.json"
            test_data = {
                "indicators": [
                    {
                        "identifier": "urn:sds:reg:1",
                        "title": "GHG Emissions",
                        "dimension": "E",
                    },
                    {
                        "identifier": "urn:sds:reg:2",
                        "title": "Diversity",
                        "dimension": "S",
                    },
                    {"identifier": "urn:sds:reg:3", "title": "Water", "dimension": "E"},
                ]
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            store = InMemoryIndicatorStore(json_path=json_path)
            results = store.search_by_dimension("E")

            assert len(results) == 2
            assert all(r.dimension == "E" for r in results)

    def test_search_by_esrs(self):
        """Test search by ESRS code prefix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "indicators.json"
            test_data = {
                "indicators": [
                    {
                        "identifier": "urn:sds:reg:1",
                        "title": "Ind 1",
                        "dimension": "E",
                        "code_esrs": "E1-1",
                    },
                    {
                        "identifier": "urn:sds:reg:2",
                        "title": "Ind 2",
                        "dimension": "E",
                        "code_esrs": "E1-2",
                    },
                    {
                        "identifier": "urn:sds:reg:3",
                        "title": "Ind 3",
                        "dimension": "E",
                        "code_esrs": "E3-1",
                    },
                ]
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            store = InMemoryIndicatorStore(json_path=json_path)
            results = store.search_by_esrs("E1")

            assert len(results) == 2

    def test_search_by_gri(self):
        """Test search by GRI code prefix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "indicators.json"
            test_data = {
                "indicators": [
                    {
                        "identifier": "urn:sds:reg:1",
                        "title": "Ind 1",
                        "dimension": "E",
                        "code_gri": "305-1",
                    },
                    {
                        "identifier": "urn:sds:reg:2",
                        "title": "Ind 2",
                        "dimension": "E",
                        "code_gri": "305-2",
                    },
                    {
                        "identifier": "urn:sds:reg:3",
                        "title": "Ind 3",
                        "dimension": "E",
                        "code_gri": "302-1",
                    },
                ]
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            store = InMemoryIndicatorStore(json_path=json_path)
            results = store.search_by_gri("305")

            assert len(results) == 2

    def test_nonexistent_file_handled_gracefully(self):
        """Test that missing JSON file is handled gracefully."""
        store = InMemoryIndicatorStore(json_path=Path("/nonexistent/path.json"))
        store._ensure_loaded()

        assert store._loaded is True
        assert store.count() == 0


# =============================================================================
# IndicatorRepository Tests
# =============================================================================


class TestIndicatorRepository:
    """Tests for IndicatorRepository database operations."""

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

        repo = IndicatorRepository(db)
        result = repo.get_all(limit=50, offset=10)

        assert result == []
        query.filter.assert_called()
        query.order_by.assert_called()
        query.offset.assert_called_with(10)
        query.limit.assert_called_with(50)

    def test_get_all_with_changed_since_filters_created_or_updated_rows(self):
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.order_by.return_value = query
        query.offset.return_value = query
        query.limit.return_value = query
        query.all.return_value = []

        repo = IndicatorRepository(db)
        repo.get_all(changed_since=datetime(2026, 1, 1, tzinfo=timezone.utc))

        assert query.filter.call_count >= 2

    def test_get_all_inactive_included(self):
        """Test get_all without active_only filter."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.order_by.return_value = query
        query.offset.return_value = query
        query.limit.return_value = query
        query.all.return_value = []

        repo = IndicatorRepository(db)
        repo.get_all(active_only=False)

        # filter should not be called when active_only=False
        query.filter.assert_not_called()

    def test_count_active_only(self):
        """Test count with active_only filter."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.count.return_value = 42

        repo = IndicatorRepository(db)
        result = repo.count(active_only=True)

        assert result == 42
        query.filter.assert_called()

    def test_count_with_changed_since_filters_created_or_updated_rows(self):
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.count.return_value = 3

        repo = IndicatorRepository(db)
        assert repo.count(changed_since=datetime(2026, 1, 1, tzinfo=timezone.utc)) == 3
        assert query.filter.call_count >= 2

    def test_get_by_id(self):
        """Test get_by_id lookup."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        mock_indicator = MagicMock()
        query.first.return_value = mock_indicator

        repo = IndicatorRepository(db)
        result = repo.get_by_id("urn:sds:reg:1")

        assert result is mock_indicator

    def test_get_by_identifier(self):
        """Test get_by_identifier lookup."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        mock_indicator = MagicMock()
        query.first.return_value = mock_indicator

        repo = IndicatorRepository(db)
        result = repo.get_by_identifier("urn:sds:reg:1")

        assert result is mock_indicator

    def test_search_by_dimension(self):
        """Test search_by_dimension filtering."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.limit.return_value = query
        query.all.return_value = ["ind1", "ind2"]

        repo = IndicatorRepository(db)
        result = repo.search_by_dimension("E", limit=50)

        assert result == ["ind1", "ind2"]
        query.limit.assert_called_with(50)

    def test_search_by_esrs(self):
        """Test search_by_esrs filtering."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.limit.return_value = query
        query.all.return_value = ["ind1"]

        repo = IndicatorRepository(db)
        result = repo.search_by_esrs("E1-1", limit=100)

        assert result == ["ind1"]

    def test_search_by_gri(self):
        """Test search_by_gri filtering."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.limit.return_value = query
        query.all.return_value = ["ind1"]

        repo = IndicatorRepository(db)
        result = repo.search_by_gri("305", limit=100)

        assert result == ["ind1"]

    def test_search_multiple_filters(self):
        """Test search with multiple filters."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.order_by.return_value = query
        query.limit.return_value = query
        query.all.return_value = []

        repo = IndicatorRepository(db)
        result = repo.search(
            dimension="E", esrs="E1", gri="305", query="emissions", limit=50
        )

        assert result == []
        # Multiple filter calls expected
        assert query.filter.call_count >= 1

    def test_search_with_changed_since_adds_freshness_filter(self):
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.order_by.return_value = query
        query.limit.return_value = query
        query.all.return_value = []

        repo = IndicatorRepository(db)
        result = repo.search(
            query="emissions",
            changed_since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        assert result == []
        assert query.filter.call_count >= 2

    def test_create_indicator(self):
        """Test create adds and commits indicator."""
        db = MagicMock()
        repo = IndicatorRepository(db)

        data = {
            "id": "urn:sds:reg:new",
            "identifier": "urn:sds:reg:new",
            "title": "New Indicator",
            "dimension": "E",
        }

        result = repo.create(data)

        db.add.assert_called()
        db.commit.assert_called()
        db.refresh.assert_called()
        assert result is not None

    def test_update_indicator_found(self):
        """Test update modifies existing indicator."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query

        mock_indicator = MagicMock()
        mock_indicator.title = "Old Title"
        query.first.return_value = mock_indicator

        repo = IndicatorRepository(db)
        result = repo.update("urn:sds:reg:1", {"title": "New Title"})

        assert result is mock_indicator
        db.commit.assert_called()

    def test_update_indicator_not_found(self):
        """Test update returns None for missing indicator."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.first.return_value = None

        repo = IndicatorRepository(db)
        result = repo.update("urn:sds:reg:missing", {"title": "New Title"})

        assert result is None

    def test_bulk_upsert_new_records(self):
        """Test bulk_upsert inserts new records."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.first.return_value = None  # No existing records

        repo = IndicatorRepository(db)
        records = [
            {"identifier": "urn:sds:reg:1", "title": "Ind 1", "dimension": "E"},
            {"identifier": "urn:sds:reg:2", "title": "Ind 2", "dimension": "S"},
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
        mock_existing.title = "Old"
        query.first.return_value = mock_existing

        repo = IndicatorRepository(db)
        records = [{"identifier": "urn:sds:reg:1", "title": "New", "dimension": "E"}]

        count = repo.bulk_upsert(records)

        assert count == 1
        db.commit.assert_called()

    def test_bulk_upsert_reactivates_inactive_existing_identifier(self):
        """Test bulk_upsert updates retired rows instead of inserting duplicate IDs."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query

        mock_existing = MagicMock()
        mock_existing.is_active = False
        mock_existing.title = "Old"
        query.first.return_value = mock_existing

        repo = IndicatorRepository(db)
        records = [
            {
                "id": "urn:sds:reg:gri:gri_sf_11_10_2_401_1_a",
                "identifier": "urn:sds:reg:gri:gri_sf_11_10_2_401_1_a",
                "title": "New",
                "dimension": "S",
            }
        ]

        count = repo.bulk_upsert(records)

        assert count == 1
        assert mock_existing.title == "New"
        assert mock_existing.is_active is True
        db.add.assert_not_called()
        db.commit.assert_called()

    def test_bulk_upsert_skips_missing_identifier(self):
        """Test bulk_upsert skips records without identifier."""
        db = MagicMock()
        repo = IndicatorRepository(db)

        records = [
            {"title": "No Identifier", "dimension": "E"},
            {
                "identifier": "urn:sds:reg:1",
                "title": "Has Identifier",
                "dimension": "E",
            },
        ]

        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.first.return_value = None

        count = repo.bulk_upsert(records)

        # Only one record should be processed (the one with identifier)
        assert count == 1

    def test_bulk_upsert_can_flush_without_commit(self):
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.first.return_value = None
        repo = IndicatorRepository(db)

        count = repo.bulk_upsert(
            [{"identifier": "urn:sds:reg:1", "title": "Has Identifier"}],
            commit=False,
        )

        assert count == 1
        db.flush.assert_called_once()
        db.commit.assert_not_called()

    def test_bulk_upsert_rolls_back_committed_write_failures(self):
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.first.return_value = None
        db.commit.side_effect = RuntimeError("db down")
        repo = IndicatorRepository(db)

        try:
            repo.bulk_upsert([{"identifier": "urn:sds:reg:1"}])
        except RuntimeError as exc:
            assert str(exc) == "db down"
        else:
            raise AssertionError("bulk_upsert should re-raise commit errors")

        db.rollback.assert_called_once()

    def test_delete_soft_deletes(self):
        """Test delete performs soft delete."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query

        mock_indicator = MagicMock()
        mock_indicator.is_active = True
        query.first.return_value = mock_indicator

        repo = IndicatorRepository(db)
        result = repo.delete("urn:sds:reg:1")

        assert result is True
        assert mock_indicator.is_active is False
        db.commit.assert_called()

    def test_delete_returns_false_not_found(self):
        """Test delete returns False for missing indicator."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.first.return_value = None

        repo = IndicatorRepository(db)
        result = repo.delete("urn:sds:reg:missing")

        assert result is False


# =============================================================================
# IndicatorStore Tests (Hybrid DB + Fallback)
# =============================================================================


class TestIndicatorStore:
    """Tests for hybrid IndicatorStore with DB primary and JSON fallback."""

    def test_uses_db_when_available(self):
        """Test store uses database when session provided."""
        db = MagicMock()
        store = IndicatorStore(db=db)

        assert store.is_db_available() is True
        assert store._get_repo() is not None

    def test_uses_fallback_when_no_db(self):
        """Test store uses fallback when no database session."""
        store = IndicatorStore(db=None)

        assert store.is_db_available() is False
        assert store._get_repo() is None

    def test_get_all_db_success(self):
        """Test get_all succeeds via database."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.order_by.return_value = query
        query.offset.return_value = query
        query.limit.return_value = query
        query.all.return_value = ["ind1", "ind2"]

        store = IndicatorStore(db=db)
        result = store.get_all(limit=10)

        assert result == ["ind1", "ind2"]

    def test_get_all_db_failure_uses_fallback(self):
        """Test get_all falls back to JSON on DB error."""
        db = MagicMock()
        db.query.side_effect = Exception("DB connection failed")

        store = IndicatorStore(db=db)

        # Inject a mock fallback store
        store._fallback._loaded = True
        store._fallback._indicators = {
            "urn:sds:reg:1": IndicatorData(
                {"identifier": "urn:sds:reg:1", "title": "Fallback", "dimension": "E"}
            )
        }

        result = store.get_all(limit=10)

        assert len(result) == 1
        assert result[0].title == "Fallback"

    def test_count_db_success(self):
        """Test count succeeds via database."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        query.count.return_value = 100

        store = IndicatorStore(db=db)
        result = store.count()

        assert result == 100

    def test_count_db_failure_uses_fallback(self):
        """Test count falls back to JSON on DB error."""
        db = MagicMock()
        db.query.side_effect = Exception("DB error")

        store = IndicatorStore(db=db)
        store._fallback._loaded = True
        store._fallback._indicators = {"a": None, "b": None}

        result = store.count()

        assert result == 2

    def test_get_by_identifier_db_success(self):
        """Test get_by_identifier via database."""
        db = MagicMock()
        query = MagicMock()
        db.query.return_value = query
        query.filter.return_value = query
        mock_ind = MagicMock()
        query.first.return_value = mock_ind

        store = IndicatorStore(db=db)
        result = store.get_by_identifier("urn:sds:reg:1")

        assert result is mock_ind

    def test_search_by_dimension_db_failure_uses_fallback(self):
        """Test search_by_dimension falls back on DB error."""
        db = MagicMock()
        db.query.side_effect = Exception("DB error")

        store = IndicatorStore(db=db)
        store._fallback._loaded = True
        store._fallback._indicators = {
            "a": IndicatorData({"identifier": "a", "title": "A", "dimension": "E"}),
            "b": IndicatorData({"identifier": "b", "title": "B", "dimension": "S"}),
        }

        result = store.search_by_dimension("E")

        assert len(result) == 1
        assert result[0].dimension == "E"

    def test_search_by_esrs_db_failure_uses_fallback(self):
        """Test search_by_esrs falls back on DB error."""
        db = MagicMock()
        db.query.side_effect = Exception("DB error")

        store = IndicatorStore(db=db)
        store._fallback._loaded = True
        store._fallback._indicators = {
            "a": IndicatorData(
                {"identifier": "a", "title": "A", "dimension": "E", "code_esrs": "E1-1"}
            ),
            "b": IndicatorData(
                {"identifier": "b", "title": "B", "dimension": "E", "code_esrs": "E3-1"}
            ),
        }

        result = store.search_by_esrs("E1")

        assert len(result) == 1
        assert result[0].code_esrs == "E1-1"

    def test_search_by_gri_db_failure_uses_fallback(self):
        """Test search_by_gri falls back on DB error."""
        db = MagicMock()
        db.query.side_effect = Exception("DB error")

        store = IndicatorStore(db=db)
        store._fallback._loaded = True
        store._fallback._indicators = {
            "a": IndicatorData(
                {"identifier": "a", "title": "A", "dimension": "E", "code_gri": "305-1"}
            ),
            "b": IndicatorData(
                {"identifier": "b", "title": "B", "dimension": "E", "code_gri": "302-1"}
            ),
        }

        result = store.search_by_gri("305")

        assert len(result) == 1
        assert result[0].code_gri == "305-1"
