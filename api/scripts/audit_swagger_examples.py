"""Execute complete Swagger/OpenAPI examples against a running SDS API.

The audit focuses on examples a Swagger user can run directly:
- named JSON request-body examples;
- parameter examples for operations without a required request body.

It also validates JSON responses against the documented 2xx response schemas
when a schema is available.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from jsonschema import Draft202012Validator

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
PLACEHOLDER_MARKERS = (
    "your_",
    "paste_",
    "<",
    ">",
    "string",
    "00000000-0000-4000-8000-",
)


@dataclass
class AuditCase:
    method: str
    path: str
    case_name: str
    status_code: int | None = None
    result: str = "pending"
    detail: str = ""
    url: str = ""
    response_preview: Any = None


@dataclass
class SkipCase:
    method: str
    path: str
    reason: str


def _load_schema(base_url: str) -> dict[str, Any]:
    response = requests.get(f"{base_url.rstrip('/')}/openapi.json", timeout=30)
    response.raise_for_status()
    return response.json()


def _login(base_url: str, username: str, password: str) -> dict[str, str]:
    response = requests.post(
        f"{base_url.rstrip('/')}/auth/login",
        json={"username": username, "password": password},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
    }


def _preview(response: requests.Response) -> Any:
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        try:
            return response.json()
        except ValueError:
            return response.text[:500]
    return response.text[:500]


def _is_placeholder(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_is_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_is_placeholder(item) for item in value)
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _parameters(operation: dict[str, Any]) -> list[dict[str, Any]]:
    return list(operation.get("parameters") or [])


def _body_content(operation: dict[str, Any]) -> dict[str, Any]:
    return (operation.get("requestBody") or {}).get("content") or {}


def _request_body_required(operation: dict[str, Any]) -> bool:
    return bool((operation.get("requestBody") or {}).get("required"))


def _resolved_schema_example(
    media_schema: dict[str, Any], openapi_schema: dict[str, Any]
) -> list[tuple[str, Any]]:
    examples: list[tuple[str, Any]] = []
    schemas = [media_schema]
    ref = media_schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        name = ref.rsplit("/", 1)[-1]
        resolved = (openapi_schema.get("components") or {}).get("schemas", {}).get(name)
        if isinstance(resolved, dict):
            schemas.append(resolved)

    for schema in schemas:
        if "example" in schema:
            examples.append(("__schema_example__", schema["example"]))
        schema_examples = schema.get("examples")
        if isinstance(schema_examples, list):
            for index, example in enumerate(schema_examples):
                examples.append((f"__schema_examples_{index}__", example))
        elif isinstance(schema_examples, dict):
            for name, example in schema_examples.items():
                if isinstance(example, dict) and "value" in example:
                    examples.append((str(name), example["value"]))
                else:
                    examples.append((str(name), example))
    return examples


def _json_body_examples(
    operation: dict[str, Any], openapi_schema: dict[str, Any]
) -> list[tuple[str, Any]]:
    media = _body_content(operation).get("application/json") or {}
    examples: list[tuple[str, Any]] = []
    for name, example in (media.get("examples") or {}).items():
        examples.append((name, example.get("value")))
    if "example" in media:
        examples.append(("__media_example__", media["example"]))
    if not examples:
        examples.extend(
            _resolved_schema_example(media.get("schema") or {}, openapi_schema)
        )
    return examples


def _param_value(parameter: dict[str, Any]) -> tuple[bool, Any]:
    if "example" in parameter:
        return True, parameter["example"]
    schema = parameter.get("schema") or {}
    if "default" in schema:
        return True, schema["default"]
    return False, None


def _request_parts(
    path_template: str,
    operation: dict[str, Any],
    base_headers: dict[str, str],
) -> tuple[str | None, dict[str, Any], dict[str, str], str | None]:
    path = path_template
    params: dict[str, Any] = {}
    headers = dict(base_headers)
    for parameter in _parameters(operation):
        has_value, value = _param_value(parameter)
        location = parameter.get("in")
        name = parameter.get("name")
        if parameter.get("required") and not has_value:
            return (
                None,
                {},
                {},
                f"missing required {location} parameter example: {name}",
            )
        if not has_value:
            continue
        if location == "path":
            path = path.replace("{" + name + "}", quote(str(value), safe=""))
        elif location == "query":
            params[name] = value
        elif location == "header":
            headers[name] = str(value)
    if "{" in path or "}" in path:
        return None, {}, {}, "missing path parameter example"
    return path, params, headers, None


def _matching_response_schema(
    operation: dict[str, Any], response: requests.Response
) -> dict[str, Any] | None:
    responses = operation.get("responses") or {}
    response_spec = responses.get(str(response.status_code))
    if response_spec is None and 200 <= response.status_code < 300:
        response_spec = (
            responses.get("200") or responses.get("201") or responses.get("204")
        )
    if not response_spec:
        return None

    content = response_spec.get("content") or {}
    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    media = content.get(content_type) or content.get("application/json")
    if not media:
        return None
    return media.get("schema")


def _validate_response(
    schema: dict[str, Any],
    operation: dict[str, Any],
    response: requests.Response,
    root_validator: Draft202012Validator,
) -> str:
    if not (200 <= response.status_code < 300):
        return f"HTTP {response.status_code}"
    if response.status_code == 204:
        return ""

    content_type = response.headers.get("content-type", "")
    if "json" not in content_type:
        return ""

    try:
        payload = response.json()
    except ValueError as exc:
        return f"response is not valid JSON: {exc}"

    response_schema = _matching_response_schema(operation, response)
    if not response_schema:
        return ""

    validator = root_validator.evolve(schema=response_schema)
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    if not errors:
        return ""
    error = errors[0]
    location = "/".join(map(str, error.path)) or "$"
    return f"response schema mismatch at {location}: {error.message}"


def _semantic_validation_error(path_template: str, response: requests.Response) -> str:
    if path_template != "/api/v1/values/resolve":
        return ""
    content_type = response.headers.get("content-type", "")
    if "json" not in content_type:
        return ""
    try:
        payload = response.json()
    except ValueError:
        return ""
    status_value = payload.get("status")
    if status_value != "resolved":
        return f"value resolver example returned status={status_value!r}"
    return ""


def _execute_case(
    *,
    base_url: str,
    schema: dict[str, Any],
    path_template: str,
    method: str,
    operation: dict[str, Any],
    case_name: str,
    body: Any | None,
    access_token: str | None,
) -> AuditCase:
    security_required = bool(operation.get("security"))
    headers: dict[str, str] = {}
    if security_required and access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    path, params, headers, error = _request_parts(path_template, operation, headers)
    case = AuditCase(method=method.upper(), path=path_template, case_name=case_name)
    if error:
        case.result = "skipped"
        case.detail = error
        return case

    if body is not None and _is_placeholder(body):
        case.result = "placeholder"
        case.detail = "request example contains placeholder values"
        return case

    url = f"{base_url.rstrip('/')}{path}"
    case.url = url
    request_kwargs: dict[str, Any] = {
        "params": params,
        "headers": headers,
        "timeout": 30,
    }
    if body is not None:
        request_kwargs["json"] = body

    try:
        response = requests.request(method.upper(), url, **request_kwargs)
    except requests.RequestException as exc:
        case.result = "error"
        case.detail = str(exc)
        return case

    case.status_code = response.status_code
    case.response_preview = _preview(response)

    root_validator = Draft202012Validator(schema)
    validation_error = _validate_response(schema, operation, response, root_validator)
    if validation_error:
        case.result = "failed"
        case.detail = validation_error
    elif semantic_error := _semantic_validation_error(path_template, response):
        case.result = "failed"
        case.detail = semantic_error
    else:
        case.result = "passed"
    return case


def _build_cases(
    schema: dict[str, Any],
) -> tuple[list[tuple[str, str, dict[str, Any], str, Any | None]], list[SkipCase]]:
    cases: list[tuple[str, str, dict[str, Any], str, Any | None]] = []
    skips: list[SkipCase] = []

    for path, item in schema.get("paths", {}).items():
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue

            json_examples = _json_body_examples(operation, schema)
            if json_examples:
                for example_name, body in json_examples:
                    cases.append((path, method, operation, example_name, body))
                continue

            if _request_body_required(operation):
                skips.append(
                    SkipCase(
                        method=method.upper(),
                        path=path,
                        reason="required request body has no runnable JSON example",
                    )
                )
                continue

            has_param_examples = any("example" in p for p in _parameters(operation))
            if has_param_examples:
                cases.append((path, method, operation, "parameter_examples", None))

    return cases, skips


def _case_sort_key(
    case: tuple[str, str, dict[str, Any], str, Any | None],
) -> tuple[int, str, str, str]:
    path, method, _operation, case_name, _body = case
    priority = 50
    if path == "/api/v1/hierarchies" and method == "post":
        priority = 5
    elif path == "/api/v1/values/import":
        priority = 10
    elif path == "/api/v1/values" and method == "post":
        priority = 15
    elif path == "/api/v1/calculate":
        priority = 30
    elif path == "/api/v1/values/resolve":
        priority = 35
    return (priority, path, method, case_name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin123")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/swagger-example-audit.json"),
    )
    args = parser.parse_args()

    schema = _load_schema(args.base_url)
    tokens = _login(args.base_url, args.username, args.password)
    cases, skips = _build_cases(schema)
    cases.sort(key=_case_sort_key)

    results: list[AuditCase] = []
    for path, method, operation, case_name, body in cases:
        results.append(
            _execute_case(
                base_url=args.base_url,
                schema=schema,
                path_template=path,
                method=method,
                operation=operation,
                case_name=case_name,
                body=body,
                access_token=tokens["access_token"],
            )
        )

    summary = {
        "base_url": args.base_url,
        "total_cases": len(results),
        "passed": sum(1 for result in results if result.result == "passed"),
        "failed": sum(1 for result in results if result.result == "failed"),
        "placeholder": sum(1 for result in results if result.result == "placeholder"),
        "error": sum(1 for result in results if result.result == "error"),
        "skipped_candidates": sum(
            1 for result in results if result.result == "skipped"
        ),
        "skipped_incomplete": len(skips),
    }
    report = {
        "summary": summary,
        "results": [asdict(result) for result in results],
        "skipped": [asdict(skip) for skip in skips],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )

    print(json.dumps(summary, indent=2, sort_keys=True))
    for result in results:
        if result.result != "passed":
            print(
                f"{result.result.upper()} {result.method} {result.path} "
                f"[{result.case_name}] status={result.status_code} {result.detail}"
            )
    if skips:
        print(f"SKIPPED incomplete operations: {len(skips)}")

    return (
        0
        if summary["failed"] == 0
        and summary["error"] == 0
        and summary["placeholder"] == 0
        and summary["skipped_candidates"] == 0
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
