"""Catalog repair follow-up on an explicitly disposable PostgreSQL.

Production shape (2026-10-04): three expression-token conflicts — m3/m³ and
the legacy kgCO2e/tCO2e units whose factors contradict the emissions base.
Run with SDS_MIGRATION_TEST_DATABASE_URL and SDS_MIGRATION_TEST_ALLOW_RESET=true.
"""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from src.api.main import app
from src.api.rate_limit import limiter
from src.auth.jwt_handler import jwt_handler
from src.auth.models import UserCreate, UserRole
from src.calculation.postgres_strategy import PostgresStrategy
from src.calculation.unit_converter import UnitConverter
from src.database.base import Base
from src.database.models import (
    AdminCatalogOperation,
    ConversionRule,
    ESGValue,
    Unit,
    UnitCategory,
)
from src.database.session import get_db_optional
from src.services import admin_catalog
from src.services.user_store import InMemoryUserStore, get_user_store

ACTOR = admin_catalog.Actor("user_admin", "bearer", "req-1")
TABLES = [
    UnitCategory.__table__,
    Unit.__table__,
    ConversionRule.__table__,
    AdminCatalogOperation.__table__,
    ESGValue.__table__,
]


@pytest.fixture
def db_factory():
    url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    if make_url(url).get_backend_name() != "postgresql":
        pytest.skip("requires disposable PostgreSQL")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine, tables=TABLES)
    maker = sessionmaker(bind=engine)
    sessions = []

    def factory():
        session = maker()
        sessions.append(session)
        return session

    yield factory
    for session in sessions:
        session.close()
    engine.dispose()


def _unit(category_id, symbol, name, factor, aliases):
    return Unit(
        category_id=category_id,
        symbol=symbol,
        name=name,
        conversion_factor=Decimal(factor),
        conversion_offset=Decimal("0"),
        aliases=aliases,
        is_active=True,
    )


def seed_production_shape(session):
    volume = UnitCategory(name="volume", base_unit="m³")
    emissions = UnitCategory(name="emissions", base_unit="kg CO2e")
    session.add_all([volume, emissions])
    session.flush()
    rows = {
        "m3": _unit(volume.id, "m3", "cubic metre", "1", ["m³"]),
        "m³": _unit(volume.id, "m³", "cubic_meter", "1", ["m3", "cubic_meter"]),
        "L": _unit(volume.id, "L", "liter", "0.001", ["l", "liter"]),
        "tCO2e": _unit(emissions.id, "tCO2e", "tCO2e", "1", ["t CO2e"]),
        "kgCO2e": _unit(emissions.id, "kgCO2e", "kgCO2e", "0.001", ["kg CO2e"]),
        "kg CO2e": _unit(
            emissions.id,
            "kg CO2e",
            "kilogram_co2_equivalent",
            "1",
            ["kgCO2e", "kg_co2e", "kg_co2_equivalent"],
        ),
        "t CO2e": _unit(
            emissions.id,
            "t CO2e",
            "tonne_co2_equivalent",
            "1000",
            ["tCO2e", "t_co2e", "tonne_co2_equivalent"],
        ),
    }
    session.add_all(rows.values())
    session.commit()
    return {symbol: row.id for symbol, row in rows.items()}


def _conflict_id(session, token):
    listing = admin_catalog.list_conflicts(session, limit=100, offset=0)
    return next(c["conflict_id"] for c in listing["items"] if c["token"] == token)


@pytest.fixture
def api(db_factory):
    limiter._storage.reset()
    users = InMemoryUserStore()
    users.create_user(
        UserCreate(
            username="admin",
            email="admin@example.com",
            password="Sufficient-Pass-2026",
            role=UserRole.ADMIN,
        )
    )
    admin = users.get_user(username="admin")
    token = jwt_handler.create_access_token(
        user_id=admin.id,
        username=admin.username,
        role=admin.role,
        auth_version=admin.auth_version,
    )
    session = db_factory()
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_db_optional] = lambda: session
    previous = getattr(app.state, "unit_converter", None)
    app.state.unit_converter = UnitConverter(
        storage_strategy=PostgresStrategy(db_factory())
    )
    yield TestClient(app), {"Authorization": f"Bearer {token}"}, session
    app.dependency_overrides.pop(get_user_store, None)
    app.dependency_overrides.pop(get_db_optional, None)
    app.state.unit_converter = previous
    limiter._storage.reset()


