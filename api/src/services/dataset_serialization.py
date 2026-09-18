"""Canonical serialization helpers for exported catalog datasets."""

from __future__ import annotations

from typing import Any


def serialize_indicator(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "identifier": item.identifier,
        "title": item.title,
        "indicator_name": getattr(item, "indicator_name", None),
        "description": getattr(item, "description", None),
        "dimension": item.dimension,
        "unit_name": getattr(item, "unit_name", None),
        "unit_type": getattr(item, "unit_type", None),
        "periodicity": getattr(item, "periodicity", None),
        "period_type": getattr(item, "period_type", None),
        "source_ref": getattr(item, "source_ref", None),
        "code_esrs": getattr(item, "code_esrs", None),
        "code_gri": getattr(item, "code_gri", None),
        "code_gri_expanded": getattr(item, "code_gri_expanded", None),
        "evidence_path": getattr(item, "evidence_path", None),
        "source_row": getattr(item, "source_row", None),
        "owner": getattr(item, "owner", None),
        "access_rights": getattr(item, "access_rights", None),
        "validation_method": getattr(item, "validation_method", None),
        "double_materiality": getattr(item, "double_materiality", None),
        "value_type": getattr(item, "value_type", None),
    }


def serialize_mapping(item: Any) -> dict[str, Any]:
    confidence = getattr(item, "confidence", None)
    if confidence is not None:
        confidence = float(confidence)
    return {
        "id": item.id,
        "source_standard": item.source_standard,
        "source_code": item.source_code,
        "source_label": getattr(item, "source_label", None),
        "target_standard": item.target_standard,
        "target_code": getattr(item, "target_code", None),
        "target_label": getattr(item, "target_label", None),
        "esg_dimension": getattr(item, "esg_dimension", None),
        "relationship_type": getattr(item, "relationship_type", None),
        "confidence": confidence,
        "dataset": getattr(item, "dataset", None),
    }
