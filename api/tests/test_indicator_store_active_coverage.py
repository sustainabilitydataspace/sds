from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from src.services import indicator_store as mod
from src.services.indicator_store import IndicatorStore, InMemoryIndicatorStore


def _indicator(identifier: str = "urn:sds:reg:esrs:e1_6") -> SimpleNamespace:
    return SimpleNamespace(
        id=identifier,
        identifier=identifier,
        title="Gross GHG emissions",
        description="Scope disclosure",
        dimension="E",
        code_esrs="E1-6",
        code_gri="305-1",
        updated_at=datetime.now(timezone.utc),
    )


def test_in_memory_indicator_store_filters_changed_since_and_bad_json(tmp_path):
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{bad json", encoding="utf-8")
    store = InMemoryIndicatorStore(json_path=bad_json)

    assert store.get_all() == []
    assert store.count(changed_since=datetime.now(timezone.utc)) == 0
    assert store.get_calculation_value_concept("anything") is None

    path = tmp_path / "indicators.json"
    path.write_text(
        json.dumps(
            {
                "indicators": [
                    {
                        "id": "1",
                        "identifier": "urn:sds:reg:esrs:e1_6",
                        "title": "Gross GHG emissions",
                        "description": "Scope disclosure",
                        "dimension": "E",
                        "code_esrs": "E1-6",
                        "code_gri": "305-1",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    populated = InMemoryIndicatorStore(json_path=path)

    assert populated.count() == 1
    assert populated.count(changed_since=datetime.now(timezone.utc)) == 0
    assert populated.get_all(changed_since=datetime.now(timezone.utc)) == []
    assert populated.search(esrs="E1", gri="305", query="gross")[0].identifier == (
        "urn:sds:reg:esrs:e1_6"
    )
    assert populated.search(changed_since=datetime.now(timezone.utc)) == []


def test_indicator_store_db_success_and_fallback_branches(monkeypatch):
    indicator = _indicator()

    class _Repo:
        def __init__(self, _db):
            pass

        def get_all(self, **_kwargs):
            return [indicator]

        def count(self, **_kwargs):
            return 1

        def get_by_identifier(self, _identifier):
            return indicator

        def search_by_dimension(self, _dimension, limit=100):
            return [indicator]

        def search_by_esrs(self, _code, limit=100):
            return [indicator]

        def search_by_gri(self, _code, limit=100):
            return [indicator]

        def search(self, **_kwargs):
            return [indicator]

    monkeypatch.setattr(mod, "IndicatorRepository", _Repo)
    store = IndicatorStore(db=object())

    assert store.is_db_available() is True
    assert store.get_all() == [indicator]
    assert store.count() == 1
    assert store.get_by_identifier(indicator.identifier) == indicator
    assert store.search_by_dimension("E") == [indicator]
    assert store.search_by_esrs("E1") == [indicator]
    assert store.search_by_gri("305") == [indicator]
    assert store.search(query="gross") == [indicator]
    assert store.get_calculation_value_concept("") is None

    class _FailingRepo(_Repo):
        def get_all(self, **_kwargs):
            raise RuntimeError("db failed")

        def count(self, **_kwargs):
            raise RuntimeError("db failed")

        def get_by_identifier(self, _identifier):
            raise RuntimeError("db failed")

        def search_by_dimension(self, _dimension, limit=100):
            raise RuntimeError("db failed")

        def search_by_esrs(self, _code, limit=100):
            raise RuntimeError("db failed")

        def search_by_gri(self, _code, limit=100):
            raise RuntimeError("db failed")

        def search(self, **_kwargs):
            raise RuntimeError("db failed")

    fallback = SimpleNamespace(
        get_all=lambda **_kwargs: ["fallback-all"],
        count=lambda **_kwargs: 7,
        get_by_identifier=lambda _identifier: "fallback-one",
        search_by_dimension=lambda _dimension, limit=100: ["fallback-dimension"],
        search_by_esrs=lambda _code, limit=100: ["fallback-esrs"],
        search_by_gri=lambda _code, limit=100: ["fallback-gri"],
        search=lambda **_kwargs: ["fallback-search"],
        get_calculation_value_concept=lambda _concept: "fallback-concept",
    )
    monkeypatch.setattr(mod, "IndicatorRepository", _FailingRepo)
    failing = IndicatorStore(db=object())
    failing._fallback = fallback

    assert failing.get_all() == ["fallback-all"]
    assert failing.count() == 7
    assert failing.get_by_identifier("x") == "fallback-one"
    assert failing.search_by_dimension("E") == ["fallback-dimension"]
    assert failing.search_by_esrs("E1") == ["fallback-esrs"]
    assert failing.search_by_gri("305") == ["fallback-gri"]
    assert failing.search(query="gross") == ["fallback-search"]

    class _BadDb:
        def query(self, _model):
            raise RuntimeError("lookup failed")

    lookup = IndicatorStore(db=_BadDb())
    lookup._fallback = fallback
    assert lookup.get_calculation_value_concept("concept") == "fallback-concept"


def test_indicator_store_factory_returns_db_bound_store():
    store = mod.get_indicator_store(db=object())
    assert isinstance(store, IndicatorStore)
    assert store.is_db_available() is True
