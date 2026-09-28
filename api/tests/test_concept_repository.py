"""Repository tests for the canonical semantic concept tables."""

from __future__ import annotations

from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from src.database.models import (
    Concept,
    ConceptEquivalence,
    ConceptFormula,
    ConceptIndicatorLink,
    ConceptState,
    ConceptVariable,
    Indicator,
)
from src.database.repositories.concept_repository import ConceptRepository


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


def _create_concept_repository_tables(engine):
    for table in (
        Indicator.__table__,
        Concept.__table__,
        ConceptFormula.__table__,
        ConceptVariable.__table__,
        ConceptEquivalence.__table__,
        ConceptIndicatorLink.__table__,
    ):
        table.create(engine)


def _session():
    engine = create_engine("sqlite:///:memory:")
    _create_concept_repository_tables(engine)
    return sessionmaker(bind=engine)()


def _db_concept(**overrides):
    defaults = {
        "uri": "urn:sds:reg:esrs:e3_4_01",
        "label": "Water consumption",
        "description": "Water consumption indicator",
        "taxonomy": "CSRD",
        "concept_type": "Indicator",
        "concept_state": ConceptState.CATALOGUED,
        "projection_source": "indicator_catalog",
    }
    defaults.update(overrides)
    return Concept(**defaults)


def test_list_concepts_applies_filters_and_pagination():
    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.options.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    query.all.return_value = []

    repo = ConceptRepository(db)
    repo.list_concepts(
        taxonomy="CSRD",
        concept_type="disclosure",
        concept_state="catalogued",
        limit=25,
        offset=5,
    )

    query.filter.assert_called()
    query.order_by.assert_called()
    query.offset.assert_called_with(5)
    query.limit.assert_called_with(25)


def test_indicator_concept_type_normalizes_to_indicator():
    assert ConceptRepository._normalize_concept_type("Indicator") == "Indicator"
    assert ConceptRepository._normalize_concept_type(" indicator ") == "Indicator"


def test_search_concepts_uses_text_filters():
    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.options.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    query.all.return_value = []

    repo = ConceptRepository(db)
    repo.search_concepts(
        query="water", taxonomy="CSRD", concept_type="variable", limit=10
    )

    query.filter.assert_called()
    query.order_by.assert_called()
    query.limit.assert_called_with(10)


def test_count_concepts_and_equivalences_query_shape():
    db = MagicMock()
    count_query = MagicMock()
    db.query.return_value = count_query
    count_query.filter.return_value = count_query
    count_query.count.return_value = 3

    repo = ConceptRepository(db)
    assert (
        repo.count_concepts(taxonomy="csrd", concept_type="indicator", search="water")
        == 3
    )
    count_query.filter.assert_called()
    count_query.count.assert_called_once()

    equiv_query = MagicMock()
    db.query.return_value = equiv_query
    equiv_query.join.return_value = equiv_query
    equiv_query.filter.return_value = equiv_query
    equiv_query.order_by.return_value = equiv_query
    equiv_query.all.return_value = []

    repo.list_equivalences(
        concept_uri="https://data.efrag.org/esrs#E3_5",
        compact_concept_uri="csrd:E3_5",
        source_taxonomy="CSRD",
        target_taxonomy="GRI",
    )
    equiv_query.join.assert_called()
    equiv_query.filter.assert_called()
    equiv_query.order_by.assert_called()


def test_list_variables_and_indicator_links_query_shape():
    db = MagicMock()

    variable_query = MagicMock()
    db.query.return_value = variable_query
    variable_query.join.return_value = variable_query
    variable_query.filter.return_value = variable_query
    variable_query.order_by.return_value = variable_query
    variable_query.offset.return_value = variable_query
    variable_query.limit.return_value = variable_query
    variable_query.all.return_value = []

    repo = ConceptRepository(db)
    repo.list_variables(taxonomy="CSRD", limit=5, offset=2)
    variable_query.join.assert_called()
    variable_query.offset.assert_called_with(2)
    variable_query.limit.assert_called_with(5)

    link_query = MagicMock()
    db.query.return_value = link_query
    link_query.filter.return_value = link_query
    link_query.order_by.return_value = link_query
    link_query.all.return_value = []

    repo.get_indicator_links(concept_id=1)
    link_query.filter.assert_called()
    link_query.order_by.assert_called()


def test_concept_repository_edge_helpers_and_delegating_paths():
    assert ConceptRepository._normalize_concept_type(None) is None

    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.options.return_value = query
    query.filter.return_value = query
    query.first.return_value = None
    query.order_by.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    query.all.return_value = []

    repo = ConceptRepository(db)
    assert repo.get_by_uri_with_children("csrd:E1_6") is None
    assert repo.search_concepts(query="   ", taxonomy="CSRD") == []
    query.options.assert_called()


