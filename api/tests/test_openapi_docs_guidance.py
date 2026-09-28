"""Contracts for stock Swagger UI and OpenAPI endpoint guidance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from tests.legacy_guided_tokens import LEGACY_API_PREFIX, LEGACY_WORDS

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
GUIDANCE_MARKERS = (
    "### What it is for",
    "### How to use it",
)
PRIVATE_MARKERS = (
    "D:/",
    "D:\\",
    "C:/",
    "C:\\",
    "OneDrive",
    ".local_artifacts",
    "workspace/consensus",
    "runbook/",
)


def _operations(openapi: dict[str, Any]):
    for path, path_item in openapi["paths"].items():
        for method, operation in path_item.items():
            if method in HTTP_METHODS:
                yield path, method, operation


def _operation(openapi: dict[str, Any], path: str, method: str) -> dict[str, Any]:
    return openapi["paths"][path][method]


def _parameter(operation: dict[str, Any], name: str) -> dict[str, Any]:
    for parameter in operation.get("parameters", []):
        if parameter["name"] == name:
            return parameter
    raise AssertionError(f"Missing parameter: {name}")


def _find_key_values(value: Any, target_key: str, path: str = "$"):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == target_key:
                yield child_path, child
            yield from _find_key_values(child, target_key, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _find_key_values(child, target_key, f"{path}[{index}]")


def test_swagger_docs_use_stock_ui_with_normal_try_it_out_flow(client):
    """Swagger UI should keep stock layout and the normal Try it out flow."""
    response = client.get("/docs")

    assert response.status_code == 200
    assert "/static/swagger-ui/swagger-ui.css" in response.text
    assert "/static/swagger-ui/sds-mobile.css" in response.text
    assert "/static/swagger-ui/sds-compact.css" not in response.text
    assert '"docExpansion": "list"' not in response.text
    assert '"defaultModelsExpandDepth": -1' not in response.text
    assert '"filter": true' not in response.text
    assert '"tryItOutEnabled": true' in response.text
    assert '"tryItOutEnabled": false' not in response.text


def test_swagger_mobile_css_wraps_stock_schema_titles():
    styles = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "static"
        / "swagger-ui"
        / "sds-mobile.css"
    )
    css = styles.read_text(encoding="utf-8")

    assert "@media (max-width: 640px)" in css
    assert ".swagger-ui .json-schema-2020-12__title" in css
    assert ".swagger-ui .json-schema-2020-12-accordion" in css
    assert "overflow-wrap: anywhere;" in css


def test_openapi_guidance_sections_are_added_to_every_operation(client):
    """Every visible API operation should explain purpose, usage, and an example."""
    response = client.get("/openapi.json")
    assert response.status_code == 200

    missing: list[str] = []
    for path, method, operation in _operations(response.json()):
        description = operation.get("description") or ""
        for marker in GUIDANCE_MARKERS:
            if marker not in description:
                missing.append(f"{method.upper()} {path}: {marker}")
    assert missing == []


def test_default_guidance_does_not_emit_generic_example_text(client):
    """Generic operations should rely on parameter/body examples, not filler text."""
    response = client.get("/openapi.json")
    assert response.status_code == 200

    manifest_description = _operation(
        response.json(), "/api/v1/values/manifest", "get"
    )["description"]

    assert "### Example" not in manifest_description
    assert "fill the required path/query fields" not in manifest_description
    assert "leave optional filters blank" not in manifest_description


def test_openapi_top_level_contract_uses_mounted_api_paths(client):
    """Top-level OpenAPI prose and servers must not point clients at dead paths."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    openapi = response.json()

    server_urls = [server["url"] for server in openapi.get("servers", [])]
    assert "https://api.sustainabilitydataspace.com/v1" not in server_urls
    assert not any(url.rstrip("/").endswith("/v1") for url in server_urls)

    description = openapi["info"]["description"]
    assert "`/api/v1/hierarchies`" in description
    assert "`/api/v1/values`" in description
    assert "`/api/v1/calculate`" in description
    assert "`/hierarchies`" not in description
    assert "`/values` endpoints" not in description
    assert "`/calculate` endpoints" not in description


