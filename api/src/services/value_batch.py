"""Shared batch execution logic for value imports."""

from __future__ import annotations

import uuid

from rdflib import Graph

from src.api.models import (
    ValueBulkImportResponse,
    ValueBulkImportRowResult,
    ValueCreate,
    ValueImportStatus,
)
from src.services.value_ingest import ValueIngestError, prepare_value_record


def execute_value_batch(
    *,
    items: list[ValueCreate],
    converter,
    conversion_engine=None,
    store,
    hierarchy_store,
    indicator_store=None,
    canonical_concept_store=None,
    graph: Graph,
    created_by: str | None,
    company_id: str | None,
    strict: bool,
    commit: bool = True,
) -> ValueBulkImportResponse:
    """Validate, normalize, and persist one request-level values batch."""
    prepared_records = []
    row_results: list[ValueBulkImportRowResult] = []
    accepted_rows = 0
    rejected_rows = 0
    seen_external_keys: set[str] = set()

    for row_number, value_data in enumerate(items, start=1):
        if value_data.external_key:
            if value_data.external_key in seen_external_keys:
                row_results.append(
                    ValueBulkImportRowResult(
                        row_number=row_number,
                        status=ValueImportStatus.REJECTED,
                        concept=value_data.concept,
                        entity=value_data.entity,
                        period=value_data.period,
                        external_key=value_data.external_key,
                        unit=value_data.unit,
                        message=f"Duplicate external_key inside batch: {value_data.external_key}",
                    )
                )
                rejected_rows += 1
                continue
            seen_external_keys.add(value_data.external_key)

        value_id = str(uuid.uuid4())
        try:
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
            prepared_records.append(prepared)
            row_results.append(
                ValueBulkImportRowResult(
                    row_number=row_number,
                    status=ValueImportStatus.ACCEPTED,
                    concept=prepared.concept,
                    entity=prepared.entity,
                    period=prepared.period,
                    external_key=prepared.external_key,
                    unit=prepared.unit,
                    value_id=prepared.value_id,
                    message="validated",
                )
            )
            accepted_rows += 1
        except ValueIngestError as error:
            row_results.append(
                ValueBulkImportRowResult(
                    row_number=row_number,
                    status=ValueImportStatus.REJECTED,
                    concept=value_data.concept,
                    entity=value_data.entity,
                    period=value_data.period,
                    external_key=value_data.external_key,
                    unit=value_data.unit,
                    message=error.message,
                )
            )
            rejected_rows += 1

    if rejected_rows:
        return ValueBulkImportResponse(
            total_rows=len(items),
            accepted_rows=accepted_rows,
            rejected_rows=rejected_rows,
            committed=False,
            items=row_results,
        )

    if any(record.external_key for record in prepared_records):
        persisted = store.save(
            records=prepared_records,
            created_by=created_by,
            refresh=False,
            return_responses=False,
            commit=commit,
        )
    else:
        persisted = store.bulk_create(
            records=prepared_records,
            created_by=created_by,
            refresh=False,
            return_responses=False,
            commit=commit,
        )

    committed_results = []
    persisted_iter = iter(persisted)
    for item in row_results:
        persisted_record = (
            next(persisted_iter, None)
            if item.status == ValueImportStatus.ACCEPTED
            else None
        )
        committed_results.append(
            item.model_copy(
                update={
                    "value_id": (
                        persisted_record.id
                        if persisted_record is not None
                        else item.value_id
                    ),
                    "message": (
                        "persisted" if persisted_record is not None else item.message
                    ),
                }
            )
        )

    return ValueBulkImportResponse(
        total_rows=len(items),
        accepted_rows=accepted_rows,
        rejected_rows=0,
        committed=True,
        items=committed_results,
    )
