"""DB async value submissions must fail before admission or execution effects."""

import json
import runpy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient
from starlette import formparsers
from starlette.requests import Request

from src.api import main
from src.api.main import app as main_app
from src.api.middleware import ValueJobAdmissionMiddleware
from src.api.models import ValueBulkImportResponse
from src.api.routers import values
from src.auth.dependencies import get_current_active_user
from src.auth.models import Permission
from src.services import value_import_job_runner
from src.services.value_idempotency_store import DatabaseValueIdempotencyStore
from src.services.value_import_job_store import DatabaseValueImportJobStore


@pytest.fixture
def db_app(monkeypatch):
    monkeypatch.setattr(values.settings, "require_database", True)
    app = FastAPI()
    app.add_middleware(ValueJobAdmissionMiddleware)
    app.include_router(values.router, prefix="/api/v1")
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        id="user-1",
        username="submitter",
        company_id="tenant-a",
        role=SimpleNamespace(value="editor"),
        permissions=[Permission.CREATE_VALUES, Permission.READ_VALUES],
    )
    return app


@pytest.fixture
def no_admission_effects(db_app, monkeypatch):
    effects = []

    def forbidden_dependency():
        effects.append("store/session dependency")
        pytest.fail("DB rejection must precede store/session resolution")

    for dependency in (
        values.get_db_optional,
        values.get_value_import_job_store,
        values.get_value_idempotency_store,
    ):
        db_app.dependency_overrides[dependency] = forbidden_dependency
        monkeypatch.setitem(
            main_app.dependency_overrides, dependency, forbidden_dependency
        )

    spies = []
    for target, name in (
        (Request, "body"),
        (Request, "json"),
        (Request, "_get_form"),
        (formparsers.MultiPartParser, "parse"),
        (formparsers, "SpooledTemporaryFile"),
        (DatabaseValueIdempotencyStore, "claim"),
        (DatabaseValueIdempotencyStore, "complete"),
        (DatabaseValueImportJobStore, "create"),
        (BackgroundTasks, "add_task"),
        (values, "_claim_idempotency"),
        (values, "_read_bounded_csv_upload"),
        (values, "load_values_from_handle"),
        (values, "run_value_import_job"),
        (values, "execute_value_batch"),
        (value_import_job_runner, "execute_value_batch"),
    ):
        spy = Mock(side_effect=AssertionError(f"Unexpected effect: {name}"))
        monkeypatch.setattr(target, name, spy)
        spies.append(spy)

    async def forbidden_stream(self):
        # BaseHTTPMiddleware constructs the iterator without consuming it.
        # Fail on iteration, which is the first possible body read.
        effects.append("request stream consumption")
        pytest.fail("DB rejection must precede request stream consumption")
        yield b""  # pragma: no cover

    monkeypatch.setattr(Request, "stream", forbidden_stream)

    yield
    assert effects == []
    for spy in spies:
        spy.assert_not_called()


@pytest.mark.parametrize("idempotency_key", [None, "h15-repeated-submission"])
@pytest.mark.parametrize(
    "path,body",
    [
        (
            "/values/import-jobs",
            {
                "json": {
                    "items": [
                        {
                            "concept": "syg:Water_Cooling",
                            "entity": "test_plant",
                            "period": "2024-01-15",
                            "value": 1,
                            "unit": "m3",
                        }
                    ]
                }
            },
        ),
        ("/values/import-jobs", {"json": {"items": []}}),
        (
            "/values/import-jobs",
            {"content": b'{"items":', "headers": {"Content-Type": "application/json"}},
        ),
        (
            "/values/import-csv-jobs",
            {
                "content": b"malformed",
                "headers": {"Content-Type": "multipart/form-data"},
            },
        ),
        (
            "/values/import-csv-jobs",
            {
                "files": {
                    "file": (
                        "values.csv",
                        b"concept,entity,period,value,unit\n"
                        b"syg:Water_Cooling,test_plant,2024-01-15,1,m3\n",
                        "text/csv",
                    )
                }
            },
        ),
        (
            "/values/import-csv-jobs",
            {"files": {"file": ("invalid.csv", b"\xff", "text/csv")}},
        ),
    ],
    ids=[
        "json",
        "invalid-json-items",
        "malformed-json",
        "malformed-multipart",
        "csv",
        "invalid-csv-encoding",
    ],
)
def test_db_value_jobs_reject_before_all_admission_effects(
    db_app, no_admission_effects, path, body, idempotency_key
):
    body = dict(body)
    headers = dict(body.pop("headers", {}))
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    with TestClient(db_app) as client:
        # A repeated key must not replay a historical 202 or acquire a new claim.
        for _ in range(2):
            response = client.post(f"/api/v1{path}", headers=headers, **body)
            assert response.status_code == 503
            assert response.json() == {
                "detail": (
                    "Database-backed async value import jobs are unsupported pending H15"
                )
            }


def test_db_value_job_status_is_unavailable_until_durable_jobs_are_qualified(
    db_app, no_admission_effects
) -> None:
    with TestClient(db_app) as client:
        response = client.get("/api/v1/values/import-jobs/job-1")

    assert response.status_code == 503
    assert "unsupported pending H15" in response.json()["detail"]


