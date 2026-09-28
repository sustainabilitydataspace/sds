from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.routers import indicators
from src.database.models import Indicator
from src.services.indicator_import import (
    IndicatorCsvContractError,
    IndicatorImportValidationError,
    apply_indicator_import_transactional,
    collect_legacy_identifier_migrations,
    load_indicator_rows_from_csv,
    normalize_indicator_row,
    validate_indicator_import_text,
    validate_indicator_rows,
)
from src.services.indicator_import_job_store import InMemoryIndicatorImportJobStore


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _indicator_csv(identifier: str = "urn:sds:reg:gri:gri 101_1_a") -> str:
    return (
        "identifier,title,indicator,description,dimension,unitName,unitType,periodicity,periodType,"
        "sourceRef,codeESRS,codeGRI,codeGRI_expanded,evidencePath,sourceRow,owner,accessRights,"
        "validationMethod,doubleMateriality,valueType\n"
        f"{identifier},Biodiversity policy,Biodiversity policy,Policy disclosure,E,Policies,text,annual,"
        "fy,GRI,,"
        "GRI 101-1.a,GRI 101-1.a,evidence.md,1,ESG,internal,documentary,double,narrative\n"
    )


def _invalid_indicator_csv() -> str:
    return (
        "identifier,title,indicator,description,dimension,unitName,unitType,periodicity,periodType,"
        "sourceRef,codeESRS,codeGRI,codeGRI_expanded,evidencePath,sourceRow,owner,accessRights,"
        "validationMethod,doubleMateriality,valueType\n"
        ",,,Policy disclosure,E,Policies,text,annual,fy,Internal,,,,evidence.md,1,ESG,internal,documentary,double,narrative\n"
    )


def test_indicator_import_validation_accepts_and_canonicalizes_register_csv():
    plan = validate_indicator_import_text(
        _indicator_csv(), db=None, source_sha256="abc", source_size_bytes=100
    )

    assert plan.valid is True
    assert plan.total_rows == 1
    assert plan.accepted_rows == 1
    assert plan.created_rows == 1
    assert plan.normalized_rows == 1
    assert plan.records[0]["identifier"] == "urn:sds:reg:gri:gri_101_1_a"


def test_indicator_import_normalizer_corrects_esrs_e1_5_energy_metadata():
    normalized = normalize_indicator_row(
        {
            "__row_number__": 2,
            "identifier": "urn:sds:reg:esrs:e1_5_13",
            "title": "Fuel consumption from other fossil sources",
            "indicator": "Fuel consumption from other fossil sources",
            "description": "Disclosure datapoint as defined in IG3.",
            "dimension": "E",
            "unitName": "Text",
            "unitType": "Text",
            "periodicity": "annual",
            "periodType": "fiscal_year",
            "sourceRef": "ESRS Official",
            "codeESRS": "E1-5_13",
            "codeGRI": "",
            "codeGRI_expanded": "",
            "evidencePath": "",
            "sourceRow": "",
            "owner": "system",
            "accessRights": "Internal",
            "validationMethod": "automated",
            "doubleMateriality": "",
            "valueType": "narrative",
        }
    )

    record = normalized["record"]
    assert record["unit_name"] == "MWh"
    assert record["unit_type"] == "Energy"
    assert record["value_type"] == "numeric"


def test_indicator_import_validation_reports_invalid_rows_without_writing():
    plan = validate_indicator_import_text(_invalid_indicator_csv(), db=None)

    assert plan.valid is False
    assert plan.rejected_rows == 1
    assert plan.errors[0].row_number == 2


def test_indicator_import_validation_counts_legacy_migration_as_update():
    engine = create_engine("sqlite:///:memory:")
    Indicator.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        Indicator(
            id="urn:sds:reg:gri:gri 101_1_a",
            identifier="urn:sds:reg:gri:gri 101_1_a",
            title="Legacy biodiversity policy",
            dimension="E",
        )
    )
    session.commit()

    plan = validate_indicator_import_text(_indicator_csv(), db=session)

    assert plan.valid is True
    assert plan.created_rows == 0
    assert plan.updated_rows == 1


def test_indicator_import_validation_enforces_row_limit():
    try:
        validate_indicator_import_text(_indicator_csv(), db=None, max_rows=0)
    except IndicatorCsvContractError as error:
        assert "maximum of 0" in str(error)
    else:
        raise AssertionError("Expected row limit validation to fail")