def test_hierarchy_operations_do_not_use_generic_fallback_guidance(client):
    """Hierarchy docs should explain concrete workflow steps, not tag fallback text."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    openapi = response.json()

    generic_text = (
        "Use this operation for organizational and temporal hierarchy "
        "configuration workflows"
    )
    expected_terms = {
        ("/api/v1/hierarchies", "get"): (
            "company_id",
            "hierarchy_type",
            "active",
            "limit",
            "offset",
        ),
        ("/api/v1/hierarchies/{hierarchy_id}", "get"): (
            "copy",
            "hierarchy_id",
            "404",
        ),
        ("/api/v1/hierarchies/{hierarchy_id}", "put"): (
            "Read the current hierarchy first",
            "stable level ids",
        ),
        ("/api/v1/hierarchies/{hierarchy_id}", "delete"): (
            "obsolete",
            "company_id",
        ),
        ("/api/v1/hierarchies/{hierarchy_id}/activate", "post"): (
            "active=true",
            "company_id",
        ),
    }

    offenders: list[str] = []
    for (path, method), terms in expected_terms.items():
        description = _operation(openapi, path, method)["description"]
        if generic_text in description:
            offenders.append(f"{method.upper()} {path}: generic fallback")
        missing_terms = [term for term in terms if term not in description]
        for term in missing_terms:
            offenders.append(f"{method.upper()} {path}: missing {term!r}")

    assert offenders == []


def test_concept_schema_and_guidance_explain_public_formula_nulls(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    openapi = response.json()

    concept_schema = openapi["components"]["schemas"]["ConceptInfo"]["properties"]
    assert (
        "ordinary list/detail reads"
        in concept_schema["similarity_score"]["description"]
    )
    assert (
        "public calculation contract"
        in concept_schema["related_variables"]["description"]
    )
    assert (
        "source-standard disclosure/catalog nodes"
        in concept_schema["formula"]["description"]
    )

    concepts_description = _operation(openapi, "/api/v1/concepts", "get")["description"]
    assert "source disclosure nodes may correctly show nulls" in concepts_description


def test_localization_translation_package_doc_has_no_local_consensus_paths():
    """Published API docs must not point readers at ignored workspace artifacts."""
    doc_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "localization-translation-package.md"
    )
    text = doc_path.read_text(encoding="utf-8")

    assert "workspace/consensus" not in text
    assert ".local_artifacts" not in text


def test_openapi_guidance_marks_internal_operations_and_hides_removed_guided_routes(
    client,
):
    """Internal/admin routes should be explicit; removed guided routes stay out."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    internal_description = paths[
        "/api/v1/internal/canonical-mapping-packages/{package_id}/validations"
    ]["post"]["description"]

    assert "Internal/admin" in internal_description
    assert "normal public consumer flow" in internal_description
    assert not any(path.startswith(LEGACY_API_PREFIX) for path in paths)


def test_openapi_guidance_does_not_publish_retired_contract_variables(client):
    """Swagger guidance must use current DB-backed contracts, not retired inputs."""
    response = client.get("/openapi.json")
    assert response.status_code == 200

    serialized = response.text
    assert "syg:Water_Industrial" not in serialized
    assert "syg:Water_Cooling" not in serialized
    assert "swagger_smoke" not in serialized
    assert "sample dataset" not in serialized
    assert "nordhaven-sample-data" not in serialized


