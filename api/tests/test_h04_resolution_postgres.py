"""H04 HTTP parity on migrated, explicitly disposable PostgreSQL; run serially.

Synthetic public contract/mapping rows qualify runtime consumption, not import,
certification governance, production data, startup/readiness or PostgreSQL RLS.
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
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from alembic.script import ScriptDirectory
from src.api.routers import calculations, values
from src.auth.jwt_handler import jwt_handler
from src.auth.models import UserRole
from src.calculation.contracts import RuntimeCalculationContractResolver
from src.calculation.json_strategy import JSONStrategy
from src.calculation.unit_converter import UnitConverter
from src.config.settings import settings
from src.database import session as database_session
from src.database.init_db import init_db_for_engine
from src.database.migrations import _alembic_config
from src.database.models import (
    AtomizerPackageImport,
    CanonicalCalculationComponent,
    CanonicalCalculationContract,
    CurrentValuePointer,
    ESGValue,
    HierarchyConfiguration,
    MappingAssertionGroup,
    MaterializedPairwiseMapping,
    StandardDatapoint,
    StandardRelease,
    UserAccount,
    ValueContext,
    ValueRevision,
)
from src.services.standard_mapping_store import StandardMappingStore
from src.services.value_store import DatabaseValueStore

TARGET = "gri:H04-total"
INPUT = "gri:H04-component"
SOURCE = "csrd:H04-source"
ALTERNATIVE = "csrd:H04-alternative"
BRIDGE = "h04-public-bridge"
TENANT_A, TENANT_B = "h04-company-a", "h04-company-b"
ENTITY, B_ONLY_ENTITY = "h04-shared-plant", "h04-b-only-plant"


@pytest.fixture
def disposable_postgres():
    url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    if make_url(url).get_backend_name() != "postgresql":
        pytest.skip("H04 qualification requires disposable PostgreSQL")

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
    # Fixture identities, not claims of importer/canonical-hash qualification.
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _value_id(label):
    return str(uuid5(NAMESPACE_URL, f"urn:sds:test:h04:{label}"))


def _seed_contract_and_mappings(db):
    package = AtomizerPackageImport(
        package_hash=_fixture_hash("h04-package"), status="completed"
    )
    contract = CanonicalCalculationContract(
        package_import=package,
        contract_version="h04-test-only",
        model_id="h04-fixture",
        node_id=BRIDGE,
        indicator_identifier=TARGET,
        label="H04 synthetic public bridge",
        exposure="public_register",
        runtime_status="executable",
        runtime_expression="component * 2",
        unit_name="kg",
        contract_hash=_fixture_hash(BRIDGE),
        components=[
            CanonicalCalculationComponent(
                package_import=package,
                component_id=INPUT,
                unit_name="kg",
                required=True,
                source_payload={
                    "local_variable": "component",
                    "allowed_mapping_relationships": ["narrower"],
                },
            )
        ],
    )
    releases = {
        standard: StandardRelease(
            standard_id=standard, name=f"H04 {standard}", version="test-only"
        )
        for standard in ("ESRS", "GRI")
    }

    def group(standard, code):
        return MappingAssertionGroup(
            source_datapoint=StandardDatapoint(
                standard_release=releases[standard], code=code, label=f"H04 {code}"
            ),
            approval_status="approved",
            publication_status="public",
            assertion_hash=_fixture_hash(code),
        )

    source, alternative = [
        group("ESRS", code) for code in ("H04-source", "H04-alternative")
    ]
    target = group("GRI", "H04-component")

    def mapping(source_group, *, current):
        return MaterializedPairwiseMapping(
            source_datapoint=source_group.source_datapoint,
            target_datapoint=target.source_datapoint,
            source_assertion_group=source_group,
            target_assertion_group=target,
            source_standard="ESRS",
            source_code=source_group.source_datapoint.code,
            target_standard="GRI",
            target_code="H04-component",
            relationship_type="narrower",
            generated_from_hash=_fixture_hash(source_group.source_datapoint.code),
            is_current=current,
        )

    authorized = mapping(source, current=True)
    ambiguous = mapping(alternative, current=False)
    db.add_all([contract, authorized, ambiguous])
    db.commit()
    resolved = RuntimeCalculationContractResolver(db=db).resolve(TARGET)
    assert resolved.contract_id == BRIDGE
    assert resolved.exposure == "public_register" and resolved.is_executable
    assert resolved.inputs[0].allowed_mapping_relationships == ("narrower",)
    rows = StandardMappingStore(db).search(target_standard="GRI")
    assert [(row.id, row.dataset) for row in rows] == [
        (authorized.id, "canonical_pairwise_mappings")
    ]
    return authorized.id, ambiguous.id


def _seed_values_and_users(db):
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
    # Same entity/period and source in both tenants; no A input/target shortcut.
    for tenant, value_id, concept, entity, amount, unit in (
        (TENANT_A, "h04-a-source", SOURCE, ENTITY, "1500", "g"),
        (TENANT_A, "h04-a-alternative", ALTERNATIVE, ENTITY, "2500", "g"),
        (TENANT_B, "h04-b-source", SOURCE, ENTITY, "987654321", "g"),
        (TENANT_B, "h04-b-target", TARGET, ENTITY, "987654322", "kg"),
        (TENANT_B, "h04-b-only-source", SOURCE, B_ONLY_ENTITY, "9000", "g"),
    ):
        DatabaseValueStore(db, tenant_id=tenant).create(
            value_id=_value_id(value_id),
            concept=concept,
            entity=entity,
            period=date(2024, 12, 31),
            value=Decimal(amount),
            unit=unit,
            original_unit=None,
            conversion_applied=False,
            metadata=None,
            created_by=tenant,
        )

    for concept in (INPUT, TARGET):
        assert (
            DatabaseValueStore(db, tenant_id=TENANT_A).latest(
                concept=concept,
                entity=ENTITY,
                period_start=date(2024, 1, 1),
                period_end=date(2024, 12, 31),
            )
            is None
        )
    return tokens


def _value_counts(db):
    return tuple(
        db.query(model).count()
        for model in (ESGValue, ValueContext, ValueRevision, CurrentValuePointer)
    )


def test_disposable_postgres_authenticated_calculate_resolve_bridge_parity(
    disposable_postgres, monkeypatch, mock_units_database
):
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", False)
    monkeypatch.setattr(settings, "value_revision_default_tenant_id", TENANT_B)
    sessions = sessionmaker(bind=disposable_postgres)
    # Persistent bearer revocation checks also use the disposable DB.
    monkeypatch.setattr(database_session, "SessionLocal", sessions)
    with sessions() as db:
        mapping_id, ambiguous_id = _seed_contract_and_mappings(db)
        tokens = _seed_values_and_users(db)
        before = _value_counts(db)
        assert before == (5, 5, 5, 5)

    # Router-only app follows containment conventions, avoiding production startup.
    # Auth, tenant/hierarchy stores, contract resolver, mapping provider and
    # request-scoped conversion engines remain real and unoverridden.
    app = FastAPI()
    app.include_router(calculations.router, prefix="/api/v1")
    app.include_router(values.router, prefix="/api/v1")
    app.state.unit_converter = UnitConverter(
        storage_strategy=JSONStrategy(mock_units_database)
    )
    app.state.ontology_graph = Graph()

    def request_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[database_session.get_db_optional] = request_db
    base = dict(period="2024", granularity="annual", include_trace=True)

    with TestClient(app) as client:

        def post_pair(*, tenant=TENANT_A, entity=ENTITY):
            headers = {"Authorization": f"Bearer {tokens[tenant]}"}
            return (
                client.post(
                    "/api/v1/calculate",
                    headers=headers,
                    json=dict(base, concept=TARGET, entity=entity),
                ),
                client.post(
                    "/api/v1/values/resolve",
                    headers=headers,
                    json=dict(base, target_concept=TARGET, entity=entity),
                ),
            )

        for path, concept_field in (
            ("calculate", "concept"),
            ("values/resolve", "target_concept"),
        ):
            response = client.post(
                f"/api/v1/{path}",
                json={**base, concept_field: TARGET, "entity": ENTITY},
            )
            assert response.status_code == 403

        calculated, resolved = post_pair()
        assert calculated.status_code == 200, calculated.text
        assert resolved.status_code == 200, resolved.text
        calc, resolve = calculated.json(), resolved.json()
        for result in (calc, resolve):
            assert Decimal(str(result["value"])) == Decimal("3")
            assert result["unit"] == "kg"
            assert result["execution_authority"] == "certified_bridge"
            assert result["bridge_id"] == BRIDGE
        assert resolve["status"] == "resolved" and resolve["method"] == "calculation"
        assert resolve["contract_id"] == BRIDGE
        assert calc["source_value_ids"] == [_value_id("h04-a-source")]
        assert calc["framework_metadata"]["input_mappings"] == [
            {
                "local_variable": "component",
                "requested_concept": INPUT,
                "source_concept": SOURCE,
                "relationship_type": "narrower",
                "mapping_row_id": str(mapping_id),
                "direction": "forward",
            }
        ]
        # Resolve exposes the nested public calculation trace, not the complete
        # CalculationResponse/framework_metadata. Assert its actual provenance.
        for trace in (calc["trace"], resolve["trace"]["calculation"]):
            assert f"contract_id={BRIDGE}" in trace["steps"]
            assert "resolver_source=canonical_calculation_contracts" in trace["steps"]
            assert f"source_value_ids={_value_id('h04-a-source')}" in trace["steps"]
            assert f"input_mapping=component:{INPUT}<-{SOURCE}" in trace["steps"]
            assert len(trace["conversions_applied"]) == 1
            conversion = trace["conversions_applied"][0]
            assert conversion["from_unit"] == "g"
            assert conversion["to_unit"] == "kg"
            assert Decimal(conversion["original_value"]) == Decimal("1500")
            assert Decimal(conversion["converted_value"]) == Decimal("1.5")
        assert (
            calc["trace"]["conversions_applied"]
            == resolve["trace"]["calculation"]["conversions_applied"]
        )

        def assert_refused(pair, reason):
            failed_calc, failed_resolve = pair
            assert failed_calc.status_code == 422, failed_calc.text
            assert reason in failed_calc.json()["detail"]
            assert failed_resolve.status_code == 422, failed_resolve.text
            assert reason in failed_resolve.json()["detail"]
            for response in pair:
                for label in ("h04-b-source", "h04-b-target", "h04-b-only-source"):
                    assert _value_id(label) not in response.text
                assert "98765432" not in response.text

        assert_refused(post_pair(entity=B_ONLY_ENTITY), "Missing required observations")
        # Positive tenant-B control proves the missing A input really exists.
        for response in post_pair(tenant=TENANT_B, entity=B_ONLY_ENTITY):
            assert response.status_code == 200, response.text
            assert Decimal(str(response.json()["value"])) == Decimal("18")
            assert response.json()["execution_authority"] == "certified_bridge"

        with sessions() as db:
            db.get(MaterializedPairwiseMapping, mapping_id).relationship_type = (
                "broader"
            )
            db.commit()
        assert_refused(post_pair(), "exact/equivalent relationship is required")

        with sessions() as db:
            db.get(MaterializedPairwiseMapping, mapping_id).relationship_type = (
                "narrower"
            )
            db.get(MaterializedPairwiseMapping, ambiguous_id).is_current = True
            db.commit()
        assert_refused(post_pair(), "ambiguous eligible mappings")

        # Removing the extra current route restores the bridge on new requests.
        with sessions() as db:
            db.get(MaterializedPairwiseMapping, ambiguous_id).is_current = False
            db.commit()
        for response in post_pair():
            assert response.status_code == 200, response.text
            assert Decimal(str(response.json()["value"])) == Decimal("3")
            assert response.json()["execution_authority"] == "certified_bridge"

        # Fresh HTTP reads exercise authenticated ownership, including direct
        # target decoys that must never win /resolve's stored-value preference.
        for value_id in ("h04-b-source", "h04-b-target", "h04-b-only-source"):
            for tenant, expected in ((TENANT_A, 404), (TENANT_B, 200)):
                response = client.get(
                    f"/api/v1/values/{_value_id(value_id)}",
                    headers={"Authorization": f"Bearer {tokens[tenant]}"},
                )
                assert response.status_code == expected, response.text
        for response in (calculated, resolved):
            for label in ("h04-b-source", "h04-b-target", "h04-b-only-source"):
                assert _value_id(label) not in response.text
            assert "98765432" not in response.text

    with sessions() as db:
        assert _value_counts(db) == before  # Calculations/refusals save no values.
