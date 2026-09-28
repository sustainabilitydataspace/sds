"""High-level API endpoint tests to increase router coverage (offline-safe)."""

from __future__ import annotations

import io
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from types import SimpleNamespace

import pandas as pd

from src.api.main import app
from src.database.session import get_db
from src.services.dataset_history import build_dataset_item_index


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _override_db():
    yield object()


def test_auth_refresh_token_and_token_info_and_logout(client):
    login = client.post(
        "/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert login.status_code == 200
    tokens = login.json()

    refresh = client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh.status_code == 200
    refresh_data = refresh.json()
    assert "access_token" in refresh_data

    info = client.get("/auth/token/info", headers=_auth(tokens["access_token"]))
    assert info.status_code == 200
    assert info.json()["token_type"] == "access"

    logout = client.post("/auth/logout", headers=_auth(tokens["access_token"]))
    assert logout.status_code == 200
    assert "logged out" in logout.json()["message"].lower()


def test_auth_admin_endpoints_create_user_api_key_change_password_update_me(
    client, admin_token
):
    headers = _auth(admin_token)

    create_user = client.post(
        "/auth/users",
        headers=headers,
        json={
            "username": "new_user",
            "email": "new_user@example.com",
            "full_name": "New User",
            "company_id": "company_001",
            "role": "viewer",
            "password": "secure_password123",
            "is_active": True,
        },
    )
    assert create_user.status_code == 200
    assert create_user.json()["username"] == "new_user"

    api_key = client.post(
        "/auth/api-keys",
        headers=headers,
        json={"name": "ci-key", "description": "test", "permissions": []},
    )
    assert api_key.status_code == 200
    assert api_key.json()["name"] == "ci-key"
    assert api_key.json()["key"].startswith("sds_")

    change_pw = client.post(
        "/auth/change-password",
        headers=headers,
        json={"current_password": "admin123", "new_password": "new_password_123"},
    )
    assert change_pw.status_code == 200

    assert client.get("/auth/me", headers=headers).status_code == 401
    login = client.post(
        "/auth/login", json={"username": "admin", "password": "new_password_123"}
    )
    assert login.status_code == 200
    headers = _auth(login.json()["access_token"])
    update_me = client.put(
        "/auth/me", headers=headers, json={"email": "updated@example.com"}
    )
    assert update_me.status_code == 200
    assert update_me.json()["email"] == "updated@example.com"


def test_values_create_convert_list_get_delete(
    client, data_manager_token, viewer_token
):
    concept = "urn:sds:reg:esrs:e5_5_09"
    create = client.post(
        "/api/v1/values",
        headers=_auth(data_manager_token),
        json={
            "concept": concept,
            "entity": "madrid_plant",
            "period": "2024-01-15",
            "value": 1000,
            "unit": "kg",
            "metadata": {"source": "test"},
        },
    )
    assert create.status_code == 201
    created = create.json()
    assert created["conversion_applied"] is True
    assert created["unit"] == "t"
    assert created["original_unit"] == "kg"
    assert abs(created["value"] - 1) < 1e-9
    value_id = created["id"]

    listing = client.get(
        f"/api/v1/values?concept={concept}&entity=madrid_plant",
        headers=_auth(viewer_token),
    )
    assert listing.status_code == 200
    listing_json = listing.json()
    assert listing_json["total"] == 1
    assert listing_json["items"][0]["id"] == value_id

    by_id_invalid = client.get("/api/v1/values/not-a-uuid", headers=_auth(viewer_token))
    assert by_id_invalid.status_code == 400

    by_id = client.get(f"/api/v1/values/{value_id}", headers=_auth(viewer_token))
    assert by_id.status_code == 200
    by_id_json = by_id.json()
    assert by_id_json["id"] == value_id
    assert by_id_json["concept"] == concept
    assert by_id_json["entity"] == "madrid_plant"
    assert by_id_json["unit"] == "t"

    delete_invalid = client.delete(
        "/api/v1/values/not-a-uuid", headers=_auth(data_manager_token)
    )
    assert delete_invalid.status_code == 400

    delete_ok = client.delete(
        f"/api/v1/values/{value_id}", headers=_auth(data_manager_token)
    )
    assert delete_ok.status_code == 200

    # Deleted values should no longer be retrievable.
    by_id_deleted = client.get(
        f"/api/v1/values/{value_id}", headers=_auth(viewer_token)
    )
    assert by_id_deleted.status_code == 404


def test_calculation_trace_includes_formula_variable_values(
    client, data_manager_token, admin_token
):
    entity = "qa_trace_vars"
    seeded = client.post(
        "/api/v1/values/import",
        headers=_auth(data_manager_token),
        json={
            "items": [
                {
                    "concept": "urn:sds:reg:esrs:e3_5_01",
                    "entity": entity,
                    "period": "2024-01-15",
                    "value": 0.12,
                    "unit": "EUR",
                },
            ]
        },
    )
    assert seeded.status_code == 201, seeded.text

    response = client.post(
        "/api/v1/calculate",
        headers=_auth(admin_token),
        json={
            "concept": "csrd:E3_5",
            "entity": entity,
            "period": "2024",
            "granularity": "annual",
            "include_trace": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["formula_used"] == "e3_5_01"
    assert body["value"] == 0.12
    assert set(body["trace"]["variables_used"]) == {"e3_5_01"}
    assert body["trace"]["variable_values"] == {"e3_5_01": 0.12}
    assert set(body["trace"]["dependencies_resolved"]) == {"urn:sds:reg:esrs:e3_5_01"}


def test_values_list_supports_cursor_pagination(
    client, data_manager_token, viewer_token
):
    seeded = client.post(
        "/api/v1/values/import",
        headers=_auth(data_manager_token),
        json={
            "items": [
                {
                    "concept": "csrd:E3_5",
                    "entity": "madrid_plant",
                    "period": "2024-01-15",
                    "value": 1,
                    "unit": "m³",
                },
                {
                    "concept": "csrd:E3_5",
                    "entity": "madrid_plant",
                    "period": "2024-01-16",
                    "value": 2,
                    "unit": "m³",
                },
                {
                    "concept": "csrd:E3_5",
                    "entity": "madrid_plant",
                    "period": "2024-01-17",
                    "value": 3,
                    "unit": "m³",
                },
            ]
        },
    )
    assert seeded.status_code == 201, seeded.text

    first = client.get(
        "/api/v1/values?concept=csrd:E3_5&entity=madrid_plant&limit=2",
        headers=_auth(viewer_token),
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["total"] == 3
    assert len(first_body["items"]) == 2
    assert first_body["has_more"] is True
    assert first_body["next_cursor"]

    second = client.get(
        f"/api/v1/values?concept=csrd:E3_5&entity=madrid_plant&limit=2&cursor={first_body['next_cursor']}",
        headers=_auth(viewer_token),
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["total"] == 3
    assert len(second_body["items"]) == 1
    assert second_body["has_more"] is False
    assert second_body["next_cursor"] is None


def test_values_list_and_export_support_changed_since(
    client, data_manager_token, viewer_token
):
    seeded = client.post(
        "/api/v1/values/import",
        headers=_auth(data_manager_token),
        json={
            "items": [
                {
                    "concept": "csrd:E3_5",
                    "entity": "madrid_plant",
                    "period": "2024-01-15",
                    "value": 1,
                    "unit": "m³",
                }
            ]
        },
    )
    assert seeded.status_code == 201, seeded.text

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    listing = client.get(
        "/api/v1/values",
        params={
            "concept": "csrd:E3_5",
            "entity": "madrid_plant",
            "changed_since": future,
        },
        headers=_auth(viewer_token),
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 0

    exported = client.get(
        "/api/v1/values/export",
        params={
            "format": "json",
            "concept": "csrd:E3_5",
            "entity": "madrid_plant",
            "changed_since": future,
        },
        headers=_auth(viewer_token),
    )
    assert exported.status_code == 200
    assert exported.json() == []

    current_export = client.get(
        "/api/v1/values/export",
        params={"format": "json", "concept": "csrd:E3_5", "entity": "madrid_plant"},
        headers=_auth(viewer_token),
    )
    assert current_export.status_code == 200
    assert current_export.headers["etag"]
    assert current_export.headers["x-sds-manifest-hash"]
    assert current_export.headers["x-sds-contract-version"] == "1.0"
    assert current_export.headers["x-sds-signature-alg"] == "HMAC-SHA256"
    assert current_export.headers["x-sds-signature-key-id"]
    assert current_export.headers["x-sds-signature"]
    assert current_export.headers["x-sds-signed-payload-hash"]

    manifest = client.get(
        "/api/v1/values/manifest",
        params={"concept": "csrd:E3_5", "entity": "madrid_plant"},
        headers=_auth(viewer_token),
    )
    assert manifest.status_code == 200
    manifest_payload = manifest.json()
    assert manifest_payload["dataset"] == "values"
    assert (
        manifest_payload["manifest_hash"]
        == current_export.headers["x-sds-manifest-hash"]
    )
    assert manifest_payload["etag"] != current_export.headers["etag"]
    assert (
        manifest_payload["signed_payload_hash"]
        == current_export.headers["x-sds-signed-payload-hash"]
    )
    assert manifest_payload["signature_algorithm"] == "HMAC-SHA256"
    assert manifest_payload["signature"]

    parquet_export = client.get(
        "/api/v1/values/export",
        params={"format": "parquet", "concept": "csrd:E3_5", "entity": "madrid_plant"},
        headers=_auth(viewer_token),
    )
    assert parquet_export.status_code == 200
    assert parquet_export.headers["content-type"].startswith(
        "application/vnd.apache.parquet"
    )
    parquet_frame = pd.read_parquet(io.BytesIO(parquet_export.content))
    assert len(parquet_frame.index) == 1
    assert "concept" in parquet_frame.columns
    csv_with_json_validator = client.get(
        "/api/v1/values/export",
        params={"format": "csv", "concept": "csrd:E3_5", "entity": "madrid_plant"},
        headers={
            **_auth(viewer_token),
            "If-None-Match": current_export.headers["etag"],
        },
    )
    assert csv_with_json_validator.status_code == 200
    assert csv_with_json_validator.headers["etag"] != current_export.headers["etag"]
    assert parquet_export.headers["etag"] != current_export.headers["etag"]

    not_modified = client.get(
        "/api/v1/values/export",
        params={"format": "json", "concept": "csrd:E3_5", "entity": "madrid_plant"},
        headers={
            **_auth(viewer_token),
            "If-None-Match": current_export.headers["etag"],
        },
    )
    assert not_modified.status_code == 304
    assert not_modified.headers["etag"] == current_export.headers["etag"]
    assert not_modified.headers["x-sds-signature"]
    assert (
        not_modified.headers["x-sds-signed-payload-hash"]
        == current_export.headers["x-sds-signed-payload-hash"]
    )

    if current_export.headers.get("last-modified"):
        not_modified_since = client.get(
            "/api/v1/values/export",
            params={"format": "json", "concept": "csrd:E3_5", "entity": "madrid_plant"},
            headers={
                **_auth(viewer_token),
                "If-Modified-Since": format_datetime(
                    parsedate_to_datetime(current_export.headers["last-modified"])
                    + timedelta(days=1),
                    usegmt=True,
                ),
            },
        )
        # Row removals can move the selected-row timestamp backwards, so a
        # date alone cannot safely validate the current representation.
        assert not_modified_since.status_code == 200

    invalid_header = client.get(
        "/api/v1/values/export",
        params={"format": "json", "concept": "csrd:E3_5", "entity": "madrid_plant"},
        headers={**_auth(viewer_token), "If-Modified-Since": "not-a-date"},
    )
    assert invalid_header.status_code == 200


def test_values_list_rejects_invalid_cursor(client, viewer_token):
    response = client.get(
        "/api/v1/values?limit=2&cursor=not-a-valid-cursor",
        headers=_auth(viewer_token),
    )
    assert response.status_code == 400
    assert "cursor" in response.text.lower()


def test_hierarchies_list_and_create(client, viewer_token, data_manager_token):
    listing = client.get("/api/v1/hierarchies", headers=_auth(viewer_token))
    assert listing.status_code == 200
    assert listing.json()["total"] == 0

    create = client.post(
        "/api/v1/hierarchies",
        headers=_auth(data_manager_token),
        json={
            "company_id": "sds_company",
            "hierarchy_type": "organizational",
            "name": "Test Hierarchy",
            "description": "test",
            "levels": [
                {
                    "id": "global",
                    "name": "Global",
                    "parent": None,
                    "level": 0,
                    "metadata": {},
                },
                {
                    "id": "spain",
                    "name": "Spain",
                    "parent": "global",
                    "level": 1,
                    "metadata": {},
                },
            ],
            "active": True,
        },
    )
    assert create.status_code == 201
    created = create.json()
    assert created["id"] is not None

    listing_after = client.get(
        "/api/v1/hierarchies?company_id=sds_company", headers=_auth(viewer_token)
    )
    assert listing_after.status_code == 200
    assert listing_after.json()["total"] == 1
    assert listing_after.json()["items"][0]["id"] == created["id"]


def test_indicator_export_supports_json_and_csv(client, admin_token):
    csv_response = client.get(
        "/api/v1/indicators/export?format=csv&dimension=E&limit=5",
        headers=_auth(admin_token),
    )
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert csv_response.headers["etag"]
    assert csv_response.headers["x-sds-manifest-hash"]
    assert csv_response.headers["x-sds-contract-version"] == "1.0"
    assert csv_response.headers["x-sds-signature-alg"] == "HMAC-SHA256"
    assert "identifier" in csv_response.text.splitlines()[0]

    json_response = client.get(
        "/api/v1/indicators/export?format=json&dimension=E&limit=5",
        headers=_auth(admin_token),
    )
    assert json_response.status_code == 200
    payload = json_response.json()
    assert isinstance(payload, list)
    if payload:
        assert "identifier" in payload[0]

    json_response_repeat = client.get(
        "/api/v1/indicators/export?format=json&dimension=E&limit=5",
        headers=_auth(admin_token),
    )
    assert json_response_repeat.status_code == 200
    assert json_response_repeat.content == json_response.content
    assert json_response_repeat.headers["etag"] == json_response.headers["etag"]

    parquet_response = client.get(
        "/api/v1/indicators/export?format=parquet&dimension=E&limit=5",
        headers=_auth(admin_token),
    )
    assert parquet_response.status_code == 200
    assert parquet_response.headers["content-type"].startswith(
        "application/vnd.apache.parquet"
    )
    parquet_frame = pd.read_parquet(io.BytesIO(parquet_response.content))
    assert len(parquet_frame.index) == len(payload)
    if not parquet_frame.empty:
        assert "identifier" in parquet_frame.columns
    csv_with_json_validator = client.get(
        "/api/v1/indicators/export?format=csv&dimension=E&limit=5",
        headers={**_auth(admin_token), "If-None-Match": json_response.headers["etag"]},
    )
    assert csv_with_json_validator.status_code == 200
    assert csv_with_json_validator.headers["etag"] != json_response.headers["etag"]
    assert parquet_response.headers["etag"] != json_response.headers["etag"]

    not_modified = client.get(
        "/api/v1/indicators/export?format=json&dimension=E&limit=5",
        headers={**_auth(admin_token), "If-None-Match": json_response.headers["etag"]},
    )
    assert not_modified.status_code == 304

    manifest = client.get(
        "/api/v1/indicators/manifest?dimension=E&limit=5",
        headers=_auth(admin_token),
    )
    assert manifest.status_code == 200
    manifest_payload = manifest.json()
    assert manifest_payload["dataset"] == "indicators"
    assert (
        manifest_payload["manifest_hash"]
        == json_response.headers["x-sds-manifest-hash"]
    )
    assert manifest_payload["etag"] != json_response.headers["etag"]
    assert (
        manifest_payload["signed_payload_hash"]
        == json_response.headers["x-sds-signed-payload-hash"]
    )
    assert manifest_payload["record_count"] == len(payload)
    assert manifest_payload["signature_algorithm"] == "HMAC-SHA256"
    assert manifest_payload["signature"]

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    changed_since = client.get(
        "/api/v1/indicators/export",
        params={"format": "json", "changed_since": future},
        headers=_auth(admin_token),
    )
    assert changed_since.status_code == 200
    assert changed_since.json() == []


def test_mapping_export_supports_json_and_csv(client, admin_token):
    csv_response = client.get(
        "/api/v1/mappings/export?format=csv&source_standard=ESRS&limit=5",
        headers=_auth(admin_token),
    )
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert csv_response.headers["etag"]
    assert csv_response.headers["x-sds-manifest-hash"]
    assert csv_response.headers["x-sds-contract-version"] == "1.0"
    assert csv_response.headers["x-sds-signature-alg"] == "HMAC-SHA256"
    assert "source_standard" in csv_response.text.splitlines()[0]

    json_response = client.get(
        "/api/v1/mappings/export?format=json&source_standard=ESRS&limit=5",
        headers=_auth(admin_token),
    )
    assert json_response.status_code == 200
    payload = json_response.json()
    assert isinstance(payload, list)
    if payload:
        assert "source_standard" in payload[0]

    json_response_repeat = client.get(
        "/api/v1/mappings/export?format=json&source_standard=ESRS&limit=5",
        headers=_auth(admin_token),
    )
    assert json_response_repeat.status_code == 200
    assert json_response_repeat.content == json_response.content
    assert json_response_repeat.headers["etag"] == json_response.headers["etag"]

    parquet_response = client.get(
        "/api/v1/mappings/export?format=parquet&source_standard=ESRS&limit=5",
        headers=_auth(admin_token),
    )
    assert parquet_response.status_code == 200
    assert parquet_response.headers["content-type"].startswith(
        "application/vnd.apache.parquet"
    )
    parquet_frame = pd.read_parquet(io.BytesIO(parquet_response.content))
    assert len(parquet_frame.index) == len(payload)
    if not parquet_frame.empty:
        assert "source_standard" in parquet_frame.columns
    csv_with_json_validator = client.get(
        "/api/v1/mappings/export?format=csv&source_standard=ESRS&limit=5",
        headers={**_auth(admin_token), "If-None-Match": json_response.headers["etag"]},
    )
    assert csv_with_json_validator.status_code == 200
    assert csv_with_json_validator.headers["etag"] != json_response.headers["etag"]
    assert parquet_response.headers["etag"] != json_response.headers["etag"]

    not_modified = client.get(
        "/api/v1/mappings/export?format=json&source_standard=ESRS&limit=5",
        headers={**_auth(admin_token), "If-None-Match": json_response.headers["etag"]},
    )
    assert not_modified.status_code == 304

    manifest = client.get(
        "/api/v1/mappings/manifest?source_standard=ESRS&limit=5",
        headers=_auth(admin_token),
    )
    assert manifest.status_code == 200
    manifest_payload = manifest.json()
    assert manifest_payload["dataset"] == "mappings"
    assert (
        manifest_payload["manifest_hash"]
        == json_response.headers["x-sds-manifest-hash"]
    )
    assert manifest_payload["etag"] != json_response.headers["etag"]
    assert (
        manifest_payload["signed_payload_hash"]
        == json_response.headers["x-sds-signed-payload-hash"]
    )
    assert manifest_payload["record_count"] == len(payload)
    assert manifest_payload["signature_algorithm"] == "HMAC-SHA256"
    assert manifest_payload["signature"]

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    changed_since = client.get(
        "/api/v1/mappings/export",
        params={"format": "json", "changed_since": future},
        headers=_auth(admin_token),
    )
    assert changed_since.status_code == 200
    assert changed_since.json() == []


def test_value_export_and_manifest_reject_unbounded_limits(client, admin_token):
    for path in ("/api/v1/values/export", "/api/v1/values/manifest"):
        response = client.get(
            f"{path}?limit=50001",
            headers=_auth(admin_token),
        )
        assert response.status_code == 422


def test_indicator_history_and_diff_endpoints(client, admin_token, monkeypatch):
    from src.api.routers import indicators as indicators_router

    payload = [
        {
            "id": "urn:sds:reg:test:a",
            "identifier": "urn:sds:reg:test:a",
            "title": "Test indicator A",
            "indicator_name": None,
            "description": None,
            "dimension": "E",
            "unit_name": None,
            "unit_type": None,
            "periodicity": None,
            "period_type": None,
            "source_ref": None,
            "code_esrs": "E1-1",
            "code_gri": None,
            "code_gri_expanded": None,
            "evidence_path": None,
            "source_row": None,
            "owner": None,
            "access_rights": None,
            "validation_method": None,
            "double_materiality": None,
            "value_type": None,
        }
    ]
    snapshot = SimpleNamespace(
        id=10,
        dataset="indicators",
        manifest_hash="a" * 64,
        record_count=1,
        contract_version="1.0",
        item_index=build_dataset_item_index("indicators", payload),
        source_ref="test:indicators",
        source_hash=None,
        created_by="test",
        created_at="2026-04-20T00:00:00Z",
    )

    class FakeSnapshotRepo:
        def __init__(self, _db):
            pass

        def list_recent(self, dataset, limit=20):
            assert dataset == "indicators"
            return [snapshot]

        def count(self, dataset):
            assert dataset == "indicators"
            return 1

        def get_by_id(self, snapshot_id):
            return snapshot if snapshot_id == 10 else None

    class FakeIndicatorStore:
        def __init__(self, db=None):
            self.db = db

        def count(self):
            return 1

        def get_all(self, limit=100, offset=0):
            return [SimpleNamespace(**payload[0], created_at=None, updated_at=None)]

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setattr(
        indicators_router, "DatasetSnapshotRepository", FakeSnapshotRepo
    )
    monkeypatch.setattr(indicators_router, "IndicatorStore", FakeIndicatorStore)
    try:
        history = client.get(
            "/api/v1/indicators/history?limit=5", headers=_auth(admin_token)
        )
        assert history.status_code == 200
        history_payload = history.json()
        assert history_payload["dataset"] == "indicators"
        assert history_payload["total"] >= 1
        assert any(item["id"] == snapshot.id for item in history_payload["items"])

        diff = client.get(
            f"/api/v1/indicators/diff?from_snapshot_id={snapshot.id}",
            headers=_auth(admin_token),
        )
        assert diff.status_code == 200
        diff_payload = diff.json()
        assert diff_payload["dataset"] == "indicators"
        assert diff_payload["from_snapshot_id"] == snapshot.id
        assert diff_payload["added_count"] == 0
        assert diff_payload["removed_count"] == 0
        assert diff_payload["changed_count"] == 0
    finally:
        app.dependency_overrides.clear()


def test_mapping_history_and_diff_endpoints(client, admin_token, monkeypatch):
    from src.api.routers import mappings as mappings_router

    payload = [
        {
            "id": 1,
            "source_standard": "ESRS",
            "source_code": "E1-1",
            "source_label": "Emissions",
            "target_standard": "GRI",
            "target_code": "305-1",
            "target_label": "Direct GHG emissions",
            "esg_dimension": "E",
            "relationship_type": "equivalent",
            "confidence": 1.0,
            "dataset": "test",
        }
    ]
    snapshot = SimpleNamespace(
        id=20,
        dataset="mappings",
        manifest_hash="b" * 64,
        record_count=1,
        contract_version="1.0",
        item_index=build_dataset_item_index("mappings", payload),
        source_ref="test:mappings",
        source_hash=None,
        created_by="test",
        created_at="2026-04-20T00:00:00Z",
    )

    class FakeSnapshotRepo:
        def __init__(self, _db):
            pass

        def list_recent(self, dataset, limit=20):
            assert dataset == "mappings"
            return [snapshot]

        def count(self, dataset):
            assert dataset == "mappings"
            return 1

        def get_by_id(self, snapshot_id):
            return snapshot if snapshot_id == 20 else None

    class FakeMappingStore:
        def __init__(self, db=None):
            self.db = db

        def count(self):
            return 1

        def get_all(self, limit=100, offset=0):
            return [SimpleNamespace(**payload[0], created_at=None, updated_at=None)]

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setattr(mappings_router, "DatasetSnapshotRepository", FakeSnapshotRepo)
    monkeypatch.setattr(mappings_router, "StandardMappingStore", FakeMappingStore)
    try:
        history = client.get(
            "/api/v1/mappings/history?limit=5", headers=_auth(admin_token)
        )
        assert history.status_code == 200
        history_payload = history.json()
        assert history_payload["dataset"] == "mappings"
        assert history_payload["total"] >= 1
        assert any(item["id"] == snapshot.id for item in history_payload["items"])

        diff = client.get(
            f"/api/v1/mappings/diff?from_snapshot_id={snapshot.id}",
            headers=_auth(admin_token),
        )
        assert diff.status_code == 200
        diff_payload = diff.json()
        assert diff_payload["dataset"] == "mappings"
        assert diff_payload["from_snapshot_id"] == snapshot.id
        assert diff_payload["added_count"] == 0
        assert diff_payload["removed_count"] == 0
        assert diff_payload["changed_count"] == 0
    finally:
        app.dependency_overrides.clear()


def test_indicator_changes_endpoint(client, admin_token, monkeypatch):
    from src.api.routers import indicators as indicators_router

    payload_v1 = [
        {
            "id": "urn:sds:reg:test:a",
            "identifier": "urn:sds:reg:test:a",
            "title": "Test indicator A",
            "indicator_name": None,
            "description": None,
            "dimension": "E",
            "unit_name": None,
            "unit_type": None,
            "periodicity": None,
            "period_type": None,
            "source_ref": None,
            "code_esrs": "E1-1",
            "code_gri": None,
            "code_gri_expanded": None,
            "evidence_path": None,
            "source_row": None,
            "owner": None,
            "access_rights": None,
            "validation_method": None,
            "double_materiality": None,
            "value_type": None,
        }
    ]
    payload_v2 = payload_v1 + [
        {
            "id": "urn:sds:reg:test:b",
            "identifier": "urn:sds:reg:test:b",
            "title": "Test indicator B",
            "indicator_name": None,
            "description": None,
            "dimension": "E",
            "unit_name": None,
            "unit_type": None,
            "periodicity": None,
            "period_type": None,
            "source_ref": None,
            "code_esrs": "E1-2",
            "code_gri": None,
            "code_gri_expanded": None,
            "evidence_path": None,
            "source_row": None,
            "owner": None,
            "access_rights": None,
            "validation_method": None,
            "double_materiality": None,
            "value_type": None,
        }
    ]
    snapshot1 = SimpleNamespace(
        id=10,
        dataset="indicators",
        manifest_hash="a" * 64,
        record_count=1,
        contract_version="1.0",
        item_index=build_dataset_item_index("indicators", payload_v1),
        source_ref="test:indicators:v1",
        source_hash=None,
        created_by="test",
        created_at=datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc),
    )
    snapshot2 = SimpleNamespace(
        id=11,
        dataset="indicators",
        manifest_hash="b" * 64,
        record_count=2,
        contract_version="1.0",
        item_index=build_dataset_item_index("indicators", payload_v2),
        source_ref="test:indicators:v2",
        source_hash=None,
        created_by="test",
        created_at=datetime(2026, 4, 20, 10, 1, tzinfo=timezone.utc),
    )

    class FakeSnapshotRepo:
        def __init__(self, _db):
            self._items = [snapshot1, snapshot2]

        def list_feed(
            self,
            datasets=None,
            limit=100,
            cursor_occurred_at=None,
            cursor_dataset=None,
            cursor_event_id=None,
        ):
            items = list(self._items)
            if cursor_occurred_at is not None:
                items = [
                    item
                    for item in items
                    if (item.created_at, item.dataset, str(item.id))
                    > (cursor_occurred_at, cursor_dataset, cursor_event_id)
                ]
            return items[:limit]

        def get_previous(self, dataset, snapshot_id):
            if dataset == "indicators" and snapshot_id == 11:
                return snapshot1
            return None

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setattr(
        indicators_router, "DatasetSnapshotRepository", FakeSnapshotRepo
    )
    try:
        first = client.get(
            "/api/v1/indicators/changes?limit=1", headers=_auth(admin_token)
        )
        assert first.status_code == 200
        first_payload = first.json()
        assert first_payload["datasets"] == ["indicators"]
        assert first_payload["has_more"] is True
        assert len(first_payload["items"]) == 1
        assert first_payload["items"][0]["snapshot_id"] == 10
        assert first_payload["items"][0]["diff"]["added_count"] == 1

        second = client.get(
            f"/api/v1/indicators/changes?limit=1&cursor={first_payload['next_cursor']}",
            headers=_auth(admin_token),
        )
        assert second.status_code == 200
        second_payload = second.json()
        assert second_payload["has_more"] is False
        assert len(second_payload["items"]) == 1
        assert second_payload["items"][0]["snapshot_id"] == 11
        assert second_payload["items"][0]["previous_snapshot_id"] == 10
        assert second_payload["items"][0]["diff"]["added_count"] == 1
    finally:
        app.dependency_overrides.clear()


def test_mapping_changes_endpoint(client, admin_token, monkeypatch):
    from src.api.routers import mappings as mappings_router

    payload_v1 = [
        {
            "id": 1,
            "source_standard": "ESRS",
            "source_code": "E1-1",
            "source_label": "Emissions",
            "target_standard": "GRI",
            "target_code": "305-1",
            "target_label": "Direct GHG emissions",
            "esg_dimension": "E",
            "relationship_type": "equivalent",
            "confidence": 1.0,
            "dataset": "test",
        }
    ]
    payload_v2 = payload_v1 + [
        {
            "id": 2,
            "source_standard": "ESRS",
            "source_code": "E3-1_10",
            "source_label": "Water",
            "target_standard": "GRI",
            "target_code": "303-3",
            "target_label": "Water withdrawal",
            "esg_dimension": "E",
            "relationship_type": "equivalent",
            "confidence": 1.0,
            "dataset": "test",
        }
    ]
    snapshot1 = SimpleNamespace(
        id=20,
        dataset="mappings",
        manifest_hash="c" * 64,
        record_count=1,
        contract_version="1.0",
        item_index=build_dataset_item_index("mappings", payload_v1),
        source_ref="test:mappings:v1",
        source_hash=None,
        created_by="test",
        created_at=datetime(2026, 4, 20, 11, 0, tzinfo=timezone.utc),
    )
    snapshot2 = SimpleNamespace(
        id=21,
        dataset="mappings",
        manifest_hash="d" * 64,
        record_count=2,
        contract_version="1.0",
        item_index=build_dataset_item_index("mappings", payload_v2),
        source_ref="test:mappings:v2",
        source_hash=None,
        created_by="test",
        created_at=datetime(2026, 4, 20, 11, 1, tzinfo=timezone.utc),
    )

    class FakeSnapshotRepo:
        def __init__(self, _db):
            self._items = [snapshot1, snapshot2]

        def list_feed(
            self,
            datasets=None,
            limit=100,
            cursor_occurred_at=None,
            cursor_dataset=None,
            cursor_event_id=None,
        ):
            items = list(self._items)
            if cursor_occurred_at is not None:
                items = [
                    item
                    for item in items
                    if (item.created_at, item.dataset, str(item.id))
                    > (cursor_occurred_at, cursor_dataset, cursor_event_id)
                ]
            return items[:limit]

        def get_previous(self, dataset, snapshot_id):
            if dataset == "mappings" and snapshot_id == 21:
                return snapshot1
            return None

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setattr(mappings_router, "DatasetSnapshotRepository", FakeSnapshotRepo)
    try:
        first = client.get(
            "/api/v1/mappings/changes?limit=1", headers=_auth(admin_token)
        )
        assert first.status_code == 200
        first_payload = first.json()
        assert first_payload["datasets"] == ["mappings"]
        assert first_payload["has_more"] is True
        assert first_payload["items"][0]["snapshot_id"] == 20

        second = client.get(
            f"/api/v1/mappings/changes?limit=1&cursor={first_payload['next_cursor']}",
            headers=_auth(admin_token),
        )
        assert second.status_code == 200
        second_payload = second.json()
        assert second_payload["has_more"] is False
        assert second_payload["items"][0]["snapshot_id"] == 21
        assert second_payload["items"][0]["diff"]["added_count"] == 1
    finally:
        app.dependency_overrides.clear()


def test_mapping_from_endpoint_uses_configured_mapping_store(
    client, admin_token, monkeypatch
):
    from src.api.routers import mappings as mappings_router

    row = SimpleNamespace(
        id=9901,
        source_standard="ESRS",
        source_code="E3-4_11",
        source_label="Total water withdrawals",
        target_standard="GRI",
        target_code="GRI 303-3.a",
        target_label="Total water withdrawal from all areas",
        esg_dimension="Environmental",
        relationship_type="partial",
        confidence=1.0,
        dataset="canonical_pairwise_mappings",
        created_at=datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc),
    )

    class FakeMappingStore:
        def __init__(self, db=None):
            self.db = db

        def find_by_source(self, standard, code, limit=100):
            assert standard == "ESRS"
            assert code == "E3-4_11"
            assert limit == 100
            return [row]

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setattr(mappings_router, "StandardMappingStore", FakeMappingStore)
    try:
        response = client.get(
            "/api/v1/mappings/from/ESRS/E3-4_11", headers=_auth(admin_token)
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["items"][0]["dataset"] == "canonical_pairwise_mappings"
    assert payload["items"][0]["target_code"] == "GRI 303-3.a"


def test_values_changes_endpoint_orders_by_updated_at_and_cursor(
    client, data_manager_token, viewer_token
):
    created_ids = []
    for idx in range(3):
        response = client.post(
            "/api/v1/values",
            headers=_auth(data_manager_token),
            json={
                "concept": "csrd:E3_5",
                "entity": "madrid_plant",
                "period": "2024-01-15",
                "external_key": f"feed-row-{idx}",
                "value": idx + 1,
                "unit": "m³",
            },
        )
        assert response.status_code == 201, response.text
        created_ids.append(response.json()["id"])

    store = app.state.value_store
    ordered_ids = sorted(created_ids)
    base_time = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
    for offset, value_id in enumerate(ordered_ids):
        stored = store._values[value_id]
        adjusted = stored.__class__(
            **{
                **stored.__dict__,
                "created_at": base_time - timedelta(hours=1),
                "updated_at": base_time + timedelta(minutes=offset),
            }
        )
        store._values[value_id] = adjusted

    first = client.get(
        "/api/v1/values/changes?concept=csrd:E3_5&entity=madrid_plant&limit=2",
        headers=_auth(viewer_token),
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["datasets"] == ["values"]
    assert first_payload["has_more"] is True
    first_ids = [item["event_id"] for item in first_payload["items"]]
    assert first_ids == ordered_ids[:2]
    assert all(item["operation"] == "updated" for item in first_payload["items"])

    second = client.get(
        f"/api/v1/values/changes?concept=csrd:E3_5&entity=madrid_plant&limit=2&cursor={first_payload['next_cursor']}",
        headers=_auth(viewer_token),
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["has_more"] is False
    second_ids = [item["event_id"] for item in second_payload["items"]]
    assert second_ids == ordered_ids[2:]


def test_values_changes_requires_auth_before_cursor_validation(client, viewer_token):
    unauthenticated = client.get("/api/v1/values/changes?cursor=not-a-valid-cursor")
    assert unauthenticated.status_code == 403

    authenticated = client.get(
        "/api/v1/values/changes?cursor=not-a-valid-cursor",
        headers=_auth(viewer_token),
    )
    assert authenticated.status_code == 400
