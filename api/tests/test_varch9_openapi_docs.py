"""VARCH-9 contract tests — Swagger/OpenAPI guidance for public temporal-read endpoints.

Verifies the served, enriched OpenAPI schema carries human guidance for the
/api/v1/semantic-dimensions endpoint and /api/v1/concepts temporal selectors. Static
Swagger examples are only advertised where they can execute against the local DB app.
"""

from __future__ import annotations

import importlib.util

import pytest

if (
    importlib.util.find_spec("slowapi") is None
    or importlib.util.find_spec("email_validator") is None
):
    pytest.skip(
        "API app deps (slowapi / email-validator) not installed in this env",
        allow_module_level=True,
    )


def _param(operation: dict, name: str) -> dict:
    for p in operation.get("parameters", []):
        if p.get("name") == name:
            return p
    raise AssertionError(f"parameter {name!r} not found")


def test_semantic_dimensions_guidance_suppresses_non_local_examples(client) -> None:
    spec = client.get("/openapi.json").json()
    op = spec["paths"]["/api/v1/semantic-dimensions"]["get"]
    # guidance merged into the description.
    assert "reproducible public temporal read" in (op.get("description") or "")
    assert "requires DB-backed canonical semantic data" in (op.get("description") or "")
    # Point-in-time catalog examples require specific DB pins, so omit static values.
    assert "example" not in _param(op, "valid_as_of")
    assert "example" not in _param(op, "decision_as_of")
    assert "example" not in _param(op, "as_of_commit_id")
    assert "example" not in _param(op, "scope")
    assert "example" not in _param(op, "cursor")


def test_concepts_temporal_selectors_do_not_advertise_static_pin_examples(
    client,
) -> None:
    spec = client.get("/openapi.json").json()
    op = spec["paths"]["/api/v1/concepts"]["get"]
    assert "example" not in _param(op, "valid_as_of")
    assert "example" not in _param(op, "decision_as_of")
    # guidance mentions the new temporal read.
    assert "pins" in (op.get("description") or "")


def test_concepts_detail_selectors_do_not_advertise_static_pin_examples(client) -> None:
    spec = client.get("/openapi.json").json()
    op = spec["paths"]["/api/v1/concepts/{concept_uri}"]["get"]
    assert _param(op, "concept_uri")["example"] == "urn:sds:disclosure:csrd:e3-5"
    assert "example" not in _param(op, "valid_as_of")
    assert "example" not in _param(op, "as_of_commit_id")


def test_guidance_tables_are_consistent() -> None:
    # unit-level: the new operation entries exist in the docs tables.
    from src.api.openapi_docs import (
        OPERATION_GUIDANCE,
        OPERATION_PARAMETER_EXAMPLES,
        PARAMETER_NAME_EXAMPLES,
    )

    assert ("/api/v1/semantic-dimensions", "get") in OPERATION_GUIDANCE
    assert OPERATION_PARAMETER_EXAMPLES[("/api/v1/semantic-dimensions", "get")] is None
    for name in ("valid_as_of", "decision_as_of", "as_of_commit_id", "scope"):
        assert name in PARAMETER_NAME_EXAMPLES
