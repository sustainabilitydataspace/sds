"""Authenticated FX calculation on gated disposable PostgreSQL; run serially.

Synthetic contract/rates qualify request runtime consumption only. This is not
startup/readiness, market-history acceptance, production or named-gate closure.
"""

import hashlib
import json
import os
from datetime import date
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rdflib import Graph
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from alembic.script import ScriptDirectory
from src.api.routers import calculations
from src.auth.jwt_handler import jwt_handler
from src.auth.models import APIKeyCreate, Permission, UserRole
from src.calculation.contracts import RuntimeCalculationContractResolver
from src.calculation.json_strategy import JSONStrategy
from src.calculation.unit_converter import UnitConverter
from src.config.settings import settings
from src.database import session as database_session
from src.database.bootstrap_conversion_catalog import (
    DEFAULT_FX_POLICY_ID,
    bootstrap_conversion_catalog_if_empty,
)
from src.database.init_db import init_db_for_engine
from src.database.migrations import _alembic_config
from src.database.models import (
    AtomizerPackageImport,
    CanonicalCalculationComponent,
    CanonicalCalculationContract,
    CurrentValuePointer,
    ESGValue,
    FXPolicy,
    FXRateObservation,
    FXRatePeriod,
    HierarchyConfiguration,
    UserAccount,
    ValueContext,
    ValueRevision,
)
from src.database.repositories.fx_repository import FXRepository
from src.services.api_key_store import DatabaseAPIKeyStore
from src.services.fx_service import FXService
from src.services.value_store import DatabaseValueStore

TARGET = "urn:sds:reg:test:runtime-fx-total"
INPUT = "urn:sds:reg:test:runtime-fx-revenue"
CONTRACT = "runtime-fx-public-contract"
TENANT_A, TENANT_B = "runtime-fx-company-a", "runtime-fx-company-b"
ENTITY, B_ONLY_ENTITY = "runtime-fx-shared-plant", "runtime-fx-b-only-plant"
START, END = date(2024, 3, 1), date(2024, 3, 31)


@pytest.fixture
def disposable_postgres():
    url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    if make_url(url).get_backend_name() != "postgresql":
        pytest.skip("Runtime FX HTTP qualification requires disposable PostgreSQL")

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        init_db_for_engine(engine)
        with engine.connect() as connection:
            expected_head = ScriptDirectory.from_config(
                _alembic_config(connection)
            ).get_current_head()
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == expected_head
            )
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "url,reset",
    [
        (None, None),
        (None, "true"),
        ("postgresql://unused.invalid/disposable", None),
        ("postgresql://unused.invalid/disposable", "false"),
        ("postgresql://unused.invalid/disposable", "TRUE"),
        ("sqlite://", "true"),
    ],
)
def test_disposable_postgres_gate_before_engine_creation(monkeypatch, url, reset):
    for name, value in (
        ("SDS_MIGRATION_TEST_DATABASE_URL", url),
        ("SDS_MIGRATION_TEST_ALLOW_RESET", reset),
    ):
        monkeypatch.delenv(name, raising=False)
        if value is not None:
            monkeypatch.setenv(name, value)

    def forbidden_engine(*_args, **_kwargs):
        pytest.fail("closed gate must not create an engine")

    monkeypatch.setitem(globals(), "create_engine", forbidden_engine)
    with pytest.raises(pytest.skip.Exception):
        next(disposable_postgres.__wrapped__())


def _fixture_hash(label):
    # Synthetic identities, not imported/certified canonical-hash evidence.
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _value_id(label):
    return str(uuid5(NAMESPACE_URL, f"urn:sds:test:runtime-fx:{label}"))


def _seed_contract(db):
    bootstrap_conversion_catalog_if_empty(db)
    policy = db.get(FXPolicy, DEFAULT_FX_POLICY_ID)
    assert policy.selection_mode == "monthly_average"
    assert policy.fallback_behavior == "fail_closed"
    assert not policy.triangulation_allowed
    package = AtomizerPackageImport(
        package_hash=_fixture_hash("runtime-fx-package"), status="completed"
    )
    db.add(
        CanonicalCalculationContract(
            package_import=package,
            contract_version="test-only",
            model_id="runtime-fx-fixture",
            node_id=CONTRACT,
            indicator_identifier=TARGET,
            label="Synthetic public currency contract",
            exposure="public_register",
            runtime_status="executable",
            runtime_expression="revenue * 2",
            unit_name="1",
            result_currency="EUR",
            contract_hash=_fixture_hash(CONTRACT),
            components=[
                CanonicalCalculationComponent(
                    package_import=package,
                    component_id=INPUT,
                    unit_name="1",
                    expected_currency="EUR",
                    fx_policy_id=DEFAULT_FX_POLICY_ID,
                    required=True,
                    source_payload={"local_variable": "revenue"},
                )
            ],
        )
    )
    db.commit()
    contract = RuntimeCalculationContractResolver(db=db).resolve(TARGET)
    assert contract.contract_id == CONTRACT
    assert contract.exposure == "public_register" and contract.is_executable
    assert contract.result_currency == "EUR"
    assert contract.inputs[0].expected_currency == "EUR"
    assert contract.inputs[0].fx_policy_id == DEFAULT_FX_POLICY_ID