@pytest.mark.parametrize(
    "path,content_type",
    [
        ("/api/v1/values/import-jobs", "application/json"),
        ("/api/v1/values/import-csv-jobs", "multipart/form-data"),
    ],
)
def test_db_value_job_rejection_keeps_cors_and_security_headers(
    monkeypatch, no_admission_effects, path, content_type
):
    origin = "https://allowed.example"
    monkeypatch.setattr(values.settings, "allowed_origins", [origin])
    # Re-run actual app registration with CORS configured, without replacing
    # the shared main app or starting the DB lifespan.
    configured_app = runpy.run_path(main.__file__)["app"]
    configured_app.dependency_overrides.update(main_app.dependency_overrides)
    client = TestClient(configured_app)
    response = client.post(
        path,
        content=b"malformed",
        headers={
            "Origin": origin,
            "Content-Type": content_type,
            "X-Request-ID": "h15-header-regression",
        },
    )
    assert response.status_code == 503
    assert response.json() == {
        "detail": "Database-backed async value import jobs are unsupported pending H15"
    }
    assert response.headers["content-type"] == "application/json"
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    from uuid import UUID

    assert (
        str(UUID(response.headers["x-request-id"])) == response.headers["x-request-id"]
    )


@pytest.mark.parametrize("source_format", ["json", "csv"])
def test_db_synchronous_imports_still_reach_strict_execution(
    db_app, monkeypatch, source_format
):
    for dependency in (
        values.get_db_optional,
        values.get_unit_converter,
        values.get_authenticated_value_store,
        values.get_value_idempotency_store,
        values.get_hierarchy_store,
        values.get_ontology_graph,
    ):
        db_app.dependency_overrides[dependency] = lambda: object()
    monkeypatch.setattr(values, "build_conversion_engine", lambda *_args: object())
    result = ValueBulkImportResponse(
        total_rows=1, accepted_rows=1, rejected_rows=0, committed=True, items=[]
    )
    execute = Mock(return_value=result)
    monkeypatch.setattr(values, "execute_value_batch", execute)
    item = dict(
        concept="syg:Water_Cooling",
        entity="test_plant",
        period="2024-01-15",
        value=1,
        unit="m3",
    )
    if source_format == "json":
        path = "/api/v1/values/import"
        body = {"json": {"items": [item]}}
    else:
        path = "/api/v1/values/import-csv"
        csv = ",".join(item) + "\n" + ",".join(map(str, item.values())) + "\n"
        body = {"files": {"file": ("values.csv", csv.encode(), "text/csv")}}
    with TestClient(db_app) as client:
        response = client.post(path, **body)
    assert response.status_code == 201, response.text
    assert response.json() == result.model_dump(mode="json")
    execute.assert_called_once()
    assert execute.call_args.kwargs["strict"] is True
    assert execute.call_args.kwargs["company_id"] == "tenant-a"
    assert len(execute.call_args.kwargs["items"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,content_type,body",
    [
        ("/api/v1/values/import-jobs", b"application/json", b'{"items":'),
        (
            "/api/v1/values/import-csv-jobs",
            b"multipart/form-data; boundary=h15",
            b'--h15\r\nContent-Disposition: form-data; name="file"; '
            b'filename="large.csv"\r\nContent-Type: text/csv\r\n\r\n'
            + b"x" * (2 * 1024 * 1024)
            + b"\r\n--h15--\r\n",
        ),
    ],
    ids=["malformed-json", "multipart-exceeding-spool-threshold"],
)
async def test_real_app_rejects_without_receiving_body(
    no_admission_effects, path, content_type, body
):
    # No lifespan: this request must be rejected even without DB initialization.
    # The receive spy holds a real malformed JSON / large multipart payload.
    receive = AsyncMock(
        return_value={"type": "http.request", "body": body, "more_body": False}
    )
    send = AsyncMock()
    await main_app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "method": "POST",
            "path": path,
            "query_string": b"dry_run=true",
            "headers": [(b"content-type", content_type)],
        },
        receive,
        send,
    )
    receive.assert_not_called()
    messages = [call.args[0] for call in send.call_args_list]
    assert messages[0]["status"] == 503
    assert json.loads(messages[1]["body"]) == {
        "detail": "Database-backed async value import jobs are unsupported pending H15"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,scope",
    [
        (False, {"type": "http", "method": "POST", "path": path})
        for path in ("/api/v1/values/import-jobs", "/api/v1/values/import-csv-jobs")
    ]
    + [
        (True, {"type": "http", "method": method, "path": path})
        for method, path in (
            ("GET", "/api/v1/values/import-jobs"),
            ("OPTIONS", "/api/v1/values/import-csv-jobs"),
            ("POST", "/api/v1/values/import-jobs/"),
            ("POST", "/api/v1/values/import-csv-jobs/extra"),
            ("POST", "/api/v1/values/import"),
            ("POST", "/api/v1/values/import-csv"),
            ("POST", "/api/v1/indicators/import-csv-jobs"),
        )
    ]
    + [(True, {"type": "lifespan"}), (True, {"type": "websocket"})],
)
async def test_admission_passes_other_scopes_through(monkeypatch, mode, scope):
    monkeypatch.setattr(values.settings, "require_database", mode)
    downstream = AsyncMock()
    receive, send = AsyncMock(), AsyncMock()
    await ValueJobAdmissionMiddleware(downstream)(scope, receive, send)
    downstream.assert_awaited_once_with(scope, receive, send)
    receive.assert_not_called()
    send.assert_not_called()
