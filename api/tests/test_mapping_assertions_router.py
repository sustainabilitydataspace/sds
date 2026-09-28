"""Tests for the internal canonical mapping assertion inspection API."""

from __future__ import annotations

import csv
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.main import app
from src.api.routers import mapping_assertions
from src.config.settings import settings
from src.services.canonical_mapping_package_job_store import (
    InMemoryCanonicalMappingPackageJobStore,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_global_mapping_import_jobs_are_not_readable_by_tenant_manager(
    client,
    admin_token,
    data_manager_token,
    monkeypatch,
):
    store = InMemoryCanonicalMappingPackageJobStore()
    store.create(
        job_id="tenant-a-import",
        job_type="import",
        package_id="private-package-id",
        submitted_by="tenant-a-operator",
        status="completed",
        error_message="private-result",
    )
    monkeypatch.setattr(
        app.state, "canonical_mapping_package_job_store", store, raising=False
    )
    path = "/api/v1/internal/canonical-mapping-packages/import-jobs/tenant-a-import"
    assert client.get(path, headers=_auth(admin_token)).status_code == 200
    foreign = client.get(path, headers=_auth(data_manager_token))
    assert foreign.status_code == 403
    assert "private-result" not in foreign.text
    store.create(
        job_id="tenant-a-validation",
        job_type="validation",
        package_id="private-package-id",
        submitted_by="tenant-a-operator",
        status="completed",
        result_body={"private": "review"},
    )
    root = "/api/v1/internal/canonical-mapping-packages"
    assert (
        client.get(
            f"{root}/validations/tenant-a-validation",
            headers=_auth(admin_token),
        ).status_code
        == 200
    )
    for method, suffix in (
        ("GET", "/validations/tenant-a-validation"),
        ("GET", "/private-package-id/assertion-inspection"),
        ("POST", "/private-package-id/validations"),
        ("POST", "/private-package-id/import-jobs"),
        ("POST", "/materialization-previews"),
    ):
        response = client.request(
            method, root + suffix, headers=_auth(data_manager_token), json={}
        )
        assert response.status_code == 403, (suffix, response.text)
        assert "review" not in response.text


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _sha256_for(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_explicit_target_package(package_dir: Path) -> None:
    files = {
        "sds_standard_releases.csv": [
            {
                "standard_id": "GHG",
                "name": "GHG Protocol",
                "version": "scope3_reporting_v1",
            },
            {
                "standard_id": "ESRS",
                "name": "European Sustainability Reporting Standards",
                "version": "ESRS_Set1_2023-12-22_OJ:E1-6-v4-proof",
            },
        ],
        "sds_standard_datapoints.csv": [
            {
                "standard_id": "GHG",
                "standard_version": "scope3_reporting_v1",
                "code": "scope3_total_emissions_by_category",
                "label": "Scope 3 total emissions by category",
            },
            {
                "standard_id": "ESRS",
                "standard_version": "ESRS_Set1_2023-12-22_OJ:E1-6-v4-proof",
                "code": "significant_scope_3_category_emissions",
                "label": "Significant Scope 3 category emissions",
            },
        ],
        "sds_mapping_assertion_groups.csv": [
            {
                "source_standard_id": "GHG",
                "source_standard_version": "scope3_reporting_v1",
                "source_code": "scope3_total_emissions_by_category",
                "mapping_profile": "target_esrs_significant_scope3_category_emissions",
                "target_standard_id": "ESRS",
                "target_standard_version": "ESRS_Set1_2023-12-22_OJ:E1-6-v4-proof",
                "target_code": "significant_scope_3_category_emissions",
                "target_datapoint_id": "esrs:e1-6-significant-scope3-category",
                "mapping_direction": "source_to_target",
                "relationship_type": "partial_overlap",
                "coverage_status": "partial",
                "confidence": "0.74",
                "rationale": "Non-exact review assertion.",
                "coverage_summary": "Category-level Scope 3 emissions.",
                "difference_summary": "Target has a different gate.",
                "valid_from": "2026-05-13T21:30:00Z",
                "valid_to": "",
                "approval_status": "draft",
                "publication_status": "internal",
                "assertion_hash": "a" * 64,
                "provenance": '{"seed":"unit-test"}',
            }
        ],
        "sds_mapping_assertion_components.csv": [
            {
                "source_standard_id": "GHG",
                "source_standard_version": "scope3_reporting_v1",
                "source_code": "scope3_total_emissions_by_category",
                "mapping_profile": "target_esrs_significant_scope3_category_emissions",
                "component_order": "1",
                "sygris_canonical_uri": "syg:Scope3CategoryGreenhouseGasEmissions",
                "sygris_revision": "1",
                "component_role": "primary",
                "coverage_fraction": "1.0000",
                "match_scope": "Category-level Scope 3 emissions.",
                "mismatch_scope": "Target has a different gate.",
                "transformation_rule": "Do not infer equivalence.",
                "rationale": "Review-only pivot.",
            }
        ],
    }
    for filename, rows in files.items():
        _write_csv(package_dir / filename, rows)

    manifest = {
        "package_schema_version": "1.0",
        "source_version": "unit-v46",
        "files": [
            {"filename": filename, "sha256": _sha256_for(package_dir / filename)}
            for filename in files
        ],
    }
    (package_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (package_dir / "MANIFEST.sha256").write_text(
        "\n".join(
            f"{_sha256_for(package_dir / filename)} *{filename}" for filename in files
        )
        + "\n",
        encoding="utf-8",
    )


def test_internal_mapping_assertion_api_inspects_allowlisted_package(
    client,
    admin_token,
    monkeypatch,
    tmp_path,
):
    package_root = tmp_path / "packages"
    package_id = "unit-v46-package"
    _write_explicit_target_package(package_root / package_id)
    monkeypatch.setattr(
        settings, "canonical_mapping_inspection_roots", [str(package_root)]
    )

    response = client.get(
        f"/api/v1/internal/canonical-mapping-packages/{package_id}/assertion-inspection",
        headers=_auth(admin_token),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["api_contract"]["read_only"] is True
    assert payload["api_contract"]["db_write"] is False
    assert payload["api_contract"]["operational_mapping_surface"] is False
    assert payload["package_id"] == package_id
    assert payload["source_version"] == "unit-v46"
    assert payload["valid"] is True
    assert payload["operational_eligible"] is False
    assert payload["relationship_type_counts"] == {"partial_overlap": 1}
    assert payload["non_operational_relationship_type_counts"] == {"partial_overlap": 1}
    assert payload["target_identity_status_counts"] == {"declared_in_package": 1}
    assert payload["operational_blockers"] == [
        "partial_overlap: 1 assertion group(s) are validator/inspection-only and cannot be committed to operational SDS surfaces"
    ]
    candidate = payload["candidates"][0]
    assert candidate["relationship_type"] == "partial_overlap"
    assert candidate["operational_eligible"] is False
    assert candidate["target_standard_id"] == "ESRS"
    assert candidate["target_code"] == "significant_scope_3_category_emissions"
    assert candidate["target_identity_status"] == "declared_in_package"
    assert candidate["components"][0]["sygris_canonical_uri"] == (
        "syg:Scope3CategoryGreenhouseGasEmissions"
    )


def test_inspection_source_version_comes_from_validated_snapshot(
    client,
    admin_token,
    monkeypatch,
    tmp_path,
):
    package_root = tmp_path / "packages"
    package_id = "unit-v46-package"
    package_dir = package_root / package_id
    _write_explicit_target_package(package_dir)
    monkeypatch.setattr(
        settings, "canonical_mapping_inspection_roots", [str(package_root)]
    )
    original_inspect = mapping_assertions.inspect_canonical_mapping_package

    def inspect_then_mutate_manifest(*args, **kwargs):
        report = original_inspect(*args, **kwargs)
        manifest_path = package_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["source_version"] = "attacker-replacement"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return report

    monkeypatch.setattr(
        mapping_assertions,
        "inspect_canonical_mapping_package",
        inspect_then_mutate_manifest,
    )

    response = client.get(
        f"/api/v1/internal/canonical-mapping-packages/{package_id}/assertion-inspection",
        headers=_auth(admin_token),
    )

    assert response.status_code == 200
    assert response.json()["source_version"] == "unit-v46"


def test_internal_mapping_assertion_api_requires_manage_mapping_permission(
    client,
    viewer_token,
    monkeypatch,
    tmp_path,
):
    package_root = tmp_path / "packages"
    package_id = "unit-v46-package"
    _write_explicit_target_package(package_root / package_id)
    monkeypatch.setattr(
        settings, "canonical_mapping_inspection_roots", [str(package_root)]
    )

    response = client.get(
        f"/api/v1/internal/canonical-mapping-packages/{package_id}/assertion-inspection",
        headers=_auth(viewer_token),
    )

    assert response.status_code == 403


def test_internal_mapping_assertion_api_requires_configured_root(
    client,
    admin_token,
    monkeypatch,
):
    monkeypatch.setattr(settings, "canonical_mapping_inspection_roots", [])

    response = client.get(
        "/api/v1/internal/canonical-mapping-packages/unit-v46/assertion-inspection",
        headers=_auth(admin_token),
    )

    assert response.status_code == 403


def test_internal_mapping_assertion_api_rejects_invalid_package_id(
    client,
    admin_token,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(settings, "canonical_mapping_inspection_roots", [str(tmp_path)])

    response = client.get(
        "/api/v1/internal/canonical-mapping-packages/%2E%2E/assertion-inspection",
        headers=_auth(admin_token),
    )

    assert response.status_code == 400


def test_mapping_assertion_private_helpers_cover_package_resolution_edges(
    monkeypatch,
    tmp_path,
):
    with pytest.raises(HTTPException) as invalid_empty:
        mapping_assertions._resolve_allowed_package_id("")
    assert invalid_empty.value.status_code == 400

    with pytest.raises(HTTPException) as invalid_separator:
        mapping_assertions._resolve_allowed_package_id("bad/path")
    assert invalid_separator.value.status_code == 400

    package_root = tmp_path / "packages"
    package_dir = package_root / "pkg"
    package_dir.mkdir(parents=True)
    monkeypatch.setattr(
        settings,
        "canonical_mapping_inspection_roots",
        [str(package_root), str(tmp_path / "missing" / ".." / "packages")],
    )
    assert mapping_assertions._resolve_allowed_package_id("pkg") == package_dir

    package_link = package_root / "pkg-link"
    package_link.symlink_to(package_dir, target_is_directory=True)
    with pytest.raises(HTTPException) as linked:
        mapping_assertions._resolve_allowed_package_id("pkg-link")
    assert linked.value.status_code == 404

    with pytest.raises(HTTPException) as missing:
        mapping_assertions._resolve_allowed_package_id("missing")
    assert missing.value.status_code == 404

    assert mapping_assertions._is_relative_to(package_dir, package_root) is True
    assert mapping_assertions._is_relative_to(package_root, package_dir) is False
    assert mapping_assertions._optional_text("  x  ") == "x"
    assert mapping_assertions._optional_text("") is None


def test_installed_release_keys_from_request_uses_db_when_request_is_absent(
    monkeypatch,
):
    monkeypatch.setattr(
        mapping_assertions,
        "active_standard_release_keys_from_db",
        lambda _db: {("ESRS", "2024")},
    )

    assert mapping_assertions._installed_release_keys_from_request(
        None, db=object()
    ) == {("ESRS", "2024")}
    assert (
        mapping_assertions._installed_release_keys_from_request(None, db=None) == set()
    )


@pytest.mark.asyncio
async def test_mapping_package_validation_and_job_lookup_direct(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    monkeypatch.setattr(
        mapping_assertions,
        "_resolve_allowed_package_id",
        lambda _package_id: package_dir,
    )

    class _Validation:
        def as_dict(self):
            return {"valid": True}

    class _Compatibility:
        validation = _Validation()
        status_counts = {"importable": 2, "pending_missing_standard": 1}
        importable_assertion_group_count = 2

        def as_dict(self):
            return {
                "status_counts": self.status_counts,
                "importable_assertion_group_count": (
                    self.importable_assertion_group_count
                ),
            }

    monkeypatch.setattr(
        mapping_assertions,
        "validate_canonical_mapping_installed_standard_compatibility",
        lambda **_kwargs: _Compatibility(),
    )
    store = InMemoryCanonicalMappingPackageJobStore()

    response = (
        await mapping_assertions.validate_mapping_package_against_installed_standards(
            package_id="pkg",
            request_body=mapping_assertions.CanonicalMappingPackageValidationRequest(
                compatibility_mode="partial",
                installed_standard_releases=[
                    mapping_assertions.InstalledStandardReleaseRequest(
                        standard_id="ESRS",
                        version="2024",
                    )
                ],
            ),
            db=None,
            job_store=store,
            user=SimpleNamespace(username="admin"),
        )
    )

    assert response.job_type == "validation"
    assert response.total_rows == 3
    assert response.accepted_rows == 2
    assert response.rejected_rows == 1
    assert response.committed is False

    fetched = await mapping_assertions.get_mapping_package_validation_job(
        response.id,
        job_store=store,
        user=SimpleNamespace(username="admin"),
    )
    assert fetched.id == response.id

    with pytest.raises(HTTPException) as exc:
        await mapping_assertions.get_mapping_package_validation_job(
            "missing",
            job_store=store,
            user=SimpleNamespace(username="admin"),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_mapping_package_import_and_preview_direct_branches(
    monkeypatch, tmp_path
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    monkeypatch.setattr(
        mapping_assertions,
        "_resolve_allowed_package_id",
        lambda _package_id: package_dir,
    )
    monkeypatch.setattr(
        mapping_assertions,
        "active_standard_release_keys_from_db",
        lambda _db: {("ESRS", "2024")},
    )

    class _ImportReport:
        valid = True
        committed = False
        blocked = False
        blockers = []
        compatibility = {
            "status_counts": {"importable": 1, "pending_missing_standard": 1},
            "importable_assertion_group_count": 1,
        }

        def as_dict(self):
            return {"committed": self.committed, "compatibility": self.compatibility}

    class _MaterializationReport:
        def as_dict(self):
            return {"created_count": 3, "dry_run": True}

    monkeypatch.setattr(
        mapping_assertions,
        "import_canonical_mapping_package_to_db",
        lambda **_kwargs: _ImportReport(),
    )
    monkeypatch.setattr(
        mapping_assertions,
        "materialize_pairwise_mappings",
        lambda **_kwargs: _MaterializationReport(),
    )
    store = InMemoryCanonicalMappingPackageJobStore()

    with pytest.raises(HTTPException) as exc:
        await mapping_assertions.submit_mapping_package_import_job(
            "pkg",
            mapping_assertions.CanonicalMappingPackageImportRequest(),
            db=None,
            job_store=store,
            user=SimpleNamespace(username="admin"),
        )
    assert exc.value.status_code == 503

    store.create(
        job_id="active",
        job_type="import",
        package_id="pkg",
        submitted_by="admin",
    )
    with pytest.raises(HTTPException) as exc:
        await mapping_assertions.submit_mapping_package_import_job(
            "pkg",
            mapping_assertions.CanonicalMappingPackageImportRequest(),
            db=object(),
            job_store=store,
            user=SimpleNamespace(username="admin"),
        )
    assert exc.value.status_code == 409

    store = InMemoryCanonicalMappingPackageJobStore()
    with pytest.raises(HTTPException) as exc:
        await mapping_assertions.submit_mapping_package_import_job(
            "pkg",
            mapping_assertions.CanonicalMappingPackageImportRequest(
                validation_id="missing"
            ),
            db=object(),
            job_store=store,
            user=SimpleNamespace(username="admin"),
        )
    assert exc.value.status_code == 404

    validation = store.create(
        job_id="validation-ok",
        job_type="validation",
        package_id="pkg",
        submitted_by="admin",
        status="completed",
        request_metadata={
            "compatibility_mode": "partial",
            "installed_standard_releases": [
                {"standard_id": "ESRS", "version": "2024"},
            ],
        },
    )
    response = await mapping_assertions.submit_mapping_package_import_job(
        "pkg",
        mapping_assertions.CanonicalMappingPackageImportRequest(
            validation_id=validation.id,
            compatibility_mode="partial",
            dry_run=True,
        ),
        db=object(),
        job_store=store,
        user=SimpleNamespace(username="admin"),
    )
    assert response.job_type == "import"
    assert response.total_rows == 2
    assert response.accepted_rows == 1
    assert response.committed is False
    assert response.validation_job_id == validation.id

    fetched = await mapping_assertions.get_mapping_package_import_job(
        response.id,
        job_store=store,
        user=SimpleNamespace(username="admin"),
    )
    assert fetched.id == response.id

    with pytest.raises(HTTPException) as exc:
        await mapping_assertions.get_mapping_package_import_job(
            "missing",
            job_store=store,
            user=SimpleNamespace(username="admin"),
        )
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await mapping_assertions.preview_mapping_package_materialization(
            mapping_assertions.CanonicalMappingMaterializationPreviewRequest(),
            db=None,
            user=SimpleNamespace(username="admin"),
        )
    assert exc.value.status_code == 503

    preview = await mapping_assertions.preview_mapping_package_materialization(
        mapping_assertions.CanonicalMappingMaterializationPreviewRequest(
            installed_standard_releases=[
                mapping_assertions.InstalledStandardReleaseRequest(
                    standard_id="ESRS",
                    version="2024",
                )
            ],
            allow_operational_subset=True,
        ),
        db=object(),
        user=SimpleNamespace(username="admin"),
    )
    assert preview == {"created_count": 3, "dry_run": True}


@pytest.mark.asyncio
async def test_import_job_holds_lifecycle_through_atomic_completion(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        mapping_assertions, "_resolve_allowed_package_id", lambda _id: tmp_path
    )
    monkeypatch.setattr(
        mapping_assertions,
        "_installed_release_keys_from_request",
        lambda *_args, **_kwargs: set(),
    )

    class Store(InMemoryCanonicalMappingPackageJobStore):
        held = False
        committed = False

        @contextmanager
        def import_lifecycle_lock(self):
            self.held = True
            try:
                yield
            finally:
                self.held = False

        def complete(self, **kwargs):
            assert self.held
            assert kwargs["commit"] is False
            return super().complete(**kwargs)

        def commit_import(self):
            assert self.held
            self.committed = True

    store = Store()

    class Report:
        valid = True
        blocked = False
        blockers = []
        committed = False
        compatibility = {
            "status_counts": {"importable": 1},
            "importable_assertion_group_count": 1,
        }

        def as_dict(self):
            return {"committed": self.committed, "compatibility": self.compatibility}

    def run_import(**kwargs):
        assert store.held
        assert kwargs["commit"] is False
        return Report()

    monkeypatch.setattr(
        mapping_assertions, "import_canonical_mapping_package_to_db", run_import
    )
    response = await mapping_assertions.submit_mapping_package_import_job(
        "pkg",
        mapping_assertions.CanonicalMappingPackageImportRequest(),
        db=object(),
        job_store=store,
        user=SimpleNamespace(username="admin"),
    )
    assert store.committed
    assert response.committed is True
    assert response.result_body["committed"] is True


@pytest.mark.asyncio
async def test_mapping_package_import_rejects_mismatched_validation_metadata(
    monkeypatch, tmp_path
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    monkeypatch.setattr(
        mapping_assertions,
        "_resolve_allowed_package_id",
        lambda _package_id: package_dir,
    )
    store = InMemoryCanonicalMappingPackageJobStore()
    validation = store.create(
        job_id="validation-strict-esrs",
        job_type="validation",
        package_id="pkg",
        submitted_by="admin",
        status="completed",
        request_metadata={
            "compatibility_mode": "strict",
            "installed_standard_releases": [{"standard_id": "ESRS", "version": "2024"}],
        },
        result_body={
            "compatibility": {
                "installed_standard_releases": [
                    {"standard_id": "ESRS", "version": "2024"}
                ]
            }
        },
    )

    called = False

    def _import_should_not_run(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("import must not run for mismatched validation metadata")

    monkeypatch.setattr(
        mapping_assertions,
        "import_canonical_mapping_package_to_db",
        _import_should_not_run,
    )

    with pytest.raises(HTTPException) as exc:
        await mapping_assertions.submit_mapping_package_import_job(
            "pkg",
            mapping_assertions.CanonicalMappingPackageImportRequest(
                validation_id=validation.id,
                compatibility_mode="partial",
                installed_standard_releases=[
                    mapping_assertions.InstalledStandardReleaseRequest(
                        standard_id="ESRS",
                        version="2024",
                    )
                ],
            ),
            db=object(),
            job_store=store,
            user=SimpleNamespace(username="admin"),
        )

    assert exc.value.status_code == 409
    assert called is False


@pytest.mark.asyncio
async def test_mapping_package_import_reserves_running_job_before_mutation(
    monkeypatch, tmp_path
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    monkeypatch.setattr(
        mapping_assertions,
        "_resolve_allowed_package_id",
        lambda _package_id: package_dir,
    )
    monkeypatch.setattr(
        mapping_assertions,
        "active_standard_release_keys_from_db",
        lambda _db: {("ESRS", "2024")},
    )
    store = InMemoryCanonicalMappingPackageJobStore()

    class _ImportReport:
        valid = True
        committed = False
        blocked = False
        blockers = []
        compatibility = {
            "status_counts": {"installable_now": 1},
            "importable_assertion_group_count": 1,
        }

        def as_dict(self):
            return {"committed": self.committed, "compatibility": self.compatibility}

    def _import_observes_active_job(**_kwargs):
        assert store.has_active_import() is True
        running = [
            job
            for job in store._jobs.values()
            if job.job_type.value == "import" and job.status.value == "running"
        ]
        assert len(running) == 1
        return _ImportReport()

    monkeypatch.setattr(
        mapping_assertions,
        "import_canonical_mapping_package_to_db",
        _import_observes_active_job,
    )

    response = await mapping_assertions.submit_mapping_package_import_job(
        "pkg",
        mapping_assertions.CanonicalMappingPackageImportRequest(),
        db=object(),
        job_store=store,
        user=SimpleNamespace(username="admin"),
    )

    assert response.job_type == "import"
    assert response.status == "completed"
    assert response.committed is True


@pytest.mark.asyncio
async def test_failed_canonical_import_job_never_persists_raw_exception_text(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        mapping_assertions, "_resolve_allowed_package_id", lambda _: tmp_path
    )
    store = InMemoryCanonicalMappingPackageJobStore()
    marker = "private-fixture-marker"

    def fail_import(**_kwargs):
        raise RuntimeError(f"SQL parameters contained {marker}")

    monkeypatch.setattr(
        mapping_assertions, "import_canonical_mapping_package_to_db", fail_import
    )
    with pytest.raises(RuntimeError, match="SQL parameters"):
        await mapping_assertions.submit_mapping_package_import_job(
            "pkg",
            mapping_assertions.CanonicalMappingPackageImportRequest(
                installed_standard_releases=[]
            ),
            db=object(),
            job_store=store,
            user=SimpleNamespace(username="admin"),
        )
    jobs = list(store._jobs.values())
    assert len(jobs) == 1
    assert jobs[0].status.value == "failed"
    assert marker not in (jobs[0].error_message or "")
