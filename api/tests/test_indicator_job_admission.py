"""DB indicator submissions must stop before reading bodies or touching state."""

import json
import runpy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import formparsers
from starlette.requests import Request

from src.api import main
from src.api.middleware import IndicatorJobAdmissionMiddleware
from src.api.routers import indicators
from src.database.models import Indicator
from src.database.repositories.indicator_import_job_repository import (
    IndicatorImportJobRepository,
)
from src.services import indicator_import_job_runner
from src.services.indicator_import_job_store import (
    DatabaseIndicatorImportJobStore,
    InMemoryIndicatorImportJobStore,
)

PATH = "/api/v1/indicators/import-csv-jobs"
DETAIL = "Database-backed async indicator import jobs are unsupported pending H15"
CSV = (
    b"identifier,title,indicator,description,dimension,unitName,unitType,periodicity,"
    b"periodType,sourceRef,codeESRS,codeGRI,codeGRI_expanded,evidencePath,sourceRow,"
    b"owner,accessRights,validationMethod,doubleMateriality,valueType\n"
    b"urn:sds:reg:custom:energy_001,Energy,Energy,Annual energy,E,kWh,Energy,annual,"
    b"fiscal_year,internal-source,E1-5,,,,1,system,Internal,import,,numeric\n"
)


@pytest.fixture
def no_admission_effects(monkeypatch):
    monkeypatch.setattr(indicators.settings, "require_database", True)
    forbidden = Mock(side_effect=AssertionError("Unexpected admission effect"))
    # Trap the actual policy callable, not only authentication. Token headers,
    # missing tokens and malformed bodies must all stop before dependencies.
    route = next(route for route in main.app.routes if route.path == PATH)
    for dependency in route.dependant.dependencies:
        monkeypatch.setitem(main.app.dependency_overrides, dependency.call, forbidden)

    targets = [
        (Request, "body"),
        (Request, "json"),
        (Request, "_get_form"),
        (formparsers.MultiPartParser, "parse"),
        (formparsers, "SpooledTemporaryFile"),
        (BackgroundTasks, "add_task"),
        (indicators, "_read_limited_csv_upload"),
        (indicators, "validate_indicator_import_text"),
        (indicators, "run_indicator_import_job"),
        (indicators.IndicatorRepository, "bulk_upsert"),
        (indicator_import_job_runner, "SessionLocal"),
        (indicator_import_job_runner, "apply_indicator_import_transactional"),
    ]
    for cls in (DatabaseIndicatorImportJobStore, InMemoryIndicatorImportJobStore):
        targets.extend(
            (cls, name)
            for name in (
                "create",
                "get",
                "get_source_payload",
                "acquire_import_submission_lock",
                "recover_stale_active_imports",
                "has_active_import",
                "mark_running",
                "complete",
                "fail",
            )
        )
    targets.extend(
        (IndicatorImportJobRepository, name)
        for name in (
            "create",
            "get",
            "get_source_payload",
            "acquire_import_submission_lock",
            "fail_stale_active_imports",
            "has_active_import",
            "mark_running",
            "complete",
            "fail",
        )
    )
    for target, name in targets:
        monkeypatch.setattr(target, name, forbidden)

    async def forbidden_stream(self):
        # BaseHTTPMiddleware constructs the iterator but must never consume it.
        forbidden()
        yield b""  # pragma: no cover

    monkeypatch.setattr(Request, "stream", forbidden_stream)
    yield
    forbidden.assert_not_called()