def test_one_repair_persists_while_other_conflicts_remain(db_factory, api):
    client, headers, session = api
    ids = seed_production_shape(session)
    preview = client.post(
        "/api/v1/admin/unit-catalog/repairs/preview",
        json={
            "conflict_id": _conflict_id(session, "m3"),
            "deactivate_unit_id": ids["m3"],
            "retain_unit_id": ids["m³"],
            "reason": "duplicate of m³",
        },
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    commit = client.post(
        "/api/v1/admin/unit-catalog/repairs/commit?confirm=true",
        json=preview.json(),
        headers=headers,
    )
    assert commit.status_code == 200, commit.text
    assert len(commit.json()["remaining_conflicts"]) == 2

    check = db_factory()
    assert check.get(Unit, ids["m3"]).is_active is False
    rows = check.query(AdminCatalogOperation).all()
    assert [(r.action, r.result) for r in rows] == [("unit_repair_commit", "ok")]
    listing = admin_catalog.list_conflicts(check, limit=100, offset=0)
    assert {c["token"] for c in listing["items"]} == {"kgCO2e", "tCO2e"}


def test_reverse_succeeds_while_other_conflicts_remain(db_factory, api):
    client, headers, session = api
    ids = seed_production_shape(session)
    preview = admin_catalog.preview_repair(
        session,
        conflict_id=_conflict_id(session, "m3"),
        deactivate_unit_id=ids["m3"],
        retain_unit_id=ids["m³"],
        reason="duplicate",
    )
    committed = admin_catalog.commit_repair(
        session, plan=preview["plan"], digest=preview["plan_digest"], actor=ACTOR
    )
    response = client.post(
        f"/api/v1/admin/unit-catalog/repairs/{committed['repair_id']}/reverse"
        "?confirm=true",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert db_factory().get(Unit, ids["m3"]).is_active is True


# --------------------------------------------------------------------------
# Factor corrections
# --------------------------------------------------------------------------


def _ack(target, old, retained, new, base="kg CO2e"):
    return (
        f"I confirm unit {target} (factor {old}) is incorrect and unit "
        f"{retained} (factor {new}) is correct relative to base {base}"
    )


KG_ACK = _ack("kgCO2e", "0.001", "kg CO2e", "1")
T_ACK = _ack("tCO2e", "1", "t CO2e", "1000")


def _factor_preview(session, ids, target, retained, ack, token=None):
    return admin_catalog.preview_factor_correction(
        session,
        conflict_id=_conflict_id(session, token or target),
        deactivate_unit_id=ids[target],
        retain_unit_id=ids[retained],
        reason="legacy factor contradicts the emissions base",
        acknowledgement=ack,
    )


def _factor_commit(session, preview, ack=None):
    return admin_catalog.commit_factor_correction(
        session,
        plan=preview["plan"],
        digest=preview["plan_digest"],
        acknowledgement=ack if ack is not None else preview["plan"]["acknowledgement"],
        actor=ACTOR,
    )


def test_factor_corrections_and_duplicate_repair_clear_the_catalog(db_factory, api):
    client, headers, session = api
    ids = seed_production_shape(session)

    wrong = client.post(
        "/api/v1/admin/unit-catalog/factor-corrections/preview",
        json={
            "conflict_id": _conflict_id(session, "kgCO2e"),
            "deactivate_unit_id": ids["kgCO2e"],
            "retain_unit_id": ids["kg CO2e"],
            "reason": "legacy factor",
            "acknowledgement": "yes",
        },
        headers=headers,
    )
    assert wrong.status_code == 422
    assert KG_ACK in wrong.text

    preview = client.post(
        "/api/v1/admin/unit-catalog/factor-corrections/preview",
        json={
            "conflict_id": _conflict_id(session, "kgCO2e"),
            "deactivate_unit_id": ids["kgCO2e"],
            "retain_unit_id": ids["kg CO2e"],
            "reason": "legacy factor",
            "acknowledgement": KG_ACK,
        },
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["plan"]["old_factor"] == "0.001"
    assert body["plan"]["new_factor"] == "1"
    assert body["impact"]["classification"] == "possibly affected"
    commit = client.post(
        "/api/v1/admin/unit-catalog/factor-corrections/commit?confirm=true",
        json={
            "plan": body["plan"],
            "plan_digest": body["plan_digest"],
            "acknowledgement": KG_ACK,
        },
        headers=headers,
    )
    assert commit.status_code == 200, commit.text
    assert len(commit.json()["remaining_conflicts"]) == 2

    _factor_commit(session, _factor_preview(session, ids, "tCO2e", "t CO2e", T_ACK))
    preview = admin_catalog.preview_repair(
        session,
        conflict_id=_conflict_id(session, "m3"),
        deactivate_unit_id=ids["m3"],
        retain_unit_id=ids["m³"],
        reason="duplicate",
    )
    last = admin_catalog.commit_repair(
        session, plan=preview["plan"], digest=preview["plan_digest"], actor=ACTOR
    )
    assert last["remaining_conflicts"] == []

    converter = UnitConverter(storage_strategy=PostgresStrategy(db_factory()))
    converter.get_physical_converter()
    assert converter.convert(1, "tCO2e", "kg CO2e").converted_value == 1000
    assert converter.convert(1, "kgCO2e", "kg CO2e").converted_value == 1

    check = db_factory()
    audit = [
        r.counts
        for r in check.query(AdminCatalogOperation).order_by(AdminCatalogOperation.id)
    ]
    assert audit[0]["operation"] == "factor_correction"
    assert (audit[0]["old_factor"], audit[0]["new_factor"], audit[0]["base"]) == (
        "0.001",
        "1",
        "kg CO2e",
    )
    assert audit[0]["impact_digest"] == body["plan"]["impact_digest"]
    assert len(audit[0]["reference_sha256"]) == 64
    assert "operation" not in audit[2]


def test_factor_correction_reverses_exactly(db_factory, api):
    client, headers, session = api
    ids = seed_production_shape(session)
    before = admin_catalog.unit_snapshot(session.get(Unit, ids["kgCO2e"]))
    committed = _factor_commit(
        session, _factor_preview(session, ids, "kgCO2e", "kg CO2e", KG_ACK)
    )
    response = client.post(
        f"/api/v1/admin/unit-catalog/repairs/{committed['repair_id']}/reverse"
        "?confirm=true",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    check = db_factory()
    assert admin_catalog.unit_snapshot(check.get(Unit, ids["kgCO2e"])) == before
    assert len(admin_catalog.list_conflicts(check, limit=100, offset=0)["items"]) == 3


def _refused(session, ids, target, retained, ack, status, fragment, token=None):
    with pytest.raises(admin_catalog.AdminCatalogError) as exc:
        _factor_preview(session, ids, target, retained, ack, token=token)
    session.rollback()
    assert exc.value.status_code == status, exc.value.message
    assert fragment in exc.value.message


def _set(session, unit_id, **values):
    unit = session.get(Unit, unit_id)
    for name, value in values.items():
        setattr(unit, name, value)
    session.commit()


def test_factor_correction_refusals(db_factory, api, tmp_path, monkeypatch):
    _, _, session = api
    ids = seed_production_shape(session)

    _refused(session, ids, "kgCO2e", "kg CO2e", KG_ACK + " ", 422, "exactly")
    # Equal factors are a duplicate repair, not a factor correction.
    _refused(session, ids, "m3", "m³", "x", 422, "equal factors")

    # Reference catalog must define the retained unit with the same factor
    # and the target only as its alias.
    reference, _ = admin_catalog.reference_catalog()
    variants = {
        "absent": lambda r: r["categories"]["emissions"]["units"].pop("kg CO2e"),
        "factor": lambda r: r["categories"]["emissions"]["units"]["kg CO2e"].update(
            conversion_factor=2
        ),
        "target_defined": lambda r: r["categories"]["mass"]["units"].update(
            kgCO2e={"symbol": "kgCO2e", "conversion_factor": 1}
        ),
        "not_alias": lambda r: r["categories"]["emissions"]["units"]["kg CO2e"].update(
            aliases=["kg_co2e"]
        ),
        "base": lambda r: r["categories"]["emissions"].update(base_unit="t CO2e"),
    }
    for name, change in variants.items():
        variant = json.loads(json.dumps(reference))
        change(variant)
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(variant))
        monkeypatch.setattr(admin_catalog, "REFERENCE_CATALOG_PATH", path)
        _refused(session, ids, "kgCO2e", "kg CO2e", KG_ACK, 422, "reference")
    monkeypatch.undo()

    _set(session, ids["kg CO2e"], conversion_factor=Decimal("2"))
    _refused(session, ids, "kgCO2e", "kg CO2e", KG_ACK, 422, "base unit")
    _set(session, ids["kg CO2e"], conversion_factor=Decimal("1"))

    _set(session, ids["kgCO2e"], conversion_offset=Decimal("1"))
    _refused(session, ids, "kgCO2e", "kg CO2e", KG_ACK, 422, "zero offsets")
    _set(session, ids["kgCO2e"], conversion_offset=Decimal("0"))

    _set(session, ids["kgCO2e"], aliases=["kg CO2e", "kgCO2eq"])
    _refused(session, ids, "kgCO2e", "kg CO2e", KG_ACK, 422, "kgCO2eq")
    _set(session, ids["kgCO2e"], aliases=["kg CO2e"])

    rule = ConversionRule(from_unit="kgCO2e", to_unit="t CO2e", formula="value / 1000")
    session.add(rule)
    session.commit()
    _refused(session, ids, "kgCO2e", "kg CO2e", KG_ACK, 422, "conversion rules")
    session.delete(rule)
    session.commit()

    # The category base unit must exist and be active.
    _set(session, ids["kg CO2e"], is_active=False)
    _refused(session, ids, "tCO2e", "t CO2e", T_ACK, 422, "base unit")
    _set(session, ids["kg CO2e"], is_active=True)

    tonne = session.get(Unit, ids["t CO2e"])
    tonne.category_id = session.get(Unit, ids["L"]).category_id
    session.commit()
    _refused(
        session, ids, "tCO2e", "t CO2e", T_ACK, 422, "different categories", "tCO2e"
    )


def test_factor_commit_refuses_tampering_expiry_and_races(db_factory, api):
    _, _, session = api
    ids = seed_production_shape(session)
    preview = _factor_preview(session, ids, "kgCO2e", "kg CO2e", KG_ACK)

    with pytest.raises(admin_catalog.AdminCatalogError) as exc:
        _factor_commit(session, preview, ack=T_ACK)
    assert exc.value.status_code == 422

    forged = dict(preview["plan"], new_factor="1000")
    with pytest.raises(admin_catalog.AdminCatalogError) as exc:
        admin_catalog.commit_factor_correction(
            session,
            plan=forged,
            digest=preview["plan_digest"],
            acknowledgement=KG_ACK,
            actor=ACTOR,
        )
    assert exc.value.status_code == 422

    expired = dict(preview["plan"], expires_at="2026-01-01T00:00:00+00:00")
    with pytest.raises(admin_catalog.AdminCatalogError) as exc:
        admin_catalog.commit_factor_correction(
            session,
            plan=expired,
            digest=admin_catalog.factor_plan_digest(expired),
            acknowledgement=KG_ACK,
            actor=ACTOR,
        )
    assert exc.value.status_code == 409

    # A later catalog operation invalidates the plan (revision race).
    m3 = admin_catalog.preview_repair(
        session,
        conflict_id=_conflict_id(session, "m3"),
        deactivate_unit_id=ids["m3"],
        retain_unit_id=ids["m³"],
        reason="duplicate",
    )
    admin_catalog.commit_repair(
        session, plan=m3["plan"], digest=m3["plan_digest"], actor=ACTOR
    )
    with pytest.raises(admin_catalog.AdminCatalogError) as exc:
        _factor_commit(session, preview)
    session.rollback()
    assert exc.value.status_code == 409
    assert db_factory().get(Unit, ids["kgCO2e"]).is_active is True


def test_factor_plan_binds_both_offsets(db_factory, api):
    _, _, session = api
    ids = seed_production_shape(session)
    preview = _factor_preview(session, ids, "kgCO2e", "kg CO2e", KG_ACK)
    assert (preview["plan"]["old_offset"], preview["plan"]["new_offset"]) == ("0", "0")

    forged = dict(preview["plan"], new_offset="5")
    with pytest.raises(admin_catalog.AdminCatalogError) as exc:
        admin_catalog.commit_factor_correction(
            session,
            plan=forged,
            digest=preview["plan_digest"],
            acknowledgement=KG_ACK,
            actor=ACTOR,
        )
    assert exc.value.status_code == 422

    # The retained unit's offset changes after the preview: refused, no mutation.
    _set(session, ids["kg CO2e"], conversion_offset=Decimal("5"))
    with pytest.raises(admin_catalog.AdminCatalogError) as exc:
        _factor_commit(session, preview)
    session.rollback()
    assert exc.value.status_code in (409, 422)
    check = db_factory()
    assert check.get(Unit, ids["kgCO2e"]).is_active is True
    assert check.query(AdminCatalogOperation).count() == 0

    _set(session, ids["kg CO2e"], conversion_offset=Decimal("0"))
    committed = _factor_commit(session, preview)
    audit = db_factory().query(AdminCatalogOperation).one()
    assert (audit.counts["old_offset"], audit.counts["new_offset"]) == ("0", "0")
    assert committed["remaining_conflicts"]


def test_unexpected_conflict_rolls_back_inside_the_transaction(
    db_factory, api, monkeypatch
):
    _, _, session = api
    ids = seed_production_shape(session)
    preview = _factor_preview(session, ids, "kgCO2e", "kg CO2e", KG_ACK)
    real = admin_catalog.verify_catalog_in_transaction

    def injected(db, expected_ids):
        return real(db, [*expected_ids, "f" * 64])

    monkeypatch.setattr(admin_catalog, "verify_catalog_in_transaction", injected)
    with pytest.raises(admin_catalog.AdminCatalogError) as exc:
        _factor_commit(session, preview)
    session.rollback()
    assert exc.value.status_code == 409
    check = db_factory()
    assert check.get(Unit, ids["kgCO2e"]).is_active is True
    assert check.query(AdminCatalogOperation).count() == 0


def test_concurrent_commit_waits_for_the_catalog_lock(db_factory, api):
    _, _, session = api
    ids = seed_production_shape(session)
    preview = _factor_preview(session, ids, "kgCO2e", "kg CO2e", KG_ACK)
    holder = db_factory()
    admin_catalog.lock_unit_catalog(holder)
    waiter = db_factory()
    waiter.execute(text("SET lock_timeout = '300ms'"))
    with pytest.raises(OperationalError):
        _factor_commit(waiter, preview)
    waiter.rollback()
    holder.rollback()
    assert _factor_commit(waiter, preview)["remaining_conflicts"]


# --------------------------------------------------------------------------
# Impact report
# --------------------------------------------------------------------------


def _create_value_revisions(session):
    # Only the columns the read-only impact report selects.
    session.execute(
        text(
            "CREATE TABLE value_revisions (id varchar(100) PRIMARY KEY, "
            "unit varchar(120), original_unit varchar(120), conversion_trace jsonb, "
            "numeric_value numeric)"
        )
    )
    session.execute(
        text(
            "INSERT INTO value_revisions VALUES "
            "('r1', 'kg CO2e', 'kgCO2e', '{\"secret\": \"TRACE\"}', 41), "
            "('r2', 'KGCO2E', NULL, NULL, 42)"
        )
    )
    session.commit()


def _value(value_id, unit, original_unit, converted):
    return ESGValue(
        id=value_id,
        tenant_id="tenant-a",
        ownership_state="resolved",
        concept="urn:sds:SECRET-CONCEPT",
        entity="SECRET-ENTITY",
        period=date(2024, 12, 31),
        value=Decimal("12.5"),
        unit=unit,
        original_unit=original_unit,
        conversion_applied=converted,
        value_metadata={"note": "SECRET-META"},
    )


def test_impact_report_counts_pages_and_never_mutates(db_factory, api):
    client, headers, session = api
    ids = seed_production_shape(session)
    session.add_all(
        [
            _value("v1", "kg CO2e", "kgCO2e", True),
            _value("v2", "kgco2e", None, False),
            _value("v3", "kg CO2e", "t CO2e", True),
            _value("v4", "MWh", None, False),
        ]
    )
    session.commit()
    _create_value_revisions(session)
    before_units = [
        admin_catalog.unit_snapshot(u) for u in session.query(Unit).order_by(Unit.id)
    ]

    url = f"/api/v1/admin/unit-catalog/units/{ids['kgCO2e']}/impact"
    first = client.get(url + "?limit=2", headers=headers)
    assert first.status_code == 200, first.text
    report = first.json()
    assert report["identifiers"] == ["kg co2e", "kgco2e"]
    assert report["counts"]["esg_values"] == {
        "unit": {"converted": 2, "not_converted": 1},
        "original_unit": {"converted": 1, "not_converted": 0},
        "rows": 3,
    }
    assert report["counts"]["value_revisions"] == {
        "unit": {"converted": 1, "not_converted": 1},
        "original_unit": {"converted": 1, "not_converted": 0},
        "rows": 2,
    }
    assert report["truncated"] is True and report["timed_out"] is False
    seen = [(i["table"], i["id"]) for i in report["items"]]
    cursor = report["next_cursor"]
    while cursor:
        page = client.get(url, params={"limit": 2, "cursor": cursor}, headers=headers)
        assert page.status_code == 200, page.text
        seen += [(i["table"], i["id"]) for i in page.json()["items"]]
        cursor = page.json()["next_cursor"]
    assert seen == [
        ("esg_values", "v1"),
        ("esg_values", "v2"),
        ("esg_values", "v3"),
        ("value_revisions", "r1"),
        ("value_revisions", "r2"),
    ]
    for item in report["items"]:
        assert set(item) == {"table", "id"}
    for secret in ("SECRET", "TRACE", "12.5"):
        assert secret not in first.text

    check = db_factory()
    assert check.query(ESGValue).count() == 4
    assert [
        admin_catalog.unit_snapshot(u) for u in check.query(Unit).order_by(Unit.id)
    ] == before_units
    assert check.query(AdminCatalogOperation).count() == 0

    missing = client.get(
        "/api/v1/admin/unit-catalog/units/999999/impact", headers=headers
    )
    assert missing.status_code == 404
    bad = client.get(url, params={"cursor": "not-a-cursor"}, headers=headers)
    assert bad.status_code == 422


def test_impact_report_is_read_only_and_times_out(db_factory, api, monkeypatch):
    _, _, session = api
    seed_production_shape(session)
    real_model = admin_catalog._impact_model

    def writing_model(table):
        # A write attempted inside the report transaction must be refused.
        session.execute(text("CREATE TABLE should_not_exist (id int)"))
        return real_model(table)

    monkeypatch.setattr(admin_catalog, "_impact_model", writing_model)
    with pytest.raises(Exception) as exc:
        admin_catalog.impact_report(session, identifiers=["kgco2e"])
    assert "read-only" in str(exc.value)
    monkeypatch.undo()

    session.add(_value("v1", "kgCO2e", None, False))
    session.commit()
    session.execute(text("ALTER TABLE esg_values RENAME TO esg_values_data"))
    session.execute(
        text(
            "CREATE VIEW esg_values AS SELECT d.* FROM esg_values_data d, "
            "LATERAL (SELECT pg_sleep(0.5)) s"
        )
    )
    session.commit()
    monkeypatch.setattr(admin_catalog, "IMPACT_STATEMENT_TIMEOUT", "100ms")
    report = admin_catalog.impact_report(session, identifiers=["kgco2e"])
    assert report["timed_out"] is True and report["truncated"] is True
    assert report["counts"] is None and report["items"] == []
