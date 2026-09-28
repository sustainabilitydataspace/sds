"""Disposable-PostgreSQL integration coverage for the public demo runtime.

This intentionally uses the real strict importer, persistence models, hierarchy,
and DB-backed unit service. It is gated like the other destructive disposable-DB
tests and does not claim that bounded UC-04..UC-06 metadata is an engine.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from scripts import demo_runtime as demo
from src.config.settings import settings
from src.database.init_db import init_db_for_engine


@pytest.fixture(autouse=True)
def revision_write_configuration(monkeypatch):
    """Match the explicit host-CLI configuration used by the Make targets."""
    for name, value in demo.REQUIRED_REVISION_WRITE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(settings, "value_revision_api_enabled", True)
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", True)
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")


@pytest.fixture
def disposable_postgres_url():
    url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    if make_url(url).get_backend_name() != "postgresql":
        pytest.skip("public demo integration requires disposable PostgreSQL")

    engine = create_engine(url)
    try:
        init_db_for_engine(engine)
        yield url
    finally:
        engine.dispose()


def test_public_demo_strict_import_and_db_unit_service(disposable_postgres_url):
    demo.reset(disposable_postgres_url)
    try:
        installed = demo.install(disposable_postgres_url)
        verified = demo.verify_database(disposable_postgres_url)
    finally:
        with pytest.raises(ValueError, match="immutable demo revision history"):
            demo.reset(disposable_postgres_url)
        # Disposable test DB is removed by its owning container; the reset may
        # not delete revision events or source rows to simulate a clean slate.

    assert installed["imported_rows"] == 7
    assert verified["persisted_rows"] == 7
    uc02 = verified["service_verification"]["UC-02"][0]
    assert uc02 == {
        "external_key": "demo-wave1:uc02-emissions",
        "source_unit": "kg CO2e",
        "target_unit": "t CO2e",
        "target_value": "2.500000",
    }
    assert verified["db_unit_service_verification"] == {
        "external_key": "demo-wave1:uc02-emissions",
        "source_unit": "kg CO2e",
        "target_unit": "t CO2e",
        "target_value": "2.500000",
    }
    assert len(verified["service_verification"]["UC-03"]) == 2
    for use_case in ("UC-04", "UC-05", "UC-06"):
        result = verified["service_verification"][use_case][0]
        assert result["verification"] == "bounded_data_contract"
        assert result["prohibited_conclusion"].startswith("No ")
