from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


class _FakeConceptQuery:
    def __init__(self, counts: list[int], rows=None):
        self._counts = counts
        self._rows = list(rows or [])

    def count(self) -> int:
        if len(self._counts) == 1:
            return self._counts[0]
        return self._counts.pop(0)

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, counts: list[int], concept_rows=None):
        self._concept_query = _FakeConceptQuery(counts, concept_rows)
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
        self.commits = 0
        self.rollbacks = 0

    def query(self, _model):
        return self._concept_query

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_bootstrap_semantic_model_skips_when_bundled_concepts_exist(monkeypatch):
    from src.database import bootstrap_semantic_model as mod

    record = SimpleNamespace(curie="csrd:E3_5", uri="https://data.efrag.org/esrs#E3_5")
    session = _FakeSession(
        [1], concept_rows=[SimpleNamespace(uri="urn:sds:disclosure:csrd:e3-5")]
    )
    monkeypatch.setattr(
        mod,
        "load_semantic_records",
        lambda _path: (Path("ontologies/base.owl"), object(), {"csrd:E3_5": record}),
    )
    monkeypatch.setattr(
        mod, "_existing_migrated_legacy_alias_count", lambda _db, _records: 0
    )

    summary = mod.bootstrap_semantic_model_if_empty(session)

    assert summary["status"] == "skipped"
    assert summary["concepts_total"] == 1
    assert summary["missing_concepts"] == 0
    assert session.commits == 0


def test_bootstrap_semantic_model_skips_after_lock_when_other_worker_seeded(
    monkeypatch,
):
    from src.database import bootstrap_semantic_model as mod

    record = SimpleNamespace(curie="csrd:E3_5", uri="https://data.efrag.org/esrs#E3_5")
    session = _FakeSession(
        [0, 1], concept_rows=[SimpleNamespace(uri="urn:sds:disclosure:csrd:e3-5")]
    )
    monkeypatch.setattr(
        mod,
        "load_semantic_records",
        lambda _path: (Path("ontologies/base.owl"), object(), {"csrd:E3_5": record}),
    )
    monkeypatch.setattr(
        mod, "_existing_migrated_legacy_alias_count", lambda _db, _records: 0
    )

    summary = mod.bootstrap_semantic_model_if_empty(session)

    assert summary["status"] == "skipped_after_lock"
    assert summary["concepts_total"] == 1
    assert summary["missing_concepts"] == 0
    assert session.commits == 0


def test_bootstrap_semantic_model_repairs_partial_concept_table(monkeypatch):
    from src.database import bootstrap_semantic_model as mod

    events: list[str] = []
    existing = SimpleNamespace(
        curie="csrd:E3_5", uri="https://data.efrag.org/esrs#E3_5"
    )
    missing = SimpleNamespace(
        curie="gri:303_3", uri="https://data.globalreporting.org/gri#303_3"
    )
    session = _FakeSession(
        [1, 1, 2], concept_rows=[SimpleNamespace(uri="urn:sds:disclosure:csrd:e3-5")]
    )

    monkeypatch.setattr(
        mod,
        "load_semantic_records",
        lambda _path: (
            Path("ontologies/base.owl"),
            object(),
            {"csrd:E3_5": existing, "gri:303_3": missing},
        ),
    )
    monkeypatch.setattr(
        mod, "_existing_migrated_legacy_alias_count", lambda _db, _records: 0
    )
    monkeypatch.setattr(
        mod, "build_indicator_lookup", lambda _db: events.append("lookup") or {}
    )
    monkeypatch.setattr(mod, "match_indicator_id", lambda _record, _lookup: None)
    monkeypatch.setattr(
        mod,
        "summarize_semantic_records",
        lambda _records, _matches, _path: events.append("summary")
        or {
            "ontology_path": "ontologies/base.owl",
            "ontology_sha256": "sha",
            "concept_count": 2,
            "formula_count": 1,
            "variable_count": 1,
            "equivalence_count": 1,
            "matched_indicator_count": 0,
            "unmatched_indicator_count": 2,
        },
    )
    monkeypatch.setattr(
        mod,
        "upsert_semantic_records",
        lambda _db, _records, _matches, purge_stale=False: events.append(
            f"upsert:{purge_stale}"
        )
        or {
            "created": 1,
            "updated": 1,
            "refreshed_children": 2,
            "purged_stale": 0,
        },
    )

    summary = mod.bootstrap_semantic_model_if_empty(session)

    assert events == ["lookup", "summary", "upsert:False"]
    assert summary["status"] == "refreshed"
    assert summary["concepts_before"] == 1
    assert summary["concepts_total"] == 2
    assert summary["missing_concepts"] == 1
    assert session.commits == 1


