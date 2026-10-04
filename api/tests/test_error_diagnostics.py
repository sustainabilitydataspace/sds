"""Sanitized admin error diagnostics (no database)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from src.api.main import app
from src.api.rate_limit import limiter
from src.auth.jwt_handler import jwt_handler
from src.auth.models import APIKeyCreate, Permission, UserCreate, UserRole
from src.calculation.unit_converter import UnitConversionError
from src.database.session import get_db_optional
from src.services import admin_catalog, error_diagnostics
from src.services.api_key_store import InMemoryAPIKeyStore, get_api_key_store
from src.services.user_store import InMemoryUserStore, get_user_store
from src.services.value_ingest import ValueIngestError

SECRET = (
    "SECRET-7f3a sk-live-SECRET postgresql-dsn SECRET-host SECRET-pw Bearer eyJSECRET"
)
ALLOWED_TRIGGER = "value source identity is immutable"


class _Diag:
    def __init__(self, **fields):
        self.message_primary = fields.get("message_primary")
        self.constraint_name = fields.get("constraint_name")
        self.table_name = fields.get("table_name")
        self.column_name = fields.get("column_name")


class _PgError(Exception):
    def __init__(self, pgcode, **diag):
        super().__init__(SECRET)
        self.pgcode = pgcode
        self.diag = _Diag(**diag)


def _db_error(kind, pgcode, **diag):
    return kind(
        "INSERT INTO esg_values VALUES (%(value)s)",
        {"value": SECRET},
        _PgError(pgcode, **diag),
    )


def _raised(exc):
    """Raise through a function under api/src so frames are recorded."""
    try:
        try:
            admin_catalog._decode_cursor("%%%")
        except admin_catalog.AdminCatalogError as inner:
            raise exc from inner
    except BaseException as outer:
        return outer


class _Model(BaseModel):
    number: int


def _validation_error():
    try:
        _Model(number=SECRET)
    except Exception as exc:
        return exc


def _request(route="/api/v1/values"):
    request = MagicMock()
    request.state.request_id = "req-1"
    request.method = "POST"
    request.scope = {"route": MagicMock(path=route)}
    return request


CASES = [
    (
        lambda: _db_error(
            IntegrityError, "23505", constraint_name="uq_x", table_name="esg_values"
        ),
        "db_unique_violation",
    ),
    (lambda: _db_error(IntegrityError, "23503"), "db_foreign_key_violation"),
    (
        lambda: _db_error(IntegrityError, "23502", column_name="unit"),
        "db_not_null_violation",
    ),
    (lambda: _db_error(IntegrityError, "23514"), "db_check_violation"),
    (
        lambda: _db_error(OperationalError, "P0001", message_primary=ALLOWED_TRIGGER),
        "db_trigger_exception",
    ),
    (lambda: _db_error(OperationalError, "40P01"), "db_serialization_failure"),
    (lambda: _db_error(OperationalError, "57014"), "db_statement_timeout"),
    (lambda: _db_error(OperationalError, "08006"), "db_connection_error"),
    (lambda: _db_error(OperationalError, None), "db_connection_error"),
    (lambda: _db_error(IntegrityError, "22P02"), "db_other"),
    (lambda: PoolTimeoutError("QueuePool " + SECRET), "db_connection_error"),
    (lambda: UnitConversionError(SECRET), "unit_conversion_error"),
    (lambda: ValueIngestError(500, SECRET), "value_ingest_error"),
    (_validation_error, "validation_error"),
    (lambda: ValueError(SECRET), "validation_error"),
    (lambda: TimeoutError(SECRET), "timeout"),
    (lambda: RuntimeError(SECRET), "unknown"),
]


class TestRecord:
    @pytest.mark.parametrize("factory,expected", CASES)
    def test_classification_and_redaction(self, factory, expected):
        record = error_diagnostics.build_record(_request(), _raised(factory()), 500)
        assert record["classification"] == expected
        serialized = json.dumps(record)
        assert "SECRET" not in serialized
        assert "INSERT" not in serialized
        assert record["request_id"] == "req-1"
        assert record["route_template"] == "/api/v1/values"

    def test_db_fields_are_allow_listed(self):
        record = error_diagnostics.build_record(
            _request(),
            _raised(
                _db_error(
                    IntegrityError,
                    "23505",
                    constraint_name="uq_x",
                    table_name="esg_values",
                    column_name="external_key",
                    message_primary=SECRET,
                )
            ),
            500,
        )
        assert record["db"] == {
            "pgcode": "23505",
            "constraint_name": "uq_x",
            "table_name": "esg_values",
            "column_name": "external_key",
            "trigger_message": None,
        }

    def test_only_static_trigger_messages_are_kept(self):
        assert ALLOWED_TRIGGER in error_diagnostics.TRIGGER_MESSAGES
        assert all("%" not in m for m in error_diagnostics.TRIGGER_MESSAGES)
        kept = error_diagnostics.build_record(
            _request(),
            _db_error(OperationalError, "P0001", message_primary=ALLOWED_TRIGGER),
            500,
        )
        assert kept["db"]["trigger_message"] == ALLOWED_TRIGGER
        dropped = error_diagnostics.build_record(
            _request(),
            _db_error(OperationalError, "P0001", message_primary="leak " + SECRET),
            500,
        )
        assert dropped["classification"] == "db_trigger_exception"
        assert dropped["db"]["trigger_message"] is None

    def test_chain_types_and_repo_relative_frames(self):
        record = error_diagnostics.build_record(
            _request(), _raised(RuntimeError(SECRET)), 500
        )
        assert record["exception_types"] == [
            "builtins.RuntimeError",
            "src.services.admin_catalog.AdminCatalogError",
        ]
        assert record["frames"]
        for frame in record["frames"]:
            assert set(frame) == {"exception", "file", "line", "function"}
            assert frame["file"].startswith("src/")
        assert any(
            frame["file"] == "src/services/admin_catalog.py"
            and frame["function"] == "_decode_cursor"
            for frame in record["frames"]
        )

    def test_cyclic_chain_is_bounded(self):
        first, second = RuntimeError("a"), ValueError("b")
        first.__context__ = second
        second.__context__ = first
        record = error_diagnostics.build_record(_request(), first, 500)
        assert len(record["exception_types"]) == 2

    def test_capture_is_fail_open(self, monkeypatch):
        def broken():
            raise OperationalError("connect", {}, _PgError("08006"))

        monkeypatch.setattr(error_diagnostics, "session_factory", broken)
        record = error_diagnostics.record_error_diagnostic(
            _request(), RuntimeError(SECRET)
        )
        assert record["classification"] == "unknown"
        assert error_diagnostics.store_record(record) is False

    def test_connection_failures_are_logged_but_not_stored(self, monkeypatch):
        store = MagicMock()
        monkeypatch.setattr(error_diagnostics, "store_record", store)
        record = error_diagnostics.record_error_diagnostic(
            _request(), PoolTimeoutError("QueuePool " + SECRET)
        )
        assert record["classification"] == "db_connection_error"
        store.assert_not_called()

    def test_build_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            error_diagnostics, "build_record", MagicMock(side_effect=KeyError("x"))
        )
        assert error_diagnostics.record_error_diagnostic(None, RuntimeError()) is None


# --------------------------------------------------------------------------
# Capture points
# --------------------------------------------------------------------------


@pytest.fixture
def captured(monkeypatch):
    records = []
    monkeypatch.setattr(
        error_diagnostics, "store_record", lambda record: records.append(record)
    )
    return records


VALUE = {
    "concept": "syg:Water_Cooling",
    "entity": "madrid_plant",
    "period": "2024-01-15",
    "value": 1500.5,
    "unit": "L",
}


def _assert_generic_500(response, records, output, route):
    assert response.status_code == 500
    assert response.json()["error"]["message"] == "Internal server error"
    assert len(records) == 1
    record = records[0]
    assert record["route_template"] == route
    assert record["request_id"] in response.text
    for text in (response.text, json.dumps(record), output):
        assert "SECRET" not in text


def test_create_value_failure_is_recorded(client, data_manager_token, captured, capsys):
    failure = RuntimeError(SECRET)
    failure.__cause__ = _db_error(IntegrityError, "23505", constraint_name="uq_x")
    payload = dict(VALUE, entity="marker-entity-7c1", concept="syg:marker-concept-7c1")
    with patch("src.api.routers.values.ingest_value", side_effect=failure):
        response = client.post(
            "/api/v1/values",
            json=payload,
            headers={"Authorization": f"Bearer {data_manager_token}"},
        )
    output = capsys.readouterr().out
    failure_lines = [
        line
        for line in output.splitlines()
        if "Failed to create value" in line or "error_diagnostic" in line
    ]
    assert len(failure_lines) == 2
    for line in failure_lines:
        assert "marker-entity-7c1" not in line
        assert "marker-concept-7c1" not in line
    _assert_generic_500(response, captured, output, "/api/v1/values")
    assert captured[0]["classification"] == "db_unique_violation"
    assert captured[0]["db"]["constraint_name"] == "uq_x"
    assert "marker" not in json.dumps(captured[0])


def test_unhandled_exception_is_recorded(client, data_manager_token, captured, capsys):
    with patch(
        "src.api.routers.values.execute_value_batch",
        side_effect=UnitConversionError(SECRET),
    ):
        response = client.post(
            "/api/v1/values/import",
            json={"items": [VALUE]},
            headers={"Authorization": f"Bearer {data_manager_token}"},
        )
    _assert_generic_500(
        response, captured, capsys.readouterr().out, "/api/v1/values/import"
    )
    assert captured[0]["classification"] == "unit_conversion_error"


# --------------------------------------------------------------------------
# Admin read routes
# --------------------------------------------------------------------------

ROUTES = [
    "/api/v1/admin/diagnostics/errors/req-1",
    "/api/v1/admin/diagnostics/errors?limit=5",
]


@pytest.fixture
def stores():
    limiter._storage.reset()
    users = InMemoryUserStore()
    for name, role in (
        ("admin", UserRole.ADMIN),
        ("manager", UserRole.DATA_MANAGER),
        ("analyst", UserRole.ANALYST),
    ):
        users.create_user(
            UserCreate(
                username=name,
                email=f"{name}@example.com",
                password="Sufficient-Pass-2026",
                role=role,
                company_id="tenant-a",
            )
        )
    keys = InMemoryAPIKeyStore()
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_api_key_store] = lambda: keys
    app.dependency_overrides[get_db_optional] = lambda: MagicMock()
    record = {"request_id": "req-1", "classification": "unknown"}
    with patch.multiple(
        error_diagnostics,
        get_error_diagnostic=MagicMock(
            side_effect=lambda db, rid: record if rid == "req-1" else None
        ),
        list_error_diagnostics=MagicMock(return_value=[record]),
    ):
        yield users, keys
    for dependency in (get_user_store, get_api_key_store, get_db_optional):
        app.dependency_overrides.pop(dependency, None)
    limiter._storage.reset()


def _bearer(users, username):
    user = users.get_user(username=username)
    token = jwt_handler.create_access_token(
        user_id=user.id,
        username=user.username,
        role=user.role,
        company_id=user.company_id,
        auth_version=user.auth_version,
    )
    return {"Authorization": f"Bearer {token}"}


class TestReadRoutes:
    @pytest.mark.parametrize("url", ROUTES)
    def test_admin_bearer_is_allowed(self, stores, url):
        users, _ = stores
        response = TestClient(app).get(url, headers=_bearer(users, "admin"))
        assert response.status_code == 200, response.text
        assert "req-1" in response.text

    @pytest.mark.parametrize("username", ["manager", "analyst"])
    @pytest.mark.parametrize("url", ROUTES)
    def test_other_roles_are_forbidden(self, stores, url, username):
        users, _ = stores
        response = TestClient(app).get(url, headers=_bearer(users, username))
        assert response.status_code == 403

    @pytest.mark.parametrize("url", ROUTES)
    def test_admin_api_key_is_forbidden(self, stores, url):
        users, keys = stores
        admin = users.get_user(username="admin")
        key = keys.create_api_key(
            user_id=admin.id,
            request=APIKeyCreate(name="ops", permissions=[Permission.MANAGE_SYSTEM]),
        ).key
        response = TestClient(app).get(url, headers={"X-API-Key": key})
        assert response.status_code == 403

    @pytest.mark.parametrize("url", ROUTES)
    def test_missing_credentials_are_rejected(self, stores, url):
        assert TestClient(app).get(url).status_code == 403

    @pytest.mark.parametrize("request_id", ["unknown-id", "x" * 100])
    def test_absent_records_are_a_uniform_404(self, stores, request_id):
        users, _ = stores
        response = TestClient(app).get(
            f"/api/v1/admin/diagnostics/errors/{request_id}",
            headers=_bearer(users, "admin"),
        )
        assert response.status_code == 404
        assert request_id not in response.text
