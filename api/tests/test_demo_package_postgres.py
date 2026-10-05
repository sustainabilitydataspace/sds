"""Demo A2.3 install on a fully migrated, explicitly disposable PostgreSQL.

Requires SDS_MIGRATION_TEST_DATABASE_URL (a server with btree_gist) and
SDS_MIGRATION_TEST_ALLOW_RESET=true. A template database is migrated once and
every test gets a fresh copy.
"""

from __future__ import annotations

import os
import threading
import uuid
from decimal import Decimal

import pytest
from rdflib import Graph
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from src.calculation.postgres_strategy import PostgresStrategy
from src.calculation.unit_converter import UnitConverter
from src.database.models import (
    AdminDemoPackageInstall,
    CanonicalCalculationContract,
    ESGValue,
    HierarchyConfiguration,
    Indicator,
    MappingAssertionGroup,
    MaterializedPairwiseMapping,
)
from src.services import canonical_pairwise_materialization as materialization
from src.services import demo_package

ACTOR = demo_package.Actor("user_admin", "bearer", "req-demo")
TEMPLATE = "sds_demo_tpl"


def _server_url():
    url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql":
        pytest.skip("requires disposable PostgreSQL")
    return parsed


@pytest.fixture(scope="module")
def template():
    parsed = _server_url()
    admin = create_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEMPLATE}"))
        conn.execute(text(f"CREATE DATABASE {TEMPLATE}"))
    engine = create_engine(parsed.set(database=TEMPLATE))
    from src.database.bootstrap_units import bootstrap_units_if_empty
    from src.database.init_db import init_db_for_engine

    init_db_for_engine(engine)
    with sessionmaker(bind=engine)() as session:
        bootstrap_units_if_empty(session)
        session.commit()
    engine.dispose()
    yield parsed, admin
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEMPLATE}"))
    admin.dispose()


@pytest.fixture(autouse=True)
def revision_backed_values(monkeypatch):
    """Production runs with revision-backed value writes and reads."""
    from src.config.settings import settings

    monkeypatch.setattr(settings, "value_revision_api_enabled", True)
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", True)
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    monkeypatch.setattr(settings, "require_database", True)


@pytest.fixture
def maker(template):
    parsed, admin = template
    name = f"sds_demo_t_{uuid.uuid4().hex[:10]}"
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {name} TEMPLATE {TEMPLATE}"))
    engine = create_engine(parsed.set(database=name), pool_size=6)
    factory = sessionmaker(bind=engine)
    sessions = []

    def make():
        session = factory()
        sessions.append(session)
        return session

    yield make
    for session in sessions:
        session.close()
    engine.dispose()
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))


def _runtime(session):
    return {
        "converter": UnitConverter(storage_strategy=PostgresStrategy(session)),
        "graph": Graph(),
    }


def _install(session, package=None):
    return demo_package.install(
        session,
        package or demo_package.load_package(),
        actor=ACTOR,
        **_runtime(session),
    )


def _counts(session):
    return {
        "indicators": session.query(Indicator).count(),
        "contracts": session.query(CanonicalCalculationContract).count(),
        "values": session.query(ESGValue).count(),
        "hierarchies": session.query(HierarchyConfiguration).count(),
        "groups": session.query(MappingAssertionGroup).count(),
        "pairs": session.query(MaterializedPairwiseMapping)
        .filter(MaterializedPairwiseMapping.is_current.is_(True))
        .count(),
    }


def test_install_on_empty_db_reproduces_v1_to_v5(maker):
    session = maker()
    result = _install(session)
    assert result["state"] == "installed"
    assert result["counts"]["mappings"]["created"] >= 12  # 6 groups + 6 components
    assert result["counts"]["materialize"]["created"] >= 3

    from src.api.routers.admin_demo import run_demo_checks

    check = maker()
    checks = run_demo_checks(check, **_runtime(check))
    assert checks["V1_indicator_with_codes"]["ok"], checks
    assert checks["V2_values"]["count"] == 9
    assert checks["V3_calculation"]["value"] == "832.964"
    assert "e1_5_10 + e1_5_11 + e1_5_12 + e1_5_13 + e1_5_14" in (
        checks["V3_calculation"]["formula"]
    )
    assert sorted(checks["V5_dependencies"]["inputs"]) == [
        "urn:sds:reg:esrs:e1_5_10",
        "urn:sds:reg:esrs:e1_5_11",
        "urn:sds:reg:esrs:e1_5_12",
        "urn:sds:reg:esrs:e1_5_13",
        "urn:sds:reg:esrs:e1_5_14",
    ]
    assert checks["V4_mappings"]["count"] >= 3
    assert all(item["ok"] for item in checks.values()), checks
    ledger = check.query(AdminDemoPackageInstall).one()
    assert ledger.state == "installed" and ledger.package_digest == (
        demo_package.MANIFEST_SHA256
    )


