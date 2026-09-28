"""Repository unit tests (SQLAlchemy session mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database.models import Indicator
from src.database.repositories.hierarchy_repository import HierarchyRepository
from src.database.repositories.indicator_repository import IndicatorRepository
from src.database.repositories.unit_repository import UnitRepository


def test_unit_repository_create_unit_sets_unit_metadata():
    db = MagicMock()
    repo = UnitRepository(db)

    unit = repo.create_unit(
        category_id=1,
        symbol="kg",
        name="kilogram",
        conversion_factor=1.0,
        conversion_offset=0.0,
        aliases=["kilogram"],
        metadata={"source": "test"},
    )

    assert getattr(unit, "unit_metadata", None) == {"source": "test"}


def test_unit_repository_create_conversion_rule_sets_rule_metadata():
    db = MagicMock()
    repo = UnitRepository(db)

    rule = repo.create_conversion_rule(
        from_unit="kg",
        to_unit="g",
        formula="value * 1000",
        reverse_formula="value / 1000",
        metadata={"source": "test"},
    )

    assert getattr(rule, "rule_metadata", None) == {"source": "test"}


def test_indicator_repository_esrs_search_uses_source_reference_for_active_canonical_rows():
    engine = create_engine("sqlite:///:memory:")
    Indicator.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add_all(
        [
            Indicator(
                id="urn:sds:reg:esrs:gross_scope_1_ghg_emissions",
                identifier="urn:sds:reg:esrs:gross_scope_1_ghg_emissions",
                title="Gross Scope 1 GHG emissions",
                dimension="E",
                code_esrs="gross_scope_1_ghg_emissions",
                source_ref="ESRS Set 1 OJ 2023-12-22 E1-6:P44",
                evidence_path="canonical://esrs/e1_6/gross_scope_1_ghg_emissions",
                is_active=True,
            ),
            Indicator(
                id="urn:sds:reg:esrs:e1_6_07",
                identifier="urn:sds:reg:esrs:e1_6_07",
                title="Retired Gross Scope 1 greenhouse gas emissions",
                dimension="E",
                code_esrs="E1-6_07",
                source_ref="ESRS ESRS_Set1_2023-12-22_OJ E1-6_07",
                evidence_path=(
                    "mappings://framework_datapoints/esrs/"
                    "esrs_set1_2023_12_22_oj/e1_6_07"
                ),
                is_active=False,
            ),
        ]
    )
    db.commit()

    repo = IndicatorRepository(db)

    assert [row.identifier for row in repo.search_by_esrs("E1-6")] == [
        "urn:sds:reg:esrs:gross_scope_1_ghg_emissions"
    ]
    assert [row.identifier for row in repo.search(esrs="E1-6")] == [
        "urn:sds:reg:esrs:gross_scope_1_ghg_emissions"
    ]
    assert [row.identifier for row in repo.search(query="E1-6")] == [
        "urn:sds:reg:esrs:gross_scope_1_ghg_emissions"
    ]


def test_unit_repository_get_units_builds_query_chain():
    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.join.return_value = query
    query.all.return_value = []

    repo = UnitRepository(db)
    assert repo.get_units(category_name="mass", active_only=True) == []
    assert query.join.called
    assert query.filter.called


def test_hierarchy_repository_activate_configuration_updates_others():
    db = MagicMock()
    repo = HierarchyRepository(db)

    deactivate_query = MagicMock()
    activate_query = MagicMock()
    db.query.side_effect = [deactivate_query, activate_query]
    deactivate_query.filter.return_value = deactivate_query
    deactivate_query.update.return_value = 1

    config = MagicMock()
    config.is_active = False
    activate_query.filter.return_value = activate_query
    activate_query.first.return_value = config

    assert (
        repo.activate_hierarchy_configuration("cfg_1", "c1", "organizational") is True
    )
    assert config.is_active is True
    db.commit.assert_called()


def test_hierarchy_repository_list_get_update_delete_and_active_lookup():
    db = MagicMock()
    repo = HierarchyRepository(db)

    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.all.return_value = ["cfg"]

    assert repo.get_hierarchy_configurations(
        company_id="c1", hierarchy_type="organizational", active_only=True
    ) == ["cfg"]

    query.first.return_value = "one"
    assert repo.get_hierarchy_configuration_by_id("cfg_1") == "one"

    # create
    db.add.reset_mock()
    db.commit.reset_mock()
    db.refresh.reset_mock()
    created = repo.create_hierarchy_configuration(
        config_id="cfg_1",
        company_id="c1",
        hierarchy_type="organizational",
        name="n",
        configuration="{}",
        description="d",
        created_by="u",
    )
    assert created is not None
    db.add.assert_called()
    db.commit.assert_called()
    db.refresh.assert_called()

    # update (found)
    config_obj = MagicMock()
    query.first.return_value = config_obj
    out = repo.update_hierarchy_configuration("cfg_1", name="new", description="d2")
    assert out is config_obj
    db.commit.assert_called()

    # update (not found)
    query.first.return_value = None
    assert repo.update_hierarchy_configuration("missing", name="x") is None

    # delete (found)
    config_obj = MagicMock()
    config_obj.is_active = True
    query.first.return_value = config_obj
    assert repo.delete_hierarchy_configuration("cfg_1") is True
    assert config_obj.is_active is False

    # delete (not found)
    query.first.return_value = None
    assert repo.delete_hierarchy_configuration("missing") is False

    # get active
    query.first.return_value = "active"
    assert repo.get_active_hierarchy_configuration("c1", "organizational") == "active"


def test_unit_repository_category_and_unit_crud_and_rules():
    db = MagicMock()
    repo = UnitRepository(db)

    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.join.return_value = query
    query.all.return_value = []
    query.first.return_value = None

    assert repo.get_unit_categories() == []
    assert repo.get_unit_category_by_name("mass") is None

    # create category
    db.add.reset_mock()
    db.commit.reset_mock()
    db.refresh.reset_mock()
    category = repo.create_unit_category(
        "mass", "kg", description="d", special_conversions=True
    )
    assert category is not None
    db.add.assert_called()
    db.commit.assert_called()
    db.refresh.assert_called()

    # unit lookup/update/delete
    query.first.return_value = MagicMock()
    assert repo.get_unit_by_symbol("kg") is not None

    unit_obj = MagicMock()
    query.first.return_value = unit_obj
    assert repo.update_unit(1, name="x") is unit_obj

    unit_obj = MagicMock()
    unit_obj.is_active = True
    query.first.return_value = unit_obj
    assert repo.delete_unit(1) is True
    assert unit_obj.is_active is False

    query.first.return_value = None
    assert repo.delete_unit(999) is False

    # conversion rules
    query.all.return_value = ["r1"]
    assert repo.get_conversion_rules(active_only=True) == ["r1"]
    assert repo.get_conversion_rules(active_only=False) == ["r1"]

    query.first.return_value = "rule"
    assert repo.get_conversion_rule("kg", "g") == "rule"
