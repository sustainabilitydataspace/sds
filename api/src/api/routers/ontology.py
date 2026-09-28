"""
API router for ontology endpoints.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from pyparsing import ParseResults
from rdflib import (
    OWL,
    RDF,
    RDFS,
    BNode,
    ConjunctiveGraph,
    Graph,
    Literal,
    Namespace,
    URIRef,
)
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.parserutils import CompValue
from sqlalchemy.orm import Session

import structlog
from src.api.models import ConceptInfo, EquivalenceInfo, PaginatedResponse, SPARQLQuery
from src.auth.dependencies import get_current_active_user, require_permission
from src.auth.models import Permission, User
from src.concept_catalog_policy import FUTURE_CATALOG_TAXONOMIES
from src.concept_runtime_aliases import (
    concept_uri_candidates,
    normalize_runtime_concept_uri,
)
from src.config.settings import settings
from src.database.session import get_db_optional
from src.ontology.curie import DEFAULT_NAMESPACES, taxonomy_from_curie
from src.ontology.local_graph import get_ontology_graph
from src.semantic import write_replay
from src.semantic.read_scope import ReadScopeError, resolve_eval_scope
from src.services.canonical_data import CanonicalDataUnavailableError
from src.services.concept_service import ConceptService
from src.services.resolver_read import SemanticResolverRepository, resolve_read_context

logger = structlog.get_logger(__name__)
_LOCAL_SPARQL_WORK_SLOTS = threading.BoundedSemaphore(2)
_LOCAL_SPARQL_WORKERS = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="sds-sparql"
)

router = APIRouter()

SDS = Namespace("https://sustainabilitydataspace.com/ontology#")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")

# VARCH-8e: backward-compatible public temporal-read selectors on /concepts.
CONCEPTS_PROJECTION_VERSION = "sds-concepts-v1"
CONCEPTS_EVS_KEY = "catalog:concepts"

_FORBIDDEN_REMOTE_SPARQL_NODES = frozenset({"ServiceGraphPattern", "DatasetClause"})


def _walk_sparql_nodes(value: Any):
    if isinstance(value, CompValue):
        yield value.name
        for child in value.values():
            yield from _walk_sparql_nodes(child)
    elif isinstance(value, (list, tuple, ParseResults)):
        for child in value:
            yield from _walk_sparql_nodes(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_sparql_nodes(child)


def _validate_local_sparql_query(query: str) -> None:
    """Reject clauses that can cause RDFLib to fetch attacker-selected URLs."""
    try:
        parsed = parseQuery(query)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid SPARQL query") from exc
    if _FORBIDDEN_REMOTE_SPARQL_NODES.intersection(_walk_sparql_nodes(parsed)):
        raise HTTPException(
            status_code=400,
            detail="Remote SPARQL graph access is not allowed",
        )


def _plain_rdf_term(term: URIRef | BNode | Literal) -> list[Optional[str]]:
    if isinstance(term, Literal):
        return [
            "literal",
            str(term),
            str(term.datatype) if term.datatype is not None else None,
            term.language,
        ]
    if isinstance(term, BNode):
        return ["blank", str(term)]
    if isinstance(term, URIRef):
        return ["iri", str(term)]
    raise HTTPException(status_code=400, detail="Unsupported ontology term")


def _rdf_term_from_plain(term: list[Optional[str]]) -> URIRef | BNode | Literal:
    if term[0] == "iri":
        return URIRef(term[1])
    if term[0] == "blank":
        return BNode(term[1])
    if term[0] == "literal":
        return Literal(
            term[1],
            datatype=URIRef(term[2]) if term[2] is not None else None,
            lang=term[3],
            normalize=False,
        )
    raise ValueError("Unsupported ontology term")


def _sparql_query_worker(
    graph_payload: bytes,
    prepared_query: str,
    max_results: int,
    max_result_bytes: int,
    connection,
) -> None:
    """Execute one local query in a killable child and return bounded plain data."""
    try:
        _validate_local_sparql_query(prepared_query)
        plain_graph = json.loads(graph_payload)
        worker_graph = Graph()
        for prefix, namespace in plain_graph["namespaces"]:
            worker_graph.bind(prefix, Namespace(namespace))
        for triple in plain_graph["triples"]:
            worker_graph.add(tuple(_rdf_term_from_plain(term) for term in triple))
        query_results = worker_graph.query(prepared_query)
        rows: list[dict[str, Optional[str]]] = []
        result_bytes = 2  # JSON array delimiters around the bounded rows.
        if getattr(query_results, "type", None) == "ASK":
            answer: dict[str, Optional[str]] = {
                "boolean": str(bool(query_results.askAnswer)).lower()
            }
            result_bytes += len(
                json.dumps(answer, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            if result_bytes > max_result_bytes:
                connection.send({"status": "overflow"})
                return
            rows.append(answer)
        else:
            for row in query_results:
                if len(rows) >= max_results:
                    connection.send({"status": "overflow"})
                    return
                plain_row: dict[str, Optional[str]] = {}
                for variable, value in row.asdict().items():
                    cell = None if value is None else str(value)
                    plain_row[str(variable)] = cell
                result_bytes += (1 if rows else 0) + len(
                    json.dumps(
                        plain_row, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                )
                if result_bytes > max_result_bytes:
                    connection.send({"status": "overflow"})
                    return
                rows.append(plain_row)
        connection.send({"status": "ok", "rows": rows})
    except HTTPException as exc:
        connection.send({"status": "invalid", "detail": exc.detail})
    except BaseException:
        connection.send({"status": "error"})
    finally:
        connection.close()


def _execute_local_sparql_bounded(
    graph: Graph,
    prepared_query: str,
    *,
    timeout_seconds: float,
    max_results: int,
    max_graph_bytes: int,
    max_result_bytes: int = 2 * 1024 * 1024,
) -> list[dict[str, Optional[str]]]:
    """Bound local query work and bytes; OS spawn/graph iterator is not preemptible."""
    deadline = time.monotonic() + timeout_seconds
    if isinstance(graph, ConjunctiveGraph):
        raise HTTPException(
            status_code=400, detail="Named ontology graphs are not supported"
        )
    namespaces = []
    namespace_bytes = len(b'{"namespaces":[],"triples":[]}')
    for prefix, namespace in graph.namespaces():
        if time.monotonic() >= deadline:
            raise HTTPException(status_code=504, detail="SPARQL query timed out")
        if prefix is not None:
            pair = [str(prefix), str(namespace)]
            if time.monotonic() >= deadline:
                raise HTTPException(status_code=504, detail="SPARQL query timed out")
            namespace_bytes += (1 if namespaces else 0) + len(
                json.dumps(pair, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            if namespace_bytes > max_graph_bytes:
                raise HTTPException(
                    status_code=413, detail="Ontology graph limit exceeded"
                )
            namespaces.append(pair)
    if time.monotonic() >= deadline:
        raise HTTPException(status_code=504, detail="SPARQL query timed out")
    prefix = json.dumps(
        {"namespaces": namespaces}, ensure_ascii=False, separators=(",", ":")
    )
    if time.monotonic() >= deadline:
        raise HTTPException(status_code=504, detail="SPARQL query timed out")
    graph_buffer = bytearray((prefix[:-1] + ',"triples":[').encode("utf-8"))
    if len(graph_buffer) + 2 > max_graph_bytes:
        raise HTTPException(status_code=413, detail="Ontology graph limit exceeded")
    first_triple = True
    for triple in graph:
        if time.monotonic() >= deadline:
            raise HTTPException(status_code=504, detail="SPARQL query timed out")
        encoded = json.dumps(
            [_plain_rdf_term(term) for term in triple],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(graph_buffer) + len(encoded) + 2 + (not first_triple) > max_graph_bytes:
            raise HTTPException(status_code=413, detail="Ontology graph limit exceeded")
        if not first_triple:
            graph_buffer.extend(b",")
        graph_buffer.extend(encoded)
        first_triple = False
    graph_buffer.extend(b"]}")
    graph_payload = bytes(graph_buffer)
    if time.monotonic() >= deadline:
        raise HTTPException(status_code=504, detail="SPARQL query timed out")

    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    worker = context.Process(
        target=_sparql_query_worker,
        args=(
            graph_payload,
            prepared_query,
            max_results,
            max_result_bytes,
            send,
        ),
        daemon=True,
    )
    worker.start()
    send.close()
    try:
        if not receive.poll(max(0.0, deadline - time.monotonic())):
            worker.terminate()
            worker.join(timeout=1.0)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=1.0)
            raise HTTPException(status_code=504, detail="SPARQL query timed out")
        reply: queue.Queue[object] = queue.Queue(maxsize=1)

        def read_reply() -> None:
            try:
                reply.put(receive.recv())
            except (EOFError, OSError) as exc:
                reply.put(exc)

        reader = threading.Thread(target=read_reply, daemon=True)
        reader.start()
        try:
            payload = reply.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty as exc:
            worker.terminate()
            worker.join(timeout=1.0)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=1.0)
            raise HTTPException(
                status_code=504, detail="SPARQL query timed out"
            ) from exc
        if isinstance(payload, (EOFError, OSError)):
            raise HTTPException(
                status_code=500, detail="SPARQL query execution failed"
            ) from payload
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="SPARQL query execution failed")
    finally:
        receive.close()
        if worker.is_alive():
            worker.join(timeout=1.0)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=1.0)
                if worker.is_alive():
                    worker.kill()
                    worker.join(timeout=1.0)

    if payload.get("status") == "overflow":
        raise HTTPException(status_code=413, detail="SPARQL result limit exceeded")
    if payload.get("status") == "invalid":
        detail = payload.get("detail")
        if detail not in (
            "Invalid SPARQL query",
            "Remote SPARQL graph access is not allowed",
        ):
            detail = "Invalid SPARQL query"
        raise HTTPException(status_code=400, detail=detail)
    if payload.get("status") != "ok" or not isinstance(payload.get("rows"), list):
        raise HTTPException(status_code=400, detail="SPARQL query execution failed")
    return payload["rows"]


def _concepts_temporal_pins(
    db: Optional[Session],
    *,
    valid_as_of: Optional[datetime],
    decision_as_of: Optional[int],
    as_of_commit_id: Optional[int],
    current_user: User,
) -> Optional[Dict[str, Any]]:
    """Reproducibility pins for a temporal /concepts read, or None when no selector is given.

    When NO selector is supplied the function returns ``None`` and the endpoint keeps its exact
    backward-compatible latest-published behavior. When any selector is supplied it resolves +
    PINS the bitemporal coordinates (latest committed decision + valid anchor when unspecified)
    via the VARCH-8c resolver read path and returns the seven contract response_pins.
    """
    if valid_as_of is None and decision_as_of is None and as_of_commit_id is None:
        return None
    # Fail closed when no real DB session is present. Guard against a non-Session value too
    # (e.g. an unresolved FastAPI Depends marker on a direct, non-HTTP call) so a temporal read
    # without a session yields 503, never an unhandled 500.
    if db is None or not isinstance(db, Session):
        raise HTTPException(
            status_code=503,
            detail="Canonical semantic data is required for a temporal /concepts read.",
        )
    try:
        eval_scope, _eval_tenant = resolve_eval_scope(
            requested_scope_class="shared",
            user_company_id=current_user.company_id,
            is_admin=current_user.role.value == "admin",
        )
    except ReadScopeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    repo = SemanticResolverRepository(db)
    request_decision = decision_as_of if decision_as_of is not None else as_of_commit_id
    if request_decision is None:
        request_decision = repo.latest_committed_commit_id()
        if request_decision is None:
            raise HTTPException(
                status_code=503, detail="No committed decision sequence is available."
            )
    if valid_as_of is not None:
        anchor = (
            valid_as_of
            if valid_as_of.tzinfo is not None
            else valid_as_of.replace(tzinfo=timezone.utc)
        )
    else:
        anchor = datetime.now(timezone.utc)

    try:
        read = resolve_read_context(
            repo,
            evs_key=CONCEPTS_EVS_KEY,
            scope_context=eval_scope,
            reporting_period=anchor,
            request_decision_commit_id=request_decision,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ctx = read.context
    manifest = read.replay_manifest
    manifest_hash = (
        write_replay.compute_manifest_hash(manifest) if manifest is not None else None
    )
    return {
        "resolved_decision_commit_id": ctx.decision_commit_id,
        "valid_time_slice": ctx.valid_as_of.isoformat(),
        "effective_version_set_hash": (
            read.effective_version_set.evs_hash
            if read.effective_version_set is not None
            else None
        ),
        "replay_manifest_id": manifest.get("manifest_id") if manifest else None,
        "replay_manifest_version": (
            manifest.get("manifest_version") if manifest else None
        ),
        "replay_manifest_hash": manifest_hash,
        "catalog_projection_version": CONCEPTS_PROJECTION_VERSION,
    }


def get_concept_service(
    db: Optional[Session] = Depends(get_db_optional),
) -> Optional[ConceptService]:
    """Return the canonical DB-backed semantic service when a DB session is available."""
    if db is None:
        return None
    return ConceptService(db=db)


def _require_db_semantic_service(
    concept_service: Optional[ConceptService], operation: str
) -> ConceptService:
    """Enforce DB-first semantic reads in strict mode."""
    if concept_service is None:
        raise HTTPException(
            status_code=503,
            detail=f"Canonical semantic data is required for {operation}, but no database session is available.",
        )

    try:
        has_data = concept_service.has_semantic_data()
    except CanonicalDataUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (
        Exception
    ) as exc:  # pragma: no cover - defensive guard for unexpected failures
        logger.error(
            "Failed to verify canonical semantic data",
            operation=operation,
            error=str(exc),
        )
        raise HTTPException(
            status_code=503,
            detail=f"Canonical semantic data is unavailable for {operation}.",
        ) from exc

    if not has_data:
        raise HTTPException(
            status_code=503,
            detail=f"Canonical semantic data is required for {operation}, but the semantic DB projection is empty or unavailable.",
        )

    return concept_service


def _best_literal(
    graph: Graph, subject: URIRef, predicate: URIRef, *, preferred_lang: str = "en"
) -> Optional[str]:
    literals: List[Literal] = [
        o for o in graph.objects(subject, predicate) if isinstance(o, Literal)
    ]
    if not literals:
        return None

    for lit in literals:
        if lit.language == preferred_lang:
            return str(lit)

    return str(literals[0])


def _concept_type_from_subject(graph: Graph, subject: URIRef) -> str:
    types = {str(o) for o in graph.objects(subject, RDF.type)}
    if str(SDS.Indicator) in types:
        return "Indicator"
    if str(SDS.Variable) in types:
        return "Variable"
    if str(SDS.Disclosure) in types:
        return "Disclosure"
    if str(SDS.Unit) in types:
        return "Unit"
    return "Unknown"


def _concept_unit(graph: Graph, subject: URIRef) -> Optional[str]:
    for unit_iri in graph.objects(subject, SDS.hasUnit):
        return DEFAULT_NAMESPACES.compact(str(unit_iri))
    return None


def _concept_formula(graph: Graph, subject: URIRef) -> Optional[str]:
    for formula_iri in graph.objects(subject, SDS.hasFormula):
        expr = _best_literal(
            graph,
            URIRef(str(formula_iri)),
            SDS.calculationExpression,
            preferred_lang="en",
        )
        if expr:
            return expr
    return None


def _concept_info_from_subject(graph: Graph, subject: URIRef) -> ConceptInfo:
    compact_uri = DEFAULT_NAMESPACES.compact(str(subject))
    uri = normalize_runtime_concept_uri(compact_uri)

    label = (
        _best_literal(graph, subject, SKOS.prefLabel, preferred_lang="en")
        or _best_literal(graph, subject, RDFS.label, preferred_lang="en")
        or uri
    )
    description = _best_literal(
        graph, subject, SKOS.definition, preferred_lang="en"
    ) or _best_literal(graph, subject, RDFS.comment, preferred_lang="en")

    return ConceptInfo(
        uri=uri,
        label=label,
        description=description,
        taxonomy=taxonomy_from_curie(uri),
        concept_type=_concept_type_from_subject(graph, subject),
        unit=_concept_unit(graph, subject),
        temporal_granularity=None,
        hierarchy_level=None,
        similarity_score=None,
        related_variables=None,
        formula=_concept_formula(graph, subject),
    )


def _iter_concept_subjects(graph: Graph) -> List[URIRef]:
    subjects: set[URIRef] = set()
    for rdf_type in (SDS.Indicator, SDS.Disclosure, SDS.Variable, SDS.Unit):
        for s in graph.subjects(RDF.type, rdf_type):
            subjects.add(URIRef(str(s)))
    return sorted(subjects, key=lambda s: str(s))


def _apply_concept_filters(
    concepts: List[ConceptInfo],
    *,
    taxonomy: Optional[str],
    concept_type: Optional[str],
    search: Optional[str],
) -> List[ConceptInfo]:
    filtered = concepts
    if taxonomy:
        filtered = [c for c in filtered if c.taxonomy.upper() == taxonomy.upper()]

    if concept_type:
        want = concept_type.lower()
        filtered = [c for c in filtered if c.concept_type.lower() == want]

    if search:
        needle = search.lower()
        filtered = [
            c
            for c in filtered
            if needle in (c.label or "").lower()
            or needle in (c.description or "").lower()
        ]

    return filtered


@router.get(
    "/concepts",
    response_model=PaginatedResponse,
    summary="List concepts",
    description="List available concepts from the ontology with optional filtering",
    dependencies=[Depends(require_permission(Permission.QUERY_ONTOLOGY))],
)
async def list_concepts(
    taxonomy: Optional[str] = Query(
        None,
        description=(
            "Filter by taxonomy. CSRD, GRI, and GHG filter by source standard; "
            "Sygris selects the unified SDS/Sygris public catalog view."
        ),
    ),
    concept_type: Optional[str] = Query(
        None, description="Filter by concept type (Variable, Indicator, Disclosure)"
    ),
    search: Optional[str] = Query(
        None, description="Search in concept labels and descriptions"
    ),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    # Plain None defaults (not Query(...)) so the router fn stays directly-callable in tests;
    # FastAPI still exposes these as optional query params. Public temporal-read selectors.
    valid_as_of: Optional[datetime] = None,
    decision_as_of: Optional[int] = None,
    as_of_commit_id: Optional[int] = None,
    lang: Optional[str] = None,
    graph: Graph = Depends(get_ontology_graph),
    concept_service: Optional[ConceptService] = Depends(get_concept_service),
    db: Optional[Session] = Depends(get_db_optional),
    current_user: User = Depends(get_current_active_user),
) -> PaginatedResponse:
    """List ontology concepts with optional filtering and ?lang localization."""
    try:
        logger.info(
            "Listing ontology concepts",
            taxonomy=taxonomy,
            concept_type=concept_type,
            search=search,
            limit=limit,
            offset=offset,
        )

        # VARCH-8e: reproducibility pins when a temporal selector is supplied (else None,
        # preserving the exact backward-compatible latest-published behavior).
        pins = _concepts_temporal_pins(
            db,
            valid_as_of=valid_as_of,
            decision_as_of=decision_as_of,
            as_of_commit_id=as_of_commit_id,
            current_user=current_user,
        )

        concepts: List[ConceptInfo] = []
        use_strict_db_semantics = settings.require_database

        if use_strict_db_semantics:
            concept_service = _require_db_semantic_service(
                concept_service, "list_concepts"
            )

        if concept_service is not None and concept_service.has_semantic_data():
            concept_items, total = concept_service.list_concepts_paginated(
                taxonomy=taxonomy,
                concept_type=concept_type,
                search=search,
                limit=limit,
                offset=offset,
            )
            if lang:
                concept_items = concept_service.localize_concept_payloads(
                    concept_items, lang
                )
            response = PaginatedResponse(
                items=[ConceptInfo(**item) for item in concept_items],
                total=total,
                page=(offset // limit) + 1,
                size=len(concept_items),
                pages=(total + limit - 1) // limit if total else 0,
                pins=pins,
            )
            logger.info(
                "Concepts listed successfully from DB",
                total=total,
                returned=len(concept_items),
            )
            return response

        if not concepts:
            concepts = [
                _concept_info_from_subject(graph, s)
                for s in _iter_concept_subjects(graph)
            ]

        concepts = _apply_concept_filters(
            concepts, taxonomy=taxonomy, concept_type=concept_type, search=search
        )

        total = len(concepts)
        paginated_concepts = concepts[offset : offset + limit]

        response = PaginatedResponse(
            items=paginated_concepts,
            total=total,
            page=(offset // limit) + 1,
            size=len(paginated_concepts),
            pages=(total + limit - 1) // limit,
            pins=pins,
        )

        logger.info(
            "Concepts listed successfully",
            total=total,
            returned=len(paginated_concepts),
        )
        return response

    except HTTPException:
        raise
    except CanonicalDataUnavailableError as e:
        logger.error("Canonical semantic data unavailable", error=str(e))
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.error("Failed to list concepts", error=str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to list concepts: {str(e)}"
        )


@router.get(
    "/concepts/search",
    summary="Search concepts (translation-aware)",
    description=(
        "Search concept labels/descriptions across canonical text and, when ?lang is "
        "given, approved/current translations; results carry the canonical label plus "
        "display_* fields, matched_language and matched_field."
    ),
    dependencies=[Depends(require_permission(Permission.QUERY_ONTOLOGY))],
)
async def search_concepts(
    q: str = Query(..., min_length=1, description="Search query"),
    lang: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    concept_service: Optional[ConceptService] = Depends(get_concept_service),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """Translation-aware concept search."""
    if settings.require_database:
        concept_service = _require_db_semantic_service(
            concept_service, "search_concepts"
        )
    if concept_service is None or not concept_service.has_semantic_data():
        return {"query": q, "language": lang, "results": []}
    results = concept_service.search_concepts_localized(q, limit=limit, lang=lang)
    return {
        "query": q,
        "language": lang,
        "result_count": len(results),
        "results": results,
    }


@router.get(
    "/concepts/localization-readiness",
    summary="Concept localization readiness",
    description=(
        "Approved/current translation coverage for public concepts in a language: "
        "approved_current / stale / draft_only / missing counts + coverage percent."
    ),
    dependencies=[Depends(require_permission(Permission.QUERY_ONTOLOGY))],
)
async def concept_localization_readiness(
    lang: str = Query(..., description="Target language (BCP-47), e.g. es"),
    field: str = Query("label", description="Localized field (label/description/...)"),
    concept_service: Optional[ConceptService] = Depends(get_concept_service),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """Localization readiness/coverage for concepts."""
    if settings.require_database:
        concept_service = _require_db_semantic_service(
            concept_service, "concept_localization_readiness"
        )
    if concept_service is None:
        raise HTTPException(
            status_code=503, detail="Canonical semantic data is required for readiness."
        )
    return concept_service.localization_readiness(lang, field=field)


@router.get(
    "/equivalences",
    response_model=List[EquivalenceInfo],
    summary="Get concept equivalences",
    description="Get equivalences between concepts from different taxonomies",
    dependencies=[Depends(require_permission(Permission.QUERY_ONTOLOGY))],
)
async def get_equivalences(
    concept: Optional[str] = Query(None, description="Source concept URI"),
    source_taxonomy: Optional[str] = Query(None, description="Source taxonomy"),
    target_taxonomy: Optional[str] = Query(None, description="Target taxonomy"),
    graph: Graph = Depends(get_ontology_graph),
    concept_service: Optional[ConceptService] = Depends(get_concept_service),
    current_user: User = Depends(get_current_active_user),
) -> List[EquivalenceInfo]:
    """Get concept equivalences between taxonomies."""
    try:
        logger.info(
            "Getting concept equivalences",
            concept=concept,
            source_taxonomy=source_taxonomy,
            target_taxonomy=target_taxonomy,
        )

        equivalences: List[EquivalenceInfo] = []
        use_strict_db_semantics = settings.require_database

        if use_strict_db_semantics:
            concept_service = _require_db_semantic_service(
                concept_service, "get_equivalences"
            )

        if concept_service is not None and concept_service.has_semantic_data():
            items = concept_service.list_equivalences(
                concept=concept,
                source_taxonomy=source_taxonomy,
                target_taxonomy=target_taxonomy,
            )
            equivalences = [EquivalenceInfo(**item) for item in items]
            logger.info(
                "Equivalences retrieved successfully from DB", count=len(equivalences)
            )
            return equivalences

        if not equivalences:
            for s, _p, o in graph.triples((None, OWL.equivalentClass, None)):
                source_concept = normalize_runtime_concept_uri(
                    DEFAULT_NAMESPACES.compact(str(s))
                )
                target_concept = normalize_runtime_concept_uri(
                    DEFAULT_NAMESPACES.compact(str(o))
                )
                equivalences.append(
                    EquivalenceInfo(
                        source_concept=source_concept,
                        target_concept=target_concept,
                        equivalence_type="exact",
                        confidence=1.0,
                        metadata={"predicate": "owl:equivalentClass"},
                    )
                )

        # Apply filters
        if concept:
            requested_concepts = {
                normalize_runtime_concept_uri(candidate)
                for candidate in concept_uri_candidates(concept)
            }
            equivalences = [
                e
                for e in equivalences
                if e.source_concept in requested_concepts
                or e.target_concept in requested_concepts
            ]
        if source_taxonomy:
            equivalences = [
                e
                for e in equivalences
                if taxonomy_from_curie(e.source_concept).upper()
                == source_taxonomy.upper()
            ]
        if target_taxonomy:
            equivalences = [
                e
                for e in equivalences
                if taxonomy_from_curie(e.target_concept).upper()
                == target_taxonomy.upper()
            ]

        logger.info("Equivalences retrieved successfully", count=len(equivalences))
        return equivalences

    except HTTPException:
        raise
    except CanonicalDataUnavailableError as e:
        logger.error("Canonical semantic data unavailable", error=str(e))
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.error("Failed to get equivalences", error=str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to get equivalences: {str(e)}"
        )


def _inject_prefixes(query: str) -> str:
    existing = {
        line.split()[1].rstrip(":").lower()
        for line in query.splitlines()
        if line.strip().lower().startswith("prefix ")
    }
    prefixes: List[Tuple[str, str]] = [
        ("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#"),
        ("rdfs", "http://www.w3.org/2000/01/rdf-schema#"),
        ("owl", "http://www.w3.org/2002/07/owl#"),
        ("xsd", "http://www.w3.org/2001/XMLSchema#"),
        ("skos", "http://www.w3.org/2004/02/skos/core#"),
        ("sds", "https://sustainabilitydataspace.com/ontology#"),
        ("csrd", "https://data.efrag.org/esrs#"),
        ("gri", "https://data.globalreporting.org/gri#"),
        ("ghg", "https://ghgprotocol.org/standards#"),
        ("syg", "https://sustainabilitydataspace.com/sygris#"),
    ]

    injected = []
    for prefix, iri in prefixes:
        if prefix not in existing:
            injected.append(f"PREFIX {prefix}: <{iri}>")

    if not injected:
        return query

    return "\n".join(injected) + "\n\n" + query


@router.post(
    "/sparql",
    summary="Execute SPARQL query",
    description="Execute a local SELECT or ASK SPARQL query against the ontology",
    dependencies=[Depends(require_permission(Permission.EXECUTE_SPARQL))],
)
async def execute_sparql(
    query: SPARQLQuery,
    graph: Graph = Depends(get_ontology_graph),
    concept_service: Optional[ConceptService] = Depends(get_concept_service),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """Execute a custom SPARQL query against the ontology.

    When REQUIRE_DATABASE is true, the endpoint requires a DB-backed semantic
    service and rejects local-graph fallback to keep semantics canonical.
    """
    try:
        logger.info(
            "Executing SPARQL query", query_length=len(query.query), format=query.format
        )

        if not query.query.strip():
            raise HTTPException(status_code=400, detail="Empty SPARQL query")

        # Allowlist: only read-only SPARQL query types are permitted
        query_stripped = query.query.strip()
        lines = query_stripped.upper().split("\n")
        query_type_found = False
        for line in lines:
            line = line.strip()
            if line and not line.startswith("PREFIX"):
                if not line.startswith(("SELECT", "ASK")):
                    raise HTTPException(
                        status_code=403,
                        detail="Only SELECT and ASK queries are supported",
                    )
                query_type_found = True
                break
        if not query_type_found:
            raise HTTPException(status_code=400, detail="No query type found")

        # DB-first enforcement: fail closed when canonical semantics are required
        if settings.require_database:
            concept_service = _require_db_semantic_service(
                concept_service, "execute_sparql"
            )
            # When DB-backed semantics are required, SPARQL is not yet supported
            # over the DB projection. Return 501 rather than silently falling back
            # to the local graph.
            raise HTTPException(
                status_code=501,
                detail="SPARQL over DB-backed semantics is not yet supported. "
                "Disable REQUIRE_DATABASE or use the local graph endpoints.",
            )

        start = datetime.now(timezone.utc)
        deadline = time.monotonic() + settings.sparql_query_timeout_seconds
        prepared = _inject_prefixes(query.query)

        if not _LOCAL_SPARQL_WORK_SLOTS.acquire(blocking=False):
            raise HTTPException(
                status_code=503, detail="Local SPARQL capacity exhausted"
            )

        def execute_off_loop():
            try:
                return _execute_local_sparql_bounded(
                    graph,
                    prepared,
                    timeout_seconds=max(0.0, deadline - time.monotonic()),
                    max_results=settings.sparql_max_results,
                    max_graph_bytes=settings.sparql_max_graph_bytes,
                    max_result_bytes=settings.sparql_max_result_bytes,
                )
            finally:
                _LOCAL_SPARQL_WORK_SLOTS.release()

        try:
            task = _LOCAL_SPARQL_WORKERS.submit(execute_off_loop)
        except BaseException:
            _LOCAL_SPARQL_WORK_SLOTS.release()
            raise
        task.add_done_callback(
            lambda completed: (
                _LOCAL_SPARQL_WORK_SLOTS.release() if completed.cancelled() else None
            )
        )
        # A cancelled waiter leaves the slot held until the worker really exits.
        results = await asyncio.wrap_future(task)

        rows: List[Dict[str, Any]] = []
        for row in results:
            row_dict: Dict[str, Any] = {}
            for var, value in row.items():
                if value is None:
                    row_dict[var] = None
                else:
                    # The public response is a flat lexical projection, not a
                    # typed SPARQL Results JSON binding. Never rewrite a
                    # literal merely because it resembles an IRI.
                    row_dict[var] = str(value)
            # G-AUDIT M1: this non-DB SPARQL path runs over the local public projection
            # (the F09 generator excludes internal/future concepts; production returns 501
            # for SPARQL). As defense-in-depth, drop any result row that references a
            # future-catalog (not-yet-public) taxonomy.
            if any(
                isinstance(cell, str)
                and (taxonomy_from_curie(cell) or "").upper()
                in FUTURE_CATALOG_TAXONOMIES
                for cell in row_dict.values()
            ):
                continue
            rows.append(row_dict)

        duration_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000

        response = {
            "query": query.query,
            "format": query.format,
            "results": rows,
            "execution_time_ms": duration_ms,
            "result_count": len(rows),
        }

        logger.info(
            "SPARQL query executed successfully",
            result_count=len(rows),
            execution_time_ms=duration_ms,
        )
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to execute SPARQL query", error=str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to execute SPARQL query: {str(e)}"
        )


@router.get(
    "/concepts/{concept_uri}",
    response_model=ConceptInfo,
    summary="Get concept details",
    description="Get detailed information about a specific concept",
    dependencies=[Depends(require_permission(Permission.QUERY_ONTOLOGY))],
)
async def get_concept_details(
    concept_uri: str,
    # Plain None defaults so the router fn stays directly-callable in tests; FastAPI still
    # exposes these as optional query params. Public temporal-read selectors.
    valid_as_of: Optional[datetime] = None,
    decision_as_of: Optional[int] = None,
    as_of_commit_id: Optional[int] = None,
    lang: Optional[str] = None,
    graph: Graph = Depends(get_ontology_graph),
    concept_service: Optional[ConceptService] = Depends(get_concept_service),
    db: Optional[Session] = Depends(get_db_optional),
    current_user: User = Depends(get_current_active_user),
) -> ConceptInfo:
    """Get detailed information about a specific concept (with ?lang localization)."""
    try:
        logger.info("Getting concept details", concept_uri=concept_uri)

        # VARCH-8e: reproducibility pins when a temporal selector is supplied (else None).
        pins = _concepts_temporal_pins(
            db,
            valid_as_of=valid_as_of,
            decision_as_of=decision_as_of,
            as_of_commit_id=as_of_commit_id,
            current_user=current_user,
        )

        if settings.require_database:
            concept_service = _require_db_semantic_service(
                concept_service, "get_concept_details"
            )

        if concept_service is not None and concept_service.has_semantic_data():
            concept = concept_service.get_concept_by_uri(
                concept_uri,
                public_catalog=True,
            )
            if concept is None:
                raise HTTPException(
                    status_code=404, detail=f"Concept not found: {concept_uri}"
                )
            logger.info(
                "Concept details retrieved successfully from DB",
                concept_uri=concept_uri,
                taxonomy=concept["taxonomy"],
            )
            if lang:
                concept_service.localize_concept_payloads([concept], lang)
            detail = ConceptInfo(**concept)
            detail.pins = pins
            return detail

        subject = None
        for candidate in concept_uri_candidates(concept_uri):
            candidate_subject = URIRef(DEFAULT_NAMESPACES.expand(candidate))
            if any(graph.triples((candidate_subject, None, None))):
                subject = candidate_subject
                break
        if subject is None:
            raise HTTPException(
                status_code=404, detail=f"Concept not found: {concept_uri}"
            )

        concept = _concept_info_from_subject(graph, subject)
        concept.pins = pins

        logger.info(
            "Concept details retrieved successfully",
            concept_uri=concept_uri,
            taxonomy=concept.taxonomy,
        )
        return concept

    except HTTPException:
        raise
    except CanonicalDataUnavailableError as e:
        logger.error(
            "Canonical semantic data unavailable", error=str(e), concept_uri=concept_uri
        )
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.error(
            "Failed to get concept details", error=str(e), concept_uri=concept_uri
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to get concept details: {str(e)}"
        )


@router.get(
    "/taxonomies",
    summary="List available taxonomies",
    description="Get list of available taxonomies and their statistics",
    dependencies=[Depends(require_permission(Permission.QUERY_ONTOLOGY))],
)
async def list_taxonomies(
    graph: Graph = Depends(get_ontology_graph),
    concept_service: Optional[ConceptService] = Depends(get_concept_service),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """List available taxonomies with statistics (derived from local ontology graph)."""
    try:
        logger.info("Listing available taxonomies")

        if settings.require_database:
            concept_service = _require_db_semantic_service(
                concept_service, "list_taxonomies"
            )

        if concept_service is not None and concept_service.has_semantic_data():
            response = concept_service.list_taxonomies()
            logger.info(
                "Taxonomies listed successfully from DB",
                total_taxonomies=response["total_taxonomies"],
            )
            return response

        # Local-graph fallback (no DB): exclude future-catalog taxonomies so the public
        # taxonomy surface cannot reveal not-yet-public catalog presence (codex
        # G-AUDIT M3). The local graph carries no projection_source, so taxonomy is the
        # only available public-catalog discriminator here; the DB path above applies
        # the full public-catalog predicate.
        concepts = [
            info
            for s in _iter_concept_subjects(graph)
            for info in (_concept_info_from_subject(graph, s),)
            if (info.taxonomy or "").upper() not in FUTURE_CATALOG_TAXONOMIES
        ]

        by_taxonomy: Dict[str, Dict[str, Any]] = {}
        for concept in concepts:
            bucket = by_taxonomy.setdefault(
                concept.taxonomy,
                {
                    "name": concept.taxonomy,
                    "description": None,
                    "type": None,
                    "concepts_count": 0,
                    "indicators_count": 0,
                    "disclosures_count": 0,
                    "variables_count": 0,
                    "units_count": 0,
                    "version": None,
                },
            )
            bucket["concepts_count"] += 1
            if concept.concept_type == "Indicator":
                bucket["indicators_count"] += 1
            elif concept.concept_type == "Disclosure":
                bucket["disclosures_count"] += 1
            elif concept.concept_type == "Variable":
                bucket["variables_count"] += 1
            elif concept.concept_type == "Unit":
                bucket["units_count"] += 1

        total_concepts = sum(t["concepts_count"] for t in by_taxonomy.values())

        last_updated = None
        ontology_path = getattr(getattr(graph, "store", None), "path", None)
        if ontology_path:
            try:
                mtime = Path(str(ontology_path)).stat().st_mtime
                last_updated = datetime.fromtimestamp(
                    mtime, tz=timezone.utc
                ).isoformat()
            except Exception:
                last_updated = None

        return {
            "taxonomies": by_taxonomy,
            "total_taxonomies": len(by_taxonomy),
            "total_concepts": total_concepts,
            "last_updated": last_updated,
        }

    except HTTPException:
        raise
    except CanonicalDataUnavailableError as e:
        logger.error("Canonical semantic data unavailable", error=str(e))
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.error("Failed to list taxonomies", error=str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to list taxonomies: {str(e)}"
        )