def test_reinstall_is_a_no_op(maker):
    session = maker()
    _install(session)
    before = _counts(maker())
    second = _install(maker())
    assert second["state"] == "installed"
    assert all(
        counts.get("created", 0) == 0 for counts in second["counts"].values()
    ), second["counts"]
    assert _counts(maker()) == before


def _create_like_production(session):
    """What the 2026-10-04 API calls created: the hierarchy and the 9 values."""
    import io
    import json

    from src.api.models import HierarchyConfiguration as HierarchyModel
    from src.services.canonical_concept_store import CanonicalConceptStore
    from src.services.hierarchy_store import DatabaseHierarchyStore
    from src.services.indicator_import import apply_indicator_import_transactional
    from src.services.indicator_store import IndicatorStore
    from src.services.value_batch import execute_value_batch
    from src.services.value_csv_import import load_values_from_handle
    from src.services.value_store import DatabaseValueStore

    package = demo_package.load_package()
    # In production the concepts were already known to value ingest.
    apply_indicator_import_transactional(
        csv_text=package.files["indicators.csv"].decode(),
        db=session,
        source_ref="test",
        source_hash=None,
        created_by="test",
    )
    hierarchy = HierarchyModel(**json.loads(package.files["hierarchy.json"]))
    store = DatabaseHierarchyStore(session)
    store.create(hierarchy, config_id=str(uuid.uuid4()), created_by="manager")
    items = load_values_from_handle(io.StringIO(package.files["values.csv"].decode()))
    runtime = _runtime(session)
    response = execute_value_batch(
        items=items,
        converter=runtime["converter"],
        store=DatabaseValueStore(session, tenant_id=demo_package.TENANT),
        hierarchy_store=store,
        indicator_store=IndicatorStore(db=session),
        canonical_concept_store=CanonicalConceptStore(db=session),
        graph=runtime["graph"],
        created_by="nordhaven_data_manager",
        company_id=demo_package.TENANT,
        strict=True,
    )
    assert response.committed and response.accepted_rows == 9, response


def test_existing_production_rows_are_reused(maker):
    """Production already has the hierarchy and the 9 values (2026-10-04)."""
    _create_like_production(maker())
    result = _install(maker())
    assert result["counts"]["values"]["created"] == 0
    assert result["counts"]["values"]["unchanged"] == 9
    assert result["counts"]["hierarchy"]["unchanged"] == 1


def _expect_conflict(maker, step):
    before = _counts(maker())
    with pytest.raises(demo_package.DemoPackageError) as exc:
        _install(maker())
    assert exc.value.status_code == 409 and exc.value.step == step
    assert _counts(maker()) == before


def test_different_value_is_a_conflict(maker):
    _install(maker())
    session = maker()
    value = (
        session.query(ESGValue)
        .filter(ESGValue.external_key == "nordhaven-uc01:nh_group:e1_5_10:2024")
        .one()
    )
    session.execute(
        text("UPDATE esg_values SET value = :v WHERE id = :id"),
        {"v": Decimal("1"), "id": value.id},
    )
    session.commit()
    _expect_conflict(maker, "values")


def test_different_hierarchy_is_a_conflict(maker):
    session = maker()
    session.add(
        HierarchyConfiguration(
            id=str(uuid.uuid4()),
            company_id=demo_package.TENANT,
            hierarchy_type="organizational",
            name="Other",
            configuration='{"levels": [{"id": "nh_group", "name": "X", "level": 0}]}',
            is_active=True,
        )
    )
    session.commit()
    _expect_conflict(maker, "hierarchy")


def test_inactive_indicator_is_a_conflict(maker):
    _install(maker())
    session = maker()
    session.execute(
        text("UPDATE indicators SET is_active = false WHERE identifier = :i"),
        {"i": "urn:sds:reg:esrs:e1_5_02"},
    )
    session.commit()
    _expect_conflict(maker, "indicators")


def test_foreign_group_with_a_demo_hash_is_a_conflict(maker):
    _install(maker())
    session = maker()
    session.execute(
        text(
            "UPDATE mapping_assertion_groups SET created_by = 'someone-else' "
            "WHERE mapping_profile = :p"
        ),
        {"p": demo_package.MAPPING_PROFILE},
    )
    session.commit()
    _expect_conflict(maker, "mappings")


def test_foreign_contract_with_a_different_formula_is_a_conflict(maker):
    _install(maker())
    session = maker()
    session.execute(
        text(
            "UPDATE canonical_calculation_contracts SET runtime_expression = "
            "'e1_5_10 + e1_5_11' WHERE indicator_identifier = :i"
        ),
        {"i": "urn:sds:reg:esrs:e1_5_02"},
    )
    # The contract now belongs to another (foreign) package import.
    session.execute(
        text("UPDATE atomizer_package_imports SET package_hash = :h"), {"h": "f" * 64}
    )
    session.commit()
    _expect_conflict(maker, "contract")


