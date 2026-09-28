"""DB-backed concept service tests."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.ontology.curie import DEFAULT_NAMESPACES


def _concept(**kwargs):
    defaults = dict(
        id=1,
        uri=DEFAULT_NAMESPACES.expand("csrd:E3_5"),
        taxonomy="CSRD",
        concept_type="Disclosure",
        label="Water consumption",
        description="Water used by the organization",
        unit="sds:CubicMeter",
        temporal_granularity="annual",
        hierarchy_level=1,
        created_at=None,
        updated_at=None,
        formulas=[],
        variables=[],
        equivalences=[],
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _variable(**kwargs):
    defaults = dict(
        id=1,
        concept_id=1,
        variable_uri="syg:Water_Cooling",
        variable_label="Cooling water",
        ordering=0,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _equivalence(**kwargs):
    defaults = dict(
        id=1,
        source_concept_id=1,
        target_uri="gri:303_3",
        target_taxonomy="GRI",
        relationship_type="equivalent",
        confidence=0.95,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _indicator_link(**kwargs):
    defaults = dict(
        id=1,
        concept_id=1,
        indicator_id="urn:sds:reg:1",
        link_type="family_prefix",
        confidence=0.9,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_ontology_service_maps_db_semantic_objects(monkeypatch):
    import src.services.concept_service as mod
    from src.services.ontology_service import OntologyService

    repo = MagicMock()
    formula = SimpleNamespace(
        id=1, version=1, is_active=True, expression="SUM(syg:Water_Cooling)"
    )
    concept = _concept(formulas=[formula], variables=[_variable()])
    repo.count_concepts.side_effect = [2, 2]
    repo.list_concepts.return_value = [
        concept,
        _concept(
            uri=DEFAULT_NAMESPACES.expand("gri:303_3"),
            taxonomy="GRI",
            concept_type="Variable",
            label="Water",
            hierarchy_level=0,
        ),
    ]
    repo.search_concepts.return_value = [
        _concept(label="Water cooling", formulas=[formula]),
        _concept(
            uri=DEFAULT_NAMESPACES.expand("gri:303_3"),
            taxonomy="GRI",
            concept_type="Variable",
            label="Water",
        ),
    ]
    repo.get_by_uri_with_children.return_value = concept
    repo.get_active_formula.return_value = formula
    repo.get_variables.return_value = [_variable()]
    repo.get_equivalences.return_value = [_equivalence()]
    repo.list_equivalences.return_value = [(concept, _equivalence())]
    repo.get_indicator_links.return_value = [_indicator_link()]
    repo.list_variables.return_value = [(_variable(), _concept())]
    repo.list_all_public_catalog_concepts.side_effect = [
        [
            concept,
            _concept(
                uri=DEFAULT_NAMESPACES.expand("gri:303_3"),
                taxonomy="GRI",
                concept_type="Variable",
                label="Water",
                hierarchy_level=0,
            ),
        ],
        [],
    ]
    monkeypatch.setattr(mod, "ConceptRepository", lambda _db: repo)

    svc = OntologyService(db=MagicMock())

    assert svc.has_semantic_data() is True
    items, total = svc.list_concepts_paginated(
        taxonomy="CSRD", concept_type="indicator", search="water", limit=10, offset=0
    )
    assert total == 2
    assert items[0]["uri"] == "csrd:E3_5"
    assert items[0]["formula"] == "SUM(syg:Water_Cooling)"

    concepts = svc.get_concepts(taxonomy="CSRD", concept_type="disclosure", limit=10)
    assert len(concepts) == 2
    assert concepts[0]["uri"] == "csrd:E3_5"
    assert concepts[0]["type"] == "Disclosure"

    search = svc.search_concepts("water", limit=10)
    assert search[0]["similarity_score"] >= search[1]["similarity_score"]
    assert search[0]["uri"] == "gri:303_3"

    detail = svc.get_concept_by_uri("csrd:E3_5")
    assert detail["formula"] == "SUM(syg:Water_Cooling)"
    assert detail["related_variables"] == ["syg:Water_Cooling"]

    variables = svc.get_variables(taxonomy="CSRD", limit=10)
    assert variables == [
        {
            "uri": "syg:Water_Cooling",
            "taxonomy": "CSRD",
            "name": "Cooling water",
            "description": "Cooling water",
            "unit": "sds:CubicMeter",
            "data_type": "decimal",
        }
    ]

    relations = svc.get_relations_by_concept("csrd:E3_5")
    assert [rel["relation_type"] for rel in relations] == [
        "equivalent",
        "family_prefix",
        "hasVariable",
    ]
    assert svc.find_equivalences("csrd:E3_5") == ["urn:sds:disclosure:gri:303-3"]
    equivalences = svc.list_equivalences(
        concept="csrd:E3_5", source_taxonomy="CSRD", target_taxonomy="GRI"
    )
    assert equivalences == [
        {
            "source_concept": "urn:sds:disclosure:csrd:e3-5",
            "target_concept": "urn:sds:disclosure:gri:303-3",
            "equivalence_type": "exact",
            "confidence": 0.95,
            "metadata": {"relationship_type": "equivalent", "target_taxonomy": "GRI"},
        }
    ]

    vars_for = svc.get_variables_for_concept("csrd:E3_5")
    assert vars_for[0]["uri"] == "syg:Water_Cooling"
    assert vars_for[0]["taxonomy"] == "CSRD"
    taxonomies = svc.list_taxonomies()
    assert taxonomies["total_concepts"] == 2


def test_concept_service_normalizes_new_disclosure_uri_equivalence_aliases(monkeypatch):
    import src.services.concept_service as mod
    from src.services.ontology_service import OntologyService

    legacy_source = _concept(uri=DEFAULT_NAMESPACES.expand("csrd:E3_5"))
    repo = MagicMock()
    repo.count_concepts.return_value = 1

    def _list_equivalences(**kwargs):
        if kwargs.get("compact_concept_uri") == "csrd:E3_5":
            return [(legacy_source, _equivalence())]
        return []

    repo.list_equivalences.side_effect = _list_equivalences
    monkeypatch.setattr(mod, "ConceptRepository", lambda _db: repo)

    svc = OntologyService(db=MagicMock())

    assert svc.find_equivalences("urn:sds:disclosure:csrd:e3-5") == [
        "urn:sds:disclosure:gri:303-3"
    ]
    equivalences = svc.list_equivalences(
        concept="urn:sds:disclosure:csrd:e3-5",
        source_taxonomy="CSRD",
        target_taxonomy="GRI",
    )
    assert equivalences == [
        {
            "source_concept": "urn:sds:disclosure:csrd:e3-5",
            "target_concept": "urn:sds:disclosure:gri:303-3",
            "equivalence_type": "exact",
            "confidence": 0.95,
            "metadata": {"relationship_type": "equivalent", "target_taxonomy": "GRI"},
        }
    ]


def test_concept_service_deduplicates_equivalences_and_counts_units(monkeypatch):
    import src.services.concept_service as mod
    from src.services.ontology_service import OntologyService

    now = datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc)
    source = _concept(updated_at=now)
    duplicate = _equivalence(relationship_type="same_as")
    unit = _concept(
        uri=DEFAULT_NAMESPACES.expand("sds:CubicMeter"),
        taxonomy="SDS",
        concept_type="Unit",
        label="Cubic metre",
        updated_at=now,
    )
    indicator = _concept(
        id=2,
        uri="urn:sds:reg:esrs:e5_5_01",
        concept_type="Indicator",
        label="Waste generated",
        updated_at=now,
    )
    ghg_indicator = _concept(
        id=4,
        uri="urn:sds:reg:ghg:scope2_market_based",
        taxonomy="GHG",
        concept_type="Indicator",
        label="Scope 2 market-based emissions",
        updated_at=now,
    )
    repo = MagicMock()
    repo.count_concepts.return_value = 4
    repo.list_equivalences.return_value = [
        (source, duplicate),
        (source, duplicate),
    ]
    repo.list_concepts.return_value = []
    repo.list_all_public_catalog_concepts.side_effect = [
        [source, indicator, ghg_indicator, unit],
        [source, indicator, ghg_indicator],
    ]
    monkeypatch.setattr(mod, "ConceptRepository", lambda _db: repo)

    svc = OntologyService(db=MagicMock())
    equivalences = svc.list_equivalences()
    taxonomies = svc.list_taxonomies()

    assert len(equivalences) == 1
    assert equivalences[0]["equivalence_type"] == "exact"
    assert taxonomies["total_concepts"] == 4
    assert taxonomies["taxonomies"]["CSRD"]["indicators_count"] == 1
    assert taxonomies["taxonomies"]["CSRD"]["disclosures_count"] == 1
    assert taxonomies["taxonomies"]["GHG"]["indicators_count"] == 1
    assert taxonomies["taxonomies"]["Sygris"]["catalog_scope"] == (
        "unified_public_catalog"
    )
    assert taxonomies["taxonomies"]["Sygris"]["concepts_count"] == 3
    assert taxonomies["taxonomies"]["Sygris"]["indicators_count"] == 2
    assert taxonomies["taxonomies"]["Sygris"]["disclosures_count"] == 1
    assert taxonomies["taxonomies"]["SDS"]["units_count"] == 1
    assert taxonomies["last_updated"] == now.isoformat()


def test_concept_service_uses_public_catalog_for_public_lists(monkeypatch):
    import src.services.concept_service as mod
    from src.services.ontology_service import OntologyService

    repo = MagicMock()
    repo.count_concepts.return_value = 1
    repo.search_concepts.return_value = []
    repo.list_concepts.return_value = []
    repo.list_all_public_catalog_concepts.return_value = []
    monkeypatch.setattr(mod, "ConceptRepository", lambda _db: repo)

    svc = OntologyService(db=MagicMock())

    svc.list_concepts_paginated(
        taxonomy="CSRD",
        concept_type="Indicator",
        search="waste",
        limit=100,
        offset=0,
    )
    repo.count_concepts.assert_called_with(
        taxonomy="CSRD",
        concept_type="Indicator",
        search="waste",
        public_catalog=True,
    )
    repo.search_concepts.assert_called_with(
        query="waste",
        taxonomy="CSRD",
        concept_type="Indicator",
        limit=100,
        offset=0,
        public_catalog=True,
    )

    svc.get_concepts(taxonomy="Sygris", concept_type="Indicator", limit=10)
    repo.list_concepts.assert_any_call(
        taxonomy="Sygris",
        concept_type="Indicator",
        limit=10,
        public_catalog=True,
    )

    svc.list_taxonomies()
    repo.list_all_public_catalog_concepts.assert_any_call()


def test_concept_service_uses_public_catalog_for_search_and_public_detail(
    monkeypatch,
):
    import src.services.concept_service as mod
    from src.services.ontology_service import OntologyService

    repo = MagicMock()
    repo.search_concepts.return_value = []
    repo.get_public_by_uri_with_children.return_value = None
    monkeypatch.setattr(mod, "ConceptRepository", lambda _db: repo)

    svc = OntologyService(db=MagicMock())

    assert svc.search_concepts("mapping pivot", limit=10) == []
    repo.search_concepts.assert_called_with(
        query="mapping pivot", limit=10, public_catalog=True
    )

    assert svc.get_concept_by_uri("syg:mapping-pivot", public_catalog=True) is None
    repo.get_public_by_uri_with_children.assert_called_with(
        DEFAULT_NAMESPACES.expand("syg:mapping-pivot")
    )


def test_concept_service_taxonomies_use_unbounded_public_catalog(monkeypatch):
    import src.services.concept_service as mod
    from src.services.ontology_service import OntologyService

    repo = MagicMock()
    repo.list_all_public_catalog_concepts.return_value = [
        _concept(uri=f"urn:sds:reg:esrs:item-{index}", concept_type="Indicator")
        for index in range(3)
    ]
    repo.list_concepts.return_value = []
    monkeypatch.setattr(mod, "ConceptRepository", lambda _db: repo)

    svc = OntologyService(db=MagicMock())

    taxonomies = svc.list_taxonomies()

    assert taxonomies["taxonomies"]["CSRD"]["indicators_count"] == 3
    repo.list_all_public_catalog_concepts.assert_any_call()
    repo.list_all_public_catalog_concepts.assert_any_call(taxonomy="Sygris")


def test_ontology_service_returns_empty_payloads_when_repo_returns_nothing(monkeypatch):
    import src.services.concept_service as mod
    from src.services.ontology_service import OntologyService

    repo = MagicMock()
    repo.count_concepts.return_value = 0
    repo.list_concepts.return_value = []
    repo.search_concepts.return_value = []
    repo.get_by_uri_with_children.return_value = None
    repo.list_equivalences.return_value = []
    repo.list_variables.return_value = []
    repo.get_variables.return_value = []
    repo.get_equivalences.return_value = []
    repo.get_indicator_links.return_value = []
    monkeypatch.setattr(mod, "ConceptRepository", lambda _db: repo)

    svc = OntologyService(db=MagicMock())
    assert svc.has_semantic_data() is False
    assert svc.get_concepts() == []
    assert svc.search_concepts("x") == []
    assert svc.get_concept_by_uri("missing") is None
    assert svc.list_equivalences() == []
    assert svc.get_variables() == []
    assert svc.get_relations_by_concept("missing") == []
    assert svc.find_equivalences("missing") == []
    assert svc.get_variables_for_concept("missing") == []
    assert svc.list_taxonomies()["total_concepts"] == 0
    svc.close()


def test_concept_service_no_repo_and_owned_session_close_paths(monkeypatch):
    import src.services.concept_service as mod

    closed = {"count": 0}

    class _Session:
        def close(self):
            closed["count"] += 1
            raise RuntimeError("ignored close failure")

    monkeypatch.setattr(mod, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(mod, "ConceptRepository", lambda _db: None)
    monkeypatch.setattr(mod, "canonical_data_required", lambda: False)

    svc = mod.ConceptService()

    assert svc.has_semantic_data() is False
    assert svc.list_concepts_paginated()[1] == 0
    assert svc.get_concepts() == []
    assert svc.search_concepts("water") == []
    assert svc.get_concept_by_uri("csrd:E3_5") is None
    assert svc.get_variables() == []
    assert svc.list_equivalences() == []
    assert svc.get_variables_for_concept("csrd:E3_5") == []
    assert svc.list_taxonomies() == {
        "taxonomies": {},
        "total_taxonomies": 0,
        "total_concepts": 0,
        "last_updated": None,
    }
    assert mod._local_name("https://example.test/ns/Water") == "Water"
    assert mod._local_name("") == ""

    svc.close()
    assert closed["count"] == 1


def test_concept_service_no_repo_respects_required_canonical_mode(monkeypatch):
    import src.services.concept_service as mod

    called = {}
    monkeypatch.setattr(mod, "SessionLocal", lambda: None)
    monkeypatch.setattr(mod, "ConceptRepository", lambda _db: None)
    monkeypatch.setattr(mod, "canonical_data_required", lambda: True)

    def _required(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(mod, "require_canonical_data", _required)

    svc = mod.ConceptService()

    assert svc.has_semantic_data() is False
    assert called["operation"] == "has_semantic_data"
    assert "no database session" in called["reason"]


def test_concept_service_failure_paths_return_empty_payloads(monkeypatch):
    import src.services.concept_service as mod

    repo = MagicMock()
    repo.count_concepts.side_effect = RuntimeError("db down")
    repo.search_concepts.side_effect = RuntimeError("db down")
    repo.get_by_uri_with_children.side_effect = RuntimeError("db down")
    repo.list_variables.side_effect = RuntimeError("db down")
    repo.get_variables.side_effect = RuntimeError("db down")
    repo.list_equivalences.side_effect = RuntimeError("db down")
    repo.list_concepts.side_effect = RuntimeError("db down")
    repo.list_all_public_catalog_concepts.side_effect = RuntimeError("db down")
    monkeypatch.setattr(mod, "ConceptRepository", lambda _db: repo)
    monkeypatch.setattr(mod, "canonical_data_required", lambda: False)

    svc = mod.ConceptService(db=MagicMock())

    assert svc.has_semantic_data() is False
    assert svc.list_concepts_paginated() == ([], 0)
    assert svc.get_concepts() == []
    assert svc.search_concepts("water") == []
    assert svc.get_concept_by_uri("csrd:E3_5") is None
    assert svc.get_variables() == []
    assert svc.get_relations_by_concept("csrd:E3_5") == []
    assert svc.find_equivalences("csrd:E3_5") == []
    assert svc.list_equivalences(concept="csrd:E3_5") == []
    assert svc.get_variables_for_concept("csrd:E3_5") == []
    assert svc.list_taxonomies() == {
        "taxonomies": {},
        "total_taxonomies": 0,
        "total_concepts": 0,
        "last_updated": None,
    }
    assert mod.ConceptService._similarity_score("", "Water", "csrd:E3_5", None) == 0.0
