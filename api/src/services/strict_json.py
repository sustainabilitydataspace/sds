"""Strict JSON decoding for package and CSV trust boundaries."""

from __future__ import annotations

import json
from typing import Any


class StrictJsonError(ValueError):
    """JSON bytes are malformed or have an ambiguous object representation."""


def loads_strict_json(raw: str | bytes) -> Any:
    """Decode JSON while rejecting duplicate keys and non-finite constants."""

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise StrictJsonError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise StrictJsonError(f"non-finite JSON constant is not allowed: {value}")

    try:
        return json.loads(
            raw,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except StrictJsonError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StrictJsonError(f"invalid JSON: {exc}") from exc