def test_public_catalog_uses_projected_disclosures_and_sygris_unified_view():
    db = _session()
    db.add_all(
        [
            _db_concept(
                uri="https://data.efrag.org/esrs#E3_5",
                label="Total water consumption",
                description="Legacy calculable disclosure support row",
                concept_type="Disclosure",
                projection_source=None,
            ),
            _db_concept(
                uri="urn:sds:disclosure:csrd:e3-5",
                label="Total water consumption",
                description="Projected disclosure catalog row",
                concept_type="Disclosure",
                projection_source="disclosure_catalog",
            ),
            _db_concept(),
            _db_concept(
                uri="https://data.globalreporting.org/gri#303_3",
                label="Water withdrawal",
                description="Legacy GRI disclosure support row",
                taxonomy="GRI",
                concept_type="Disclosure",
                projection_source=None,
            ),
            _db_concept(
                uri="urn:sds:disclosure:gri:303-3",
                label="Water withdrawal",
                description="Projected GRI disclosure catalog row",
                taxonomy="GRI",
                concept_type="Disclosure",
                projection_source="disclosure_catalog",
            ),
            _db_concept(
                uri="csrd:UNCONTROLLED_DISCLOSURE",
                label="Uncontrolled legacy disclosure",
                description="Legacy disclosure row that is not part of the public catalog.",
                taxonomy="CSRD",
                concept_type="Disclosure",
                projection_source=None,
            ),
            _db_concept(
                uri="urn:sds:reg:sygris:site-energy",
                label="Site energy",
                description="Operational Sygris catalog indicator",
                taxonomy="Sygris",
            ),
            _db_concept(
                uri="urn:sds:reg:ghg:scope3_category1_supplier_emissions",
                label="Scope 3 Category 1 supplier emissions",
                description="GHG Protocol catalog indicator",
                taxonomy="GHG",
                projection_source="standard_datapoint_catalog",
            ),
            _db_concept(
                uri="syg:mapping-pivot",
                label="Mapping pivot",
                description="Legacy mapping pivot support row",
                taxonomy="Sygris",
                concept_type="MappingPivot",
                projection_source=None,
            ),
        ]
    )
    db.commit()

    repo = ConceptRepository(db)

    assert repo.count_concepts(taxonomy="CSRD", public_catalog=True) == 2
    assert (
        repo.count_concepts(
            taxonomy="CSRD", concept_type="Indicator", public_catalog=True
        )
        == 1
    )
    assert (
        repo.count_concepts(
            taxonomy="CSRD", concept_type="Disclosure", public_catalog=True
        )
        == 1
    )
    assert (
        repo.count_concepts(
            taxonomy="GRI", concept_type="Disclosure", public_catalog=True
        )
        == 1
    )
    assert repo.count_concepts(taxonomy="GHG", public_catalog=True) == 1
    assert repo.count_concepts(taxonomy="Sygris", public_catalog=True) == 5
    assert (
        repo.count_concepts(
            taxonomy="Sygris", concept_type="Indicator", public_catalog=True
        )
        == 3
    )
    assert (
        repo.count_concepts(
            taxonomy="Sygris", concept_type="Disclosure", public_catalog=True
        )
        == 2
    )

    sygris_rows = repo.list_concepts(taxonomy="Sygris", public_catalog=True)
    assert {concept.uri for concept in sygris_rows} == {
        "urn:sds:disclosure:csrd:e3-5",
        "urn:sds:disclosure:gri:303-3",
        "urn:sds:reg:ghg:scope3_category1_supplier_emissions",
        "urn:sds:reg:esrs:e3_4_01",
        "urn:sds:reg:sygris:site-energy",
    }
    assert (
        repo.get_public_by_uri_with_children("urn:sds:disclosure:csrd:e3-5") is not None
    )
    assert (
        repo.get_public_by_uri_with_children("https://data.efrag.org/esrs#E3_5") is None
    )
    assert repo.get_public_by_uri_with_children("csrd:UNCONTROLLED_DISCLOSURE") is None

    water_rows = repo.search_concepts(
        query="water",
        taxonomy="CSRD",
        concept_type="Indicator",
        public_catalog=True,
    )
    assert [concept.uri for concept in water_rows] == ["urn:sds:reg:esrs:e3_4_01"]

    sygris_indicator_rows = repo.search_concepts(
        query="water",
        taxonomy="Sygris",
        concept_type="Indicator",
        public_catalog=True,
    )
    assert [concept.uri for concept in sygris_indicator_rows] == [
        "urn:sds:reg:esrs:e3_4_01"
    ]


def test_public_catalog_blocks_future_catalogs_even_if_support_rows_exist():
    db = _session()
    db.add_all(
        [
            _db_concept(
                uri="urn:sds:future:cdp:climate",
                label="Future CDP support row",
                description="Declared future catalog, not current version.",
                taxonomy="CDP",
                concept_type="Disclosure",
                projection_source=None,
            ),
            _db_concept(
                uri="urn:sds:future:issb:s1",
                label="Future ISSB projected row",
                description="Declared future catalog, not current version.",
                taxonomy="ISSB",
                concept_type="Indicator",
                projection_source="indicator_catalog",
            ),
            _db_concept(
                uri="urn:sds:reg:esrs:e3_4_01",
                label="Water consumption",
                taxonomy="CSRD",
                concept_type="Indicator",
                projection_source="indicator_catalog",
            ),
        ]
    )
    db.commit()

    repo = ConceptRepository(db)

    assert repo.count_concepts(taxonomy="CDP", public_catalog=True) == 0
    assert repo.count_concepts(taxonomy="ISSB", public_catalog=True) == 0
    assert (
        repo.search_concepts(query="future", taxonomy="CDP", public_catalog=True) == []
    )
    assert [concept.uri for concept in repo.list_concepts(public_catalog=True)] == [
        "urn:sds:reg:esrs:e3_4_01"
    ]


def test_public_concept_detail_excludes_non_public_support_rows():
    db = _session()
    db.add_all(
        [
            _db_concept(
                uri="syg:mapping-pivot",
                label="Mapping pivot",
                description="Internal support row",
                taxonomy="Sygris",
                concept_type="MappingPivot",
                projection_source=None,
            ),
            _db_concept(
                uri="urn:sds:reg:esrs:e3_4_01",
                label="Water consumption",
                taxonomy="CSRD",
                concept_type="Indicator",
                projection_source="indicator_catalog",
            ),
        ]
    )
    db.commit()

    repo = ConceptRepository(db)

    assert repo.get_public_by_uri_with_children("syg:mapping-pivot") is None
    public = repo.get_public_by_uri_with_children("urn:sds:reg:esrs:e3_4_01")
    assert public is not None
    assert public.concept_type == "Indicator"