def test_openapi_guidance_examples_are_present_for_core_calls(client):
    """High-value request bodies should expose named Swagger examples."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    value_examples = paths["/api/v1/values"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    import_examples = paths["/api/v1/values/import"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    hierarchy_examples = paths["/api/v1/hierarchies"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    calculation_examples = paths["/api/v1/calculate"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    calculation_description = paths["/api/v1/calculate"]["post"]["description"]
    calculation_response_schema = response.json()["components"]["schemas"][
        "CalculationResponse"
    ]["properties"]
    value_resolve_response_schema = response.json()["components"]["schemas"][
        "ValueResolveResponse"
    ]["properties"]
    resolve_examples = paths["/api/v1/values/resolve"]["post"]["requestBody"][
        "content"
    ]["application/json"]["examples"]
    resolve_description = paths["/api/v1/values/resolve"]["post"]["description"]
    convert_examples = paths["/api/v1/convert"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    fx_examples = paths["/api/v1/fx/convert"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]

    assert (
        value_examples["nordhaven_2024_energy_value"]["value"]["concept"]
        == "urn:sds:reg:esrs:e1_5_12"
    )
    assert (
        value_examples["nordhaven_2024_energy_value"]["summary"]
        == "Nordhaven 2024 energy value"
    )
    assert (
        "loaded Nordhaven 2024 group energy observation"
        in value_examples["nordhaven_2024_energy_value"]["description"]
    )
    assert value_examples["nordhaven_2024_energy_value"]["value"]["entity"] == (
        "nh_group"
    )
    assert value_examples["nordhaven_2024_energy_value"]["value"]["period"] == (
        "2024-12-31"
    )
    assert (
        value_examples["nordhaven_2024_energy_value"]["value"]["period_start"]
        == "2024-01-01"
    )
    assert (
        value_examples["nordhaven_2024_energy_value"]["value"]["period_end"]
        == "2024-12-31"
    )
    assert value_examples["nordhaven_2024_energy_value"]["value"]["value"] == 390.604
    assert value_examples["nordhaven_2024_energy_value"]["value"]["unit"] == "MWh"
    assert value_examples["nordhaven_2024_energy_value"]["value"]["metadata"] == {
        "source": "nordhaven-operational-data"
    }
    assert (
        import_examples["nordhaven_2024_energy_history"]["value"]["items"][0][
            "external_key"
        ]
        == "synthetic:sds-full-operational-v2:annual:f1453aa1d754e3ef7034"
    )
    assert (
        "Nordhaven" in import_examples["nordhaven_2024_energy_history"]["description"]
    )
    assert (
        import_examples["nordhaven_2024_energy_history"]["value"]["items"][1]["value"]
        == 345.174
    )
    assert any(
        level["id"] == "nh_br_sao_paulo_plant"
        for level in hierarchy_examples["nordhaven_operational_perimeter"]["value"][
            "levels"
        ]
    )
    assert (
        hierarchy_examples["nordhaven_operational_perimeter"]["value"]["company_id"]
        == "nordhaven_components_group"
    )
    assert import_examples["energy_input"]["value"]["items"][0]["unit"] == "MWh"
    assert import_examples["energy_input"]["value"]["items"][0]["external_key"] == (
        "nordhaven:sao-paulo:esrs-e1-5-12:2024"
    )
    assert "csrd_disclosure_e3_5" not in calculation_examples
    complex_calc = calculation_examples["esrs_e1_5_fossil_energy_sum"]
    assert complex_calc["value"] == {
        "concept": "urn:sds:reg:esrs:e1_5_02",
        "entity": "nh_group",
        "period": "2024",
        "granularity": "annual",
        "include_trace": True,
    }
    assert "e1_5_10 + e1_5_11 + e1_5_12 + e1_5_13 + e1_5_14" in (
        complex_calc["description"]
    )
    assert "source_value_ids" in calculation_description
    assert "framework_metadata.input_mappings" in calculation_description
    assert "certified_bridge" in calculation_description
    assert "execution_authority" in resolve_description
    assert "bridge_id" in resolve_description
    execution_authority_description = calculation_response_schema[
        "execution_authority"
    ]["description"]
    assert "native" in execution_authority_description
    assert "native_contract" in execution_authority_description
    assert "equivalent_mapping" in execution_authority_description
    assert "certified_bridge" in execution_authority_description
    resolve_execution_authority_description = value_resolve_response_schema[
        "execution_authority"
    ]["description"]
    assert "native_contract" in resolve_execution_authority_description
    assert "certified_bridge" in resolve_execution_authority_description
    assert (
        "Certified bridge contract id"
        in value_resolve_response_schema["bridge_id"]["description"]
    )
    assert "gri_302_1e_fail_closed_narrower_esrs" not in calculation_examples
    assert (
        resolve_examples["resolve_csrd_to_gri_water"]["value"]["target_concept"]
        == "gri:303-5.c"
    )
    assert (
        resolve_examples["resolve_csrd_to_gri_water"]["value"]["entity"] == "nh_group"
    )
    gri_bridge_resolve = resolve_examples["resolve_gri_302_1e_from_certified_bridge"]
    assert gri_bridge_resolve["value"] == {
        "target_concept": "urn:sds:reg:gri:gri_302_1_e_total_energy_consumption_within_organization",
        "entity": "nh_group",
        "period": "2024",
        "granularity": "annual",
        "include_trace": True,
    }
    assert "GRI 302-1.e" in gri_bridge_resolve["summary"]
    assert "execution_authority" in gri_bridge_resolve["description"]
    assert "bridge_id" in gri_bridge_resolve["description"]
    assert "any indicator with sufficient runtime evidence" in resolve_description
    assert "certified bridge contract explicitly authorizes" in resolve_description
    assert (
        resolve_examples["refuse_energy_to_emissions"]["value"]["target_unit"]
        == "t CO2e"
    )
    assert (
        LEGACY_WORDS
        not in resolve_examples["refuse_energy_to_emissions"]["description"]
    )
    revision_parameters = {
        parameter["name"]: parameter
        for parameter in paths["/api/v1/values/revisions"]["get"]["parameters"]
    }
    indicator_parameters = {
        parameter["name"]: parameter
        for parameter in paths["/api/v1/indicators/{indicator_id}"]["get"]["parameters"]
    }
    assert "00000000-0000-4000-8000" not in response.text
    assert revision_parameters["tenant_id"]["example"] == "nordhaven_components_group"
    assert revision_parameters["state"]["example"] == "approved"
    assert indicator_parameters["indicator_id"]["example"] == "urn:sds:reg:esrs:e1_5_12"
    assert "urn:sds:reg:esrs:e1_6_01" not in response.text
    assert convert_examples["kilowatt_hours_to_megawatt_hours"]["value"] == {
        "value": 1000,
        "from_unit": "kWh",
        "to_unit": "MWh",
    }
    fx_example = fx_examples["usd_to_eur_monthly_average_preview"]["value"]
    assert fx_example["from_currency"] == "USD"
    assert fx_example["to_currency"] == "EUR"
    assert fx_example["period_start"] == "2024-12-01"
    assert fx_example["period_end"] == "2024-12-31"
    assert "eur_to_usd_preview" not in fx_examples


def test_openapi_examples_are_swagger_try_it_out_instructions(client):
    """Examples should tell users what to enter into Swagger Try it out fields."""
    response = client.get("/openapi.json")
    assert response.status_code == 200

    login_description = _operation(response.json(), "/auth/login", "post")[
        "description"
    ]
    values_description = _operation(response.json(), "/api/v1/values", "get")[
        "description"
    ]
    mapping_description = _operation(
        response.json(), "/api/v1/mappings/from/{standard}/{code}", "get"
    )["description"]
    dependencies_description = _operation(
        response.json(), "/api/v1/calculate/dependencies/{concept}", "get"
    )["description"]
    sparql_description = _operation(response.json(), "/api/v1/sparql", "post")[
        "description"
    ]

    assert "In Swagger, click **Try it out**" in login_description
    assert '`username` = `"your_username"`' in login_description
    assert '`password` = `"your_password"`' in login_description
    assert "paste `access_token` into **Authorize**" in login_description

    assert '`entity` = `"nh_group"`' in values_description
    assert '`period_start` = `"2024-01-01"`' in values_description
    assert '`period_end` = `"2024-12-31"`' in values_description
    assert "`limit` = `5`" in values_description
    assert "`cursor` blank" in values_description

    assert '`standard` = `"ESRS"`' in mapping_description
    assert '`code` = `"E3-4_05"`' in mapping_description
    assert "`limit` = `10`" in mapping_description
    assert "Start with the ESRS/GRI/GHG code the user knows" in (
        dependencies_description
    )
    assert "granular datapoint" in dependencies_description
    assert "`required_variables`" in dependencies_description
    assert "`input_bindings`" in dependencies_description
    assert "`supports_certified_bridge_inputs`" in dependencies_description

    assert "DB-backed semantics" in sparql_description
    assert "501" in sparql_description


def test_openapi_csv_import_guidance_documents_strict_contracts(client):
    """CSV upload endpoints should describe the exact headers Swagger users need."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()

    values_csv = _operation(schema, "/api/v1/values/import-csv", "post")["description"]
    values_csv_job = _operation(schema, "/api/v1/values/import-csv-jobs", "post")[
        "description"
    ]
    indicator_validation = _operation(
        schema, "/api/v1/indicators/import-csv-validations", "post"
    )["description"]
    indicator_job = _operation(schema, "/api/v1/indicators/import-csv-jobs", "post")[
        "description"
    ]

    assert "concept,entity,period,value,unit" in values_csv
    assert "metadata_json" in values_csv
    assert "valid JSON escaped for CSV" in values_csv
    assert "/api/v1/values/import-jobs/{job_id}" in values_csv_job
    assert "memory/demo-only" in values_csv_job
    assert "REQUIRE_DATABASE=false" in values_csv_job
    assert "DB mode returns `503` pending H15" in values_csv_job
    assert "before body parsing, upload spooling, authorization" in values_csv_job
    assert "not durable across restarts" in values_csv_job
    for path in ("/api/v1/values/import-jobs", "/api/v1/values/import-csv-jobs"):
        unavailable = _operation(schema, path, "post")["responses"]["503"]
        content = unavailable["content"]["application/json"]
        assert content["schema"] == {
            "type": "object",
            "properties": {"detail": {"type": "string"}},
            "required": ["detail"],
        }
        assert content["example"] == {
            "detail": "Database-backed async value import jobs are unsupported pending H15"
        }

    required_indicator_columns = (
        "identifier,title,indicator,description,dimension,"
        "unitName,unitType,periodicity,periodType,sourceRef,codeESRS,"
        "codeGRI,codeGRI_expanded,evidencePath,sourceRow,owner,"
        "accessRights,validationMethod,doubleMateriality,valueType"
    )
    assert required_indicator_columns in indicator_validation
    assert "not a shortened CSV" in indicator_validation
    assert "/api/v1/indicators/import-jobs/{job_id}" in indicator_job
    assert "/errors" in indicator_job