def test_indicator_import_validation_truncates_duplicate_errors():
    rows = [
        {
            "__row_number__": 2,
            "identifier": "urn:sds:reg:esrs:e1_1",
            "title": "First",
        },
        {
            "__row_number__": 3,
            "identifier": "urn:sds:reg:esrs:e1_1",
            "title": "Duplicate",
        },
        {
            "__row_number__": 4,
            "identifier": "",
            "title": "",
        },
    ]

    plan = validate_indicator_rows(rows, max_errors=1)

    assert plan.valid is False
    assert plan.accepted_rows == 1
    assert plan.rejected_rows == 2
    assert len(plan.errors) == 1
    assert plan.errors_truncated is True


def test_indicator_csv_path_loader_skips_rows_and_rejects_bad_headers(tmp_path):
    csv_path = tmp_path / "indicators.csv"
    csv_path.write_text(_indicator_csv() + _indicator_csv().splitlines()[1] + "\n")

    rows = load_indicator_rows_from_csv(csv_path, skip_rows=1)

    assert len(rows) == 1
    assert rows[0]["__row_number__"] == 3

    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("identifier,identifier\nx,y\n", encoding="utf-8")
    try:
        load_indicator_rows_from_csv(bad_csv)
    except IndicatorCsvContractError as error:
        assert "duplicate columns" in str(error)
    else:
        raise AssertionError("Expected duplicate-header validation to fail")


def test_indicator_import_detects_legacy_ids_already_in_database():
    engine = create_engine("sqlite:///:memory:")
    Indicator.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        Indicator(
            id="urn:sds:reg:gri:gri 101_1_a",
            identifier="urn:sds:reg:gri:gri 101_1_a",
            title="Legacy biodiversity policy",
            dimension="E",
        )
    )
    session.commit()

    migrations = collect_legacy_identifier_migrations(session, [])

    assert migrations == {"urn:sds:reg:gri:gri 101_1_a": "urn:sds:reg:gri:gri_101_1_a"}


def test_apply_indicator_import_transactional_rolls_back_invalid_plan(monkeypatch):
    from src.services import indicator_import as mod

    class _Db:
        def __init__(self):
            self.rollbacks = 0

        def rollback(self):
            self.rollbacks += 1

    db = _Db()
    monkeypatch.setattr(mod, "collect_legacy_identifier_migrations", lambda *_args: {})
    monkeypatch.setattr(
        mod, "_classify_catalog_changes", lambda *_args, **_kw: (0, 0, 0)
    )

    try:
        apply_indicator_import_transactional(
            csv_text=_invalid_indicator_csv(),
            db=db,
            source_ref="bad.csv",
            source_hash=None,
            created_by="admin",
        )
    except IndicatorImportValidationError as error:
        assert error.plan.valid is False
        assert db.rollbacks == 1
    else:
        raise AssertionError("Expected invalid import plan to fail")