@pytest.mark.parametrize(
    "step", ["indicators", "contract", "hierarchy", "values", "mappings", "materialize"]
)
def test_failure_after_each_step_persists_nothing(maker, monkeypatch, step):
    def boom(*args, **kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(
        demo_package, "_after_step_hook", lambda name: boom() if name == step else None
    )
    before = _counts(maker())
    with pytest.raises(RuntimeError):
        _install(maker())
    assert _counts(maker()) == before
    failed = maker().query(AdminDemoPackageInstall).one()
    assert failed.state == "failed" and failed.failed_step == step
    assert failed.error_class == "RuntimeError"


def test_concurrent_installs_serialize(maker):
    results, errors = [], []

    def run():
        try:
            results.append(_install(maker())["state"])
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)
    assert not errors and results == ["installed", "installed"]
    assert _counts(maker())["values"] == 9


def test_materializer_profiles_coexist(maker):
    """Default profile wins a shared pair; demo-only pairs stay current."""
    _install(maker())
    session = maker()
    demo_pairs = _counts(session)["pairs"]
    # Promote one demo assertion pair to an approved default-profile copy.
    session.execute(
        text(
            "INSERT INTO mapping_assertion_groups (source_datapoint_id, mapping_profile, "
            "relationship_type, coverage_status, confidence, valid_from, approval_status, "
            "publication_status, assertion_hash, created_by) "
            "SELECT source_datapoint_id, 'default', relationship_type, coverage_status, "
            "confidence, valid_from, 'approved', publication_status, "
            "encode(sha256(('default-' || assertion_hash)::bytea), 'hex'), "
            "'full-package' FROM mapping_assertion_groups "
            "WHERE mapping_profile = :p"
        ),
        {"p": demo_package.MAPPING_PROFILE},
    )
    session.execute(
        text(
            "INSERT INTO mapping_assertion_components (assertion_group_id, "
            "canonical_concept_id, component_order, component_role, coverage_fraction) "
            "SELECT d.id, c.canonical_concept_id, c.component_order, c.component_role, "
            "c.coverage_fraction FROM mapping_assertion_components c "
            "JOIN mapping_assertion_groups g ON g.id = c.assertion_group_id "
            "JOIN mapping_assertion_groups d ON d.assertion_hash = "
            "encode(sha256(('default-' || g.assertion_hash)::bytea), 'hex')"
        )
    )
    session.commit()
    report = materialization.materialize_pairwise_mappings(db=maker())
    assert report.committed
    assert report.counts.get("pairwise_superseded_other_profile", 0) == demo_pairs
    after = maker()
    assert _counts(after)["pairs"] == demo_pairs
    # Re-install of the demo: covered pairs are not taken back.
    again = _install(maker())
    assert again["state"] == "installed"
    assert _counts(maker())["pairs"] == demo_pairs


def test_default_only_materialization_behaviour_is_unchanged(maker):
    session = maker()
    report = materialization.materialize_pairwise_mappings(db=session)
    assert report.committed and report.candidate_count == 0


def test_dry_run_cannot_skip_commit(maker):
    with pytest.raises(ValueError):
        materialization.materialize_pairwise_mappings(
            db=maker(), dry_run=True, commit=False
        )


def _pending_ledger_row(session):
    session.add(
        AdminDemoPackageInstall(
            package_id="probe",
            package_digest="0" * 64,
            actor_user_id="probe",
            auth_method="bearer",
            state="installed",
        )
    )
    session.flush()


def _mapping_dir(tmp_path):
    package = demo_package.load_package()
    target = tmp_path / "mappings"
    target.mkdir()
    for name, data in package.files.items():
        if name.startswith("mappings/"):
            (target / name.split("/", 1)[1]).write_bytes(data)
    return target


def test_importer_security_error_keeps_caller_transaction(maker, tmp_path):
    from src.services.canonical_mapping_db_import import (
        import_canonical_mapping_package_to_db,
    )

    target = _mapping_dir(tmp_path)
    data = target / "sds_standard_releases.csv"
    outside = tmp_path / "outside.csv"
    outside.write_bytes(data.read_bytes())
    data.unlink()
    data.symlink_to(outside)
    session = maker()
    _pending_ledger_row(session)
    report = import_canonical_mapping_package_to_db(
        package_dir=target, db=session, commit=False
    )
    assert report.valid is False
    assert session.query(AdminDemoPackageInstall).count() == 1