def test_bootstrap_semantic_model_repairs_when_legacy_alias_rows_exist(monkeypatch):
    from src.database import bootstrap_semantic_model as mod

    events: list[str] = []
    record = SimpleNamespace(curie="csrd:E3_5", uri="https://data.efrag.org/esrs#E3_5")
    session = _FakeSession(
        [1, 1, 1], concept_rows=[SimpleNamespace(uri="urn:sds:disclosure:csrd:e3-5")]
    )

    monkeypatch.setattr(
        mod,
        "load_semantic_records",
        lambda _path: (Path("ontologies/base.owl"), object(), {"csrd:E3_5": record}),
    )
    monkeypatch.setattr(
        mod, "_existing_migrated_legacy_alias_count", lambda _db, _records: 1
    )
    monkeypatch.setattr(
        mod, "build_indicator_lookup", lambda _db: events.append("lookup") or {}
    )
    monkeypatch.setattr(mod, "match_indicator_id", lambda _record, _lookup: None)
    monkeypatch.setattr(
        mod,
        "summarize_semantic_records",
        lambda _records, _matches, _path: events.append("summary")
        or {
            "ontology_path": "ontologies/base.owl",
            "ontology_sha256": "sha",
            "concept_count": 1,
            "formula_count": 1,
            "variable_count": 1,
            "equivalence_count": 1,
            "matched_indicator_count": 0,
            "unmatched_indicator_count": 1,
        },
    )
    monkeypatch.setattr(
        mod,
        "upsert_semantic_records",
        lambda _db, _records, _matches, purge_stale=False: events.append(
            f"upsert:{purge_stale}"
        )
        or {
            "created": 0,
            "updated": 1,
            "refreshed_children": 1,
            "purged_stale": 0,
            "purged_legacy_aliases": 1,
        },
    )

    summary = mod.bootstrap_semantic_model_if_empty(session)

    assert events == ["lookup", "summary", "upsert:False"]
    assert summary["status"] == "refreshed"
    assert summary["missing_concepts"] == 0
    assert summary["legacy_alias_concepts"] == 1
    assert summary["purged_legacy_aliases"] == 1


def test_bootstrap_semantic_model_loads_ontology_and_commits_when_empty(monkeypatch):
    from src.database import bootstrap_semantic_model as mod

    events: list[str] = []
    record = SimpleNamespace(curie="csrd:E3_5", uri="https://data.efrag.org/esrs#E3_5")
    session = _FakeSession([0, 0, 1])

    monkeypatch.setattr(
        mod,
        "load_semantic_records",
        lambda _path: events.append("load")
        or (Path("ontologies/base.owl"), object(), {"csrd:E3_5": record}),
    )
    monkeypatch.setattr(
        mod, "_existing_migrated_legacy_alias_count", lambda _db, _records: 0
    )
    monkeypatch.setattr(
        mod, "build_indicator_lookup", lambda _db: events.append("lookup") or {}
    )
    monkeypatch.setattr(
        mod, "match_indicator_id", lambda _record, _lookup: "urn:sds:reg:test"
    )
    monkeypatch.setattr(
        mod,
        "summarize_semantic_records",
        lambda _records, _matches, _path: events.append("summary")
        or {
            "ontology_path": "ontologies/base.owl",
            "ontology_sha256": "sha",
            "concept_count": 1,
            "formula_count": 1,
            "variable_count": 2,
            "equivalence_count": 1,
            "matched_indicator_count": 1,
            "unmatched_indicator_count": 0,
        },
    )
    monkeypatch.setattr(
        mod,
        "upsert_semantic_records",
        lambda _db, _records, _matches, purge_stale=False: events.append(
            f"upsert:{purge_stale}"
        )
        or {
            "created": 1,
            "updated": 0,
            "refreshed_children": 1,
            "purged_stale": 0,
        },
    )

    summary = mod.bootstrap_semantic_model_if_empty(session)

    assert events == ["load", "lookup", "summary", "upsert:False"]
    assert summary["status"] == "seeded"
    assert summary["concepts_total"] == 1
    assert summary["created"] == 1
    assert session.commits == 1
    assert session.rollbacks == 0