def test_apply_indicator_import_transactional_commits_snapshot(monkeypatch):
    from src.services import indicator_import as mod

    projector_calls = []

    class _Db:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    class _Repo:
        def __init__(self, db):
            self.db = db

        def bulk_upsert(self, records, commit=False):
            assert commit is False
            return len(records)

        def count(self):
            return 1

        def get_all(self, *, limit, offset):
            assert limit == 1
            assert offset == 0
            return [
                Indicator(
                    id="urn:sds:reg:gri:gri_101_1_a",
                    identifier="urn:sds:reg:gri:gri_101_1_a",
                    title="Biodiversity policy",
                    dimension="E",
                )
            ]

    class _SnapshotRepo:
        def __init__(self, db):
            self.db = db

    class _Manifest:
        manifest_hash = "manifest-sha"

    class _Projector:
        def __init__(self, db):
            self.db = db

        def project(self, *, commit):
            assert commit is False
            projector_calls.append(self.db)
            return type(
                "ProjectionResult",
                (),
                {
                    "active_indicators": 1,
                    "canonical_created": 1,
                    "canonical_revised": 0,
                    "canonical_unchanged": 0,
                    "concepts_created": 1,
                    "concepts_updated": 0,
                    "concepts_unchanged": 0,
                    "links_created": 2,
                    "links_unchanged": 0,
                    "missing_active_indicator_identifiers": [],
                    "as_dict": lambda self: {
                        "active_indicators": self.active_indicators,
                        "concepts_created": self.concepts_created,
                    },
                },
            )()

    db = _Db()
    monkeypatch.setattr(mod, "IndicatorRepository", _Repo)
    monkeypatch.setattr(mod, "DatasetSnapshotRepository", _SnapshotRepo)
    monkeypatch.setattr(mod, "SemanticConceptProjector", _Projector, raising=False)
    monkeypatch.setattr(mod, "migrate_legacy_identifiers", lambda *_args, **_kw: (1, 2))
    monkeypatch.setattr(mod, "collect_legacy_identifier_migrations", lambda *_args: {})
    monkeypatch.setattr(
        mod, "_classify_catalog_changes", lambda *_args, **_kw: (0, 1, 0)
    )
    monkeypatch.setattr(mod, "build_dataset_manifest", lambda **_kwargs: _Manifest())
    monkeypatch.setattr(mod, "serialize_indicator", lambda item: {"id": item.id})
    monkeypatch.setattr(
        mod,
        "persist_dataset_snapshot",
        lambda **_kwargs: type("Snapshot", (), {"id": 42})(),
    )

    plan = apply_indicator_import_transactional(
        csv_text=_indicator_csv("urn:sds:reg:gri:gri_101_1_a"),
        db=db,
        source_ref="register.csv",
        source_hash="source-sha",
        created_by="admin",
    )

    assert db.commits == 1
    assert db.rollbacks == 0
    assert projector_calls == [db]
    assert plan.committed is True
    assert plan.accepted_rows == 1
    assert plan.migrated_legacy_ids == 1
    assert plan.removed_legacy_duplicates == 2
    assert plan.snapshot_id == 42
    assert plan.manifest_hash == "manifest-sha"


def test_indicator_validation_endpoint_returns_completed_job(client, admin_token):
    response = client.post(
        "/api/v1/indicators/import-csv-validations",
        headers=_auth(admin_token),
        files={
            "file": ("indicators.csv", _indicator_csv().encode("utf-8"), "text/csv")
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["job_type"] == "validation"
    assert payload["status"] == "completed"
    assert payload["result_body"]["valid"] is True
    assert payload["result_body"]["normalized_rows"] == 1


def test_indicator_validation_endpoint_requires_manage_indicators(client, viewer_token):
    response = client.post(
        "/api/v1/indicators/import-csv-validations",
        headers=_auth(viewer_token),
        files={
            "file": ("indicators.csv", _indicator_csv().encode("utf-8"), "text/csv")
        },
    )

    assert response.status_code == 403


def test_indicator_validation_errors_are_paginated(client, admin_token):
    submit = client.post(
        "/api/v1/indicators/import-csv-validations",
        headers=_auth(admin_token),
        files={
            "file": (
                "bad-indicators.csv",
                _invalid_indicator_csv().encode("utf-8"),
                "text/csv",
            )
        },
    )
    assert submit.status_code == 200, submit.text
    job_id = submit.json()["id"]

    errors = client.get(
        f"/api/v1/indicators/import-jobs/{job_id}/errors?limit=1",
        headers=_auth(admin_token),
    )

    assert errors.status_code == 200, errors.text
    payload = errors.json()
    assert payload["total_errors"] == 1
    assert payload["items"][0]["row_number"] == 2


def test_indicator_job_status_hides_foreign_tenant_jobs():
    store = InMemoryIndicatorImportJobStore()
    store.create(
        job_id="tenant-a-job",
        job_type="validation",
        source_format="csv",
        submitted_by="manager-a",
        tenant_id="tenant-a",
        owner_user_id="user-a",
        status="completed",
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            indicators.get_indicator_import_job(
                "tenant-a-job",
                job_store=store,
                user=SimpleNamespace(company_id="tenant-b", id="user-b"),
            )
        )

    assert exc_info.value.status_code == 404


def test_indicator_import_job_requires_database_backed_mode(client, admin_token):
    response = client.post(
        "/api/v1/indicators/import-csv-jobs",
        headers=_auth(admin_token),
        files={
            "file": ("indicators.csv", _indicator_csv().encode("utf-8"), "text/csv")
        },
    )

    assert response.status_code == 503
