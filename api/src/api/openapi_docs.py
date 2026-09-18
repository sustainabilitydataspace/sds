"""OpenAPI documentation enrichment for Swagger/ReDoc consumers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.concept_runtime_aliases import CSRD_E3_5_DISCLOSURE_URI

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
GUIDANCE_MARKERS = (
    "### What it is for",
    "### How to use it",
    "### Example",
)

TAG_CONTEXT: Mapping[str, str] = {
    "Authentication": "sign-in, user profile, password, and API key flows",
    "Values": "ESG observation ingest, read, export, lineage, and change-feed workflows",
    "Calculations": "indicator calculation, dependency inspection, and traceability workflows",
    "Hierarchies": "organizational and temporal hierarchy configuration workflows",
    "Ontology": "taxonomy, concept, equivalence, and read-only semantic query workflows",
    "Units": "unit catalog and conversion workflows",
    "FX": "currency catalog, rate coverage, FX conversion, and controlled rate import workflows",
    "Interoperability": "runtime readiness and cross-standard resolution workflows",
    "Indicators": "indicator catalog search, export, import, history, and change-feed workflows",
    "Mappings": "standard-to-standard mapping discovery, export, history, and change-feed workflows",
    "Internal Mapping Assertions": "controlled canonical mapping package validation and promotion workflows",
    "General": "service discovery and operational status checks",
}


@dataclass(frozen=True)
class OperationGuidance:
    """Concise user guidance layered onto generated OpenAPI operations."""

    purpose: str
    how: str
    example: str | None = None
    auth: str | None = None


OPERATION_GUIDANCE: dict[tuple[str, str], OperationGuidance] = {
    ("/auth/login", "post"): OperationGuidance(
        purpose="Authenticate a user and receive access and refresh tokens for protected API calls.",
        how="Open Try it out, enter the username and password, execute, then copy the access_token into Authorize.",
        example=(
            "In Swagger, click **Try it out**. In the JSON body enter "
            '`username` = `"your_username"` and `password` = `"your_password"` '
            "click **Execute**, then paste `access_token` into **Authorize**."
        ),
        auth="No bearer token is required for login.",
    ),
    ("/auth/me", "get"): OperationGuidance(
        purpose="Confirm which user and role the current bearer token represents.",
        how="Authorize with an access token, execute this call, and verify the returned role before trying protected writes.",
        example=(
            "In Swagger, click **Authorize**, paste the `access_token` from "
            "/auth/login, then open this operation, click **Try it out**, and "
            "click **Execute**. There are no request fields to fill."
        ),
    ),
    ("/api/v1/values", "get"): OperationGuidance(
        purpose="Read ESG observation values with filters for concept, entity, period, unit, and pagination.",
        how=(
            "Set the entity and period filters first, keep optional filters blank "
            "unless you need them, and only paste cursor when it comes from a "
            "previous `next_cursor` response."
        ),
        example=(
            "In Swagger, click **Try it out** and set "
            '`entity` = `"nh_group"`, '
            '`period_start` = `"2024-01-01"`, '
            '`period_end` = `"2024-12-31"`, and `limit` = `5`; leave '
            "`concept`, `unit`, `changed_since`, `offset`, and `cursor` blank "
            "for the first read, then click **Execute**."
        ),
    ),
    ("/api/v1/values", "post"): OperationGuidance(
        purpose="Create one ESG observation value and normalize units or currencies when expected targets are supplied.",
        how="Use this after you know the operational concept to store. If you only know an ESRS/GRI/GHG code, inspect calculation dependencies or mappings first.",
        example=(
            "In Swagger, click **Try it out**, choose the "
            "`nordhaven_2024_energy_value` request example, or paste a JSON "
            'body with `concept` = `"urn:sds:reg:esrs:e1_5_12"`, '
            '`entity` = `"nh_group"`, `period` = `"2024-12-31"`, '
            '`value` = `390.604`, and `unit` = `"MWh"`; then click **Execute**.'
        ),
    ),
    ("/api/v1/values/import", "post"): OperationGuidance(
        purpose="Validate, normalize, and persist a small operational values batch as one atomic request.",
        how=(
            "Use this when several operational observations must be loaded "
            "together. Include stable `external_key` values so the same "
            "business observation can be updated deterministically."
        ),
        example=(
            "In Swagger, click **Try it out**, choose the "
            "`nordhaven_2024_energy_history` request example, confirm the "
            "`urn:sds:reg:esrs:e1_5_12` rows for `nh_group`, then click "
            "**Execute**. A successful "
            "response has "
            "`accepted_rows` = `2`, `rejected_rows` = `0`, and "
            "`committed` = `true`."
        ),
    ),
    ("/api/v1/values/import-csv", "post"): OperationGuidance(
        purpose="Upload one strict operational values CSV and persist it synchronously.",
        how=(
            "Use the strict CSV header `concept,entity,period,value,unit` plus "
            "optional columns such as `external_key`, `value_type`, "
            "`period_start`, and `period_end`. Only include `metadata_json` when "
            "the cell is valid JSON escaped for CSV."
        ),
        example=(
            "In Swagger, click **Try it out**, choose a CSV file with header "
            "`concept,entity,period,value,unit,external_key,value_type,"
            "period_start,period_end`, then click **Execute**. A successful "
            "response has `committed` = `true` and accepted row results."
        ),
    ),
    ("/api/v1/values/import-csv-jobs", "post"): OperationGuidance(
        purpose="Upload one strict operational values CSV as an asynchronous import job.",
        how=(
            "Use the same CSV contract as `/api/v1/values/import-csv`. Poll the "
            "returned job id with `/api/v1/values/import-jobs/{job_id}` until "
            "`status` is `completed` or `failed`."
        ),
        example=(
            "In Swagger, click **Try it out**, upload a values CSV with header "
            "`concept,entity,period,value,unit,external_key,value_type,"
            "period_start,period_end`, click **Execute**, then copy the returned "
            "`id` into `/api/v1/values/import-jobs/{job_id}`."
        ),
    ),
    ("/api/v1/calculate", "post"): OperationGuidance(
        purpose="Calculate an ESG indicator from stored inputs and return optional trace details.",
        how="Create or import the required input values first, then send concept, entity, period, and include_trace.",
        example=(
            "In Swagger, click **Try it out**, choose the "
            "`esrs_e1_5_fossil_energy_sum` request example, confirm "
            '`concept` = `"urn:sds:reg:esrs:e1_5_02"`, '
            '`entity` = `"nh_group"`, `period` = `"2024"`, and '
            "`include_trace` = `true`, then click **Execute**. The trace shows "
            "`e1_5_10 + e1_5_11 + e1_5_12 + e1_5_13 + e1_5_14`. For "
            "cross-standard or mapped-input calculations, check "
            "`source_value_ids` for the operational rows used and "
            "`framework_metadata.input_mappings` for the exact/equivalent "
            "mapping rows that supplied framework inputs. If a runtime "
            "contract explicitly authorizes non-equivalent component inputs, "
            "the response sets `execution_authority` = `certified_bridge` and "
            "`bridge_id` = the executing contract id."
        ),
    ),
    ("/api/v1/calculate/dependencies/{concept}", "get"): OperationGuidance(
        purpose="Discover the formula inputs SDS requires for a known indicator concept before entering operational values.",
        how=(
            "Start with the ESRS/GRI/GHG code the user knows. It may be a "
            "disclosure/concept or a granular datapoint. "
            "SDS returns the formula and `required_variables`; use those returned "
            "variables when checking or creating values. Inspect `input_bindings` "
            "for each local formula variable, unit, aggregation scope, and any "
            "authorized mapped-input relationships. When "
            "`supports_certified_bridge_inputs` is true, the calculation contract "
            "can use non-equivalent mapped component values only for the listed "
            "relationships."
        ),
        example=(
            "In Swagger, click **Try it out**, set `concept` = "
            f'`"{CSRD_E3_5_DISCLOSURE_URI}"`, and click **Execute**. '
            "The response lists the current DB-backed datapoint inputs, for "
            "example `urn:sds:reg:esrs:e3_5_01` when the local catalogue and "
            "values support that calculation."
        ),
    ),
    ("/api/v1/concepts", "get"): OperationGuidance(
        purpose="List the public semantic catalog with taxonomy, concept type, text search, and pagination filters.",
        how=(
            "Use `concept_type=Indicator` for operational SDS datapoints and "
            "GHG Protocol metrics. Use `concept_type=Disclosure` for "
            "source-standard disclosure/catalog nodes. "
            "`taxonomy=CSRD`, `taxonomy=GRI`, and `taxonomy=GHG` filter by "
            "source standard. "
            "Sygris is the unified SDS/Sygris public catalog view across source "
            "standards; returned rows retain their source taxonomy in the "
            "`taxonomy` field. Formula, dimensions, and related variables are "
            "populated only when a public calculation contract/projected formula "
            "backs the concept; source disclosure nodes may correctly show nulls."
        ),
        example=(
            "In Swagger, click **Try it out**, set `taxonomy` = `CSRD`, "
            "`concept_type` = `Disclosure`, `search` = `waste`, `limit` = `100`, "
            "and `offset` = `0`, then click **Execute**. Returned rows should "
            "all have `concept_type` = `Disclosure`. For a reproducible point-in-time "
            "read add `valid_as_of` and/or `decision_as_of` (alias `as_of_commit_id`); "
            "the response then carries a `pins` object. Omit them for latest published."
        ),
    ),
    ("/api/v1/semantic-dimensions", "get"): OperationGuidance(
        purpose=(
            "Browse the published semantic dimension catalog as a reproducible public "
            "temporal read (bitemporal valid-time + decision-time selectors)."
        ),
        how=(
            "This operation requires DB-backed canonical semantic data. "
            "Omit the selectors for the latest published catalog; the response still "
            "pins the resolved decision commit and valid-time slice so the read is "
            "reproducible. Supply `valid_as_of` and/or `decision_as_of` (alias "
            "`as_of_commit_id`) to read a past slice. `scope` selects the authorization "
            "scope class (public/shared/tenant). For the first page use `limit` and "
            "`offset`; when the response contains `next_cursor`, copy that value into "
            "`cursor` for the next page. Leave `cursor` blank for the first page."
        ),
        example=(
            "In Swagger, click **Try it out**, leave the selectors blank, set "
            "`scope` = `shared`, leave `cursor` blank, set `limit` = `100`, "
            "`offset` = `0`, and click "
            "**Execute**. Confirm the response `pins.resolved_decision_commit_id` and "
            "`pins.valid_time_slice` are populated. If `next_cursor` is non-null, "
            "copy it into `cursor` and execute again to continue the same pinned "
            "read. Use historical selectors only with valid times and commit ids "
            "available in your database."
        ),
    ),
    ("/api/v1/values/resolve", "post"): OperationGuidance(
        purpose=(
            "Resolve one ESG value through direct storage, calculation "
            "contracts, exact/equivalent mappings, certified bridge "
            "calculations, and supported unit conversions."
        ),
        how=(
            "Use this after values, mappings, and calculation contracts are "
            "loaded. If the user only knows an ESRS/GRI/GHG code, use "
            "dependency or mapping discovery first. It works for any indicator "
            "with sufficient runtime evidence; inspect status/method/trace to "
            "confirm whether SDS resolved or refused the route. When the "
            "resolver calculates through an approved bridge, "
            "`execution_authority` explains the route and `bridge_id` names "
            "the bridge contract."
        ),
        example=(
            "In Swagger, click **Try it out**, choose "
            "`resolve_csrd_to_gri_water`, confirm `source_concept` = "
            '`"csrd:E3-4_05"`, `target_concept` = `"gri:303-5.c"`, '
            '`entity` = `"nh_group"`, `period` = `"2024"`, '
            'and `target_unit` = `"m3"`, then click **Execute**. To test a '
            "calculated GRI value returned from authorized ESRS component "
            "inputs, choose `resolve_gri_302_1e_from_certified_bridge` and "
            "check `execution_authority` and `bridge_id` in the response."
        ),
    ),
    ("/api/v1/interoperability/readiness", "get"): OperationGuidance(
        purpose="Check whether the real SDS runtime has the catalogues, mappings, calculation contract, and unit behavior needed for operational API workflows.",
        how="Authorize as an analyst or admin, execute the check, and only run operational cross-standard proof steps when status is ready.",
        example=(
            "In Swagger, click **Try it out** and **Execute**. The response "
            "lists checks such as `semantic_catalogues_loaded`, "
            "`csrd_gri_exact_mapping_loaded`, and "
            "`csrd_disclosure_e3_5_calculation_contract_loaded`."
        ),
    ),
    ("/api/v1/hierarchies", "get"): OperationGuidance(
        purpose=(
            "List hierarchy configurations available to the current tenant so "
            "operators can see which entity perimeters are registered before "
            "loading or validating values."
        ),
        how=(
            "Use `company_id` to focus on one organization, `hierarchy_type` "
            "for organizational/temporal/geographical structures, `active` to "
            "separate usable configurations from drafts, and `limit`/`offset` "
            "for paging."
        ),
        example=(
            "In Swagger, click **Try it out**, set `company_id` = "
            '`"nordhaven_components_group"`, `hierarchy_type` = '
            '`"organizational"`, `active` = `true`, `limit` = `100`, '
            "and `offset` = `0`, then click **Execute**. Use an `id` from the "
            "response with `/api/v1/hierarchies/{hierarchy_id}`."
        ),
    ),
    ("/api/v1/hierarchies", "post"): OperationGuidance(
        purpose="Create an organizational or temporal hierarchy used to validate entity identifiers before value ingestion.",
        how=(
            "Create the entity perimeter before importing values in strict mode. "
            "Use the `nordhaven_operational_perimeter` request example when "
            "testing directly from Swagger."
        ),
        example=(
            "In Swagger, click **Try it out**, choose "
            "`nordhaven_operational_perimeter`, confirm `company_id` = "
            '`"nordhaven_components_group"` and one level has `id` = '
            '`"nh_br_sao_paulo_plant"`, '
            "then click **Execute**."
        ),
    ),
    ("/api/v1/hierarchies/{hierarchy_id}", "get"): OperationGuidance(
        purpose=(
            "Inspect one hierarchy configuration, including its levels and "
            "relationships, before using its entity ids in value ingestion or "
            "calculation requests."
        ),
        how=(
            "First list hierarchies, copy the returned `id`, paste it into "
            "`hierarchy_id`, and execute. A 404 means the id is unknown or not "
            "visible to the current tenant."
        ),
        example=(
            "In Swagger, run `GET /api/v1/hierarchies`, copy one response `id`, "
            "open this operation, paste it into `hierarchy_id`, and click "
            "**Execute** to review the stored configuration."
        ),
    ),
    ("/api/v1/hierarchies/{hierarchy_id}", "put"): OperationGuidance(
        purpose=(
            "Replace an existing hierarchy configuration when the registered "
            "entity perimeter, levels, or relationships need to change."
        ),
        how=(
            "Read the current hierarchy first, update the JSON body deliberately, "
            "and keep stable level ids for entities already referenced by stored "
            "values unless a controlled migration is intended."
        ),
        example=(
            "In Swagger, copy the `id` from `GET /api/v1/hierarchies`, paste it "
            "into `hierarchy_id`, provide the revised hierarchy JSON body, and "
            "click **Execute**. Re-read the same id to verify the stored shape."
        ),
    ),
    ("/api/v1/hierarchies/{hierarchy_id}", "delete"): OperationGuidance(
        purpose=(
            "Delete a hierarchy configuration that should no longer be available "
            "for validation or operational workflows."
        ),
        how=(
            "Use this only after confirming the hierarchy is obsolete. Read it "
            "first, confirm the `company_id` and levels, then delete by id."
        ),
        example=(
            "In Swagger, list or read the hierarchy, paste the confirmed `id` "
            "into `hierarchy_id`, and click **Execute**. A successful response "
            "returns the standard success envelope."
        ),
    ),
    ("/api/v1/hierarchies/{hierarchy_id}/activate", "post"): OperationGuidance(
        purpose=(
            "Mark a hierarchy configuration as the active one for its company "
            "so downstream reads and validation workflows use that perimeter."
        ),
        how=(
            "Create or update the hierarchy first, verify its levels, then "
            "activate it by id. List with `active=true` afterward to confirm "
            "which configuration is currently usable."
        ),
        example=(
            "In Swagger, paste the hierarchy `id` into `hierarchy_id`, click "
            "**Execute**, then run `GET /api/v1/hierarchies` with "
            "`active=true` and the same `company_id` to confirm activation."
        ),
    ),
    ("/api/v1/convert", "post"): OperationGuidance(
        purpose="Preview unit conversion using the SDS unit catalog without storing a value.",
        how="Send value, from_unit, and to_unit; use /api/v1/units first if you need the supported symbols.",
        example=(
            "In Swagger, click **Try it out**, choose the "
            "`kilowatt_hours_to_megawatt_hours` request example, or enter "
            '`value` = `1000`, `from_unit` = `"kWh"`, and '
            '`to_unit` = `"MWh"` in the JSON body, then click **Execute**.'
        ),
    ),
    ("/api/v1/fx/convert", "post"): OperationGuidance(
        purpose="Preview currency conversion with an explicit FX policy and trace.",
        how="Send the source/target currencies, value date, reporting period if needed, and fx_policy_id.",
        example=(
            "In Swagger, click **Try it out**, choose the "
            "`usd_to_eur_monthly_average_preview` "
            "request example, confirm `value` = `1250`, "
            '`from_currency` = `"USD"`, `to_currency` = `"EUR"`, '
            '`value_date` = `"2024-12-31"`, '
            '`period_start` = `"2024-12-01"`, '
            '`period_end` = `"2024-12-31"`, and '
            '`fx_policy_id` = `"ecb-reference-monthly-average"`, then click '
            "**Execute**."
        ),
    ),
    ("/api/v1/sparql", "post"): OperationGuidance(
        purpose=(
            "Validate a read-only SPARQL query shape. In DB-backed production "
            "semantics this endpoint intentionally fails closed with HTTP 501 "
            "because SPARQL execution over the canonical DB projection is not "
            "implemented yet."
        ),
        how=(
            "Use concept, taxonomy, equivalence, mapping, and indicator endpoints "
            "for supported DB-backed semantic reads. Only use this endpoint in "
            "local non-DB graph mode, or execute it in production expecting a "
            "clear 501 unsupported response."
        ),
        example=(
            "In Swagger, click **Try it out**, enter a read-only `SELECT` query, "
            "and click **Execute**. With DB-backed semantics enabled, a coherent "
            "response is HTTP `501` with `SPARQL over DB-backed semantics is not "
            "yet supported`."
        ),
    ),
    ("/api/v1/indicators/import-csv-validations", "post"): OperationGuidance(
        purpose="Validate a full SDS indicator register CSV without changing the live indicator catalog.",
        how=(
            "Upload the full register contract, not a shortened CSV. Required "
            "columns are `identifier,title,indicator,description,dimension,"
            "unitName,unitType,periodicity,periodType,sourceRef,codeESRS,"
            "codeGRI,codeGRI_expanded,evidencePath,sourceRow,owner,"
            "accessRights,validationMethod,doubleMateriality,valueType`."
        ),
        example=(
            "In Swagger, click **Try it out**, upload an indicator register CSV "
            "with all required columns, then click **Execute**. A valid response "
            "has `status` = `completed`, `accepted_rows` > `0`, "
            "`rejected_rows` = `0`, and `committed` = `false`."
        ),
    ),
    ("/api/v1/indicators/import-csv-jobs", "post"): OperationGuidance(
        purpose="Confirm a full SDS indicator register CSV import as an asynchronous all-or-nothing job.",
        how=(
            "Upload a CSV with the same full register header used by the "
            "validation endpoint, or submit a retained validation id. Poll "
            "`/api/v1/indicators/import-jobs/{job_id}` and inspect row errors "
            "with `/errors` if the job fails."
        ),
        example=(
            "In Swagger, click **Try it out**, upload the same full indicator "
            "register CSV used for validation, click **Execute**, then copy the "
            "returned `id` into `/api/v1/indicators/import-jobs/{job_id}` until "
            "the job reaches `completed` or `failed`."
        ),
    ),
    ("/api/v1/mappings/from/{standard}/{code}", "get"): OperationGuidance(
        purpose="Find canonical mappings that start from one external-standard code.",
        how="Set standard and code path parameters, then inspect the returned target standards and relationship metadata.",
        example=(
            "In Swagger, click **Try it out** and set "
            '`standard` = `"ESRS"`, `code` = `"E3-4_05"`, and '
            "`limit` = `10`; then click **Execute** to inspect mapped target "
            "standards."
        ),
    ),
    (
        "/api/v1/internal/canonical-mapping-packages/{package_id}/validations",
        "post",
    ): OperationGuidance(
        purpose="Internal/admin validation for canonical mapping packages before they can affect live mappings.",
        how="Use this only in controlled package promotion flows; validate installed-standard compatibility before import.",
        example=(
            "In Swagger, click **Try it out**, set the path field "
            "`package_id` to a package name available under the configured "
            "canonical mapping package roots, start from the request schema, "
            "and click **Execute**. If no package roots are configured, the "
            "coherent response is a controlled `403`."
        ),
        auth="Internal/admin operation requiring mapping write permission; not part of the normal public consumer flow.",
    ),
}


REQUEST_EXAMPLES: dict[tuple[str, str], dict[str, dict[str, Any]]] = {
    ("/api/v1/values", "post"): {
        "nordhaven_2024_energy_value": {
            "summary": "Nordhaven 2024 energy value",
            "description": (
                "Swagger Try it out JSON body using the loaded Nordhaven 2024 "
                "group energy observation."
            ),
            "value": {
                "concept": "urn:sds:reg:esrs:e1_5_12",
                "entity": "nh_group",
                "period": "2024-12-31",
                "period_start": "2024-01-01",
                "period_end": "2024-12-31",
                "external_key": "synthetic:sds-full-operational-v2:annual:f1453aa1d754e3ef7034",
                "value": 390.604,
                "value_type": "numeric",
                "unit": "MWh",
                "metadata": {"source": "nordhaven-operational-data"},
            },
        },
    },
    ("/api/v1/values/import", "post"): {
        "nordhaven_2024_energy_history": {
            "summary": "Nordhaven 2024 energy history",
            "description": (
                "Swagger JSON body using loaded Nordhaven group energy rows "
                "from the loaded Nordhaven operational dataset."
            ),
            "value": {
                "items": [
                    {
                        "concept": "urn:sds:reg:esrs:e1_5_12",
                        "entity": "nh_group",
                        "period": "2024-12-31",
                        "period_start": "2024-01-01",
                        "period_end": "2024-12-31",
                        "external_key": "synthetic:sds-full-operational-v2:annual:f1453aa1d754e3ef7034",
                        "value": 390.604,
                        "value_type": "numeric",
                        "unit": "MWh",
                        "metadata": {"source": "nordhaven-operational-data"},
                    },
                    {
                        "concept": "urn:sds:reg:esrs:e1_5_12",
                        "entity": "nh_group",
                        "period": "2023-12-31",
                        "period_start": "2023-01-01",
                        "period_end": "2023-12-31",
                        "external_key": "synthetic:sds-full-operational-v2:annual:ba88df153cdf8a1b8ece",
                        "value": 345.174,
                        "value_type": "numeric",
                        "unit": "MWh",
                        "metadata": {"source": "nordhaven-operational-data"},
                    },
                ]
            },
        },
        "energy_input": {
            "summary": "Energy input for unit conversion",
            "description": (
                "Swagger Try it out JSON body for the kWh to MWh resolution path."
            ),
            "value": {
                "items": [
                    {
                        "concept": "urn:sds:reg:esrs:e1_5_12",
                        "entity": "nh_br_sao_paulo_plant",
                        "period": "2024-12-31",
                        "period_start": "2024-01-01",
                        "period_end": "2024-12-31",
                        "external_key": "nordhaven:sao-paulo:esrs-e1-5-12:2024",
                        "value": 35.265,
                        "value_type": "numeric",
                        "unit": "MWh",
                        "metadata": {"source": "nordhaven-operational-data"},
                    }
                ]
            },
        },
    },
    ("/api/v1/hierarchies", "post"): {
        "nordhaven_operational_perimeter": {
            "summary": "Nordhaven operational perimeter",
            "description": (
                "Swagger Try it out JSON body for the Nordhaven group and one "
                "plant used by the values examples."
            ),
            "value": {
                "company_id": "nordhaven_components_group",
                "hierarchy_type": "organizational",
                "name": "Nordhaven Components Group operational perimeter",
                "description": "Traceable Nordhaven perimeter for API examples.",
                "levels": [
                    {
                        "id": "nh_group",
                        "name": "Nordhaven Components Group",
                        "level": 0,
                        "metadata": {"region": "global", "entity_type": "group"},
                    },
                    {
                        "id": "nh_br_sao_paulo_plant",
                        "name": "Nordhaven Sao Paulo Plant",
                        "parent": "nh_group",
                        "level": 1,
                        "metadata": {"country": "BR", "entity_type": "plant"},
                    },
                ],
                "active": True,
            },
        }
    },
    ("/api/v1/calculate", "post"): {
        "esrs_e1_5_fossil_energy_sum": {
            "summary": "Calculate ESRS E1-5 fossil energy sum",
            "description": (
                "Nordhaven 2024 JSON body for a multi-input traceable formula: "
                "`e1_5_10 + e1_5_11 + e1_5_12 + e1_5_13 + e1_5_14`."
            ),
            "value": {
                "concept": "urn:sds:reg:esrs:e1_5_02",
                "entity": "nh_group",
                "period": "2024",
                "granularity": "annual",
                "include_trace": True,
            },
        },
    },
    ("/api/v1/values/resolve", "post"): {
        "resolve_csrd_to_gri_water": {
            "summary": "Resolve CSRD water to GRI water",
            "description": (
                "Swagger body for resolving a value through an exact/equivalent mapping."
            ),
            "value": {
                "source_concept": "csrd:E3-4_05",
                "target_concept": "gri:303-5.c",
                "entity": "nh_group",
                "period": "2024",
                "granularity": "annual",
                "target_unit": "m3",
                "include_trace": True,
            },
        },
        "resolve_gri_302_1e_from_certified_bridge": {
            "summary": "Resolve GRI 302-1.e with certified bridge",
            "description": (
                "Swagger body for calculating the requested GRI 302-1.e value "
                "from authorized ESRS component inputs. A successful response "
                "should show `execution_authority` = `certified_bridge` and "
                "a `bridge_id` naming the approved calculation contract."
            ),
            "value": {
                "target_concept": "urn:sds:reg:gri:gri_302_1_e_total_energy_consumption_within_organization",
                "entity": "nh_group",
                "period": "2024",
                "granularity": "annual",
                "include_trace": True,
            },
        },
        "refuse_energy_to_emissions": {
            "summary": "Refuse energy to emissions without factor",
            "description": (
                "Swagger body showing that kWh to t CO2e needs an emissions-factor contract."
            ),
            "value": {
                "target_concept": "urn:sds:reg:esrs:e1_5_12",
                "entity": "nh_group",
                "period": "2024",
                "granularity": "annual",
                "target_unit": "t CO2e",
                "include_trace": True,
            },
        },
        "resolve_energy_kwh_to_mwh": {
            "summary": "Resolve energy with unit conversion",
            "description": (
                "Swagger body for same-dimension energy conversion without an emissions factor."
            ),
            "value": {
                "target_concept": "urn:sds:reg:esrs:e1_5_12",
                "entity": "nh_group",
                "period": "2024",
                "granularity": "annual",
                "target_unit": "MWh",
                "include_trace": True,
            },
        },
    },
    ("/api/v1/convert", "post"): {
        "kilowatt_hours_to_megawatt_hours": {
            "summary": "Kilowatt-hours to megawatt-hours",
            "description": "Swagger Try it out JSON body for energy unit conversion.",
            "value": {"value": 1000, "from_unit": "kWh", "to_unit": "MWh"},
        },
        "liters_to_cubic_meters": {
            "summary": "Liters to cubic meters",
            "description": "Swagger Try it out JSON body for a unit conversion preview.",
            "value": {"value": 500, "from_unit": "L", "to_unit": "m3"},
        },
    },
    ("/api/v1/fx/convert", "post"): {
        "usd_to_eur_monthly_average_preview": {
            "summary": "USD to EUR monthly-average preview",
            "description": (
                "Swagger Try it out JSON body for an FX conversion preview using "
                "the loaded ECB history orientation: foreign currency to EUR."
            ),
            "value": {
                "value": 1250,
                "from_currency": "USD",
                "to_currency": "EUR",
                "value_date": "2024-12-31",
                "period_start": "2024-12-01",
                "period_end": "2024-12-31",
                "fx_policy_id": "ecb-reference-monthly-average",
            },
        },
    },
    ("/api/v1/fx/rates/import", "post"): {
        "single_rate": {
            "summary": "One controlled FX rate",
            "description": "Swagger Try it out JSON body for one controlled FX rate row.",
            "value": {
                "provider": "SDS_MANUAL_FX",
                "rate_type": "reference",
                "base_currency": "USD",
                "source_hash": "0" * 64,
                "rows": [
                    {
                        "quote_currency": "EUR",
                        "rate_date": "2024-12-31",
                        "rate_value": 0.905,
                    }
                ],
            },
        },
    },
}


OPERATION_PARAMETER_EXAMPLES: Mapping[tuple[str, str], Mapping[str, Any] | None] = {
    ("/api/v1/values", "get"): {
        "entity": "nh_group",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "limit": 5,
    },
    ("/api/v1/values", "post"): {
        "Idempotency-Key": "nordhaven-energy-2024",
    },
    ("/api/v1/values/{value_id}", "get"): {
        "value_id": None,
    },
    ("/api/v1/values/{value_id}", "delete"): {
        "value_id": None,
    },
    ("/api/v1/values/revisions", "get"): {
        "tenant_id": "nordhaven_components_group",
        "state": "approved",
        "limit": 10,
        "offset": 0,
    },
    ("/api/v1/values/revisions/{revision_id}/lineage", "get"): {
        "revision_id": None,
    },
    ("/api/v1/values/contexts/{context_id}/lineage", "get"): {
        "context_id": None,
    },
    ("/api/v1/hierarchies/{hierarchy_id}", "get"): {
        "hierarchy_id": "sds-full-esrs-gri-v1-organizational",
    },
    ("/api/v1/hierarchies/{hierarchy_id}", "put"): {
        "hierarchy_id": None,
    },
    ("/api/v1/hierarchies/{hierarchy_id}", "delete"): {
        "hierarchy_id": None,
    },
    ("/api/v1/hierarchies/{hierarchy_id}/activate", "post"): {
        "hierarchy_id": None,
    },
    ("/api/v1/indicators/{indicator_id}", "get"): {
        "indicator_id": "urn:sds:reg:esrs:e1_5_12",
    },
    ("/api/v1/indicators/import-jobs/{job_id}/errors", "get"): {
        "job_id": None,
    },
    (
        "/api/v1/internal/canonical-mapping-packages/{package_id}/assertion-inspection",
        "get",
    ): {
        "package_id": None,
    },
    (
        "/api/v1/internal/canonical-mapping-packages/{package_id}/validations",
        "post",
    ): {
        "package_id": None,
    },
    ("/api/v1/mappings/from/{standard}/{code}", "get"): {
        "standard": "ESRS",
        "code": "E3-4_05",
        "limit": 10,
    },
    ("/api/v1/mappings/search", "get"): {
        "source_standard": "ESRS",
        "target_standard": "GRI",
        "target_code": "GRI 305",
        "limit": 10,
    },
    ("/api/v1/fx/rates/coverage", "get"): {
        "provider": "ECB",
        "rate_type": "reference",
        "base_currency": "USD",
        "quote_currency": "EUR",
        "start": "2024-12-01",
        "end": "2024-12-31",
    },
    ("/api/v1/concepts", "get"): {
        "taxonomy": "CSRD",
        "concept_type": "Disclosure",
        "search": "waste",
        "limit": 100,
        "offset": 0,
        # VARCH-8e: optional public temporal-read selectors (omit for latest published).
        "valid_as_of": None,
        "decision_as_of": None,
        "as_of_commit_id": None,
    },
    ("/api/v1/concepts/{concept_uri}", "get"): {
        "concept_uri": CSRD_E3_5_DISCLOSURE_URI,
        "valid_as_of": None,
        "decision_as_of": None,
        "as_of_commit_id": None,
    },
    # Database-specific pins and scopes cannot be executable static examples.
    ("/api/v1/semantic-dimensions", "get"): None,
}


PARAMETER_NAME_EXAMPLES: Mapping[str, Any] = {
    "Idempotency-Key": "nordhaven-energy-2024",
    # VARCH-8 public temporal-read selectors (semantic-dimensions + concepts).
    "as_of_commit_id": 1,
    "decision_as_of": 1,
    "scope": "shared",
    "valid_as_of": "2024-12-31T00:00:00Z",
    "active": True,
    "after_event_seq": 0,
    "base_currency": "USD",
    "category": "volume",
    "changed_since": "2024-01-01T00:00:00Z",
    "company_id": "nordhaven_components_group",
    "concept": CSRD_E3_5_DISCLOSURE_URI,
    "concept_type": "Indicator",
    "concepts": f"{CSRD_E3_5_DISCLOSURE_URI},gri:303-5.c",
    "dimension": "E",
    "end": "2024-12-31",
    "entity": "nh_group",
    "esrs": "E3",
    "format": "json",
    "from_unit": "L",
    "granularity": "annual",
    "gri": "303",
    "hierarchy_type": "organizational",
    "include_operational": False,
    "indicator_id": "urn:sds:reg:esrs:e1_5_12",
    "limit": 10,
    "min_confidence": 0.8,
    "offset": 0,
    "period": "2024",
    "period_end": "2024-12-31",
    "period_start": "2024-01-01",
    "provider": "ECB",
    "q": "water",
    "quote_currency": "EUR",
    "rate_type": "reference",
    "search": "water",
    "source_code": "E3-4_05",
    "source_taxonomy": "CSRD",
    "start": "2024-01-01",
    "state": "approved",
    "target_code": "303-5",
    "target_taxonomy": "GRI",
    "taxonomy": "CSRD",
    "tenant_id": "nordhaven_components_group",
    "to_unit": "m3",
    "unit": "L",
}


PATH_PARAMETER_EXAMPLES: Mapping[str, Any] = {
    "hierarchy_id": "sds-full-esrs-gri-v1-organizational",
    "concept": CSRD_E3_5_DISCLOSURE_URI,
    "concept_uri": CSRD_E3_5_DISCLOSURE_URI,
    "standard": "ESRS",
    "source_standard": "ESRS",
    "target_standard": "GRI",
    "code": "E3-4_05",
}


def enrich_openapi_schema(openapi_schema: dict[str, Any]) -> dict[str, Any]:
    """Mutate generated OpenAPI schema with compact human guidance."""
    for path, path_item in openapi_schema.get("paths", {}).items():
        for method, operation in list(path_item.items()):
            if method not in HTTP_METHODS:
                continue
            _enrich_operation(path, method, operation)
    return openapi_schema


def _enrich_operation(path: str, method: str, operation: dict[str, Any]) -> None:
    guidance = OPERATION_GUIDANCE.get((path, method)) or _default_guidance(
        path, method, operation
    )
    operation["description"] = _merge_description(
        operation.get("description") or "",
        guidance,
        _scope_note(path),
        _auth_note(operation, guidance.auth),
    )
    _apply_request_examples(path, method, operation)
    _apply_parameter_examples(path, method, operation)


def _default_guidance(
    path: str, method: str, operation: dict[str, Any]
) -> OperationGuidance:
    summary = (operation.get("summary") or f"{method.upper()} {path}").rstrip(".")
    tag = _primary_tag(operation)
    context = TAG_CONTEXT.get(tag, f"{tag} API workflows")
    purpose = f"{summary}. Use this operation for {context}."
    return OperationGuidance(
        purpose=purpose,
        how=_default_how(method, operation),
    )


def _merge_description(
    existing: str,
    guidance: OperationGuidance,
    scope_note: str | None,
    auth_note: str,
) -> str:
    if all(marker in existing for marker in GUIDANCE_MARKERS):
        return existing

    parts = [existing.strip()] if existing.strip() else []
    if scope_note:
        parts.append(scope_note)
    parts.extend(
        [
            f"### What it is for\n{guidance.purpose}",
            f"### How to use it\n{guidance.how}",
        ]
    )
    if guidance.example:
        parts.append(f"### Example\n{guidance.example}")
    parts.append(f"**Authentication:** {auth_note}")
    return "\n\n".join(parts)


def _scope_note(path: str) -> str | None:
    if path.startswith("/api/v1/internal/"):
        return (
            "Internal/admin: this endpoint supports controlled SDS administration "
            "and is not part of the normal public consumer flow."
        )
    return None


def _primary_tag(operation: dict[str, Any]) -> str:
    tags = operation.get("tags") or ["General"]
    return str(tags[0])


def _default_how(method: str, operation: dict[str, Any]) -> str:
    verb = method.upper()
    if verb == "GET":
        return (
            "Expand the operation, fill path or query parameters, authorize if a "
            "padlock is shown, then execute. Use filters and pagination to keep "
            "responses small."
        )
    if verb == "POST":
        if operation.get("requestBody"):
            return (
                "Expand the operation, start from the JSON example or schema, "
                "authorize if a padlock is shown, then execute. Review any 4xx "
                "validation details before retrying."
            )
        return (
            "Expand the operation, authorize if a padlock is shown, then execute. "
            "No JSON body is required."
        )
    if verb == "PUT":
        return (
            "Fetch the current resource first, adjust the JSON body, authorize, "
            "and execute the update."
        )
    if verb == "PATCH":
        return (
            "Send only the fields you want to change, authorize, and execute the "
            "partial update."
        )
    if verb == "DELETE":
        return (
            "Confirm the resource identifier, authorize with a role allowed to "
            "delete it, and execute once."
        )
    return "Expand the operation, provide the required inputs, then execute it."


def _auth_note(operation: dict[str, Any], override: str | None) -> str:
    if override:
        return override
    if operation.get("security"):
        return (
            "Use Authorize with a bearer access token from /auth/login before "
            "executing this operation."
        )
    return "No bearer token is normally required."


def _apply_request_examples(path: str, method: str, operation: dict[str, Any]) -> None:
    examples = REQUEST_EXAMPLES.get((path, method))
    if not examples:
        return
    content = (
        operation.get("requestBody", {}).get("content", {}).get("application/json")
    )
    if not isinstance(content, dict):
        return
    target = content.setdefault("examples", {})
    for name, example in examples.items():
        target[name] = example


def _apply_parameter_examples(
    path: str, method: str, operation: dict[str, Any]
) -> None:
    operation_examples = OPERATION_PARAMETER_EXAMPLES.get((path, method), {})
    for parameter in operation.get("parameters", []) or []:
        if not isinstance(parameter, dict):
            continue
        name = parameter.get("name")
        if operation_examples is None:
            _clear_parameter_examples(parameter)
        elif name in operation_examples:
            value = operation_examples[name]
            if value is None:
                _clear_parameter_examples(parameter)
            else:
                parameter["example"] = value
        elif operation_examples:
            _clear_parameter_examples(parameter)
        elif name in PARAMETER_NAME_EXAMPLES and "example" not in parameter:
            parameter["example"] = PARAMETER_NAME_EXAMPLES[name]
        elif name in PATH_PARAMETER_EXAMPLES and "example" not in parameter:
            parameter["example"] = PATH_PARAMETER_EXAMPLES[name]


def _clear_parameter_examples(parameter: dict[str, Any]) -> None:
    parameter.pop("example", None)
    parameter.pop("examples", None)
