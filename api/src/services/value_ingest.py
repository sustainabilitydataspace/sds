"""Shared value-ingest logic for API and batch imports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Optional
from urllib.parse import unquote

from fastapi import HTTPException
from rdflib import RDF, Graph, Namespace, URIRef
from rdflib.namespace import SKOS

from src.api.models import ValueCreate, ValueResponse, ValueScalar
from src.calculation.conversion.orchestrator import (
    ConversionDependencyError,
    ConversionRequest,
)
from src.calculation.semantic_formula import display_unit_from_reference
from src.calculation.unit_converter import UnitConversionError
from src.concept_runtime_aliases import concept_uri_candidates
from src.ontology.curie import DEFAULT_NAMESPACES
from src.services.value_versioning import (
    CONTEXT_HASH_RECIPE_VERSION_V2,
    SDS_CANONICAL_OPERATIONAL_RELEASE_ID,
    SOURCE_OBSERVATION_CANONICAL_OPERATIONAL,
)

SDS = Namespace("https://sustainabilitydataspace.com/ontology#")
_CALCULATION_VALUE_CONCEPT_MATCH_FIELDS = (
    "canonical_datapoint_id",
    "node_id",
    "indicator_identifier",
    "component_id",
    "component_node_id",
    "variable_uri",
)


@dataclass(frozen=True)
class ValueIngestError(RuntimeError):
    """Structured value-ingest failure."""

    status_code: int
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class PreparedValueRecord:
    """Normalized value payload ready for persistence."""

    value_id: str
    concept: str
    entity: str
    period: date
    value: ValueScalar
    value_type: str
    unit: str
    original_value: Optional[ValueScalar] = None
    original_unit: Optional[str] = None
    conversion_applied: bool = False
    currency: Optional[str] = None
    original_currency: Optional[str] = None
    currency_conversion_applied: bool = False
    conversion_trace: Optional[list[dict[str, Any]]] = None
    value_date: Optional[date] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    metadata: Optional[dict[str, Any]] = None
    external_key: Optional[str] = None


def ingest_value(
    *,
    value_id: str,
    value_data: ValueCreate,
    converter,
    conversion_engine=None,
    store,
    hierarchy_store,
    indicator_store=None,
    canonical_concept_store=None,
    graph: Graph,
    created_by: Optional[str],
    company_id: Optional[str],
    strict: bool,
    commit: bool = True,
) -> ValueResponse:
    """Validate, normalize, and persist one ESG value."""
    prepared = prepare_value_record(
        value_id=value_id,
        value_data=value_data,
        converter=converter,
        conversion_engine=conversion_engine,
        hierarchy_store=hierarchy_store,
        indicator_store=indicator_store,
        canonical_concept_store=canonical_concept_store,
        graph=graph,
        company_id=company_id,
        strict=strict,
    )

    if prepared.external_key:
        return store.save(records=[prepared], created_by=created_by, commit=commit)[0]

    return store.create(
        value_id=prepared.value_id,
        concept=prepared.concept,
        entity=prepared.entity,
        period=prepared.period,
        external_key=prepared.external_key,
        value=prepared.value,
        value_type=prepared.value_type,
        unit=prepared.unit,
        original_value=prepared.original_value,
        original_unit=prepared.original_unit,
        conversion_applied=prepared.conversion_applied,
        currency=prepared.currency,
        original_currency=prepared.original_currency,
        currency_conversion_applied=prepared.currency_conversion_applied,
        conversion_trace=prepared.conversion_trace,
        value_date=prepared.value_date,
        period_start=prepared.period_start,
        period_end=prepared.period_end,
        metadata=prepared.metadata,
        created_by=created_by,
        commit=commit,
    )


def prepare_value_record(
    *,
    value_id: str,
    value_data: ValueCreate,
    converter,
    conversion_engine=None,
    hierarchy_store,
    indicator_store=None,
    canonical_concept_store=None,
    graph: Graph,
    company_id: Optional[str],
    strict: bool,
    standard_unit_cache: Optional[dict[str, Optional[str]]] = None,
) -> PreparedValueRecord:
    """Validate and normalize one ESG value without persisting it."""
    canonical_concept = _resolve_current_canonical_concept(
        value_data.concept,
        canonical_concept_store,
    )
    if strict:
        if canonical_concept is None:
            _validate_concept_exists(
                value_data.concept, graph, indicator_store=indicator_store
            )
        _validate_entity_exists(
            value_data.entity,
            hierarchy_store,
            company_id=company_id,
        )

    value_type = _normalize_value_type(value_data.value_type, value_data.value)
    if value_type == "numeric" and not _is_numeric_value(value_data.value):
        raise ValueIngestError(
            status_code=400, message="Numeric values must use a numeric JSON/CSV value."
        )

    is_numeric = value_type == "numeric"
    standard_unit = (
        _get_standard_unit_for_concept_cached(
            value_data.concept,
            graph,
            converter,
            standard_unit_cache,
        )
        if is_numeric
        else None
    )
    converted_value = value_data.value
    conversion_applied = False
    currency_conversion_applied = False
    original_unit = value_data.unit
    original_value: ValueScalar | None = None
    original_currency: str | None = None
    conversion_trace: list[dict[str, Any]] | None = None
    source_currency = value_data.currency
    normalized_currency = source_currency
    expected_unit = value_data.expected_unit
    expected_currency = value_data.expected_currency
    used_conversion_engine = False

    expected_unit_differs = bool(expected_unit and value_data.unit != expected_unit)
    expected_currency_differs = bool(
        expected_currency and source_currency != expected_currency
    )

    if expected_currency_differs and conversion_engine is None:
        raise ValueIngestError(
            status_code=400,
            message=(
                "conversion_engine is required when expected_currency differs from "
                "the row-level source currency"
            ),
        )

    if (
        is_numeric
        and conversion_engine is not None
        and (expected_unit_differs or expected_currency_differs)
    ):
        try:
            conversion_result = conversion_engine.normalize(
                ConversionRequest(
                    value=Decimal(str(value_data.value)),
                    unit=value_data.unit,
                    expected_unit=expected_unit,
                    currency=source_currency,
                    expected_currency=expected_currency,
                    value_date=value_data.value_date,
                    period_start=value_data.period_start,
                    period_end=value_data.period_end,
                    fx_policy_id=value_data.fx_policy_id,
                )
            )
        except (ConversionDependencyError, ValueError) as error:
            raise ValueIngestError(status_code=400, message=str(error)) from error

        converted_value = conversion_result.value
        used_conversion_engine = True
        normalized_unit = conversion_result.unit or value_data.unit
        normalized_currency = conversion_result.currency or source_currency
        conversion_trace = conversion_result.trace or None
        conversion_applied = value_data.unit != normalized_unit
        currency_conversion_applied = source_currency != normalized_currency
        original_value = (
            value_data.value
            if (conversion_applied or currency_conversion_applied)
            else None
        )
        original_unit = value_data.unit
        original_currency = source_currency if currency_conversion_applied else None
        standard_unit = normalized_unit

    if is_numeric and standard_unit is None and expected_unit:
        standard_unit = expected_unit

    if (
        is_numeric
        and not used_conversion_engine
        and standard_unit
        and value_data.unit != standard_unit
    ):
        try:
            conversion_result = converter.convert(
                value_data.value,
                value_data.unit,
                standard_unit,
            )
            converted_value = conversion_result.converted_value
            conversion_applied = True
            original_value = value_data.value
        except UnitConversionError as error:
            if strict:
                raise ValueIngestError(
                    status_code=400,
                    message=(
                        f"Unit '{value_data.unit}' is not compatible with standard unit "
                        f"'{standard_unit}' for concept '{value_data.concept}'"
                    ),
                ) from error
            standard_unit = value_data.unit

    return PreparedValueRecord(
        value_id=value_id,
        concept=value_data.concept,
        entity=value_data.entity,
        period=value_data.period,
        external_key=value_data.external_key,
        value=converted_value,
        value_type=value_type,
        unit=standard_unit or value_data.unit,
        original_value=original_value,
        original_unit=original_unit if conversion_applied else None,
        conversion_applied=conversion_applied,
        currency=normalized_currency,
        original_currency=original_currency,
        currency_conversion_applied=currency_conversion_applied,
        conversion_trace=conversion_trace,
        value_date=value_data.value_date,
        period_start=value_data.period_start,
        period_end=value_data.period_end,
        metadata=_metadata_with_canonical_context(
            value_data.metadata,
            canonical_concept=canonical_concept,
        ),
    )


def prepare_value_records(
    *,
    rows: list[tuple[str, ValueCreate]],
    row_numbers: Optional[list[int]] = None,
    converter,
    conversion_engine=None,
    hierarchy_store,
    indicator_store=None,
    canonical_concept_store=None,
    graph: Graph,
    company_id: Optional[str],
    strict: bool,
) -> list[PreparedValueRecord]:
    """Validate and normalize a batch of ESG values with cached lookups.

    Each element of *rows* is a ``(value_id, value_data)`` pair.
    Concept, entity, and standard-unit lookups are deduplicated per batch
    so repeated references do not re-query the ontology graph or hierarchy
    store.  Row-level validation (value type, numeric checks, unit
    conversion) still runs individually.
    """
    if row_numbers is not None and len(row_numbers) != len(rows):
        raise ValueError("row_numbers must match rows length")

    cached_hierarchy_store = _CachedHierarchyStore(hierarchy_store)
    cached_indicator_store = (
        _CachedIndicatorStore(indicator_store) if indicator_store is not None else None
    )
    cached_canonical_concept_store = (
        _CachedCanonicalConceptStore(canonical_concept_store)
        if canonical_concept_store is not None
        else None
    )
    standard_unit_cache: dict[str, Optional[str]] = {}
    prepared_records: list[PreparedValueRecord] = []

    for index, (value_id, value_data) in enumerate(rows):
        try:
            prepared_records.append(
                prepare_value_record(
                    value_id=value_id,
                    value_data=value_data,
                    converter=converter,
                    conversion_engine=conversion_engine,
                    hierarchy_store=cached_hierarchy_store,
                    indicator_store=cached_indicator_store,
                    canonical_concept_store=cached_canonical_concept_store,
                    graph=graph,
                    company_id=company_id,
                    strict=strict,
                    standard_unit_cache=standard_unit_cache,
                )
            )
        except ValueIngestError as error:
            row_number = row_numbers[index] if row_numbers is not None else index + 1
            raise _with_row_context(error, row_number, value_data) from error

    return prepared_records


class _CachedHierarchyStore:
    def __init__(self, delegate):
        self._delegate = delegate
        self._list_cache: dict[tuple[tuple[str, Any], ...], Any] = {}

    def list(self, **kwargs):
        key = tuple(sorted(kwargs.items()))
        if key not in self._list_cache:
            self._list_cache[key] = self._delegate.list(**kwargs)
        return self._list_cache[key]

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


class _CachedIndicatorStore:
    def __init__(self, delegate):
        self._delegate = delegate
        self._cache: dict[tuple[Any, ...], Any] = {}

    def get_by_identifier(self, identifier: str):
        return self._cached_call("get_by_identifier", identifier)

    def search_by_esrs(self, code: str, *, limit: int = 100):
        return self._cached_call("search_by_esrs", code, limit)

    def search_by_gri(self, code: str, *, limit: int = 100):
        return self._cached_call("search_by_gri", code, limit)

    def get_calculation_value_concept(self, concept: str):
        return self._cached_call("get_calculation_value_concept", concept)

    def _cached_call(self, method_name: str, *args):
        method = getattr(self._delegate, method_name, None)
        if not callable(method):
            return None
        key = (method_name, *args)
        if key not in self._cache:
            self._cache[key] = method(*args)
        return self._cache[key]

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


class _CachedCanonicalConceptStore:
    def __init__(self, delegate):
        self._delegate = delegate
        self._cache: dict[str, Any] = {}

    def get_current_by_uri(self, canonical_uri: str):
        if canonical_uri not in self._cache:
            lookup = getattr(self._delegate, "get_current_by_uri", None)
            self._cache[canonical_uri] = (
                lookup(canonical_uri) if callable(lookup) else None
            )
        return self._cache[canonical_uri]

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


def _with_row_context(
    error: ValueIngestError, row_number: int, value_data: ValueCreate
) -> ValueIngestError:
    context = f"CSV row {row_number}"
    if value_data.external_key:
        context = f"{context} external_key={value_data.external_key}"
    return ValueIngestError(
        status_code=error.status_code,
        message=f"{context}: {error.message}",
    )


def as_http_exception(error: ValueIngestError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


def _validate_concept_exists(
    concept: str, graph: Graph, *, indicator_store=None
) -> None:
    for subject in _concept_subject_candidates(concept):
        if next(graph.triples((subject, RDF.type, None)), None) is not None:
            return

    if concept.startswith("urn:sds:reg:") and indicator_store is not None:
        if indicator_store.get_by_identifier(concept) is not None:
            return

    if indicator_store is not None:
        if _matches_imported_catalog_code(concept, indicator_store):
            return

        lookup = getattr(indicator_store, "get_calculation_value_concept", None)
        if callable(lookup) and _matches_calculation_value_concept(
            concept, lookup(concept)
        ):
            return

    raise ValueIngestError(status_code=400, message=f"Unknown concept: {concept}")


def _resolve_current_canonical_concept(concept: str, canonical_concept_store):
    if canonical_concept_store is None:
        return None
    lookup = getattr(canonical_concept_store, "get_current_by_uri", None)
    if not callable(lookup):
        return None

    candidates = [
        candidate
        for candidate in (concept_uri_candidates(concept) or [concept])
        if _looks_like_canonical_operational_concept(candidate)
    ]
    for candidate in candidates:
        canonical_concept = lookup(candidate)
        if _is_current_canonical_concept(canonical_concept):
            return canonical_concept
    return None


def _looks_like_canonical_operational_concept(concept: str) -> bool:
    token = str(concept or "").strip()
    return token.startswith(("syg:", "urn:sds:canonical:", "urn:sds:concept:"))


def _is_current_canonical_concept(canonical_concept) -> bool:
    if canonical_concept is None:
        return False
    return (
        getattr(canonical_concept, "canonical_uri", None)
        and getattr(canonical_concept, "effective_to", None) is None
        and getattr(canonical_concept, "superseded_by", None) is None
    )


def _metadata_with_canonical_context(
    metadata: Optional[dict[str, Any]],
    *,
    canonical_concept,
) -> Optional[dict[str, Any]]:
    if canonical_concept is None:
        return metadata

    canonical_uri = str(getattr(canonical_concept, "canonical_uri"))
    enriched = dict(metadata or {})
    if (
        "standard_release_id" in enriched
        and enriched["standard_release_id"] != SDS_CANONICAL_OPERATIONAL_RELEASE_ID
    ):
        enriched.setdefault(
            "source_standard_release_id", enriched["standard_release_id"]
        )
    if (
        "standard_datapoint_id" in enriched
        and enriched["standard_datapoint_id"] != canonical_uri
    ):
        enriched.setdefault(
            "source_standard_datapoint_id", enriched["standard_datapoint_id"]
        )
    enriched["canonical_uri"] = canonical_uri
    enriched["canonical_concept_id"] = getattr(canonical_concept, "id", None)
    enriched["source_observation_type"] = SOURCE_OBSERVATION_CANONICAL_OPERATIONAL
    enriched["context_hash_recipe_version"] = CONTEXT_HASH_RECIPE_VERSION_V2
    enriched["indicator_identifier"] = canonical_uri
    enriched["standard_release_id"] = SDS_CANONICAL_OPERATIONAL_RELEASE_ID
    enriched["standard_datapoint_id"] = canonical_uri
    return enriched


def _matches_imported_catalog_code(concept: str, indicator_store) -> bool:
    prefix, _, local_code = concept.partition(":")
    if not local_code:
        return False

    prefix = prefix.strip().lower()
    code_variants = _catalog_code_variants(prefix, local_code)
    if not code_variants:
        return False

    if prefix in {"csrd", "esrs"}:
        search = getattr(indicator_store, "search_by_esrs", None)
        field_names = ("code_esrs",)
    elif prefix == "gri":
        search = getattr(indicator_store, "search_by_gri", None)
        field_names = ("code_gri", "code_gri_expanded")
    else:
        return False

    if not callable(search):
        return False

    normalized_variants = {_normalize_catalog_code(code) for code in code_variants}
    for code in code_variants:
        matches = search(code, limit=5)
        for match in matches or []:
            for field_name in field_names:
                value = (
                    match.get(field_name)
                    if isinstance(match, dict)
                    else getattr(match, field_name, None)
                )
                if _normalize_catalog_code(value) in normalized_variants:
                    return True
    return False


def _catalog_code_variants(prefix: str, local_code: str) -> set[str]:
    stripped = local_code.strip()
    if not stripped:
        return set()

    variants = {stripped}
    if prefix == "gri" and not stripped.upper().startswith("GRI "):
        variants.add(f"GRI {stripped}")
    return variants


def _normalize_catalog_code(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip()).lower()


def _matches_calculation_value_concept(concept: str, candidate: Any) -> bool:
    if candidate is None:
        return False

    for field_name in _CALCULATION_VALUE_CONCEPT_MATCH_FIELDS:
        if isinstance(candidate, dict):
            value = candidate.get(field_name)
        else:
            value = getattr(candidate, field_name, None)
        if isinstance(value, str) and value == concept:
            return True
    return False


def _validate_entity_exists(
    entity: str, hierarchy_store, *, company_id: Optional[str]
) -> None:
    configs, _ = hierarchy_store.list(
        company_id=company_id,
        hierarchy_type=None,
        active=True,
        limit=1000,
        offset=0,
    )

    for config in configs:
        for level in config.levels:
            if level.id == entity:
                return

    raise ValueIngestError(status_code=400, message=f"Unknown entity: {entity}")


def _get_standard_unit_for_concept(
    concept: str, graph: Graph, converter
) -> Optional[str]:
    normalize_unit_symbol = getattr(converter, "normalize_unit_symbol", None)
    if not callable(normalize_unit_symbol):
        return None

    for subject in _concept_subject_candidates(concept):
        unit_iri = next(graph.objects(subject, SDS.hasUnit), None)
        if unit_iri is None:
            continue

        symbol = next(graph.objects(URIRef(str(unit_iri)), SKOS.altLabel), None)
        candidates = [
            str(symbol) if symbol is not None else None,
            display_unit_from_reference(str(unit_iri)),
            _unit_reference_token(str(unit_iri)),
        ]
        unresolved_unit: str | None = None
        for candidate in candidates:
            if not candidate:
                continue
            concrete_unit = _concrete_unresolved_unit_label(candidate)
            if unresolved_unit is None and concrete_unit is not None:
                unresolved_unit = concrete_unit
            try:
                return normalize_unit_symbol(candidate)
            except UnitConversionError:
                continue
        if unresolved_unit is not None:
            return unresolved_unit
    return None


def _concept_subject_candidates(concept: str) -> list[URIRef]:
    candidates = concept_uri_candidates(concept) or [concept]
    subjects: list[URIRef] = []
    seen: set[str] = set()
    for candidate in candidates:
        expanded = DEFAULT_NAMESPACES.expand(candidate)
        if expanded not in seen:
            seen.add(expanded)
            subjects.append(URIRef(expanded))
    return subjects


def _unit_reference_token(value: str) -> str:
    text = unquote(str(value).strip())
    local_ontology_marker = "/ontologies/"
    if local_ontology_marker in text:
        return text.rsplit(local_ontology_marker, 1)[-1]
    return text.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _concrete_unresolved_unit_label(candidate: str) -> str | None:
    text = " ".join(str(candidate).strip().split())
    if not text:
        return None
    if "/ontologies/" in text:
        text = _unit_reference_token(text)
    if text.startswith(("file:", "http://", "https://")):
        return None
    if ("CO2e" in text or "CO2eq" in text) and ("/" in text or " per " in text):
        return text
    return None


def _get_standard_unit_for_concept_cached(
    concept: str,
    graph: Graph,
    converter,
    cache: Optional[dict[str, Optional[str]]],
) -> Optional[str]:
    if cache is None:
        return _get_standard_unit_for_concept(concept, graph, converter)
    if concept not in cache:
        cache[concept] = _get_standard_unit_for_concept(concept, graph, converter)
    return cache[concept]


def _is_numeric_value(value: ValueScalar) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _normalize_value_type(raw_value_type: Optional[str], value: ValueScalar) -> str:
    value_type = (raw_value_type or "").strip().lower().replace("_", "-")
    if value_type in {"number", "numeric", "decimal"}:
        return "numeric"
    if value_type in {"boolean", "bool"}:
        return "boolean"
    if value_type in {"text", "string", "narrative"}:
        return "narrative"
    if value_type in {"semi-narrative", "seminarrative"}:
        return "semi-narrative"
    if value_type:
        raise ValueIngestError(
            status_code=400,
            message="value_type must be one of: numeric, boolean, narrative, or semi-narrative.",
        )

    if isinstance(value, bool):
        return "boolean"
    if _is_numeric_value(value):
        return "numeric"
    return "narrative"