def test_openapi_parameters_expose_try_it_out_example_values(client):
    """Swagger form fields should carry usable example values where possible."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()

    values_get = _operation(schema, "/api/v1/values", "get")
    values_post = _operation(schema, "/api/v1/values", "post")
    mappings_get = _operation(schema, "/api/v1/mappings/from/{standard}/{code}", "get")
    mappings_search = _operation(schema, "/api/v1/mappings/search", "get")
    fx_coverage = _operation(schema, "/api/v1/fx/rates/coverage", "get")
    concepts_get = _operation(schema, "/api/v1/concepts", "get")

    assert _parameter(values_get, "entity")["example"] == "nh_group"
    assert _parameter(values_get, "limit")["example"] == 5
    assert (
        _parameter(_operation(schema, "/api/v1/values/manifest", "get"), "entity")[
            "example"
        ]
        == "nh_group"
    )
    assert (
        _parameter(
            _operation(schema, "/api/v1/values/manifest", "get"), "period_start"
        )["example"]
        == "2024-01-01"
    )
    assert _parameter(values_post, "Idempotency-Key")["example"] == (
        "nordhaven-energy-2024"
    )
    assert _parameter(mappings_get, "standard")["example"] == "ESRS"
    assert _parameter(mappings_get, "code")["example"] == "E3-4_05"
    assert _parameter(mappings_get, "limit")["example"] == 10
    assert _parameter(mappings_search, "source_standard")["example"] == "ESRS"
    assert _parameter(mappings_search, "target_standard")["example"] == "GRI"
    assert _parameter(mappings_search, "target_code")["example"] == "GRI 305"
    assert _parameter(mappings_search, "limit")["example"] == 10
    assert "example" not in _parameter(mappings_search, "dimension")
    assert _parameter(fx_coverage, "provider")["example"] == "ECB"
    assert _parameter(fx_coverage, "base_currency")["example"] == "USD"
    assert _parameter(fx_coverage, "quote_currency")["example"] == "EUR"
    assert _parameter(fx_coverage, "start")["example"] == "2024-12-01"
    assert _parameter(fx_coverage, "end")["example"] == "2024-12-31"
    assert _parameter(concepts_get, "taxonomy")["example"] == "CSRD"
    assert _parameter(concepts_get, "concept_type")["example"] == "Disclosure"
    assert _parameter(concepts_get, "search")["example"] == "waste"
    assert _parameter(concepts_get, "limit")["example"] == 100
    assert _parameter(concepts_get, "offset")["example"] == 0
    assert (
        "Sygris is the unified SDS/Sygris public catalog view"
        in concepts_get["description"]
    )
    assert "`taxonomy=GHG`" in concepts_get["description"]
    assert "retain their source taxonomy" in concepts_get["description"]


def test_openapi_hierarchy_type_examples_use_valid_filter_values(client):
    """Swagger must not suggest hierarchy_type values that exact-match no rows."""
    response = client.get("/openapi.json")
    assert response.status_code == 200

    valid_hierarchy_types = {"organizational", "temporal", "geographical"}
    offenders: list[str] = []
    for path, method, operation in _operations(response.json()):
        for parameter in operation.get("parameters", []) or []:
            if parameter.get("name") == "hierarchy_type" and "example" in parameter:
                example = parameter["example"]
                if example not in valid_hierarchy_types:
                    offenders.append(f"{method.upper()} {path} parameter={example!r}")

        media_type = (
            operation.get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
        )
        for example_name, example in media_type.get("examples", {}).items():
            for key_path, value in _find_key_values(
                example.get("value"), "hierarchy_type"
            ):
                if value not in valid_hierarchy_types:
                    offenders.append(
                        f"{method.upper()} {path} example={example_name} "
                        f"{key_path}={value!r}"
                    )

    assert offenders == []


def test_openapi_request_body_examples_match_their_schemas(client):
    """Every named JSON body example published in Swagger must validate."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()

    base_validator = Draft202012Validator(schema)
    failures: list[str] = []
    for path, method, operation in _operations(schema):
        media_type = (
            operation.get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
        )
        request_schema = media_type.get("schema")
        if not request_schema:
            continue
        validator = base_validator.evolve(schema=request_schema)
        for example_name, example in media_type.get("examples", {}).items():
            example_failures = sorted(
                validator.iter_errors(example.get("value")),
                key=lambda error: list(error.path),
            )
            for failure in example_failures:
                location = "/".join(map(str, failure.path)) or "$"
                failures.append(
                    f"{method.upper()} {path} example={example_name} "
                    f"at {location}: {failure.message}"
                )

    assert failures == []


