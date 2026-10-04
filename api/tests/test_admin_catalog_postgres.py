"""Unit-catalog repair on an explicitly disposable PostgreSQL.

Creates only the unit and audit tables it needs in a disposable database.
Run with SDS_MIGRATION_TEST_DATABASE_URL and SDS_MIGRATION_TEST_ALLOW_RESET=true.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

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
from src.services import admin_catalog

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


def _seed(session, *, m3_aliases=(), m3_factor="1", m3_name="cubic meter (duplicate)"):
    volume = UnitCategory(name="volume", base_unit="m³")
    session.add(volume)
    session.flush()
    rows = {
        "m³": Unit(
            category_id=volume.id,
            symbol="m³",
            name="cubic meter",
            conversion_factor=Decimal("1"),
            conversion_offset=Decimal("0"),
            aliases=["m3", "cubic_meter"],
            is_active=True,
        ),
        "L": Unit(
            category_id=volume.id,
            symbol="L",
            name="liter",
            conversion_factor=Decimal("0.001"),
            conversion_offset=Decimal("0"),
            aliases=["l", "liter"],
            is_active=True,
        ),
        "m3": Unit(
            category_id=volume.id,
            symbol="m3",
            name=m3_name,
            conversion_factor=Decimal(m3_factor),
            conversion_offset=Decimal("0"),
            aliases=list(m3_aliases),
            unit_metadata={"source": "legacy"},
            is_active=True,
        ),
    }
    session.add_all(rows.values())
    session.commit()
    return {symbol: row.id for symbol, row in rows.items()}


def _conflict(session):
    listing = admin_catalog.list_conflicts(session, limit=100, offset=0)
    assert listing["total"] == 1
    return listing["items"][0]


def _preview(session, ids, **overrides):
    conflict = _conflict(session)
    params = {
        "conflict_id": conflict["conflict_id"],
        "deactivate_unit_id": ids["m3"],
        "retain_unit_id": ids["m³"],
        "reason": "duplicate of m³",
    }
    params.update(overrides)
    return admin_catalog.preview_repair(session, **params)


def test_conflict_listing_matches_the_converter(db_factory):
    session = db_factory()
    _seed(session)
    conflict = _conflict(session)
    assert conflict["kind"] == "expression_token"
    assert {unit["symbol"] for unit in conflict["units"]} == {"m³", "m3"}
    converter = UnitConverter(storage_strategy=PostgresStrategy(db_factory()))
    with pytest.raises(ValueError, match="ambiguous expression unit token"):
        converter.get_physical_converter()


def test_repair_commit_resolves_conflict_and_keeps_m3_text_convertible(db_factory):
    session = db_factory()
    ids = _seed(session)
    preview = _preview(session, ids)
    result = admin_catalog.commit_repair(
        session, plan=preview["plan"], digest=preview["plan_digest"], actor=ACTOR
    )

    check = db_factory()
    m3 = check.get(Unit, ids["m3"])
    assert m3.is_active is False
    assert m3.unit_metadata["deactivation"]["superseded_by"] == "m³"
    assert m3.unit_metadata["source"] == "legacy"
    audit = check.query(AdminCatalogOperation).one()
    assert audit.action == "unit_repair_commit" and audit.result == "ok"
    assert audit.unit_snapshot == preview["plan"]["before_image"]
    assert audit.catalog_revision_after == audit.id == result["catalog_revision"]
    assert admin_catalog.list_conflicts(check, limit=100, offset=0)["total"] == 0

    converter = UnitConverter(storage_strategy=PostgresStrategy(db_factory()))
    converter.get_physical_converter()
    assert converter.normalize_unit_symbol("m3") == "m³"
    assert converter.convert(2, "m3", "L").converted_value == 2000


def test_second_process_reloads_after_repair(db_factory):
    session = db_factory()
    ids = _seed(session)
    other = UnitConverter(storage_strategy=PostgresStrategy(db_factory()))
    with pytest.raises(ValueError):
        other.get_physical_converter()

    preview = _preview(session, ids)
    admin_catalog.commit_repair(
        session, plan=preview["plan"], digest=preview["plan_digest"], actor=ACTOR
    )
    other._last_revision_check = 0
    assert other.normalize_unit_symbol("m3") == "m³"
    other.get_physical_converter()


@pytest.mark.parametrize(
    "seed,direction",
    [
        ({"m3_factor": "2"}, "drop_m3"),  # different conversion factor
        ({"m3_aliases": ["kubikmeter"]}, "drop_m3"),  # alias m³ does not cover
        ({}, "drop_m³"),  # m3 does not cover m³'s identifiers; m³ is base unit
    ],
)
def test_unsafe_repairs_are_refused_without_changes(db_factory, seed, direction):
    session = db_factory()
    ids = _seed(session, **seed)
    override = (
        {"deactivate_unit_id": ids["m³"], "retain_unit_id": ids["m3"]}
        if direction == "drop_m³"
        else {}
    )
    with pytest.raises(admin_catalog.AdminCatalogError) as exc:
        _preview(session, ids, **override)
    assert exc.value.status_code == 422
    check = db_factory()
    assert all(row.is_active for row in check.query(Unit).all())
    assert check.query(AdminCatalogOperation).count() == 0


def test_active_conversion_rule_reference_blocks_repair(db_factory):
    session = db_factory()
    ids = _seed(session)
    session.add(ConversionRule(from_unit="m3", to_unit="L", formula="value * 1000"))
    session.commit()
    with pytest.raises(admin_catalog.AdminCatalogError) as exc:
        _preview(session, ids)
    assert "conversion rules" in exc.value.message


def test_stale_expired_or_forged_plans_are_refused(db_factory):
    session = db_factory()
    ids = _seed(session)
    preview = _preview(session, ids)

    forged = dict(preview["plan"], reason="other")
    with pytest.raises(admin_catalog.AdminCatalogError) as exc:
        admin_catalog.commit_repair(
            session, plan=forged, digest=preview["plan_digest"], actor=ACTOR
        )
    assert exc.value.status_code == 422

    expired = dict(
        preview["plan"],
        expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    with pytest.raises(admin_catalog.AdminCatalogError) as exc:
        admin_catalog.commit_repair(
            session,
            plan=expired,
            digest=admin_catalog.plan_digest(expired),
            actor=ACTOR,
        )
    assert exc.value.status_code == 409

    session.add(
        AdminCatalogOperation(
            actor_user_id="someone",
            auth_method="bearer",
            action="unit_repair_reverse",
            scope="unit_catalog",
            result="ok",
            unit_snapshot={"id": 0},
        )
    )
    session.commit()
    with pytest.raises(admin_catalog.AdminCatalogError) as exc:
        admin_catalog.commit_repair(
            session, plan=preview["plan"], digest=preview["plan_digest"], actor=ACTOR
        )
    assert exc.value.status_code == 409
    assert db_factory().get(Unit, ids["m3"]).is_active is True


def test_reverse_restores_the_exact_snapshot(db_factory):
    session = db_factory()
    ids = _seed(session)
    preview = _preview(session, ids)
    before = preview["plan"]["before_image"]
    committed = admin_catalog.commit_repair(
        session, plan=preview["plan"], digest=preview["plan_digest"], actor=ACTOR
    )
    reversed_ = admin_catalog.reverse_repair(
        session, repair_id=committed["repair_id"], actor=ACTOR
    )

    check = db_factory()
    assert admin_catalog.unit_snapshot(check.get(Unit, ids["m3"])) == before
    rows = check.query(AdminCatalogOperation).order_by(AdminCatalogOperation.id).all()
    assert [row.action for row in rows] == ["unit_repair_commit", "unit_repair_reverse"]
    assert all(row.unit_snapshot for row in rows)
    assert reversed_["catalog_revision"] == rows[1].id
    assert admin_catalog.list_conflicts(check, limit=100, offset=0)["total"] == 1

    with pytest.raises(admin_catalog.AdminCatalogError) as exc:
        admin_catalog.reverse_repair(
            session, repair_id=committed["repair_id"], actor=ACTOR
        )
    assert exc.value.status_code == 409


def test_reverse_refuses_after_a_later_operation_or_a_modified_row(db_factory):
    session = db_factory()
    ids = _seed(session)
    preview = _preview(session, ids)
    committed = admin_catalog.commit_repair(
        session, plan=preview["plan"], digest=preview["plan_digest"], actor=ACTOR
    )

    unit = session.get(Unit, ids["m3"])
    unit.name = "edited after repair"
    session.commit()
    with pytest.raises(admin_catalog.AdminCatalogError) as exc:
        admin_catalog.reverse_repair(
            session, repair_id=committed["repair_id"], actor=ACTOR
        )
    assert exc.value.status_code == 409

    session.add(
        AdminCatalogOperation(
            actor_user_id="someone",
            auth_method="bearer",
            action="unit_repair_commit",
            scope="unit_catalog",
            result="ok",
            unit_snapshot={"id": 0},
        )
    )
    session.commit()
    with pytest.raises(admin_catalog.AdminCatalogError) as exc:
        admin_catalog.reverse_repair(
            session, repair_id=committed["repair_id"], actor=ACTOR
        )
    assert exc.value.status_code == 409


def test_snapshot_is_required_for_ok_repair_rows(db_factory):
    from sqlalchemy.exc import IntegrityError

    session = db_factory()
    session.add(
        AdminCatalogOperation(
            actor_user_id="someone",
            auth_method="bearer",
            action="unit_repair_commit",
            scope="unit_catalog",
            result="ok",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def _production_like_seed(session):
    # Production shape: m3 is named "cubic metre" and aliases "m³".
    return _seed(session, m3_aliases=["m³"], m3_name="cubic metre")


def test_production_shaped_duplicate_is_repairable_when_its_name_is_unused(db_factory):
    session = db_factory()
    ids = _production_like_seed(session)
    preview = _preview(session, ids)
    admin_catalog.commit_repair(
        session, plan=preview["plan"], digest=preview["plan_digest"], actor=ACTOR
    )
    assert db_factory().get(Unit, ids["m3"]).is_active is False


def test_repair_refused_when_stored_values_use_the_uncovered_name(db_factory):
    session = db_factory()
    ids = _production_like_seed(session)
    session.add(
        ESGValue(
            id="v1",
            concept="urn:sds:reg:esrs:e3_4_01",
            entity="e1",
            period=datetime(2024, 12, 31).date(),
            value=Decimal("1"),
            unit="Cubic Metre",
            ownership_state="quarantined",
        )
    )
    session.commit()
    with pytest.raises(admin_catalog.AdminCatalogError) as exc:
        _preview(session, ids)
    assert exc.value.status_code == 422
    assert "cubic metre" in exc.value.message
    assert db_factory().get(Unit, ids["m3"]).is_active is True


def _hold_lock_and_bump_revision(factory, started, release):
    """Second session: take the catalog lock, commit another catalog op."""
    other = factory()
    admin_catalog.lock_unit_catalog(other)
    started.set()
    release.wait(10)
    other.add(
        AdminCatalogOperation(
            actor_user_id="other-admin",
            auth_method="bearer",
            action="unit_repair_reverse",
            scope="unit_catalog",
            result="ok",
            unit_snapshot={"id": 0},
        )
    )
    other.commit()


def _run_while_locked(db_factory, action):
    import threading

    started, release = threading.Event(), threading.Event()
    holder = threading.Thread(
        target=_hold_lock_and_bump_revision, args=(db_factory, started, release)
    )
    holder.start()
    assert started.wait(10)
    result = {}

    def run():
        try:
            result["value"] = action()
        except Exception as exc:  # noqa: BLE001 - asserted below
            result["error"] = exc

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(1.0)
    assert worker.is_alive(), "mutation must wait for the catalog lock"
    release.set()
    holder.join(10)
    worker.join(10)
    return result


def test_concurrent_catalog_change_makes_a_waiting_commit_stale(db_factory):
    session = db_factory()
    ids = _seed(session)
    preview = _preview(session, ids)
    session.rollback()
    result = _run_while_locked(
        db_factory,
        lambda: admin_catalog.commit_repair(
            session, plan=preview["plan"], digest=preview["plan_digest"], actor=ACTOR
        ),
    )
    assert isinstance(result.get("error"), admin_catalog.AdminCatalogError)
    assert result["error"].status_code == 409
    session.rollback()
    assert db_factory().get(Unit, ids["m3"]).is_active is True


def test_concurrent_catalog_change_blocks_a_waiting_reverse(db_factory):
    session = db_factory()
    ids = _seed(session)
    preview = _preview(session, ids)
    committed = admin_catalog.commit_repair(
        session, plan=preview["plan"], digest=preview["plan_digest"], actor=ACTOR
    )
    result = _run_while_locked(
        db_factory,
        lambda: admin_catalog.reverse_repair(
            session, repair_id=committed["repair_id"], actor=ACTOR
        ),
    )
    assert isinstance(result.get("error"), admin_catalog.AdminCatalogError)
    assert result["error"].status_code == 409
    session.rollback()
    assert db_factory().get(Unit, ids["m3"]).is_active is False
