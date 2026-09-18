"""Mapping-aware observation provider for contract input resolution."""

from __future__ import annotations

import re
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from src.calculation.contracts import ContractExecutionError
from src.calculation.value_provider import ObservationSnapshot
from src.services.value_resolution import find_mapping_routes


class MappingAwareObservationProvider:
    """Resolve contract input observations through contract-authorized mappings."""

    def __init__(self, base_provider: Any, mapping_store: Any):
        self.base_provider = base_provider
        self.mapping_store = mapping_store
        self._mapping_events: list[dict[str, Any]] = []

    def get_observations(
        self,
        contract_input: Any,
        *,
        entities: list[str],
        period_start,
        period_end,
    ) -> list[Any]:
        return list(
            self.get_snapshot(
                contract_input,
                entities=entities,
                period_start=period_start,
                period_end=period_end,
            ).observations
        )

    def get_snapshot(
        self,
        contract_input: Any,
        *,
        entities: list[str],
        period_start,
        period_end,
    ) -> ObservationSnapshot:
        direct = self.base_provider.get_snapshot(
            contract_input,
            entities=entities,
            period_start=period_start,
            period_end=period_end,
        )
        if direct.observations or self.mapping_store is None:
            return direct

        routes = find_mapping_routes(
            self.mapping_store,
            source_concept=None,
            target_concept=str(contract_input.concept),
        )
        if not routes:
            return direct

        eligible_routes = _eligible_routes(contract_input, routes)
        if not eligible_routes:
            relationship_types = sorted(
                {route.relationship_type for route in routes if route.relationship_type}
            )
            raise ContractExecutionError(
                f"{contract_input.local_variable}: mapping relationship "
                f"{', '.join(relationship_types) or 'unknown'} cannot satisfy "
                f"{contract_input.concept}; exact/equivalent relationship is required "
                "unless the contract input explicitly authorizes this relationship"
            )

        source_concepts = sorted({route.source_concept for route in eligible_routes})
        if len(source_concepts) > 1:
            ambiguity_kind = (
                "eligible"
                if any(not route.is_equivalent for route in eligible_routes)
                else "equivalent"
            )
            raise ContractExecutionError(
                f"{contract_input.local_variable}: ambiguous {ambiguity_kind} mappings for "
                f"{contract_input.concept}: {', '.join(source_concepts)}"
            )

        mapped_input = _replace_input_concept(contract_input, source_concepts[0])
        mapped = self.base_provider.get_snapshot(
            mapped_input,
            entities=entities,
            period_start=period_start,
            period_end=period_end,
        )
        if not mapped.observations:
            return direct
        self._mapping_events.append(
            {
                "local_variable": contract_input.local_variable,
                "requested_concept": contract_input.concept,
                "source_concept": source_concepts[0],
                "relationship_type": eligible_routes[0].relationship_type,
                "mapping_row_id": eligible_routes[0].row_id,
                "direction": eligible_routes[0].direction,
            }
        )
        return ObservationSnapshot(
            local_variable=contract_input.local_variable,
            concept=contract_input.concept,
            entities=tuple(entities),
            period_start=period_start,
            period_end=period_end,
            observations=mapped.observations,
        )

    def get_mapping_events(self) -> list[dict[str, Any]]:
        return list(self._mapping_events)


def _eligible_routes(contract_input: Any, routes: list[Any]) -> list[Any]:
    allowed = {
        _normalize_relationship(item)
        for item in getattr(contract_input, "allowed_mapping_relationships", ()) or ()
        if _normalize_relationship(item)
    }
    return [
        route
        for route in routes
        if route.is_equivalent
        or _normalize_relationship(route.relationship_type) in allowed
    ]


def _normalize_relationship(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _replace_input_concept(contract_input: Any, concept: str) -> Any:
    try:
        return replace(contract_input, concept=concept)
    except TypeError:
        data = dict(getattr(contract_input, "__dict__", {}))
        data["concept"] = concept
        return SimpleNamespace(**data)
