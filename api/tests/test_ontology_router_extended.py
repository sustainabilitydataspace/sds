"""Ontology router coverage tests (offline-safe)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from rdflib import BNode, Dataset, Graph, Literal, Namespace, URIRef

from src.api.routers import ontology


def _pickle_load_marker(path: str, value: str) -> URIRef:
    Path(path).write_text("pickle was executed", encoding="utf-8")
    return URIRef(value)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_ontology_list_concepts_fallback_filters_and_pagination(client, admin_token):
    r = client.get(
        "/api/v1/concepts?taxonomy=CSRD&concept_type=Disclosure&search=e3&limit=1&offset=0",
        headers=_auth(admin_token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert len(body["items"]) == 1
    assert body["items"][0]["taxonomy"] == "CSRD"


def test_ontology_list_concepts_reads_base_owl_examples(client, admin_token):
    r = client.get(
        "/api/v1/concepts?taxonomy=CSRD&limit=200", headers=_auth(admin_token)
    )
    assert r.status_code == 200
    uris = {item["uri"] for item in r.json()["items"]}

    # Present in api/ontologies/base.owl but not in previous hardcoded mocks.
    assert "urn:sds:disclosure:csrd:e1-6" in uris


def test_ontology_equivalences_filters(client, admin_token):
    r = client.get(
        "/api/v1/equivalences?concept=urn:sds:disclosure:csrd:e3-5&source_taxonomy=CSRD&target_taxonomy=GRI",
        headers=_auth(admin_token),
    )
    assert r.status_code == 200
    body = r.json()
    assert all(
        "urn:sds:disclosure:csrd:e3-5" in (e["source_concept"], e["target_concept"])
        for e in body
    )
    assert any(
        e["target_concept"] == "urn:sds:disclosure:gri:303-3"
        or e["source_concept"] == "urn:sds:disclosure:gri:303-3"
        for e in body
    )


def test_ontology_execute_sparql_validation_and_local_results(client, admin_token):
    # empty query -> 400
    r = client.post(
        "/api/v1/sparql",
        headers=_auth(admin_token),
        json={"query": "  ", "format": "json"},
    )
    assert r.status_code == 400

    # forbidden operations -> 403
    r = client.post(
        "/api/v1/sparql",
        headers=_auth(admin_token),
        json={"query": "DELETE WHERE { ?s ?p ?o }", "format": "json"},
    )
    assert r.status_code == 403

    # Graph-returning queries are not part of the tabular transport contract.
    for unsupported in (
        "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o } LIMIT 1",
        "DESCRIBE <urn:test:s>",
    ):
        rejected = client.post(
            "/api/v1/sparql",
            headers=_auth(admin_token),
            json={"query": unsupported, "format": "json"},
        )
        assert rejected.status_code == 403

    ok = client.post(
        "/api/v1/sparql",
        headers=_auth(admin_token),
        json={
            "query": "SELECT ?concept ?label WHERE { ?concept rdfs:label ?label . } LIMIT 1",
            "format": "json",
        },
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["result_count"] >= 1
    assert "note" not in body


def test_ontology_get_concept_details_and_taxonomies(client, admin_token):
    ok = client.get(
        "/api/v1/concepts/urn:sds:disclosure:csrd:e3-5",
        headers=_auth(admin_token),
    )
    assert ok.status_code == 200
    assert ok.json()["taxonomy"] == "CSRD"

    missing = client.get("/api/v1/concepts/not-found", headers=_auth(admin_token))
    assert missing.status_code == 404

    tax = client.get("/api/v1/taxonomies", headers=_auth(admin_token))
    assert tax.status_code == 200
    assert tax.json()["total_taxonomies"] >= 1


@pytest.mark.asyncio
async def test_sparql_parser_is_inside_killable_query_worker(monkeypatch):
    from unittest.mock import MagicMock

    from rdflib import Graph, Literal, URIRef

    import src.api.routers.ontology as ontology

    monkeypatch.setattr(ontology.settings, "require_database", False)

    def forbidden_parent_parser(_query):
        raise AssertionError("unbounded SPARQL parser ran on the API process")

    monkeypatch.setattr(
        ontology, "_validate_local_sparql_query", forbidden_parent_parser
    )
    graph = Graph()
    graph.add((URIRef("urn:test:s"), URIRef("urn:test:p"), Literal("ok")))
    response = await ontology.execute_sparql(
        query=MagicMock(
            query="SELECT ?v WHERE { <urn:test:s> <urn:test:p> ?v }", format="json"
        ),
        graph=graph,
        concept_service=None,
        current_user=MagicMock(),
    )
    assert response["results"] == [{"v": "ok"}]


@pytest.mark.asyncio
async def test_sparql_fails_closed_when_db_required_and_no_db(monkeypatch):
    """When REQUIRE_DATABASE is true and no DB semantic service is available,
    SPARQL must fail closed (503) rather than silently falling back to the
    local ontology graph."""
    from unittest.mock import MagicMock

    from rdflib import Graph

    import src.api.routers.ontology as ontology
    import src.config.settings as settings_module

    monkeypatch.setattr(
        settings_module.settings, "require_database", True, raising=False
    )

    with pytest.raises(Exception) as exc_info:
        await ontology.execute_sparql(
            query=MagicMock(
                query="SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 1", format="json"
            ),
            graph=Graph(),
            concept_service=None,
            current_user=MagicMock(),
        )
    assert exc_info.value.status_code == 503
    assert (
        "Canonical semantic data is required for execute_sparql"
        in exc_info.value.detail
    )


@pytest.mark.asyncio
async def test_sparql_returns_501_when_db_service_present_but_sparql_unsupported(
    monkeypatch,
):
    """When REQUIRE_DATABASE is true and a DB semantic service IS available,
    SPARQL must return 501 (not yet supported) rather than silently falling
    back to the local ontology graph."""
    from unittest.mock import MagicMock

    from rdflib import Graph

    import src.api.routers.ontology as ontology
    import src.config.settings as settings_module

    monkeypatch.setattr(
        settings_module.settings, "require_database", True, raising=False
    )

    svc = MagicMock()
    svc.has_semantic_data.return_value = True

    with pytest.raises(Exception) as exc_info:
        await ontology.execute_sparql(
            query=MagicMock(
                query="SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 1", format="json"
            ),
            graph=Graph(),
            concept_service=svc,
            current_user=MagicMock(),
        )
    assert exc_info.value.status_code == 501
    assert (
        "SPARQL over DB-backed semantics is not yet supported" in exc_info.value.detail
    )


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * WHERE { SERVICE <http://127.0.0.1/sparql> { ?s ?p ?o } }",
        "SELECT * FROM <http://127.0.0.1/data> WHERE { ?s ?p ?o }",
    ],
)
def test_custom_sparql_rejects_remote_graph_clauses(query):
    with pytest.raises(HTTPException) as exc_info:
        ontology._validate_local_sparql_query(query)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Remote SPARQL graph access is not allowed"


def test_custom_sparql_structural_guard_allows_keywords_inside_literals():
    ontology._validate_local_sparql_query(
        'SELECT * WHERE { ?s ?p "SERVICE FROM" } LIMIT 1'
    )


def test_custom_sparql_result_count_is_bounded():
    graph = Graph()
    for index in range(3):
        graph.add(
            (
                URIRef(f"urn:test:s:{index}"),
                URIRef("urn:test:p"),
                Literal(index),
            )
        )

    with pytest.raises(HTTPException) as exc_info:
        ontology._execute_local_sparql_bounded(
            graph,
            "SELECT * WHERE { ?s ?p ?o }",
            timeout_seconds=5.0,
            max_results=2,
            max_graph_bytes=1024 * 1024,
        )

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == "SPARQL result limit exceeded"


def test_custom_sparql_never_executes_pickle_payload_from_graph_term(tmp_path):
    marker = tmp_path / "unexpected-pickle-load"

    class HostileURIRef(URIRef):
        def __reduce__(self):
            return (_pickle_load_marker, (str(marker), str(self)))

    graph = Graph()
    graph.add((HostileURIRef("urn:test:s"), URIRef("urn:test:p"), Literal("o")))

    rows = ontology._execute_local_sparql_bounded(
        graph,
        "SELECT ?s WHERE { ?s <urn:test:p> ?o }",
        timeout_seconds=5.0,
        max_results=10,
        max_graph_bytes=1024 * 1024,
    )

    assert rows == [{"s": "urn:test:s"}]
    assert not marker.exists()


def test_custom_sparql_rejects_named_graph_contexts():
    dataset = Dataset()
    dataset.graph(URIRef("urn:test:g")).add(
        (URIRef("urn:test:s"), URIRef("urn:test:p"), Literal("o"))
    )

    with pytest.raises(HTTPException) as exc_info:
        ontology._execute_local_sparql_bounded(
            dataset,
            "SELECT ?s WHERE { ?s <urn:test:p> ?o }",
            timeout_seconds=5.0,
            max_results=10,
            max_graph_bytes=1024 * 1024,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Named ontology graphs are not supported"


def test_custom_sparql_preserves_blank_nodes_languages_and_graph_prefixes():
    graph = Graph()
    graph.bind("ex", Namespace("urn:test:"))
    graph.add(
        (BNode("local-node"), URIRef("urn:test:p"), Literal("bonjour", lang="fr"))
    )

    rows = ontology._execute_local_sparql_bounded(
        graph,
        'SELECT ?s ?v WHERE { ?s ex:p ?v FILTER(lang(?v) = "fr") }',
        timeout_seconds=5.0,
        max_results=10,
        max_graph_bytes=1024 * 1024,
    )

    assert rows == [{"s": "local-node", "v": "bonjour"}]


def test_custom_sparql_timeout_terminates_worker():
    graph = Graph()
    graph.add((URIRef("urn:test:s"), URIRef("urn:test:p"), Literal("o")))

    with pytest.raises(HTTPException) as exc_info:
        ontology._execute_local_sparql_bounded(
            graph,
            "SELECT * WHERE { ?s ?p ?o }",
            timeout_seconds=0.000001,
            max_results=10,
            max_graph_bytes=1024 * 1024,
        )

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail == "SPARQL query timed out"


def test_custom_sparql_serialization_exhausts_total_timeout_before_child(monkeypatch):
    """The query deadline includes graph preparation, not just child execution."""
    import time

    graph = Graph()
    graph.add((URIRef("urn:test:s"), URIRef("urn:test:p"), Literal("o")))
    original = ontology._plain_rdf_term

    def slow_term(term):
        time.sleep(0.012)
        return original(term)

    monkeypatch.setattr(ontology, "_plain_rdf_term", slow_term)

    def no_child_after_deadline(*_args, **_kwargs):
        raise AssertionError("expired request launched a query worker")

    monkeypatch.setattr(
        ontology.multiprocessing, "get_context", no_child_after_deadline
    )
    with pytest.raises(HTTPException) as exc_info:
        ontology._execute_local_sparql_bounded(
            graph,
            "ASK { ?s ?p ?o }",
            timeout_seconds=0.002,
            max_results=10,
            max_graph_bytes=1024 * 1024,
        )
    assert exc_info.value.status_code == 504
    assert exc_info.value.detail == "SPARQL query timed out"
