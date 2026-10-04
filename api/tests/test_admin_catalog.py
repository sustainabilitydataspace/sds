"""Admin calculation-contract import and unit-catalog repair (no database).

Database-level repair, locking and cross-process freshness are covered by
``test_admin_catalog_postgres.py`` on a disposable PostgreSQL.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.rate_limit import limiter
from src.auth.jwt_handler import jwt_handler
from src.auth.models import APIKeyCreate, Permission, UserCreate, UserRole
from src.calculation import unit_converter as uc
from src.calculation.conversion.dimensions import DimensionVector
from src.calculation.conversion.physical import (
    PhysicalUnit,
    PhysicalUnitConverter,
    analyze_expression_registry,
)
from src.calculation.postgres_strategy import PostgresStrategy
from src.database import models as db_models
from src.database.session import get_db_optional
from src.services import admin_catalog
from src.services.api_key_store import InMemoryAPIKeyStore, get_api_key_store
from src.services.calculation_contract_import import (
    import_calculation_contract_bytes,
    import_calculation_contract_file,
)
from src.services.user_store import InMemoryUserStore, get_user_store
from tests.test_calculation_contract_import import FIXTURE_PACKAGE_DIR, _FakeSession

VOLUME = DimensionVector.of(length=3)
CONTRACT_FILE = FIXTURE_PACKAGE_DIR / "sds_calculation_contract.json"


def _unit(symbol, aliases=(), factor="1", dimension=VOLUME):
    return PhysicalUnit(
        symbol=symbol,
        name=symbol,
        dimension=dimension,
        factor_to_base=Decimal(factor),
        aliases=tuple(aliases),
    )


# --------------------------------------------------------------------------
# Shared analyzer
# --------------------------------------------------------------------------


class TestAnalyzer:
    def test_m3_duplicate_is_an_expression_token_conflict(self):
        units = {
            "m³": _unit("m³", ["m3", "cubic_meter"]),
            "m3": _unit("m3"),
        }
        analysis = analyze_expression_registry(units)
        assert [c.kind for c in analysis.conflicts] == ["expression_token"]
        conflict = analysis.conflicts[0]
        assert conflict.token == "m3"
        assert set(conflict.symbols) == {"m³", "m3"}
        assert (
            conflict.message
            == "ambiguous expression unit token: m3 maps to both 'm³' and 'm3'"
        )

    def test_converter_fails_closed_with_the_analyzer_message(self):
        units = {"m³": _unit("m³", ["m3"]), "m3": _unit("m3")}
        with pytest.raises(ValueError) as exc:
            PhysicalUnitConverter(units=units, rules=[])
        assert str(exc.value) == analyze_expression_registry(units).conflicts[0].message

    def test_canonical_symbol_precedence_is_not_a_conflict(self):
        units = {
            "mmHg": _unit("mmHg", ["torr"], dimension=DimensionVector.dimensionless()),
            "torr": _unit("torr", dimension=DimensionVector.dimensionless()),
        }
        assert analyze_expression_registry(units).conflicts == ()

    def test_clean_catalog_builds(self):
        units = {"m³": _unit("m³", ["m3"]), "L": _unit("L", factor="0.001")}
        assert analyze_expression_registry(units).conflicts == ()
        PhysicalUnitConverter(units=units, rules=[])


# --------------------------------------------------------------------------
# Catalog freshness guard
# --------------------------------------------------------------------------


class TestCatalogFreshness:
    def test_every_public_method_is_guarded_or_a_reload(self):
        public = {
            name
            for name, value in vars(uc.UnitConverter).items()
            if callable(value) and not name.startswith("_")
        }
        assert public == set(uc.CATALOG_FRESH_METHODS) | uc.CATALOG_RELOAD_METHODS
        for name in uc.CATALOG_FRESH_METHODS:
            assert getattr(getattr(uc.UnitConverter, name), "__catalog_fresh__", False)

    @pytest.mark.parametrize("name", uc.CATALOG_FRESH_METHODS)
    def test_guard_runs_before_each_public_method(self, name):
        converter = uc.UnitConverter()
        calls = []

        def guard():
            calls.append(name)
            raise RuntimeError("guard")

        converter._ensure_catalog_fresh = guard
        with pytest.raises(RuntimeError, match="guard"):
            getattr(converter, name)()
        assert calls == [name]

    def test_json_mode_never_queries_a_revision(self):
        converter = uc.UnitConverter()
        assert converter._revision_tracked() is False
        converter._last_revision_check = 0
        with patch.object(converter, "_read_catalog_revision") as read:
            converter.normalize_unit_symbol("kg")
        read.assert_not_called()

    def _tracked(self, revisions):
        converter = uc.UnitConverter()
        converter._storage_strategy = MagicMock(spec=PostgresStrategy)
        converter._catalog_revision = 1
        values = iter(revisions)

        def read():
            value = next(values)
            if isinstance(value, Exception):
                raise value
            return value

        converter._read_catalog_revision = read
        converter.reload_from_storage = MagicMock()
        return converter

    def test_revision_change_reloads_before_serving(self):
        converter = self._tracked([2])
        converter._last_revision_check = 0
        converter._ensure_catalog_fresh()
        converter.reload_from_storage.assert_called_once()

    def test_unchanged_revision_does_not_reload(self):
        converter = self._tracked([1])
        converter._last_revision_check = 0
        converter._ensure_catalog_fresh()
        converter.reload_from_storage.assert_not_called()

    def test_checks_at_most_once_per_second(self):
        converter = self._tracked([1, 2])
        converter._last_revision_check = 0
        converter._ensure_catalog_fresh()
        converter._ensure_catalog_fresh()
        converter.reload_from_storage.assert_not_called()

    def test_revision_failure_fails_closed(self):
        converter = self._tracked([uc.UnitConversionError("revision unavailable")])
        converter._last_revision_check = 0
        with pytest.raises(uc.UnitConversionError):
            converter._ensure_catalog_fresh()


# --------------------------------------------------------------------------
# Contract service (fake session shared with the existing import tests)
# --------------------------------------------------------------------------


def _fixture_session():
    import csv

    indicators = []
    with (FIXTURE_PACKAGE_DIR / "sds_dataset_register.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        for row in csv.DictReader(handle):
            indicators.append(
                db_models.Indicator(
                    id=row["identifier"],
                    identifier=row["identifier"],
                    title=row["title"],
                    dimension=row["dimension"],
                    concept_state=db_models.ConceptState.CATALOGUED,
                )
            )
    session = _FakeSession(indicators)
    bind = MagicMock()
    bind.dialect.name = "sqlite"
    session.get_bind = lambda: bind
    return session


class TestContractService:
    def test_bytes_path_matches_cli_file_path(self):
        from_file = import_calculation_contract_file(
            contract_path=CONTRACT_FILE, db=_fixture_session(), dry_run=True
        )
        from_bytes = import_calculation_contract_bytes(
            CONTRACT_FILE.read_bytes(), db=_fixture_session(), dry_run=True
        )
        assert from_bytes.package_hash == from_file.package_hash
        assert from_bytes.contract_sha256 == from_file.contract_sha256
        assert from_bytes.counts == from_file.counts

    def test_commit_false_leaves_the_transaction_to_the_caller(self):
        session = _fixture_session()
        report = import_calculation_contract_bytes(
            CONTRACT_FILE.read_bytes(), db=session, commit=False
        )
        assert report.status == "completed"
        assert session.committed is False

    def test_admin_import_is_idempotent_and_audited(self):
        session = _fixture_session()
        actor = admin_catalog.Actor("user_admin", "bearer")
        first = admin_catalog.import_contract(
            session, CONTRACT_FILE.read_bytes(), actor, retirement_scope="incoming_keys"
        )
        assert first.status == "completed" and first.committed is True
        contracts = len(session.rows[db_models.CanonicalCalculationContract])
        second = admin_catalog.import_contract(
            session, CONTRACT_FILE.read_bytes(), actor, retirement_scope="incoming_keys"
        )
        assert second.status == "already_imported" and second.committed is False
        assert len(session.rows[db_models.CanonicalCalculationContract]) == contracts
        audit = session.rows[db_models.AdminCatalogOperation]
        assert [row.result for row in audit] == ["ok", "already_imported"]
        assert all(row.package_hash == first.package_hash for row in audit)

    def test_invalid_payload_is_rejected_without_rows(self):
        session = _fixture_session()
        with pytest.raises(admin_catalog.AdminCatalogError) as exc:
            admin_catalog.import_contract(
                session,
                b'{"nodes": []}',
                admin_catalog.Actor("user_admin", "bearer"),
                retirement_scope="incoming_keys",
            )
        assert exc.value.status_code == 422
        assert session.rows[db_models.CanonicalCalculationContract] == []

    def test_advisory_lock_key_is_deterministic_signed_64_bit(self):
        key = admin_catalog.advisory_lock_key("a" * 64)
        assert key == admin_catalog.advisory_lock_key("a" * 64)
        assert key != admin_catalog.advisory_lock_key("b" * 64)
        assert -(2**63) <= key < 2**63
        digest = hashlib.sha256(b"sds-contract-import:" + b"a" * 64).digest()
        assert key == int.from_bytes(digest[:8], "big", signed=True)

    def test_tampered_plan_digest_is_refused_before_any_query(self):
        db = MagicMock()
        plan = {"repair_id": "x" * 32, "expires_at": "2999-01-01T00:00:00+00:00"}
        with pytest.raises(admin_catalog.AdminCatalogError) as exc:
            admin_catalog.commit_repair(
                db, plan=plan, digest="0" * 64, actor=admin_catalog.Actor("a", "bearer")
            )
        assert exc.value.status_code == 422
        db.query.assert_not_called()

    def _signed(self, **changes):
        from datetime import datetime, timedelta, timezone

        plan = {
            "repair_id": "a" * 32,
            "conflict_id": "b" * 64,
            "deactivate_unit_id": 2,
            "retain_unit_id": 1,
            "reason": "duplicate",
            "catalog_revision": 0,
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=5)
            ).isoformat(),
        }
        plan.update(changes)
        return plan

    def test_plain_sha256_digest_is_not_accepted(self):
        import json as _json

        db = MagicMock()
        plan = self._signed()
        forged = hashlib.sha256(
            _json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with pytest.raises(admin_catalog.AdminCatalogError) as exc:
            admin_catalog.commit_repair(
                db, plan=plan, digest=forged, actor=admin_catalog.Actor("a", "bearer")
            )
        assert exc.value.status_code == 422
        db.query.assert_not_called()

    @pytest.mark.parametrize(
        "changes",
        [
            {"reason": "R" * 501},
            {"reason": 7},
            {"repair_id": "not-hex"},
            {"deactivate_unit_id": "2"},
            {"expires_at": "2999-01-01T00:00:00+00:00"},
            {"expires_at": "2026-01-01T00:00:00"},
        ],
    )
    def test_server_signed_but_out_of_bounds_plans_are_refused(self, changes):
        db = MagicMock()
        plan = self._signed(**changes)
        with pytest.raises(admin_catalog.AdminCatalogError) as exc:
            admin_catalog.commit_repair(
                db,
                plan=plan,
                digest=admin_catalog.plan_digest(plan),
                actor=admin_catalog.Actor("a", "bearer"),
            )
        assert exc.value.status_code == 422
        db.query.assert_not_called()

    def test_unparseable_import_is_audited_without_values(self):
        session = _fixture_session()
        with pytest.raises(admin_catalog.AdminCatalogError):
            admin_catalog.import_contract(
                session,
                b'{"MARKER-x": ',
                admin_catalog.Actor("user_admin", "bearer"),
                retirement_scope="incoming_keys",
            )
        rows = session.rows[db_models.AdminCatalogOperation]
        assert [(r.action, r.result, r.detail) for r in rows] == [
            ("contract_import", "rejected", "validation_error")
        ]
        assert "MARKER-x" not in repr(vars(rows[0]))


# --------------------------------------------------------------------------
# Router authorization and input handling
# --------------------------------------------------------------------------

ROUTES = [
    ("post", "/api/v1/admin/calculation-contracts/validations", None),
    ("post", "/api/v1/admin/calculation-contracts/imports?confirm=true", None),
    ("get", "/api/v1/admin/unit-catalog/conflicts", None),
    (
        "post",
        "/api/v1/admin/unit-catalog/repairs/preview",
        {
            "conflict_id": "a" * 64,
            "deactivate_unit_id": 2,
            "retain_unit_id": 1,
            "reason": "duplicate m3",
        },
    ),
    (
        "post",
        "/api/v1/admin/unit-catalog/repairs/commit?confirm=true",
        {"plan": {}, "plan_digest": "a" * 64},
    ),
    (
        "post",
        f"/api/v1/admin/unit-catalog/repairs/{'a' * 32}/reverse?confirm=true",
        None,
    ),
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
    db = MagicMock()
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_api_key_store] = lambda: keys
    app.dependency_overrides[get_db_optional] = lambda: db
    yield users, keys, db
    for dependency in (get_user_store, get_api_key_store, get_db_optional):
        app.dependency_overrides.pop(dependency, None)
    limiter._storage.reset()


@pytest.fixture
def client(stores):
    return TestClient(app)


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


def _call(client, method, url, body, headers, content=None):
    kwargs = {"headers": headers}
    if content is not None:
        kwargs["content"] = content
    elif body is not None:
        kwargs["json"] = body
    elif method == "post":
        kwargs["content"] = b"{}"
    return getattr(client, method)(url, **kwargs)


@pytest.fixture
def service_stub():
    outcome = admin_catalog.ContractOutcome("completed", "h" * 64, "s" * 64, True, {})
    with patch.multiple(
        admin_catalog,
        validate_contract=MagicMock(return_value=outcome),
        import_contract=MagicMock(return_value=outcome),
        list_conflicts=MagicMock(
            return_value={"catalog_revision": 0, "total": 0, "items": []}
        ),
        preview_repair=MagicMock(return_value={"plan": {}, "plan_digest": "d"}),
        commit_repair=MagicMock(return_value={"repair_id": "r", "catalog_revision": 1}),
        reverse_repair=MagicMock(
            return_value={"repair_id": "r", "catalog_revision": 2}
        ),
    ):
        yield


class TestAuthorization:
    @pytest.mark.parametrize("method,url,body", ROUTES)
    def test_admin_bearer_is_allowed(
        self, client, stores, service_stub, method, url, body
    ):
        users, _, _ = stores
        response = _call(client, method, url, body, _bearer(users, "admin"))
        assert response.status_code == 200

    @pytest.mark.parametrize("username", ["manager", "analyst"])
    @pytest.mark.parametrize("method,url,body", ROUTES)
    def test_other_roles_are_forbidden(
        self, client, stores, service_stub, method, url, body, username
    ):
        users, _, _ = stores
        response = _call(client, method, url, body, _bearer(users, username))
        assert response.status_code == 403

    @pytest.mark.parametrize("method,url,body", ROUTES)
    def test_admin_api_key_with_manage_system_is_forbidden(
        self, client, stores, service_stub, method, url, body
    ):
        users, keys, _ = stores
        admin = users.get_user(username="admin")
        key = keys.create_api_key(
            user_id=admin.id,
            request=APIKeyCreate(name="ops", permissions=[Permission.MANAGE_SYSTEM]),
        ).key
        response = _call(client, method, url, body, {"X-API-Key": key})
        assert response.status_code == 403

    @pytest.mark.parametrize("method,url,body", ROUTES)
    def test_missing_credentials_are_rejected(
        self, client, stores, service_stub, method, url, body
    ):
        response = _call(client, method, url, body, {})
        assert response.status_code == 403


class TestInputHandling:
    @pytest.mark.parametrize(
        "url",
        [
            "/api/v1/admin/calculation-contracts/imports",
            "/api/v1/admin/unit-catalog/repairs/commit",
            f"/api/v1/admin/unit-catalog/repairs/{'a' * 32}/reverse",
        ],
    )
    def test_writes_require_confirm(self, client, stores, service_stub, url):
        users, _, _ = stores
        body = {"plan": {}, "plan_digest": "a" * 64} if "commit" in url else None
        response = _call(client, "post", url, body, _bearer(users, "admin"))
        assert response.status_code == 400

    def test_unknown_retirement_scope_is_422(self, client, stores, service_stub):
        users, _, _ = stores
        response = client.post(
            "/api/v1/admin/calculation-contracts/imports?confirm=true"
            "&retirement_scope=everything",
            content=b"{}",
            headers=_bearer(users, "admin"),
        )
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "body",
        [
            {
                "conflict_id": "short",
                "deactivate_unit_id": 2,
                "retain_unit_id": 1,
                "reason": "x",
            },
            {
                "conflict_id": "A" * 64,
                "deactivate_unit_id": 2,
                "retain_unit_id": 1,
                "reason": "x",
            },
            {
                "conflict_id": "a" * 64,
                "deactivate_unit_id": "two",
                "retain_unit_id": 1,
                "reason": "x",
            },
            {
                "conflict_id": "a" * 64,
                "deactivate_unit_id": 2,
                "retain_unit_id": 1,
                "reason": "R" * 501,
            },
            {
                "conflict_id": "a" * 64,
                "deactivate_unit_id": 2,
                "retain_unit_id": 1,
                "reason": "x",
                "sql": "drop",
            },
        ],
    )
    def test_preview_bounds_are_422_without_echo(
        self, client, stores, service_stub, body
    ):
        users, _, _ = stores
        response = client.post(
            "/api/v1/admin/unit-catalog/repairs/preview",
            json=body,
            headers=_bearer(users, "admin"),
        )
        assert response.status_code == 422
        assert "R" * 501 not in response.text
        assert "drop" not in response.text

    def test_no_database_is_503(self, client, stores, service_stub):
        users, _, _ = stores
        app.dependency_overrides[get_db_optional] = lambda: None
        response = client.get(
            "/api/v1/admin/unit-catalog/conflicts", headers=_bearer(users, "admin")
        )
        assert response.status_code == 503

    def test_body_above_global_admission_limit_is_413(
        self, client, stores, service_stub
    ):
        from src.config.settings import settings

        users, _, _ = stores
        response = client.post(
            "/api/v1/admin/calculation-contracts/imports?confirm=true",
            content=b"x" * (settings.request_max_body_bytes + 1),
            headers={**_bearer(users, "admin"), "Content-Type": "application/json"},
        )
        assert response.status_code == 413

    def test_imports_are_rate_limited(self, client, stores, service_stub):
        users, _, _ = stores
        codes = [
            client.post(
                "/api/v1/admin/calculation-contracts/imports?confirm=true",
                content=b"{}",
                headers=_bearer(users, "admin"),
            ).status_code
            for _ in range(6)
        ]
        assert codes == [200] * 5 + [429]

    def test_rejected_contract_is_422_with_bounded_detail(self, client, stores):
        users, _, _ = stores
        with patch.object(
            admin_catalog,
            "import_contract",
            side_effect=admin_catalog.AdminCatalogError(422, "nodes must be a list"),
        ):
            response = client.post(
                "/api/v1/admin/calculation-contracts/imports?confirm=true",
                content=b'{"secret_formula": "x"}',
                headers=_bearer(users, "admin"),
            )
        assert response.status_code == 422
        assert "secret_formula" not in response.text

    def test_unexpected_import_error_is_generic_500(self, client, stores):
        users, _, _ = stores
        with patch.object(
            admin_catalog,
            "import_contract",
            side_effect=RuntimeError("host=db.internal"),
        ):
            response = client.post(
                "/api/v1/admin/calculation-contracts/imports?confirm=true",
                content=b"{}",
                headers=_bearer(users, "admin"),
            )
        assert response.status_code == 500
        assert "db.internal" not in response.text


MARKER = "MARKER-7f3a"


class TestNonReflectiveContractErrors:
    @pytest.mark.parametrize(
        "raw",
        [
            (
                '{"contract_version": "1", "nodes": [{"node_id": "%s"}, '
                '{"node_id": "%s"}]}' % (MARKER, MARKER)
            ).encode(),
            ('{"contract_version": "1", "nodes": "%s"}' % MARKER).encode(),
            ('{"%s": ' % MARKER).encode(),
        ],
    )
    def test_service_errors_and_audit_never_reflect_submitted_values(self, raw):
        session = _fixture_session()
        actor = admin_catalog.Actor("user_admin", "bearer")
        for call in (
            lambda: admin_catalog.validate_contract(session, raw, actor),
            lambda: admin_catalog.import_contract(
                session, raw, actor, retirement_scope="incoming_keys"
            ),
        ):
            with pytest.raises(admin_catalog.AdminCatalogError) as exc:
                call()
            assert exc.value.status_code == 422
            assert exc.value.message == admin_catalog.CONTRACT_INVALID
        for row in session.rows[db_models.AdminCatalogOperation]:
            assert MARKER not in repr(vars(row))

    def test_route_response_does_not_reflect_submitted_values(self, client, stores):
        users, _, _ = stores
        app.dependency_overrides[get_db_optional] = _fixture_session
        response = client.post(
            "/api/v1/admin/calculation-contracts/validations",
            content=(
                '{"contract_version": "1", "nodes": [{"node_id": "%s"}, '
                '{"node_id": "%s"}]}' % (MARKER, MARKER)
            ).encode(),
            headers=_bearer(users, "admin"),
        )
        assert response.status_code == 422
        assert MARKER not in response.text
