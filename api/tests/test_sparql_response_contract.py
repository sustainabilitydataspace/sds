"""SPARQL's public JSON projection must not rewrite literal lexical content."""

import asyncio
import threading
from unittest.mock import MagicMock

import pytest
from rdflib import Graph, Literal, URIRef

import src.api.routers.ontology as ontology
from src.api.models import SPARQLQuery


@pytest.mark.asyncio
async def test_sparql_does_not_compact_uri_looking_language_literal(monkeypatch):
    monkeypatch.setattr(ontology.settings, "require_database", False)
    uri_looking_text = "https://data.efrag.org/esrs#E1"
    graph = Graph()
    graph.add(
        (
            URIRef("urn:test:s"),
            URIRef("urn:test:p"),
            Literal(uri_looking_text, lang="fr"),
        )
    )
    response = await ontology.execute_sparql(
        query=SPARQLQuery(
            query="SELECT ?v WHERE { <urn:test:s> <urn:test:p> ?v }", format="json"
        ),
        graph=graph,
        concept_service=None,
        current_user=MagicMock(),
    )
    assert response["results"] == [{"v": uri_looking_text}]


def test_sparql_rejects_an_xml_format_not_implemented_by_the_api():
    with pytest.raises(ValueError):
        SPARQLQuery(query="ASK { ?s ?p ?o }", format="xml")


def test_sparql_cleanup_kills_child_that_ignores_terminate(monkeypatch):
    class StubbornWorker:
        killed = False
        terminated = False

        def start(self):
            pass

        def is_alive(self):
            return not self.killed

        def join(self, timeout=None):
            pass

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    worker = StubbornWorker()
    receive = MagicMock()
    receive.poll.return_value = True
    receive.recv.return_value = {"status": "ok", "rows": []}
    context = MagicMock()
    context.Pipe.return_value = (receive, MagicMock())
    context.Process.return_value = worker
    monkeypatch.setattr(ontology.multiprocessing, "get_context", lambda _: context)
    assert (
        ontology._execute_local_sparql_bounded(
            Graph(),
            "SELECT ?s WHERE { ?s ?p ?o }",
            timeout_seconds=3,
            max_results=10,
            max_graph_bytes=32 * 1024,
        )
        == []
    )
    assert worker.terminated and worker.killed


@pytest.mark.asyncio
async def test_local_sparql_parent_preparation_does_not_block_event_loop(monkeypatch):
    monkeypatch.setattr(ontology.settings, "require_database", False)
    entered = threading.Event()
    release = threading.Event()

    def delayed_preparation(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return []

    monkeypatch.setattr(ontology, "_execute_local_sparql_bounded", delayed_preparation)
    task = asyncio.create_task(
        ontology.execute_sparql(
            query=SPARQLQuery(query="SELECT ?s WHERE { ?s ?p ?o }", format="json"),
            graph=Graph(),
            concept_service=None,
            current_user=MagicMock(),
        )
    )
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 1), timeout=2)
        await asyncio.wait_for(asyncio.sleep(0), timeout=0.25)
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=3)


@pytest.mark.asyncio
async def test_local_sparql_rejects_third_blocking_query_without_stalling(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(ontology.settings, "require_database", False)
    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    calls = 0

    def delayed(*args, **kwargs):
        nonlocal calls
        with lock:
            calls += 1
            if calls == 2:
                entered.set()
        assert release.wait(3)
        return []

    monkeypatch.setattr(ontology, "_execute_local_sparql_bounded", delayed)

    def request():
        return ontology.execute_sparql(
            query=SPARQLQuery(query="SELECT ?s WHERE { ?s ?p ?o }", format="json"),
            graph=Graph(),
            concept_service=None,
            current_user=MagicMock(),
        )

    first, second = asyncio.create_task(request()), asyncio.create_task(request())
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 1), 2)
        with pytest.raises(HTTPException) as refused:
            await request()
        assert refused.value.status_code == 503
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 3)


@pytest.mark.asyncio
async def test_local_sparql_worker_submission_error_releases_slot(monkeypatch):
    monkeypatch.setattr(ontology.settings, "require_database", False)

    def reject_submission(*args):
        raise RuntimeError("executor unavailable")

    monkeypatch.setattr(ontology._LOCAL_SPARQL_WORKERS, "submit", reject_submission)
    with pytest.raises(Exception, match="executor unavailable"):
        await ontology.execute_sparql(
            query=SPARQLQuery(query="SELECT ?s WHERE { ?s ?p ?o }", format="json"),
            graph=Graph(),
            concept_service=None,
            current_user=MagicMock(),
        )
    assert ontology._LOCAL_SPARQL_WORK_SLOTS.acquire(blocking=False)
    assert ontology._LOCAL_SPARQL_WORK_SLOTS.acquire(blocking=False)
    ontology._LOCAL_SPARQL_WORK_SLOTS.release()
    ontology._LOCAL_SPARQL_WORK_SLOTS.release()
