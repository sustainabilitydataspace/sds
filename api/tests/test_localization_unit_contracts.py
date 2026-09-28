from __future__ import annotations

from types import SimpleNamespace

from src.database import models as m
from src.ontology.curie import DEFAULT_NAMESPACES
from src.services.concept_service import ConceptService
from src.services.localization import text_hash
from src.services.localization_service import (
    LocalizationError,
    LocalizationRepository,
    import_concept_translations,
    import_indicator_translations,
    import_translations,
)
from src.services.localization_worklist import (
    build_translation_worklist,
    public_subject_uris,
)


class _Query:
    def __init__(self, db):
        self._db = db

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return self._db.all_results.pop(0) if self._db.all_results else []

    def one_or_none(self):
        return self._db.one_results.pop(0) if self._db.one_results else None

    def first(self):
        return self._db.first_results.pop(0) if self._db.first_results else None


class _FakeDb:
    def __init__(self, *, all_results=None, one_results=None, first_results=None):
        self.all_results = list(all_results or [])
        self.one_results = list(one_results or [])
        self.first_results = list(first_results or [])
        self.added = []
        self.flushed = False

    def query(self, *_args, **_kwargs):
        return _Query(self)

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flushed = True


def _localized_row(
    *,
    subject_uri="csrd:E5-5_09",
    field="label",
    language="es",
    text="Residuo peligroso",
    source_text="Hazardous waste",
    status="approved",
    effective_to=None,
    revision=1,
):
    return m.LocalizedText(
        subject_kind="concept",
        subject_uri=subject_uri,
        field=field,
        language=language,
        display_language=language,
        text=text,
        status=status,
        source_language="en",
        source_hash=text_hash(source_text),
        translation_hash=text_hash(text),
        source_type="human_reviewed",
        source_ref="pkg:v1",
        reviewer_ref="reviewer:qa",
        effective_to=effective_to,
        tenant_id=None,
        revision=revision,
    )


def _approved_payload(
    *,
    subject_kind="concept",
    subject_uri="csrd:E5-5_09",
    field="label",
    language="es",
    text="Residuo peligroso",
    source_text="Hazardous waste",
):
    return {
        "subject_kind": subject_kind,
        "subject_uri": subject_uri,
        "field": field,
        "language": language,
        "text": text,
        "status": "approved",
        "source_language": "en",
        "source_hash": text_hash(source_text),
        "translation_hash": text_hash(text),
        "source_type": "human_reviewed",
        "source_ref": "pkg:v1",
        "reviewer_ref": "reviewer:qa",
    }


def test_localization_repository_resolves_empty_and_approved_rows_without_postgres():
    empty = LocalizationRepository(_FakeDb()).localize(
        subject_kind="concept",
        subject_uri="csrd:E5-5_09",
        source_fields={},
        requested_language="es",
    )
    assert empty.display == {}
    assert empty.status == "source_fallback"

    db = _FakeDb(all_results=[[_localized_row()]])
    result = LocalizationRepository(db).localize(
        subject_kind="concept",
        subject_uri="csrd:E5-5_09",
        source_fields={"label": "Hazardous waste"},
        requested_language="es",
    )
    assert result.display == {"label": "Residuo peligroso"}
    assert result.status == "approved_current"


def test_localization_repository_coverage_classifies_current_stale_draft_and_missing():
    db = _FakeDb(
        all_results=[
            [
                _localized_row(subject_uri="c:current", source_text="One"),
                _localized_row(subject_uri="c:stale", source_text="Old"),
                _localized_row(
                    subject_uri="c:draft",
                    text="Borrador",
                    source_text="Draft",
                    status="machine_draft",
                ),
            ]
        ]
    )
    result = LocalizationRepository(db).coverage(
        subject_kind="concept",
        language="ES_es",
        subjects={
            "c:current": {"label": "One"},
            "c:stale": {"label": "Updated"},
            "c:draft": {"label": "Draft"},
            "c:missing": {"label": "Missing"},
        },
    )

    assert result["language"] == "es-es"
    assert result["approved_current"] == 1
    assert result["stale"] == 1
    assert result["draft_only"] == 1
    assert result["missing"] == 1
    assert result["coverage_percent"] == 25.0