@pytest.mark.parametrize("suffix", ["", "/"])
@pytest.mark.parametrize("authorization", [None, "Bearer invalid", "Bearer test-admin"])
@pytest.mark.parametrize(
    "body",
    [
        {"files": {"file": ("indicators.csv", CSV, "text/csv")}},
        {"data": {"validation_id": "completed-validation"}},
        {"files": {"validation_id": (None, "completed-validation")}},
        {"files": {"file": ("invalid.csv", b"\xff", "text/csv")}},
        {"content": b"broken", "headers": {"Content-Type": "multipart/form-data"}},
        {
            "content": b'{"validation_id":',
            "headers": {"Content-Type": "application/json"},
        },
        {"data": {"validation_id": "missing-or-expired"}},
        {"files": {"file": ("indicators.csv", CSV)}, "data": {"validation_id": "both"}},
        {"content": b""},
    ],
    ids=[
        "csv",
        "validation-form",
        "validation-multipart",
        "invalid-encoding",
        "malformed-multipart",
        "malformed-json",
        "unknown-validation",
        "both",
        "empty",
    ],
)
def test_db_rejection_precedes_all_admission_effects(
    no_admission_effects, suffix, authorization, body
):
    body = dict(body)
    headers = dict(body.pop("headers", {}))
    if authorization:
        headers["Authorization"] = authorization
    headers["Idempotency-Key"] = "h15-repeated-indicator-submission"
    client = TestClient(main.app)  # Do not initialize DB lifespan.
    for _ in range(2):
        response = client.post(PATH + suffix, headers=headers, **body)
        assert response.status_code == 503
        assert response.json() == {"detail": DETAIL}
        assert response.headers["content-type"] == "application/json"
        assert "location" not in response.headers
        assert "retry-after" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/"])
@pytest.mark.parametrize(
    "content_type,body",
    [
        (b"application/json", b'{"validation_id":'),
        (b"application/x-www-form-urlencoded", b"validation_id=retained"),
        (b"multipart/form-data", b"missing boundary"),
        (
            b"multipart/form-data; boundary=h15",
            b'--h15\r\nContent-Disposition: form-data; name="file"; '
            b'filename="large.csv"\r\nContent-Type: text/csv\r\n\r\n'
            + b"x" * (2 * 1024 * 1024)
            + b"\r\n--h15--\r\n",
        ),
    ],
)
async def test_real_app_never_receives_body(
    no_admission_effects, suffix, content_type, body
):
    receive = AsyncMock(side_effect=AssertionError("ASGI receive must not be called"))
    send = AsyncMock()
    await main.app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "method": "POST",
            "path": PATH + suffix,
            "query_string": b"dry_run=true",
            "headers": [
                (b"content-type", content_type),
                (b"content-length", str(len(body)).encode()),
            ],
        },
        receive,
        send,
    )
    receive.assert_not_called()
    messages = [call.args[0] for call in send.call_args_list]
    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 503
    assert json.loads(b"".join(m.get("body", b"") for m in messages[1:])) == {
        "detail": DETAIL
    }


