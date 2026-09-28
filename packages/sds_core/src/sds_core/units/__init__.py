"""Narrow offline unit helper — not the production DB-backed unit catalog.

This module provides basic normalization and conversion for a small hardcoded
unit set. The production runtime uses the PostgreSQL-backed ``UnitService`` and
``UnitRepository`` (``api/src/services/unit_service.py``), not this helper.
"""

from sds_core.units.conversion import UnitConversionError, convert_unit, normalize_unit

__all__ = ["UnitConversionError", "convert_unit", "normalize_unit"]