def test_import_translations_reports_invalid_partial_rows_and_flushes():
    rows = [
        {"subject_kind": "indicator", "subject_uri": "c:1"},
        {"subject_kind": "concept", "field": "label"},
        _approved_payload(subject_uri="c:unknown"),
        {
            **_approved_payload(subject_uri="c:1"),
            "field": "title",
        },
        {
            **_approved_payload(subject_uri="c:1"),
            "field": "label",
            "status": "unsupported",
        },
        {
            **_approved_payload(subject_uri="c:1"),
            "field": "label",
            "text": " ",
        },
        {
            **_approved_payload(subject_uri="c:1"),
            "field": "label",
            "source_hash": "not-a-hash",
        },
        {
            **_approved_payload(subject_uri="c:1"),
            "field": "label",
            "translation_hash": text_hash("different"),
        },
    ]
    db = _FakeDb()

    report = import_translations(
        db,
        rows,
        subject_kind="concept",
        strict=False,
        known_subject_uris={"c:1"},
    )

    assert report.created == 0
    assert report.skipped_unknown == [
        "row[0]: subject_kind=indicator",
        "row[2]: subject_uri=c:unknown",
    ]
    assert len(report.rejected) == 6
    assert report.rejected[0] == "row[1]: missing subject_uri"
    assert report.as_dict()["committed"] is False
    assert db.flushed is True


def test_import_translations_is_idempotent_supersedes_and_imports_drafts_without_postgres():
    existing = _localized_row(subject_uri="c:1", source_text="Source", revision=3)
    duplicate_draft = _localized_row(
        subject_uri="c:3", text="Borrador", source_text="Draft", status="machine_draft"
    )
    db = _FakeDb(
        one_results=[
            _localized_row(subject_uri="c:same", source_text="Same"),
            existing,
            None,
        ],
        first_results=[
            duplicate_draft,
            None,
        ],
    )
    rows = [
        _approved_payload(
            subject_uri="c:same", text="Residuo peligroso", source_text="Same"
        ),
        _approved_payload(subject_uri="c:1", text="Nuevo", source_text="Source"),
        _approved_payload(subject_uri="c:2", text="Dos", source_text="Two"),
        {
            **_approved_payload(
                subject_uri="c:3", text="Borrador", source_text="Draft"
            ),
            "status": "machine_draft",
            "source_ref": "",
            "reviewer_ref": "",
        },
        {
            **_approved_payload(subject_uri="c:4", text="Cuatro", source_text="Four"),
            "status": "reviewed",
            "source_ref": "",
            "reviewer_ref": "",
        },
    ]

    report = import_concept_translations(
        db, rows, known_subject_uris={"c:same", "c:1", "c:2", "c:3", "c:4"}
    )

    assert report.unchanged == 2
    assert report.superseded == 1
    assert report.created == 3
    assert existing.status == "deprecated"
    assert existing.effective_to is not None
    assert [row.subject_uri for row in db.added] == ["c:1", "c:2", "c:4"]
    assert db.added[0].revision == 4
    assert db.added[2].source_ref is None


def test_import_translations_rejects_unsupported_kind_and_duplicate_approved_rows():
    try:
        import_translations(
            _FakeDb(),
            [],
            subject_kind="unit",
            known_subject_uris=set(),
        )
    except LocalizationError as exc:
        assert "unsupported subject_kind" in str(exc)
    else:
        raise AssertionError("unsupported kind must fail closed")

    duplicate = [
        _approved_payload(subject_uri="c:1"),
        _approved_payload(subject_uri="c:1", text="Otro"),
    ]
    try:
        import_concept_translations(_FakeDb(), duplicate, known_subject_uris={"c:1"})
    except LocalizationError as exc:
        assert "duplicate active approved row" in str(exc)
    else:
        raise AssertionError("duplicate approved rows must fail closed")


def test_indicator_translation_wrapper_uses_indicator_subject_kind():
    db = _FakeDb(one_results=[None])
    report = import_indicator_translations(
        db,
        [
            _approved_payload(
                subject_kind="indicator",
                subject_uri="urn:sds:indicator:e1",
                field="title",
                text="Emisiones",
                source_text="Emissions",
            )
        ],
        known_subject_uris={"urn:sds:indicator:e1"},
    )

    assert report.created == 1
    assert db.added[0].subject_kind == "indicator"
    assert db.added[0].field == "title"


class _WorklistDb:
    def __init__(self, *, concepts=None, indicators=None, translations=None):
        self.concepts = list(concepts or [])
        self.indicators = list(indicators or [])
        self.translations = list(translations or [])

    def query(self, model):
        if model is m.Concept:
            return _StaticQuery(self.concepts)
        if model is m.Indicator:
            return _StaticQuery(self.indicators)
        if model is m.LocalizedText:
            return _StaticQuery(self.translations)
        return _StaticQuery([])


class _StaticQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)