def test_importer_blocked_path_keeps_caller_transaction(maker, tmp_path):
    from src.services.canonical_mapping_db_import import (
        import_canonical_mapping_package_to_db,
    )

    session = maker()
    _pending_ledger_row(session)
    report = import_canonical_mapping_package_to_db(
        package_dir=_mapping_dir(tmp_path),
        db=session,
        commit=False,
        installed_standard_releases={("ESRS", "not-installed")},
    )
    assert report.blocked is True
    assert session.query(AdminDemoPackageInstall).count() == 1


def test_mapping_import_lock_is_held_from_preflight(maker, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from src.services.canonical_mapping_import_lock import (
        CANONICAL_MAPPING_IMPORT_LOCK_KEY,
    )

    observed = []

    def probe(step):
        if step != "values":
            return
        other = maker()
        other.execute(text("SET LOCAL lock_timeout = '300ms'"))
        try:
            other.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": CANONICAL_MAPPING_IMPORT_LOCK_KEY},
            )
            observed.append("acquired")
        except OperationalError:
            observed.append("blocked")
        finally:
            other.rollback()

    monkeypatch.setattr(demo_package, "_after_step_hook", probe)
    assert _install(maker())["state"] == "installed"
    assert observed == ["blocked"]


def test_foreign_assertion_injected_after_preflight_is_a_conflict(maker, monkeypatch):
    package = demo_package.load_package()
    first = demo_package._package_assertions(package)[0]

    def inject(step):
        if step != "mappings_preflight":
            return
        other = maker()
        datapoint_id = other.execute(
            text(
                "SELECT d.id FROM standard_datapoints d JOIN standard_releases r "
                "ON r.id = d.standard_release_id WHERE r.standard_id = :s "
                "AND r.version = :v AND d.code = :c"
            ),
            {
                "s": first["source_standard_id"],
                "v": first["source_standard_version"],
                "c": first["source_code"],
            },
        ).scalar()
        if datapoint_id is None:
            other.execute(
                text(
                    "INSERT INTO standard_releases (standard_id, name, version, "
                    "lifecycle_status) VALUES (:s, 'foreign', :v, 'active')"
                ),
                {
                    "s": first["source_standard_id"],
                    "v": first["source_standard_version"],
                },
            )
            datapoint_id = other.execute(
                text(
                    "INSERT INTO standard_datapoints (standard_release_id, code, label, "
                    "lifecycle_status) SELECT id, :c, 'foreign', 'active' FROM "
                    "standard_releases WHERE standard_id = :s AND version = :v "
                    "RETURNING id"
                ),
                {
                    "s": first["source_standard_id"],
                    "v": first["source_standard_version"],
                    "c": first["source_code"],
                },
            ).scalar()
        other.execute(
            text(
                "INSERT INTO mapping_assertion_groups (source_datapoint_id, "
                "mapping_profile, relationship_type, coverage_status, confidence, "
                "valid_from, approval_status, publication_status, assertion_hash, "
                "created_by) VALUES (:d, 'foreign', 'related', 'partial', 0.5, now(), "
                "'draft', 'internal', :h, 'someone-else')"
            ),
            {"d": datapoint_id, "h": first["assertion_hash"]},
        )
        other.commit()

    monkeypatch.setattr(demo_package, "_after_step_hook", inject)
    with pytest.raises(demo_package.DemoPackageError) as exc:
        _install(maker())
    assert exc.value.status_code == 409 and exc.value.step == "mappings"
    check = maker()
    rows = check.execute(
        text(
            "SELECT mapping_profile, relationship_type, approval_status, created_by "
            "FROM mapping_assertion_groups"
        )
    ).fetchall()
    assert rows == [("foreign", "related", "draft", "someone-else")]
    assert check.query(Indicator).count() == 0
    assert check.query(ESGValue).count() == 0


def test_demo_assertion_altered_after_preflight_is_not_repaired(maker, monkeypatch):
    _install(maker())

    def alter(step):
        if step != "mappings_preflight":
            return
        other = maker()
        other.execute(
            text(
                "UPDATE mapping_assertion_groups SET confidence = 0.10 "
                "WHERE mapping_profile = :p"
            ),
            {"p": demo_package.MAPPING_PROFILE},
        )
        other.commit()

    monkeypatch.setattr(demo_package, "_after_step_hook", alter)
    before = _counts(maker())
    with pytest.raises(demo_package.DemoPackageError) as exc:
        _install(maker())
    assert exc.value.status_code == 409 and exc.value.step == "mappings"
    check = maker()
    confidences = {
        row[0]
        for row in check.execute(
            text(
                "SELECT confidence FROM mapping_assertion_groups WHERE mapping_profile = :p"
            ),
            {"p": demo_package.MAPPING_PROFILE},
        )
    }
    assert confidences == {Decimal("0.1000")}
    assert _counts(check) == before
