from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import OWL, RDF

from src.config.settings import settings
from src.ontology import local_graph

SDS = Namespace("https://sustainabilitydataspace.com/ontology#")
CSRD = Namespace("https://data.efrag.org/esrs#")


def test_local_graph_import_does_not_initialize_settings():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import pydantic_settings; "
            "pydantic_settings.BaseSettings.__init__ = lambda *args, **kwargs: "
            "(_ for _ in ()).throw(RuntimeError('SETTINGS_EAGER')); "
            "from src.ontology import local_graph; "
            "assert 'src.config.settings' not in __import__('sys').modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _write_graph(path: Path, triples: list[tuple[URIRef, URIRef, URIRef]]) -> None:
    graph = Graph()
    graph.bind("sds", SDS)
    graph.bind("csrd", CSRD)
    graph.bind("owl", OWL)
    graph.bind("rdf", RDF)
    for triple in triples:
        graph.add(triple)
    path.write_text(graph.serialize(format="xml"), encoding="utf-8")


def test_load_ontology_graph_merges_split_files(monkeypatch, tmp_path):
    core = tmp_path / "core_tbox.owl"
    projection = tmp_path / "generated_projection.owl"
    _write_graph(core, [(SDS.Disclosure, RDF.type, OWL.Class)])
    _write_graph(projection, [(CSRD.E3_5, RDF.type, SDS.Disclosure)])

    local_graph.load_ontology_graph.cache_clear()
    monkeypatch.setattr(
        local_graph, "_resolve_ontology_files", lambda: [core, projection]
    )

    graph, descriptor = local_graph.load_ontology_graph()

    assert (SDS.Disclosure, RDF.type, OWL.Class) in graph
    assert (CSRD.E3_5, RDF.type, SDS.Disclosure) in graph
    assert "core_tbox.owl" in descriptor
    assert "generated_projection.owl" in descriptor

    local_graph.load_ontology_graph.cache_clear()


def test_load_ontology_graph_falls_back_to_single_file(monkeypatch, tmp_path):
    base = tmp_path / "base.owl"
    _write_graph(base, [(SDS.Variable, RDF.type, OWL.Class)])

    local_graph.load_ontology_graph.cache_clear()
    monkeypatch.setattr(settings, "require_database", False)
    monkeypatch.setattr(local_graph, "_resolve_ontology_files", lambda: [base])

    graph, descriptor = local_graph.load_ontology_graph()

    assert (SDS.Variable, RDF.type, OWL.Class) in graph
    assert descriptor == str(base)

    local_graph.load_ontology_graph.cache_clear()


def test_load_ontology_graph_rejects_single_file_in_db_first(monkeypatch, tmp_path):
    base = tmp_path / "base.owl"
    _write_graph(base, [(SDS.Variable, RDF.type, OWL.Class)])

    local_graph.load_ontology_graph.cache_clear()
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(local_graph, "_resolve_ontology_files", lambda: [base])

    with pytest.raises(
        RuntimeError, match="core_tbox.owl \\+ generated_projection.owl"
    ):
        local_graph.load_ontology_graph()

    local_graph.load_ontology_graph.cache_clear()


def test_get_ontology_graph_returns_503_in_db_first_when_projection_missing(
    monkeypatch, tmp_path
):
    base = tmp_path / "base.owl"
    _write_graph(base, [(SDS.Variable, RDF.type, OWL.Class)])

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    local_graph.load_ontology_graph.cache_clear()
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(local_graph, "_resolve_ontology_files", lambda: [base])

    with pytest.raises(HTTPException) as exc:
        local_graph.get_ontology_graph(request)

    assert exc.value.status_code == 503
    assert exc.value.detail == "Canonical ontology projection unavailable"

    local_graph.load_ontology_graph.cache_clear()


def test_resolve_ontology_files_handles_strict_and_legacy_fallbacks(monkeypatch):
    calls: list[str] = []

    def fake_is_file(path: Path) -> bool:
        calls.append(path.name)
        if path.name == "base.owl":
            return True
        return False

    monkeypatch.setattr(settings, "require_database", False)
    monkeypatch.setattr(Path, "is_file", fake_is_file)

    resolved = local_graph._resolve_ontology_files()

    assert resolved is not None
    assert [path.name for path in resolved] == ["base.owl"]
    assert "core_tbox.owl" in calls


def test_resolve_ontology_files_prefers_split_pair(monkeypatch):
    def fake_is_file(path: Path) -> bool:
        return path.name in {"core_tbox.owl", "generated_projection.owl"}

    monkeypatch.setattr(settings, "require_database", False)
    monkeypatch.setattr(Path, "is_file", fake_is_file)

    resolved = local_graph._resolve_ontology_files()

    assert resolved is not None
    assert [path.name for path in resolved] == [
        "core_tbox.owl",
        "generated_projection.owl",
    ]


def test_resolve_ontology_files_returns_none_in_strict_mode(monkeypatch):
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(Path, "is_file", lambda self: False)

    assert local_graph._resolve_ontology_files() is None


def test_resolve_ontology_files_ignores_candidate_probe_errors(monkeypatch):
    def fake_is_file(path: Path) -> bool:
        if path.name == "base.owl":
            raise OSError("stat failed")
        return False

    monkeypatch.setattr(settings, "require_database", False)
    monkeypatch.setattr(Path, "is_file", fake_is_file)

    assert local_graph._resolve_ontology_files() is None


def test_load_ontology_graph_missing_files_uses_mode_specific_errors(monkeypatch):
    local_graph.load_ontology_graph.cache_clear()
    monkeypatch.setattr(local_graph, "_resolve_ontology_files", lambda: None)

    monkeypatch.setattr(settings, "require_database", False)
    with pytest.raises(RuntimeError, match="Ontology file not available"):
        local_graph.load_ontology_graph()

    local_graph.load_ontology_graph.cache_clear()
    monkeypatch.setattr(settings, "require_database", True)
    with pytest.raises(RuntimeError, match="core_tbox.owl"):
        local_graph.load_ontology_graph()

    local_graph.load_ontology_graph.cache_clear()


def test_get_ontology_graph_uses_cached_graph_and_masks_non_strict_loader_errors(
    monkeypatch,
):
    cached_graph = Graph()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(ontology_graph=cached_graph))
    )

    assert local_graph.get_ontology_graph(request) is cached_graph

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    local_graph.load_ontology_graph.cache_clear()
    monkeypatch.setattr(settings, "require_database", False)
    monkeypatch.setattr(
        local_graph,
        "load_ontology_graph",
        lambda: (_ for _ in ()).throw(RuntimeError("missing local file")),
    )

    with pytest.raises(HTTPException) as exc:
        local_graph.get_ontology_graph(request)

    assert exc.value.status_code == 503
    assert exc.value.detail == "Failed to load ontology"

    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(
        local_graph,
        "load_ontology_graph",
        lambda: (_ for _ in ()).throw(RuntimeError("private database detail")),
    )

    with pytest.raises(HTTPException) as strict_exc:
        local_graph.get_ontology_graph(request)

    assert strict_exc.value.status_code == 503
    assert strict_exc.value.detail == "Canonical ontology projection unavailable"
    assert "private database detail" not in str(strict_exc.value.detail)