def test_worklist_exports_public_subjects_fields_hashes_and_translation_statuses():
    concept = SimpleNamespace(
        uri="urn:sds:disclosure:csrd:e5-5",
        label="Resource outflows",
        description="Waste metrics",
    )
    indicator = SimpleNamespace(
        identifier="urn:sds:indicator:e1",
        title="Emissions",
        indicator_name="GHG emissions",
        description="",
    )
    stale = _localized_row(
        subject_uri="urn:sds:disclosure:csrd:e5-5",
        field="description",
        text="Viejo",
        source_text="Old waste metrics",
    )
    draft = _localized_row(
        subject_uri="urn:sds:indicator:e1",
        field="indicator_name",
        text="Borrador",
        source_text="GHG emissions",
        status="reviewed",
    )
    db = _WorklistDb(
        concepts=[concept], indicators=[indicator], translations=[stale, draft]
    )

    assert public_subject_uris(db, "concept") == {"urn:sds:disclosure:csrd:e5-5"}
    concept_rows = build_translation_worklist(
        db, subject_kind="concept", target_language="es", include_empty=True
    )
    indicator_rows = build_translation_worklist(
        db, subject_kind="indicator", target_language="es", include_empty=False
    )

    by_concept_field = {row["field"]: row for row in concept_rows}
    assert by_concept_field["label"]["subject_uri"] == "urn:sds:disclosure:csrd:e5-5"
    assert by_concept_field["label"]["source_hash"] == text_hash("Resource outflows")
    assert by_concept_field["label"]["status"] == "missing"
    assert by_concept_field["description"]["status"] == "stale"

    by_indicator_field = {row["field"]: row for row in indicator_rows}
    assert set(by_indicator_field) == {"title", "indicator_name"}
    assert by_indicator_field["indicator_name"]["status"] == "draft_only"
    assert by_indicator_field["title"]["status"] == "missing"


def test_worklist_rejects_unknown_public_subject_kind():
    try:
        public_subject_uris(_WorklistDb(), "unit")
    except ValueError as exc:
        assert "unsupported subject_kind" in str(exc)
    else:
        raise AssertionError("unsupported subject kind must fail closed")


def _concept(
    *,
    uri="csrd:E5-5_09",
    label="Hazardous waste",
    description="Waste directed to disposal",
    taxonomy="CSRD",
    concept_type="Disclosure",
):
    return SimpleNamespace(
        id=1,
        uri=DEFAULT_NAMESPACES.expand(uri),
        taxonomy=taxonomy,
        concept_type=concept_type,
        label=label,
        description=description,
        unit="tonnes",
        temporal_granularity="annual",
        hierarchy_level=1,
        formulas=[],
        variables=[],
        equivalences=[],
    )


def _service_with_repo(repo, db):
    svc = ConceptService.__new__(ConceptService)
    svc._repo = repo
    svc._db = db
    return svc


def test_concept_service_projection_coverage_fail_closed_paths(monkeypatch):
    import src.services.concept_service as mod

    monkeypatch.setattr(mod, "canonical_data_required", lambda: False)
    no_db = _service_with_repo(repo=None, db=None)
    assert no_db.semantic_projection_coverage() == {
        "active_indicators": 0,
        "projected_active_indicators": 0,
        "missing_active_indicator_identifiers": [],
        "stale_active_indicator_identifiers": [],
        "active_source_datapoints": 0,
        "projected_source_datapoints": 0,
        "missing_source_datapoint_uris": [],
        "stale_source_datapoint_uris": [],
        "ready": False,
    }

    class _FailingProjector:
        def __init__(self, _db):
            pass

        def coverage_summary(self):
            raise RuntimeError("projection unavailable")

    monkeypatch.setattr(mod, "SemanticConceptProjector", _FailingProjector)
    failing = _service_with_repo(repo=object(), db=object())
    failing.logger = SimpleNamespace(error=lambda *_args, **_kwargs: None)

    result = failing.semantic_projection_coverage()

    assert result["ready"] is False
    assert result["error"] == "projection unavailable"


def test_concept_service_localization_readiness_without_db_is_zero_coverage():
    svc = _service_with_repo(repo=None, db=None)

    result = svc.localization_readiness("ES_es", field="description")

    assert result == {
        "subject_kind": "concept",
        "field": "description",
        "language": "es-es",
        "total_subjects": 0,
        "approved_current": 0,
        "stale": 0,
        "draft_only": 0,
        "missing": 0,
        "coverage_percent": 0.0,
    }


def test_concept_service_contract_payload_orders_dimensions_and_dedupes_components():
    contract = SimpleNamespace(
        formula_kind="aggregation",
        aggregation_policy={"operation": "sum"},
        contract_hash="contract-hash",
        contract_version="v2",
        semantic_expression=None,
        runtime_expression="SUM(syg:WastePlastic)",
        dimensions=[
            SimpleNamespace(
                dimension_id="treatment",
                mode="fixed",
                fixed_value="recycling",
                member_values=[],
                required=True,
            ),
            SimpleNamespace(
                dimension_id="hazard_class",
                mode="required",
                fixed_value=None,
                member_values=["hazardous", "non_hazardous"],
                required=False,
            ),
        ],
        components=[
            SimpleNamespace(id=2, component_order=1, variable_uri="syg:WastePlastic"),
            SimpleNamespace(id=1, component_order=0, variable_uri="syg:WasteGlass"),
            SimpleNamespace(id=3, component_order=2, variable_uri="syg:WastePlastic"),
            SimpleNamespace(id=4, component_order=3, variable_uri=None),
        ],
    )

    assert ConceptService._contract_payload(None, public_only=True) == {}
    public_payload = ConceptService._contract_payload(contract, public_only=True)
    private_payload = ConceptService._contract_payload(contract, public_only=False)

    assert [dim["dimension_id"] for dim in public_payload["dimensions"]] == [
        "hazard_class",
        "treatment",
    ]
    assert public_payload["_vars_fallback"] == ["syg:WasteGlass", "syg:WastePlastic"]
    assert public_payload["_formula_fallback"] is None
    assert private_payload["_formula_fallback"] == "SUM(syg:WastePlastic)"