def _seed_values_and_credentials(db):
    tokens = {}
    for tenant in (TENANT_A, TENANT_B):
        db.add(
            UserAccount(
                id=tenant,
                username=tenant,
                email=f"{tenant}@example.com",
                password_hash="unused-test-password-hash",
                role=UserRole.DATA_MANAGER.value,
                company_id=tenant,
                is_active=True,
            )
        )
        db.add(
            HierarchyConfiguration(
                id=tenant,
                company_id=tenant,
                hierarchy_type="organizational",
                name=tenant,
                is_active=True,
                configuration=json.dumps(
                    dict(
                        id=tenant,
                        company_id=tenant,
                        hierarchy_type="organizational",
                        name=tenant,
                        active=True,
                        levels=[
                            dict(id=entity, name=entity, level=0)
                            for entity in (ENTITY, B_ONLY_ENTITY)
                        ],
                    )
                ),
            )
        )
        tokens[tenant] = jwt_handler.create_access_token(
            user_id=tenant,
            username=tenant,
            role=UserRole.DATA_MANAGER,
            company_id=tenant,
        )
    db.commit()
    for tenant, label, entity, amount in (
        (TENANT_A, "a-source", ENTITY, "100"),
        (TENANT_B, "b-decoy", ENTITY, "987654321"),
        (TENANT_B, "b-only-source", B_ONLY_ENTITY, "700"),
    ):
        DatabaseValueStore(db, tenant_id=tenant).create(
            value_id=_value_id(label),
            concept=INPUT,
            entity=entity,
            period=END,
            period_start=START,
            period_end=END,
            value=Decimal(amount),
            unit="1",
            currency="USD",
            original_unit=None,
            conversion_applied=False,
            metadata=None,
            created_by=tenant,
        )
    key = DatabaseAPIKeyStore(db).create_api_key(
        user_id=TENANT_A,
        request=APIKeyCreate(
            name="runtime-fx-calculate-only",
            permissions=[Permission.EXECUTE_CALCULATIONS],
        ),
    )
    return tokens, key.key


def _value_counts(db):
    return tuple(
        db.query(model).count()
        for model in (ESGValue, ValueContext, ValueRevision, CurrentValuePointer)
    )