@pytest.mark.parametrize(
    "origin,allowed",
    [("https://allowed.example", True), ("https://denied.example", False)],
)
def test_rejection_keeps_cors_security_and_request_headers(
    monkeypatch, no_admission_effects, origin, allowed
):
    monkeypatch.setattr(
        indicators.settings, "allowed_origins", ["https://allowed.example"]
    )
    configured_app = runpy.run_path(main.__file__)["app"]
    configured_app.dependency_overrides.update(main.app.dependency_overrides)
    client = TestClient(configured_app)
    response = client.post(
        PATH,
        content=b"malformed",
        headers={
            "Origin": origin,
            "Content-Type": "multipart/form-data",
            "X-Request-ID": "h15-indicator-headers",
        },
    )
    assert response.status_code == 503
    assert response.json() == {"detail": DETAIL}
    assert response.headers["content-type"] == "application/json"
    assert response.headers.get("access-control-allow-origin") == (
        origin if allowed else None
    )
    assert (
        response.headers["strict-transport-security"]
        == "max-age=31536000; includeSubDomains"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    from uuid import UUID

    assert (
        str(UUID(response.headers["x-request-id"])) == response.headers["x-request-id"]
    )
    preflight = client.options(
        PATH,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert preflight.status_code == (200 if allowed else 400)


@pytest.mark.parametrize("db_mode", [True, False])
def test_openapi_json_503_contract(monkeypatch, db_mode):
    monkeypatch.setattr(indicators.settings, "require_database", db_mode)
    response = TestClient(main.app).get("/openapi.json")
    assert response.status_code == 200
    operation = response.json()["paths"][PATH]["post"]
    unavailable = operation["responses"]["503"]
    assert set(unavailable["content"]) == {"application/json"}
    content = unavailable["content"]["application/json"]
    assert content["schema"] == {
        "type": "object",
        "properties": {"detail": {"type": "string"}},
        "required": ["detail"],
    }
    assert content["example"] == {"detail": DETAIL}
    assert "DB mode returns `503` pending H15" in operation["description"]
    assert "`validation_id`" in operation["description"]
    assert "H15 remains open" in operation["description"]
    assert "202" in operation["responses"]  # Preserve the existing non-strict contract.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,scope",
    [
        (False, {"type": "http", "method": "POST", "path": PATH + suffix})
        for suffix in ("", "/")
    ]
    + [
        (True, {"type": "http", "method": method, "path": path})
        for method, path in (
            ("GET", PATH),
            ("OPTIONS", PATH),
            ("POST", PATH + "/extra"),
            ("POST", "/api/v1/indicators/import-csv-validations"),
            ("GET", "/api/v1/indicators/import-jobs/existing"),
            ("GET", "/api/v1/indicators/import-jobs/existing/errors"),
            ("POST", "/api/v1/values/import-csv"),
        )
    ]
    + [(True, {"type": "lifespan"}), (True, {"type": "websocket", "path": PATH})],
)
async def test_other_scopes_pass_through(monkeypatch, mode, scope):
    monkeypatch.setattr(indicators.settings, "require_database", mode)
    downstream, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
    await IndicatorJobAdmissionMiddleware(downstream)(scope, receive, send)
    downstream.assert_awaited_once_with(scope, receive, send)
    receive.assert_not_called()
    send.assert_not_called()


@pytest.mark.parametrize("db_mode", [True, False])
def test_synchronous_validation_and_existing_job_reads_remain_available(
    monkeypatch, db_mode
):
    monkeypatch.setattr(indicators.settings, "require_database", db_mode)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Indicator.__table__.create(engine)
    store = InMemoryIndicatorImportJobStore()
    with sessionmaker(bind=engine)() as db:
        app = FastAPI()
        app.add_middleware(IndicatorJobAdmissionMiddleware)
        app.include_router(indicators.router, prefix="/api/v1/indicators")
        app.dependency_overrides[indicators.get_db_optional] = lambda: (
            db if db_mode else None
        )
        app.dependency_overrides[indicators.get_indicator_import_job_store] = (
            lambda: store
        )
        for route in app.routes:
            if getattr(route, "dependant", None):
                for dep in route.dependant.dependencies:
                    if dep.name == "user":
                        app.dependency_overrides[dep.call] = lambda: SimpleNamespace(
                            username="admin", id="admin-id", company_id="tenant-a"
                        )
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/indicators/import-csv-validations",
                files={"file": ("indicators.csv", CSV, "text/csv")},
            )
            assert response.status_code == 200, response.text
            job = response.json()
            assert job["status"] == "completed"
            assert job["result_body"]["valid"] is True
            assert job["committed"] is False
            assert job["request_metadata"]["catalog_compared"] is db_mode
            job_path = f'/api/v1/indicators/import-jobs/{job["id"]}'
            assert client.get(job_path).json() == job
            assert client.get(job_path + "/errors").json()["total_errors"] == 0
            assert db.query(Indicator).count() == 0
            if not db_mode:
                response = client.post(PATH, data={"validation_id": job["id"]})
                assert response.status_code == 503
                assert response.json() == {
                    "detail": "Indicator imports require database-backed mode."
                }
    engine.dispose()