def test_bootstrap_semantic_model_rolls_back_seed_failures(monkeypatch):
    from src.database import bootstrap_semantic_model as mod

    record = SimpleNamespace(curie="csrd:E3_5", uri="https://data.efrag.org/esrs#E3_5")
    session = _FakeSession([0, 0])
    monkeypatch.setattr(
        mod,
        "load_semantic_records",
        lambda _path: (Path("ontologies/base.owl"), object(), {"csrd:E3_5": record}),
    )
    monkeypatch.setattr(
        mod, "_existing_migrated_legacy_alias_count", lambda _db, _records: 0
    )
    monkeypatch.setattr(mod, "build_indicator_lookup", lambda _db: {})
    monkeypatch.setattr(mod, "match_indicator_id", lambda _record, _lookup: None)
    monkeypatch.setattr(
        mod,
        "summarize_semantic_records",
        lambda _records, _matches, _path: {},
    )
    monkeypatch.setattr(
        mod,
        "upsert_semantic_records",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("seed failed")),
    )

    try:
        mod.bootstrap_semantic_model_if_empty(session)
    except RuntimeError as exc:
        assert str(exc) == "seed failed"
    else:
        raise AssertionError("bootstrap should re-raise seed failures")

    assert session.rollbacks == 1


class _FakeQuery:
    def __init__(self, rows=None, count_value=0, delete_counter=None):
        self.rows = list(rows or [])
        self.count_value = count_value
        self.delete_counter = delete_counter

    def count(self):
        return self.count_value

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self.rows)

    def delete(self, synchronize_session=False):
        if self.delete_counter is not None:
            self.delete_counter.append(synchronize_session)
        return len(self.rows)


class _SemanticSession:
    def __init__(self, *, concepts=None, indicators=None, stale=None):
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        self.concepts = list(concepts or [])
        self.indicators = list(indicators or [])
        self.stale = list(stale or [])
        self.added = []
        self.deleted = []
        self.delete_calls = []
        self.executed = []
        self._concept_query_calls = 0

    def execute(self, statement, params):
        self.executed.append((str(statement), params))

    def query(self, model):
        from src.database import bootstrap_semantic_model as mod

        if model is mod.Concept:
            self._concept_query_calls += 1
            if self._concept_query_calls == 2 and self.stale:
                return _FakeQuery(self.stale)
            return _FakeQuery(self.concepts)
        if model is mod.Indicator:
            return _FakeQuery(self.indicators)
        return _FakeQuery(delete_counter=self.delete_calls)

    def add(self, obj):
        self.added.append(obj)
        from src.database import bootstrap_semantic_model as mod

        if isinstance(obj, mod.Concept) and obj not in self.concepts:
            self.concepts.append(obj)

    def delete(self, obj):
        self.deleted.append(obj)

    def flush(self):
        from src.database import bootstrap_semantic_model as mod

        for index, concept in enumerate(
            [obj for obj in self.concepts if isinstance(obj, mod.Concept)],
            start=1,
        ):
            if concept.id is None:
                concept.id = f"concept-{index}"


def _ontology_record(**overrides):
    from src.ontology.semantic_backfill import (
        OntologyConceptRecord,
        OntologyEquivalenceRecord,
        OntologyFormulaRecord,
        OntologyVariableRecord,
    )

    values = {
        "uri": "https://example.test/concept/E3-5",
        "curie": "csrd:E3-5",
        "label": "Water withdrawal",
        "description": "Water withdrawal disclosure",
        "taxonomy": "CSRD",
        "concept_type": "Disclosure",
        "unit": "m3",
        "temporal_granularity": "annual",
        "concept_state": "active",
        "variables": (
            OntologyVariableRecord(
                uri="https://example.test/variable/input",
                label="Input",
                ordering=1,
                aggregation_method="SUM",
                temporal_granularity="annual",
            ),
        ),
        "formula": OntologyFormulaRecord(
            expression="input", expression_language="sds-formula"
        ),
        "equivalences": (
            OntologyEquivalenceRecord(
                target_uri="gri:303-3",
                target_taxonomy="GRI",
                relationship_type="equivalent",
            ),
        ),
    }
    values.update(overrides)
    return OntologyConceptRecord(**values)


