"""Local SPARQL must refuse work when cooperative graph preparation exhausts its budget."""

import time

import pytest
from fastapi import HTTPException
from rdflib import Graph, Namespace

from src.api.routers.ontology import _execute_local_sparql_bounded


class SlowNamespacesGraph(Graph):
    def namespaces(self):
        time.sleep(0.12)
        yield "ex", Namespace("urn:example:")


def test_sparql_namespace_preparation_refuses_after_deadline_before_size_check():
    with pytest.raises(HTTPException) as error:
        _execute_local_sparql_bounded(
            SlowNamespacesGraph(),
            "ASK { ?s ?p ?o }",
            timeout_seconds=0.04,
            max_results=1,
            max_graph_bytes=32,
        )
    assert error.value.status_code == 504


def test_sparql_namespace_budget_stops_enumeration_before_collecting_all():
    class ManyNamespacesGraph(Graph):
        visited = 0

        def namespaces(self):
            for number in range(200):
                self.visited += 1
                yield f"p{number}" + "x" * 512, Namespace("urn:example:")

    graph = ManyNamespacesGraph()
    with pytest.raises(HTTPException) as error:
        _execute_local_sparql_bounded(
            graph,
            "ASK { ?s ?p ?o }",
            timeout_seconds=5,
            max_results=1,
            max_graph_bytes=1024,
        )
    assert error.value.status_code == 413
    assert graph.visited <= 3
