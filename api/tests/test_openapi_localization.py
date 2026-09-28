"""Tests for the Swagger/OpenAPI localization overlay + language selector.

Covers the OpenAPI-docs localization layer: canonical /openapi.json is untouched,
/openapi.localized.json?lang=es translates human-facing text only (machine contract
identical), and /docs renders a language selector that points at the localized spec.
"""

from __future__ import annotations

import copy
import json

import pytest

from src.api.openapi_localization import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    language_options,
    localize_openapi_schema,
    normalize_language,
)

SAMPLE_SCHEMA = {
    "openapi": "3.1.0",
    "info": {
        "title": "SustainabilityDataSpace API",
        "description": "## Unified ESG Ontology API",
    },
    "paths": {
        "/api/v1/values": {
            "post": {
                "operationId": "create_value_api_v1_values_post",
                "summary": "Create a new value",
                "parameters": [{"name": "x_request_id", "in": "header"}],
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/auth/login": {"post": {"operationId": "login", "summary": "User login"}},
    },
    "components": {"schemas": {"ValueCreate": {"type": "object"}}},
}


def test_normalize_language():
    assert normalize_language(None) == DEFAULT_LANGUAGE
    assert normalize_language("") == DEFAULT_LANGUAGE
    assert normalize_language("es") == "es"
    assert normalize_language("ES") == "es"
    assert normalize_language("es-ES") == "es"
    assert normalize_language("es_MX") == "es"
    assert normalize_language("fr") == DEFAULT_LANGUAGE  # unsupported -> default
    assert normalize_language("en") == "en"


def test_language_options_cover_supported_languages():
    codes = [opt["code"] for opt in language_options()]
    assert codes == list(SUPPORTED_LANGUAGES)
    assert all(opt["label"] for opt in language_options())


def test_english_returns_canonical_copy_unchanged():
    result = localize_openapi_schema(SAMPLE_SCHEMA, "en")
    assert result == SAMPLE_SCHEMA
    # deep copy: mutating the result must not touch the input
    result["info"]["title"] = "MUTATED"
    assert SAMPLE_SCHEMA["info"]["title"] == "SustainabilityDataSpace API"


def test_unsupported_language_returns_canonical_copy():
    assert localize_openapi_schema(SAMPLE_SCHEMA, "fr") == SAMPLE_SCHEMA


def test_spanish_translates_human_text_only():
    result = localize_openapi_schema(SAMPLE_SCHEMA, "es")
    # human-facing text translated
    assert result["info"]["title"] == "API de SustainabilityDataSpace"
    assert "Ontología ESG" in result["info"]["description"]
    assert (
        result["paths"]["/api/v1/values"]["post"]["summary"] == "Crear un nuevo valor"
    )
    assert result["paths"]["/auth/login"]["post"]["summary"] == "Inicio de sesión"
    # machine contract UNCHANGED
    assert set(result["paths"]) == set(SAMPLE_SCHEMA["paths"])
    assert (
        result["paths"]["/api/v1/values"]["post"]["operationId"]
        == "create_value_api_v1_values_post"
    )
    assert (
        result["paths"]["/api/v1/values"]["post"]["parameters"]
        == SAMPLE_SCHEMA["paths"]["/api/v1/values"]["post"]["parameters"]
    )
    assert result["components"] == SAMPLE_SCHEMA["components"]


def test_untranslated_summary_falls_back_to_english():
    schema = copy.deepcopy(SAMPLE_SCHEMA)
    schema["paths"]["/x"] = {"get": {"operationId": "x", "summary": "Totally novel op"}}
    result = localize_openapi_schema(schema, "es")
    assert result["paths"]["/x"]["get"]["summary"] == "Totally novel op"


# --- route-level (direct handler calls; no DB) --------------------------------


@pytest.mark.asyncio
async def test_localized_openapi_route_parity_with_canonical():
    import src.api.main as main

    canonical = main.app.openapi()
    response = await main.localized_openapi(lang="es")
    localized = json.loads(bytes(response.body))

    # Spanish info, but identical path/operation contract
    assert localized["info"]["title"] == "API de SustainabilityDataSpace"
    assert set(localized["paths"]) == set(canonical["paths"])
    canon_ops = {
        op.get("operationId")
        for item in canonical["paths"].values()
        for op in item.values()
        if isinstance(op, dict)
    }
    loc_ops = {
        op.get("operationId")
        for item in localized["paths"].values()
        for op in item.values()
        if isinstance(op, dict)
    }
    assert canon_ops == loc_ops
    assert localized.get("components") == canonical.get("components")


@pytest.mark.asyncio
async def test_spanish_openapi_localizes_every_operation_summary_and_guidance():
    import src.api.main as main

    canonical = main.app.openapi()
    response = await main.localized_openapi(lang="es")
    localized = json.loads(bytes(response.body))

    untranslated_summaries = []
    english_guidance = []
    for path, methods in canonical["paths"].items():
        for method, operation in methods.items():
            if not isinstance(operation, dict):
                continue
            localized_operation = localized["paths"][path][method]
            summary = operation.get("summary")
            if summary and localized_operation.get("summary") == summary:
                untranslated_summaries.append(f"{method.upper()} {path}: {summary}")

            description = localized_operation.get("description") or ""
            if any(
                marker in description
                for marker in (
                    "### What it is for",
                    "### How to use it",
                    "### Example",
                    "**Authentication:**",
                    "Use this operation for",
                    "Expand the operation",
                )
            ):
                english_guidance.append(f"{method.upper()} {path}")

    assert untranslated_summaries == []
    assert english_guidance == []


@pytest.mark.asyncio
async def test_spanish_openapi_localizes_named_request_examples():
    import src.api.main as main

    canonical = main.app.openapi()
    response = await main.localized_openapi(lang="es")
    localized = json.loads(bytes(response.body))

    canonical_example = canonical["paths"]["/api/v1/values/resolve"]["post"][
        "requestBody"
    ]["content"]["application/json"]["examples"][
        "resolve_gri_302_1e_from_certified_bridge"
    ]
    localized_example = localized["paths"]["/api/v1/values/resolve"]["post"][
        "requestBody"
    ]["content"]["application/json"]["examples"][
        "resolve_gri_302_1e_from_certified_bridge"
    ]

    assert localized_example["value"] == canonical_example["value"]
    assert localized_example["summary"] == "Resolver GRI 302-1.e con puente certificado"
    assert "entradas ESRS autorizadas" in localized_example["description"]
    assert "execution_authority" in localized_example["description"]
    assert localized_example["description"] != canonical_example["description"]


@pytest.mark.asyncio
async def test_spanish_docs_selector_uses_spanish_accessible_label():
    import src.api.main as main

    es = (await main.docs(lang="es")).body.decode("utf-8")
    en = (await main.docs(lang="en")).body.decode("utf-8")

    assert 'aria-label="Idioma de la documentación API"' in es
    assert 'aria-label="API docs language"' in en


@pytest.mark.asyncio
async def test_localized_openapi_quickstart_uses_mounted_api_paths():
    import src.api.main as main

    response = await main.localized_openapi(lang="es")
    localized = json.loads(bytes(response.body))

    description = localized["info"]["description"]
    assert "`/api/v1/hierarchies`" in description
    assert "`/api/v1/values`" in description
    assert "`/api/v1/calculate`" in description
    assert "compute métricas" not in description
    assert "calcule métricas" in description
    assert "`/hierarchies`" not in description
    assert "`/values`" not in description
    assert "`/calculate`" not in description


@pytest.mark.asyncio
async def test_localized_openapi_route_english_is_canonical():
    import src.api.main as main

    canonical = main.app.openapi()
    response = await main.localized_openapi(lang="en")
    assert (
        json.loads(bytes(response.body))["info"]["title"] == canonical["info"]["title"]
    )


@pytest.mark.asyncio
async def test_docs_route_injects_selector_and_localized_spec_url():
    import src.api.main as main

    es = (await main.docs(lang="es")).body.decode("utf-8")
    assert 'id="sds-lang-selector"' in es
    assert "/openapi.localized.json?lang=es" in es
    assert '<option value="es" selected>' in es

    en = (await main.docs()).body.decode("utf-8")
    assert 'id="sds-lang-selector"' in en
    # default points at the canonical spec, not the localized variant
    assert "/openapi.localized.json" not in en
    assert '<option value="en" selected>' in en