def test_semantic_bootstrap_helpers_match_and_summarize_records(tmp_path):
    from src.database import bootstrap_semantic_model as mod

    indicator = SimpleNamespace(
        id="indicator-1",
        code_esrs="E3-5",
        code_gri="303-3",
        concept_state=None,
    )
    db = _SemanticSession(indicators=[indicator])
    lookup = mod.build_indicator_lookup(db)
    record = _ontology_record()
    ontology_path = tmp_path / "ontology.owl"
    ontology_path.write_text("<rdf/>", encoding="utf-8")

    assert mod._with_postgres_transaction_lock(db) is True
    assert db.executed[0][1]["key"] == mod.SEMANTIC_BOOTSTRAP_LOCK_KEY
    assert mod.match_indicator_id(record, lookup) == "indicator-1"
    assert (
        mod.match_indicator_id(
            _ontology_record(curie="gri:303-3", taxonomy="GRI"), lookup
        )
        == "indicator-1"
    )
    assert (
        mod.match_indicator_id(record, {"CSRD": {"E35": [indicator, indicator]}})
        is None
    )
    assert mod.match_indicator_id(_ontology_record(curie=""), lookup) is None

    summary = mod.summarize_semantic_records(
        {"csrd:E3-5": record, "gri:303-3": _ontology_record(curie="gri:303-3")},
        {"csrd:E3-5": "indicator-1", "gri:303-3": None},
        ontology_path,
    )

    assert summary["concept_count"] == 2
    assert summary["formula_count"] == 2
    assert summary["variable_count"] == 2
    assert summary["equivalence_count"] == 2
    assert summary["matched_indicator_count"] == 1
    assert summary["unmatched_concepts"] == ["gri:303-3"]


def test_semantic_bootstrap_runtime_uri_helpers_migrate_disclosure_aliases():
    from src.database import bootstrap_semantic_model as mod

    record = _ontology_record(
        uri="https://data.efrag.org/esrs#E3_5",
        curie="csrd:E3_5",
    )

    assert mod._runtime_record_uri(record) == "urn:sds:disclosure:csrd:e3-5"
    assert (
        mod._runtime_target_uri("https://data.globalreporting.org/gri#303_3")
        == "urn:sds:disclosure:gri:303-3"
    )


def test_upsert_semantic_records_creates_updates_children_and_purges_stale():
    from src.database import bootstrap_semantic_model as mod

    existing = mod.Concept(uri="https://example.test/concept/existing")
    existing.id = "existing-id"
    stale = SimpleNamespace(uri="https://example.test/concept/stale")
    indicator = SimpleNamespace(id="indicator-1", concept_state=None)
    db = _SemanticSession(concepts=[existing], indicators=[indicator], stale=[stale])
    records = {
        "existing": _ontology_record(
            uri=existing.uri,
            curie="csrd:E3-5",
            label="Updated label",
        ),
        "created": _ontology_record(
            uri="https://example.test/concept/created",
            curie="csrd:E3-6",
            label="Created label",
            formula=None,
            variables=(),
            equivalences=(),
        ),
    }

    stats = mod.upsert_semantic_records(
        db,
        records,
        {"csrd:E3-5": "indicator-1", "csrd:E3-6": None},
        purge_stale=True,
    )

    assert stats == {
        "created": 1,
        "updated": 1,
        "refreshed_children": 2,
        "purged_stale": 1,
        "purged_legacy_aliases": 0,
    }
    assert existing.label == "Updated label"
    assert indicator.concept_state == "active"
    assert any(isinstance(obj, mod.ConceptFormula) for obj in db.added)
    assert any(isinstance(obj, mod.ConceptVariable) for obj in db.added)
    assert any(isinstance(obj, mod.ConceptEquivalence) for obj in db.added)
    assert db.delete_calls == [False, False, False]
