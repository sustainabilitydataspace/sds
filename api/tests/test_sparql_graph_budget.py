"""Adversarial graph preparation bounds for local SPARQL queries."""

import time

import pytest
from fastapi import HTTPException
from rdflib import Graph, Literal, URIRef

from src.api.routers import ontology

SPARQL_WORKER_TIMEOUT_SECONDS = 15


def test_rejects_oversized_graph_during_encoding(monkeypatch):
    """The byte cap must stop graph iteration before constructing all triples."""
    graph = Graph()
    for index in range(50):
        graph.add(
            (
                URIRef(f"urn:test:{index}:" + "x" * 700),
                URIRef("urn:test:p"),
                Literal("o"),
            )
        )
    original = ontology._plain_rdf_term
    encoded_terms = 0

    def bounded_term(term):
        nonlocal encoded_terms
        encoded_terms += 1
        assert encoded_terms <= 9, "graph read past the configured byte cap"
        return original(term)

    monkeypatch.setattr(ontology, "_plain_rdf_term", bounded_term)
    with pytest.raises(HTTPException) as exc_info:
        ontology._execute_local_sparql_bounded(
            graph,
            "ASK { ?s ?p ?o }",
            timeout_seconds=SPARQL_WORKER_TIMEOUT_SECONDS,
            max_results=10,
            max_graph_bytes=2000,
        )
    assert exc_info.value.status_code == 413


def test_sparql_reply_read_cannot_outlive_total_deadline(monkeypatch):
    """A readable pipe can still block while its frame is being received."""
    graph = Graph()
    graph.add((URIRef("urn:test:s"), URIRef("urn:test:p"), Literal("o")))

    class Receiver:
        def poll(self, _timeout):
            return True

        def recv(self):
            time.sleep(0.4)
            return {"status": "ok", "rows": []}

        def close(self):
            pass

    class Sender:
        def close(self):
            pass

    class Worker:
        alive = True

        def start(self):
            pass

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.alive = False

        def kill(self):
            self.alive = False

        def join(self, **_kwargs):
            pass

    class Context:
        def Pipe(self, **_kwargs):
            return Receiver(), Sender()

        def Process(self, **_kwargs):
            return Worker()

    monkeypatch.setattr(ontology.multiprocessing, "get_context", lambda _: Context())
    start = time.monotonic()
    with pytest.raises(HTTPException) as exc_info:
        ontology._execute_local_sparql_bounded(
            graph,
            "ASK { ?s ?p ?o }",
            timeout_seconds=0.04,
            max_results=10,
            max_graph_bytes=1024 * 1024,
        )
    assert time.monotonic() - start < 0.25
    assert exc_info.value.status_code == 504


def test_sparql_query_parser_rejects_malformed_input():
    with pytest.raises(HTTPException) as exc_info:
        ontology._validate_local_sparql_query("SELECT WHERE unbalanced {")
    assert exc_info.value.status_code == 400


def test_sparql_ask_result_is_bounded_plain_boolean():
    graph = Graph()
    graph.add((URIRef("urn:test:s"), URIRef("urn:test:p"), Literal("o")))
    assert ontology._execute_local_sparql_bounded(
        graph,
        "ASK { <urn:test:s> <urn:test:p> ?v }",
        timeout_seconds=SPARQL_WORKER_TIMEOUT_SECONDS,
        max_results=1,
        max_graph_bytes=1024 * 1024,
    ) == [{"boolean": "true"}]


def test_sparql_result_cell_byte_budget_rejects_without_partial_result():
    graph = Graph()
    graph.add((URIRef("urn:test:s"), URIRef("urn:test:p"), Literal("large")))
    with pytest.raises(HTTPException) as exc_info:
        ontology._execute_local_sparql_bounded(
            graph,
            "SELECT ?s WHERE { ?s <urn:test:p> ?v }",
            timeout_seconds=SPARQL_WORKER_TIMEOUT_SECONDS,
            max_results=10,
            max_graph_bytes=1024 * 1024,
            max_result_bytes=1,
        )
    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == "SPARQL result limit exceeded"


def test_sparql_repeated_variable_keys_count_toward_serialized_result_budget():
    graph = Graph()
    for number in range(20):
        graph.add((URIRef(f"urn:test:s{number}"), URIRef("urn:test:p"), Literal("x")))
    with pytest.raises(HTTPException) as exc_info:
        ontology._execute_local_sparql_bounded(
            graph,
            "SELECT ?long_variable_name WHERE { ?s <urn:test:p> ?long_variable_name }",
            timeout_seconds=SPARQL_WORKER_TIMEOUT_SECONDS,
            max_results=20,
            max_graph_bytes=1024 * 1024,
            max_result_bytes=80,
        )
    assert exc_info.value.status_code == 413


def test_sparql_worker_rejects_invalid_query_without_raw_parser_error():
    graph = Graph()
    graph.add((URIRef("urn:test:s"), URIRef("urn:test:p"), Literal("o")))
    with pytest.raises(HTTPException) as exc_info:
        ontology._execute_local_sparql_bounded(
            graph,
            "THIS IS NOT SPARQL",
            timeout_seconds=SPARQL_WORKER_TIMEOUT_SECONDS,
            max_results=10,
            max_graph_bytes=1024 * 1024,
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid SPARQL query"


def test_sparql_worker_rejects_remote_graph_clause_before_execution():
    graph = Graph()
    with pytest.raises(HTTPException) as exc_info:
        ontology._execute_local_sparql_bounded(
            graph,
            "SELECT * WHERE { SERVICE <http://127.0.0.1:1/unreachable> { ?s ?p ?o } }",
            timeout_seconds=SPARQL_WORKER_TIMEOUT_SECONDS,
            max_results=1,
            max_graph_bytes=1024 * 1024,
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Remote SPARQL graph access is not allowed"


def test_sparql_ask_boolean_is_subject_to_serialized_result_budget():
    graph = Graph()
    with pytest.raises(HTTPException) as exc_info:
        ontology._execute_local_sparql_bounded(
            graph,
            "ASK { ?s ?p ?o }",
            timeout_seconds=SPARQL_WORKER_TIMEOUT_SECONDS,
            max_results=1,
            max_graph_bytes=1024 * 1024,
            max_result_bytes=3,
        )
    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == "SPARQL result limit exceeded"


def test_sparql_worker_rejects_malformed_graph_term_and_closes_transport():
    import json

    class CaptureConnection:
        message = None
        closed = False

        def send(self, message):
            self.message = message

        def close(self):
            self.closed = True

    connection = CaptureConnection()
    graph_payload = json.dumps(
        {
            "namespaces": [],
            "triples": [
                [
                    ["iri", "urn:test:s"],
                    ["iri", "urn:test:p"],
                    ["unsupported", "opaque"],
                ]
            ],
        }
    ).encode("utf-8")
    ontology._sparql_query_worker(
        graph_payload,
        "ASK { ?s ?p ?o }",
        1,
        1024,
        connection,
    )
    assert connection.message == {"status": "error"}
    assert connection.closed is True
