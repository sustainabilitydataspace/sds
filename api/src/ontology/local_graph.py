"""Local, offline-safe ontology graph loader (rdflib)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request
from rdflib import Graph, Namespace

import structlog
from src.config.settings import settings

logger = structlog.get_logger(__name__)


def _resolve_ontology_files() -> list[Path] | None:
    here = Path(__file__).resolve()
    api_root = here.parents[2]

    if settings.semantic_bundle_path:
        bundle_root = Path(settings.semantic_bundle_path).expanduser()
        split_candidates = [
            bundle_root / "core_tbox.owl",
            bundle_root / "generated_projection.owl",
        ]
        if all(candidate.is_file() for candidate in split_candidates):
            return split_candidates

    split_candidates = [
        api_root / "ontologies" / "core_tbox.owl",
        api_root / "ontologies" / "generated_projection.owl",
    ]
    if all(candidate.is_file() for candidate in split_candidates):
        return split_candidates

    if settings.require_database:
        return None

    candidates = [
        api_root / "ontologies" / "base.owl",
        api_root / "src" / "data" / "base_ontology.owl",
    ]

    for candidate in candidates:
        try:
            if candidate.is_file():
                return [candidate]
        except Exception:
            continue

    return None


def _load_graph(file_path: Path) -> Graph:
    graph = Graph()

    # Bind common namespaces so rdflib query() can resolve prefixes when present.
    graph.bind("sds", Namespace("https://sustainabilitydataspace.com/ontology#"))
    graph.bind("csrd", Namespace("https://data.efrag.org/esrs#"))
    graph.bind("gri", Namespace("https://data.globalreporting.org/gri#"))
    graph.bind("ghg", Namespace("https://ghgprotocol.org/standards#"))
    graph.bind("syg", Namespace("https://sustainabilitydataspace.com/sygris#"))
    graph.bind("skos", Namespace("http://www.w3.org/2004/02/skos/core#"))
    graph.bind("owl", Namespace("http://www.w3.org/2002/07/owl#"))
    graph.bind("rdf", Namespace("http://www.w3.org/1999/02/22-rdf-syntax-ns#"))
    graph.bind("rdfs", Namespace("http://www.w3.org/2000/01/rdf-schema#"))
    graph.bind("xsd", Namespace("http://www.w3.org/2001/XMLSchema#"))

    graph.parse(file_path, format="xml")
    return graph


def _assert_strict_runtime_projection(ontology_files: list[Path]) -> None:
    """Require the generated split ontology pair in DB-first mode."""
    if not settings.require_database:
        return

    expected = {"core_tbox.owl", "generated_projection.owl"}
    actual = {path.name for path in ontology_files}
    if len(ontology_files) != 2 or actual != expected:
        raise RuntimeError(
            "Canonical DB-first semantic runtime requires core_tbox.owl + generated_projection.owl."
        )


@lru_cache(maxsize=1)
def load_ontology_graph() -> tuple[Graph, str]:
    """Load and cache the ontology graph for non-FastAPI callers (e.g. engine/tests)."""
    ontology_files = _resolve_ontology_files()
    if not ontology_files:
        if settings.require_database:
            raise RuntimeError(
                "Canonical DB-first semantic runtime requires core_tbox.owl + generated_projection.owl."
            )
        raise RuntimeError("Ontology file not available")

    _assert_strict_runtime_projection(ontology_files)

    graph = _load_graph(ontology_files[0])
    for ontology_file in ontology_files[1:]:
        partial = _load_graph(ontology_file)
        for triple in partial:
            graph.add(triple)

    graph_descriptor = " + ".join(str(path) for path in ontology_files)
    logger.info(
        "Ontology graph loaded (cached)",
        file_path=graph_descriptor,
        file_count=len(ontology_files),
        triples=len(graph),
    )
    return graph, graph_descriptor


def get_ontology_graph(request: Request) -> Graph:
    """FastAPI dependency: cached local ontology graph (no Fuseki required)."""
    cached = getattr(request.app.state, "ontology_graph", None)
    if cached is not None:
        return cached

    try:
        graph, file_path = load_ontology_graph()
        request.app.state.ontology_graph = graph
        request.app.state.ontology_graph_path = file_path
        return graph
    except Exception as e:
        logger.error("Failed to load ontology graph", error=str(e))
        if settings.require_database:
            raise HTTPException(status_code=503, detail=str(e))
        raise HTTPException(status_code=503, detail="Failed to load ontology")