def test_concept_service_translation_search_uses_current_translations_only():
    current = _localized_row(
        subject_uri="csrd:E5-5_09",
        field="label",
        text="Residuo peligroso",
        source_text="Hazardous waste",
    )
    stale = _localized_row(
        subject_uri="csrd:E5-5_10",
        field="label",
        text="Residuo obsoleto",
        source_text="Old hazardous waste",
    )
    unsupported_field = _localized_row(
        subject_uri="csrd:E5-5_11",
        field="title",
        text="Residuo titulo",
        source_text="Title",
    )
    db = _FakeDb(all_results=[[current, stale, unsupported_field], [current]])
    repo = SimpleNamespace(
        search_concepts=lambda **_kwargs: [
            _concept(uri="csrd:E5-5_09", label="Hazardous waste")
        ],
        get_public_by_uri_with_children=lambda uri: (
            _concept(uri=uri, label="Fresh hazardous waste")
            if uri.endswith("E5-5_10")
            else None
        ),
    )
    svc = _service_with_repo(repo=repo, db=db)

    results = svc.search_concepts_localized("Residuo", limit=10, lang="es-ES")

    assert len(results) == 1
    assert results[0]["uri"] == "csrd:E5-5_09"
    assert results[0]["matched_language"] == "es"
    assert results[0]["matched_field"] == "label"
    assert results[0]["display_label"] == "Residuo peligroso"
    assert results[0]["label"] == "Hazardous waste"


def test_concept_service_assertion_equivalence_projection_filters_and_metadata():
    release = SimpleNamespace(
        id=7,
        standard_id="ESRS",
        name="ESRS Set 1",
        version="2024",
        release_date=None,
    )
    datapoint = SimpleNamespace(
        id=11,
        code="E5-5_09",
        label="Waste directed to disposal",
        disclosure_text="Disclosure text",
        datapoint_type="metric",
        unit="tonnes",
        metadata_json={},
    )
    group = SimpleNamespace(
        id=101,
        relationship_type="broader",
        coverage_status="partial",
        mapping_profile="default",
        confidence=0.88,
    )
    component = SimpleNamespace(component_role="primary", coverage_fraction=0.5)
    canonical = SimpleNamespace(
        canonical_uri="syg:WastePlastic",
        taxonomy="Sygris",
    )
    repo = SimpleNamespace(
        list_assertion_equivalence_rows=lambda: [
            (group, release, datapoint, component, canonical),
            (
                SimpleNamespace(
                    id=102,
                    relationship_type="supporting_context",
                    coverage_status="context",
                    mapping_profile="context",
                    confidence=1.0,
                ),
                release,
                datapoint,
                component,
                canonical,
            ),
            (
                group,
                release,
                SimpleNamespace(
                    **{
                        **datapoint.__dict__,
                        "metadata_json": {"sds_identifier": "syg:x"},
                    }
                ),
                component,
                canonical,
            ),
        ]
    )
    svc = _service_with_repo(repo=repo, db=object())

    rows = svc._assertion_equivalences(
        concept="syg:WastePlastic",
        source_taxonomy=" csrd ",
        target_taxonomy=" sygris ",
    )

    assert rows == [
        {
            "source_concept": "urn:sds:standard-datapoint:csrd:esrs:2024:e5-5-09",
            "target_concept": "syg:WastePlastic",
            "equivalence_type": "partial",
            "confidence": 0.88,
            "metadata": {
                "relationship_type": "broader",
                "coverage_status": "partial",
                "mapping_profile": "default",
                "assertion_group_id": 101,
                "component_role": "primary",
                "coverage_fraction": 0.5,
                "source_standard_id": "ESRS",
                "source_standard_version": "2024",
                "source_code": "E5-5_09",
                "target_taxonomy": "SYGRIS",
                "provenance": "mapping_assertion",
            },
        }
    ]

    assert svc._assertion_equivalences(concept="syg:Other") == []
    assert (
        _service_with_repo(
            repo=SimpleNamespace(), db=object()
        )._assertion_equivalences()
        == []
    )