def test_values_read_examples_do_not_execute_optional_cursor_filters(client):
    """The values read example must not send opaque cursor examples as real input."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()

    values_get = _operation(schema, "/api/v1/values", "get")
    assert _parameter(values_get, "entity")["example"] == "nh_group"
    assert _parameter(values_get, "period_start")["example"] == "2024-01-01"
    assert _parameter(values_get, "period_end")["example"] == "2024-12-31"
    assert _parameter(values_get, "limit")["example"] == 5

    for optional_name in ["concept", "unit", "changed_since", "offset", "cursor"]:
        assert "example" not in _parameter(values_get, optional_name)


def test_openapi_avoids_invented_stateful_identifier_examples(client):
    """Stateful IDs must be copied from prior responses, not invented in Swagger."""
    response = client.get("/openapi.json")
    assert response.status_code == 200

    blocked_names = {
        "api_key_id",
        "from_snapshot_id",
        "job_id",
        "package_id",
        "to_snapshot_id",
    }
    blocked_values = {
        "canonical_mapping_climate_ghg_v2",
        "job_123",
        "key_123",
        17,
        18,
    }
    offenders: list[str] = []
    for path, method, operation in _operations(response.json()):
        for parameter in operation.get("parameters", []) or []:
            name = parameter.get("name")
            example = parameter.get("example")
            if name in blocked_names and "example" in parameter:
                offenders.append(f"{method.upper()} {path} {name}={example!r}")
            if example in blocked_values:
                offenders.append(f"{method.upper()} {path} {name}={example!r}")

    assert offenders == []


def test_openapi_never_suggests_invented_cursor_values(client):
    """Cursor fields are opaque and must come from a previous response."""
    response = client.get("/openapi.json")
    assert response.status_code == 200

    offenders: list[str] = []
    for path, method, operation in _operations(response.json()):
        for parameter in operation.get("parameters", []):
            if parameter.get("name") == "cursor" and "example" in parameter:
                offenders.append(f"{method.upper()} {path}")

    assert offenders == []


def test_openapi_guidance_overrides_match_existing_paths(client):
    """Central docs overrides should fail fast if an endpoint path is renamed."""
    from src.api.openapi_docs import OPERATION_GUIDANCE

    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema_paths = response.json()["paths"]

    stale = [
        f"{method.upper()} {path}"
        for path, method in OPERATION_GUIDANCE
        if path not in schema_paths or method not in schema_paths[path]
    ]
    assert stale == []


def test_openapi_guidance_has_no_local_private_markers(client):
    """Public Swagger text should not leak local paths or private work artifacts."""
    response = client.get("/openapi.json")
    assert response.status_code == 200

    text = "\n".join(
        operation.get("description") or ""
        for _, _, operation in _operations(response.json())
    )
    leaked = [marker for marker in PRIVATE_MARKERS if marker in text]

    assert leaked == []
