"""Bundled demo A2.3 package and its admin routes (no database)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.rate_limit import limiter
from src.auth.jwt_handler import jwt_handler
from src.auth.models import APIKeyCreate, Permission, UserCreate, UserRole
from src.database.session import get_db_optional
from src.services import demo_package
from src.services.api_key_store import InMemoryAPIKeyStore, get_api_key_store
from src.services.user_store import InMemoryUserStore, get_user_store


def _copy(tmp_path):
    target = tmp_path / "a23"
    shutil.copytree(demo_package.PACKAGE_DIR, target)
    return target


def _digest(path):
    return hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest()


def _rewrite_manifest(path):
    manifest = json.loads((path / "manifest.json").read_text())
    for name, entry in manifest["files"].items():
        data = (path / name).read_bytes()
        entry["sha256"] = hashlib.sha256(data).hexdigest()
        entry["size_bytes"] = len(data)
    (path / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return _digest(path)


class TestIntegrity:
    def test_bundled_package_matches_the_pinned_digest(self):
        package = demo_package.load_package()
        assert package.digest == demo_package.MANIFEST_SHA256
        assert package.package_id == "sds-demo-a23-v1"
        assert set(package.files) == {
            "NOTICE.md",
            "approval.json",
            "calculation_contract.json",
            "hierarchy.json",
            "indicators.csv",
            "values.csv",
            "mappings/MANIFEST.sha256",
            "mappings/manifest.json",
            "mappings/sds_mapping_assertion_components.csv",
            "mappings/sds_mapping_assertion_groups.csv",
            "mappings/sds_standard_datapoints.csv",
            "mappings/sds_standard_releases.csv",
        }

    def test_changed_byte_is_refused(self, tmp_path):
        path = _copy(tmp_path)
        values = path / "values.csv"
        values.write_bytes(values.read_bytes().replace(b"68.339", b"68.340"))
        with pytest.raises(demo_package.DemoPackageError) as exc:
            demo_package.load_package(path, expected_digest=_digest(path))
        assert exc.value.step == "integrity"

    def test_manifest_not_matching_the_pin_is_refused(self, tmp_path):
        path = _copy(tmp_path)
        values = path / "values.csv"
        values.write_bytes(values.read_bytes().replace(b"68.339", b"68.340"))
        _rewrite_manifest(path)
        with pytest.raises(demo_package.DemoPackageError):
            demo_package.load_package(path)

    def test_extra_or_missing_file_is_refused(self, tmp_path):
        path = _copy(tmp_path)
        (path / "extra.csv").write_text("x")
        with pytest.raises(demo_package.DemoPackageError):
            demo_package.load_package(path, expected_digest=_digest(path))
        (path / "extra.csv").unlink()
        (path / "NOTICE.md").unlink()
        with pytest.raises(demo_package.DemoPackageError):
            demo_package.load_package(path, expected_digest=_digest(path))

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
    def test_symlink_is_refused(self, tmp_path):
        path = _copy(tmp_path)
        target = path / "values.csv"
        moved = tmp_path / "values.csv"
        target.rename(moved)
        target.symlink_to(moved)
        with pytest.raises(demo_package.DemoPackageError):
            demo_package.load_package(path, expected_digest=_digest(path))


def _replace(package, name, old, new):
    data = package.files[name]
    assert old in data
    package.files[name] = data.replace(old, new)


class TestApprovalBinding:
    def test_bundled_assertions_match_the_approval(self):
        demo_package.validate_approval(demo_package.load_package())

    @pytest.mark.parametrize(
        "old,new",
        [
            (b'"0.94"', b'"0.95"'),
            (b'"equivalent"', b'"related"'),
            (b'"approved"', b'"draft"'),
            (b'"demo_a23"', b'"default"'),
        ],
    )
    def test_any_group_difference_is_refused(self, old, new):
        package = demo_package.load_package()
        _replace(package, "mappings/sds_mapping_assertion_groups.csv", old, new)
        with pytest.raises(demo_package.DemoPackageError) as exc:
            demo_package.validate_approval(package)
        assert exc.value.status_code == 422

    def test_component_pivot_difference_is_refused(self):
        package = demo_package.load_package()
        _replace(
            package,
            "mappings/sds_mapping_assertion_components.csv",
            b"syg:GrossScope3GreenhouseGasEmissions",
            b"syg:SomethingElse",
        )
        with pytest.raises(demo_package.DemoPackageError):
            demo_package.validate_approval(package)


class TestContent:
    def test_contract_is_the_e1_5_model_without_sygris_logic(self):
        contract = json.loads(
            demo_package.load_package().files["calculation_contract.json"]
        )
        assert "gates" not in contract and "support_rules" not in contract
        assert contract["node_count"] == len(contract["nodes"]) == 22
        executable = {
            node["datapoint_id"]: node["formula"]["runtime_expression"]
            for node in contract["nodes"]
            if node["runtime_status"] == "executable"
        }
        assert executable["E1-5_02"] == (
            "e1_5_10 + e1_5_11 + e1_5_12 + e1_5_13 + e1_5_14"
        )
        assert len(executable) == 6
        for node in contract["nodes"]:
            assert not {"gate_ids", "support_rule_ids", "gates", "evidence"} & set(node)
            assert node["label"]

    def test_no_internal_paths_or_gri_text(self):
        package = demo_package.load_package()
        blob = b"\n".join(package.files.values())
        lowered = blob.lower()
        assert b"docs/tmp" not in lowered
        for phrase in (b"metric tons", b"in metric tons of co2", b"gri standards text"):
            assert phrase not in lowered
        notice = package.files["NOTICE.md"].decode()
        assert "Source: EFRAG" in notice
        assert "synthetic" in notice

    def test_values_and_hierarchy_share_the_fixed_tenant(self):
        package = demo_package.load_package()
        hierarchy = json.loads(package.files["hierarchy.json"])
        assert hierarchy["company_id"] == demo_package.TENANT
        rows = list(csv.DictReader(io.StringIO(package.files["values.csv"].decode())))
        assert len(rows) == 9
        assert {row["entity"] for row in rows} == {"nh_group"}
        total = sum(
            float(row["value"])
            for row in rows
            if row["concept"].rsplit("_", 1)[-1] in {"10", "11", "12", "13", "14"}
        )
        assert round(total, 3) == 832.964


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

ROUTES = [
    ("get", "/api/v1/admin/demo-packages/a23"),
    ("post", "/api/v1/admin/demo-packages/a23/install?confirm=true"),
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
    from src.api.routers import admin_demo

    with patch.multiple(
        admin_demo,
        _status=MagicMock(return_value={"package_id": "sds-demo-a23-v1"}),
        _install=MagicMock(return_value={"state": "verified"}),
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


class TestRoutes:
    @pytest.mark.parametrize("method,url", ROUTES)
    def test_admin_bearer_is_allowed(self, stores, method, url):
        users, _ = stores
        response = getattr(TestClient(app), method)(
            url, headers=_bearer(users, "admin")
        )
        assert response.status_code == 200, response.text

    @pytest.mark.parametrize("username", ["manager", "analyst"])
    @pytest.mark.parametrize("method,url", ROUTES)
    def test_other_roles_are_forbidden(self, stores, method, url, username):
        users, _ = stores
        response = getattr(TestClient(app), method)(
            url, headers=_bearer(users, username)
        )
        assert response.status_code == 403

    @pytest.mark.parametrize("method,url", ROUTES)
    def test_api_key_and_anonymous_are_refused(self, stores, method, url):
        users, keys = stores
        admin = users.get_user(username="admin")
        key = keys.create_api_key(
            user_id=admin.id,
            request=APIKeyCreate(name="ops", permissions=[Permission.MANAGE_SYSTEM]),
        ).key
        client = TestClient(app)
        assert (
            getattr(client, method)(url, headers={"X-API-Key": key}).status_code == 403
        )
        assert getattr(client, method)(url).status_code == 403

    def test_install_requires_confirm(self, stores):
        users, _ = stores
        response = TestClient(app).post(
            "/api/v1/admin/demo-packages/a23/install", headers=_bearer(users, "admin")
        )
        assert response.status_code == 400

    def test_install_accepts_no_parameters(self, stores):
        users, _ = stores
        response = TestClient(app).post(
            "/api/v1/admin/demo-packages/a23/install?confirm=true&tenant=x",
            json={"tenant": "other"},
            headers=_bearer(users, "admin"),
        )
        assert response.status_code == 422