def test_disposable_postgres_authenticated_fx_calculation(
    disposable_postgres, monkeypatch, mock_units_database
):
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", False)
    monkeypatch.setattr(settings, "value_revision_default_tenant_id", TENANT_B)
    sessions = sessionmaker(bind=disposable_postgres, autocommit=False, autoflush=False)
    # Real get_db_optional acquires/closes each request session. Persistent JWT
    # revocation uses the same factory; no auth/store/runtime dependencies mocked.
    monkeypatch.setattr(database_session, "SessionLocal", sessions)
    with sessions() as db:
        _seed_contract(db)
        tokens, key = _seed_values_and_credentials(db)
        before = _value_counts(db)
        assert before == (3, 3, 3, 3)
        assert db.query(FXRateObservation).count() == 0
        assert db.query(FXRatePeriod).count() == 0

    app = FastAPI()
    app.include_router(calculations.router, prefix="/api/v1")
    app.state.unit_converter = UnitConverter(
        storage_strategy=JSONStrategy(mock_units_database)
    )
    app.state.ontology_graph = Graph()
    # No app/global conversion engine and no production lifespan/bootstrap.
    assert not app.dependency_overrides
    base = dict(
        concept=TARGET, period="2024-03", granularity="monthly", include_trace=True
    )
    bearer_a = {"Authorization": f"Bearer {tokens[TENANT_A]}"}
    bearer_b = {"Authorization": f"Bearer {tokens[TENANT_B]}"}
    key_a = {"X-API-Key": key}

    with TestClient(app) as client:

        def post(headers, entity=ENTITY):
            value_writes = []

            def observe_write(_conn, _cursor, statement, _params, context, _many):
                if context.isinsert or context.isupdate or context.isdelete:
                    table = context.compiled.statement.table.name
                    if table in {
                        model.__tablename__
                        for model in (
                            ESGValue,
                            ValueContext,
                            ValueRevision,
                            CurrentValuePointer,
                        )
                    }:
                        value_writes.append(statement)

            event.listen(disposable_postgres, "before_cursor_execute", observe_write)
            try:
                response = client.post(
                    "/api/v1/calculate", headers=headers, json=dict(base, entity=entity)
                )
            finally:
                event.remove(
                    disposable_postgres, "before_cursor_execute", observe_write
                )
            # Observe real DML without replacing execution: even rolled-back
            # value writes must not disappear behind the committed-count check.
            assert value_writes == []
            # Independent reader after every request: success and refusal must
            # leave all four committed value persistence lanes unchanged.
            with sessions() as db:
                assert _value_counts(db) == before
            return response

        def assert_refused(response, reason):
            assert response.status_code == 422, response.text
            assert reason in response.json()["detail"]
            assert "value" not in response.json()
            for label in ("b-decoy", "b-only-source"):
                assert _value_id(label) not in response.text
            assert "987654321" not in response.text

        assert post({}).status_code == 403
        # A complete public contract and A-owned input are insufficient without
        # persisted rates. No implicit 1:1 FX or bundled-rate fallback is valid.
        assert_refused(post(bearer_a), "FX rate not found")

        with sessions() as db:
            repository = FXRepository(db)
            FXService(repository).import_rates_csv(
                [
                    {"quote_currency": "EUR", "rate_date": day, "rate_value": rate}
                    for day, rate in ((START, "0.8"), (date(2024, 3, 4), "1.0"))
                ],
                provider="ECB",
                rate_type="reference",
                base_currency="USD",
                source_hash=_fixture_hash("synthetic-fx-history"),
                source_name="Synthetic runtime fixture; not market evidence",
                created_by=TENANT_A,
            )
            materialized = repository.materialize_monthly_periods(
                provider="ECB",
                rate_type="reference",
                base_currency="USD",
                quote_currency="EUR",
                start=START,
                end=END,
            )
            assert materialized.created_periods == 1
            period = db.query(FXRatePeriod).one()
            assert period.rate_value == Decimal("0.9")
            rate_id = period.id
            source_ids = period.source_observation_ids
            source_hash = period.source_hash
            assert set(source_ids) == {
                row.id for row in db.query(FXRateObservation).all()
            }
            assert len(source_ids) == 2

        def assert_converted(response, source_label, original, converted, result):
            assert response.status_code == 200, response.text
            payload = response.json()
            assert Decimal(str(payload["value"])) == Decimal(result)
            assert payload["unit"] == "1"
            assert payload["formula_used"] == "revenue * 2"
            assert payload["execution_authority"] == "native_contract"
            assert payload["source_value_ids"] == [_value_id(source_label)]
            metadata = payload["framework_metadata"]
            assert metadata["contract_id"] == CONTRACT
            assert metadata["contract_hash"] == _fixture_hash(CONTRACT)
            assert metadata["resolver_source"] == "canonical_calculation_contracts"
            trace = payload["trace"]
            assert f"contract_id={CONTRACT}" in trace["steps"]
            assert f"source_value_ids={_value_id(source_label)}" in trace["steps"]
            assert payload["unit_conversions"] == trace["conversions_applied"]
            assert len(trace["conversions_applied"]) == 1
            step = trace["conversions_applied"][0]
            assert step["step_type"] == "fx"
            assert step["from_currency"] == "USD" and step["to_currency"] == "EUR"
            assert Decimal(step["original_value"]) == Decimal(original)
            assert Decimal(step["converted_value"]) == Decimal(converted)
            assert step["policy_id"] == DEFAULT_FX_POLICY_ID
            assert step["rate_period_id"] == str(rate_id)
            assert "rate_observation_id" not in step
            assert step["source"] == "ECB:reference"
            assert step["metadata"]["source_observation_ids"] == source_ids
            assert step["metadata"]["source_hash"] == source_hash
            assert step["metadata"]["selection_mode"] == "monthly_average"
            assert step["metadata"]["period_start"] == START.isoformat()
            assert step["metadata"]["period_end"] == END.isoformat()
            return response

        for headers in (bearer_a, key_a):
            response = assert_converted(post(headers), "a-source", "100", "90", "180")
            for label in ("b-decoy", "b-only-source"):
                assert _value_id(label) not in response.text
            assert "987654321" not in response.text
            assert_refused(
                post(headers, B_ONLY_ENTITY), "Missing required observations"
            )
        # Positive B control excludes missing fixture data or entity denial as
        # explanations for A's refusal, and proves the B-only input carries FX.
        assert_converted(
            post(bearer_b, B_ONLY_ENTITY), "b-only-source", "700", "630", "1260"
        )

        with sessions() as db:
            db.delete(db.get(FXRatePeriod, rate_id))
            db.commit()
            assert db.query(FXRatePeriod).count() == 0
            assert db.query(FXRateObservation).count() == 2
        # A fresh request cannot reuse the previous rate or substitute daily
        # observations for the policy's required materialized monthly period.
        for headers in (bearer_a, key_a):
            assert_refused(post(headers), "FX rate not found")
