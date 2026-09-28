"""Focused tests for bulk values import and export endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from src.api.main import app
from src.api.models import HierarchyConfiguration, HierarchyLevel
from src.auth.dependencies import get_current_active_user
from src.auth.models import Permission, User, UserRole
from src.config.settings import settings
from src.services.hierarchy_store import InMemoryHierarchyStore, get_hierarchy_store
from src.services.value_store import InMemoryValueStore, get_value_store

TEST_COMPANY_ID = "tenant_test_001"
KNOWN_NUMERIC_CONCEPT = "urn:sds:reg:esrs:e5_5_09"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _override_user() -> User:
    now = datetime.now(timezone.utc)
    return User(
        id="dm-001",
        username="data_manager",
        email="data_manager@example.com",
        full_name="Data Manager",
        company_id=TEST_COMPANY_ID,
        role=UserRole.DATA_MANAGER,
        is_active=True,
        created_at=now,
        updated_at=now,
        last_login=None,
        permissions=[
            Permission.CREATE_VALUES,
            Permission.READ_VALUES,
            Permission.DELETE_VALUES,
            Permission.MANAGE_HIERARCHIES,
        ],
    )


def _install_strict_value_overrides() -> (
    tuple[InMemoryValueStore, InMemoryHierarchyStore]
):
    value_store = InMemoryValueStore()
    hierarchy_store = InMemoryHierarchyStore()
    hierarchy_store.create(
        HierarchyConfiguration(
            company_id=TEST_COMPANY_ID,
            hierarchy_type="organizational",
            name="Test",
            description=None,
            levels=[
                HierarchyLevel(
                    id="global", name="Global", parent=None, level=0, metadata={}
                ),
                HierarchyLevel(
                    id="madrid_plant",
                    name="Madrid",
                    parent="global",
                    level=1,
                    metadata={},
                ),
            ],
            active=True,
        ),
        config_id="cfg-1",
        created_by="pytest",
    )

    async def _current_user_override() -> User:
        return _override_user()

    def _value_store_override():
        return value_store

    def _hierarchy_store_override():
        return hierarchy_store

    app.dependency_overrides[get_current_active_user] = _current_user_override
    app.dependency_overrides[get_value_store] = _value_store_override
    app.dependency_overrides[get_hierarchy_store] = _hierarchy_store_override
    return value_store, hierarchy_store


def _clear_overrides():
    app.dependency_overrides.pop(get_current_active_user, None)
    app.dependency_overrides.pop(get_value_store, None)
    app.dependency_overrides.pop(get_hierarchy_store, None)


def test_bulk_import_values_endpoint_imports_two_rows_and_returns_report(
    client, data_manager_token, viewer_token
):
    payload = {
        "items": [
            {
                "concept": "csrd:E3_5",
                "entity": "madrid_plant",
                "period": "2024-01-15",
                "value": 1000,
                "unit": "L",
                "metadata": {"source": "batch-a"},
            },
            {
                "concept": "csrd:E3_5",
                "entity": "madrid_plant",
                "period": "2024-01-16",
                "value": 2,
                "unit": "m³",
                "metadata": {"source": "batch-a"},
            },
        ]
    }

    response = client.post(
        "/api/v1/values/import", headers=_auth(data_manager_token), json=payload
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["total_rows"] == 2
    assert body["accepted_rows"] == 2
    assert body["rejected_rows"] == 0
    assert body["committed"] is True
    assert all(item["message"] == "persisted" for item in body["items"])

    listing = client.get(
        "/api/v1/values?concept=csrd:E3_5&entity=madrid_plant",
        headers=_auth(viewer_token),
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 2


def test_bulk_import_values_endpoint_is_all_or_nothing_when_one_row_is_invalid(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "require_database", True)
    _install_strict_value_overrides()
    try:
        payload = {
            "items": [
                {
                    "concept": KNOWN_NUMERIC_CONCEPT,
                    "entity": "madrid_plant",
                    "period": "2024-01-15",
                    "value": 1.5,
                    "unit": "kg",
                },
                {
                    "concept": "syg:DoesNotExist",
                    "entity": "madrid_plant",
                    "period": "2024-01-15",
                    "value": 1.5,
                    "unit": "m³",
                },
            ]
        }

        response = client.post("/api/v1/values/import", json=payload)
        assert response.status_code == 400, response.text
        body = response.json()
        assert body["committed"] is False
        assert body["accepted_rows"] == 1
        assert body["rejected_rows"] == 1

        store = app.dependency_overrides[get_value_store]()
        items, total = store.list(
            concept=None,
            entity=None,
            period_start=None,
            period_end=None,
            unit=None,
            limit=100,
            offset=0,
        )
        assert total == 0
        assert items == []
    finally:
        _clear_overrides()


def test_values_export_csv_returns_filtered_rows_with_stable_columns(
    client, data_manager_token, viewer_token
):
    payload = {
        "items": [
            {
                "concept": "csrd:E3_5",
                "entity": "madrid_plant",
                "period": "2024-01-15",
                "value": 1000,
                "unit": "L",
                "metadata": {"source": "batch-a"},
            },
            {
                "concept": "csrd:E3_5",
                "entity": "barcelona_plant",
                "period": "2024-01-16",
                "value": 2,
                "unit": "m³",
                "metadata": {"source": "batch-a"},
            },
        ]
    }
    create = client.post(
        "/api/v1/values/import", headers=_auth(data_manager_token), json=payload
    )
    assert create.status_code == 201, create.text

    response = client.get(
        "/api/v1/values/export?format=csv&concept=csrd:E3_5&entity=madrid_plant",
        headers=_auth(viewer_token),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    lines = response.text.strip().splitlines()
    assert (
        lines[0]
        == "id,concept,entity,period,external_key,value,value_type,unit,original_unit,conversion_applied,metadata_json,created_at,updated_at"
    )
    assert len(lines) == 2
    assert "madrid_plant" in lines[1]
    assert "barcelona_plant" not in response.text


def test_values_export_json_returns_same_filtered_dataset_without_paginated_envelope(
    client, data_manager_token, viewer_token
):
    payload = {
        "items": [
            {
                "concept": "csrd:E3_5",
                "entity": "madrid_plant",
                "period": "2024-01-15",
                "value": 1000,
                "unit": "L",
                "metadata": {"source": "batch-a"},
            }
        ]
    }
    create = client.post(
        "/api/v1/values/import", headers=_auth(data_manager_token), json=payload
    )
    assert create.status_code == 201, create.text

    response = client.get(
        "/api/v1/values/export?format=json&concept=csrd:E3_5&entity=madrid_plant",
        headers=_auth(viewer_token),
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["entity"] == "madrid_plant"
    assert body[0]["external_key"] is None
    assert body[0]["metadata"]["source"] == "batch-a"
    assert "items" not in body


def test_bulk_import_values_csv_endpoint_imports_rows_with_strict_contract(
    client, data_manager_token, viewer_token
):
    csv_bytes = (
        "concept,entity,period,value,unit,metadata_json\n"
        'csrd:E3_5,madrid_plant,2024-01-15,1000,L,"{""source"": ""csv""}"\n'
        'csrd:E3_5,madrid_plant,2024-01-16,2,m³,"{""source"": ""csv""}"\n'
    ).encode("utf-8")

    response = client.post(
        "/api/v1/values/import-csv",
        headers=_auth(data_manager_token),
        files={"file": ("values.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["committed"] is True
    assert body["accepted_rows"] == 2

    exported = client.get(
        "/api/v1/values/export?format=json&entity=madrid_plant",
        headers=_auth(viewer_token),
    )
    assert exported.status_code == 200
    payload = exported.json()
    assert len(payload) == 2
    assert all(item["metadata"]["source"] == "csv" for item in payload)


def test_bulk_import_values_csv_endpoint_preserves_boolean_and_narrative_values(
    client, data_manager_token, viewer_token
):
    csv_bytes = (
        "concept,entity,period,external_key,value,unit,value_type,metadata_json\n"
        'syg:Policy_Status,madrid_plant,2024-12-31,erp:policy-1,true,Boolean,boolean,"{""source"": ""csv""}"\n'
        'syg:Transition_Narrative,madrid_plant,2024-12-31,erp:narrative-1,Board approved transition plan,Text,narrative,"{""source"": ""csv""}"\n'
        'syg:Mixed_Disclosure,madrid_plant,2024-12-31,erp:semi-narrative-1,See table 2 and 2024 baseline,Text,semi-narrative,"{""source"": ""csv""}"\n'
    ).encode("utf-8")

    response = client.post(
        "/api/v1/values/import-csv",
        headers=_auth(data_manager_token),
        files={"file": ("values.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["accepted_rows"] == 3

    exported = client.get(
        "/api/v1/values/export?format=json&entity=madrid_plant",
        headers=_auth(viewer_token),
    )
    rows = {row["external_key"]: row for row in exported.json()}
    assert rows["erp:policy-1"]["value"] is True
    assert rows["erp:policy-1"]["value_type"] == "boolean"
    assert rows["erp:narrative-1"]["value"] == "Board approved transition plan"
    assert rows["erp:narrative-1"]["value_type"] == "narrative"
    assert rows["erp:semi-narrative-1"]["value"] == "See table 2 and 2024 baseline"
    assert rows["erp:semi-narrative-1"]["value_type"] == "semi-narrative"


def test_bulk_import_values_csv_endpoint_rejects_invalid_contract(
    client, data_manager_token
):
    csv_bytes = (
        "identifier,title,dimension,unitName\n" "urn:sds:reg:test:1,Example,E,m³\n"
    ).encode("utf-8")

    response = client.post(
        "/api/v1/values/import-csv",
        headers=_auth(data_manager_token),
        files={"file": ("invalid.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 400, response.text
    payload = response.json()
    detail = (
        payload.get("detail")
        or payload.get("error", {}).get("message")
        or response.text
    )
    assert "missing required columns" in detail.lower()


def test_values_create_replays_with_same_idempotency_key(client, data_manager_token):
    payload = {
        "concept": "csrd:E3_5",
        "entity": "madrid_plant",
        "period": "2024-01-15",
        "external_key": "erp:obs-1",
        "value": 1,
        "unit": "L",
        "metadata": {"source": "api"},
    }
    headers = {**_auth(data_manager_token), "Idempotency-Key": "values-create-1"}

    first = client.post("/api/v1/values", headers=headers, json=payload)
    assert first.status_code == 201, first.text

    second = client.post("/api/v1/values", headers=headers, json=payload)
    assert second.status_code == 201, second.text
    assert second.json() == first.json()


def test_values_create_rejects_same_idempotency_key_with_different_payload(
    client, data_manager_token
):
    first = client.post(
        "/api/v1/values",
        headers={**_auth(data_manager_token), "Idempotency-Key": "values-create-2"},
        json={
            "concept": "csrd:E3_5",
            "entity": "madrid_plant",
            "period": "2024-01-15",
            "external_key": "erp:obs-2",
            "value": 1,
            "unit": "L",
        },
    )
    assert first.status_code == 201, first.text

    second = client.post(
        "/api/v1/values",
        headers={**_auth(data_manager_token), "Idempotency-Key": "values-create-2"},
        json={
            "concept": "csrd:E3_5",
            "entity": "madrid_plant",
            "period": "2024-01-15",
            "external_key": "erp:obs-2",
            "value": 2,
            "unit": "L",
        },
    )
    assert second.status_code == 409
    assert "different payload" in second.text


def test_bulk_import_values_json_rejects_existing_external_key_without_overwrite(
    client, data_manager_token, viewer_token
):
    payload = {
        "items": [
            {
                "concept": "csrd:E3_5",
                "entity": "madrid_plant",
                "period": "2024-01-15",
                "external_key": "erp:bulk-1",
                "value": 1000,
                "unit": "L",
                "metadata": {"source": "batch-a"},
            }
        ]
    }
    first = client.post(
        "/api/v1/values/import", headers=_auth(data_manager_token), json=payload
    )
    assert first.status_code == 201, first.text
    first_body = first.json()
    first_id = first_body["items"][0]["value_id"]

    original = client.get(f"/api/v1/values/{first_id}", headers=_auth(viewer_token))
    assert original.status_code == 200

    second = client.post(
        "/api/v1/values/import",
        headers=_auth(data_manager_token),
        json={
            "items": [
                {
                    "concept": "csrd:E3_5",
                    "entity": "madrid_plant",
                    "period": "2024-01-16",
                    "external_key": "erp:bulk-1",
                    "value": 2,
                    "unit": "m³",
                    "metadata": {"source": "batch-b"},
                }
            ]
        },
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["message"] == "Value external key already exists"

    exported = client.get(
        "/api/v1/values/export?format=json&entity=madrid_plant&concept=csrd:E3_5",
        headers=_auth(viewer_token),
    )
    assert exported.status_code == 200
    rows = [row for row in exported.json() if row["external_key"] == "erp:bulk-1"]
    assert len(rows) == 1
    assert rows[0] == original.json()
    assert rows[0]["id"] == first_id
    assert rows[0]["period"] == "2024-01-15"
    assert rows[0]["metadata"]["source"] == "batch-a"


def test_bulk_import_values_csv_rejects_existing_external_key_without_overwrite(
    client, data_manager_token, viewer_token
):
    first = client.post(
        "/api/v1/values/import-csv",
        headers=_auth(data_manager_token),
        files={
            "file": (
                "values.csv",
                (
                    "concept,entity,period,external_key,value,unit,metadata_json\n"
                    'csrd:E3_5,madrid_plant,2024-01-15,erp:csv-1,1000,L,"{""source"": ""csv-a""}"\n'
                ).encode("utf-8"),
                "text/csv",
            )
        },
    )
    assert first.status_code == 201, first.text
    first_id = first.json()["items"][0]["value_id"]

    original = client.get(f"/api/v1/values/{first_id}", headers=_auth(viewer_token))
    assert original.status_code == 200

    second = client.post(
        "/api/v1/values/import-csv",
        headers=_auth(data_manager_token),
        files={
            "file": (
                "values.csv",
                (
                    "concept,entity,period,external_key,value,unit,metadata_json\n"
                    'csrd:E3_5,madrid_plant,2024-01-16,erp:csv-1,2,m³,"{""source"": ""csv-b""}"\n'
                ).encode("utf-8"),
                "text/csv",
            )
        },
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["message"] == "Value external key already exists"

    exported = client.get(
        "/api/v1/values/export?format=json&entity=madrid_plant",
        headers=_auth(viewer_token),
    )
    rows = [row for row in exported.json() if row["external_key"] == "erp:csv-1"]
    assert len(rows) == 1
    assert rows[0] == original.json()
    assert rows[0]["id"] == first_id
    assert rows[0]["period"] == "2024-01-15"
    assert rows[0]["metadata"]["source"] == "csv-a"


def test_bulk_import_rejects_duplicate_external_key_inside_same_batch(
    client, data_manager_token
):
    response = client.post(
        "/api/v1/values/import",
        headers=_auth(data_manager_token),
        json={
            "items": [
                {
                    "concept": "csrd:E3_5",
                    "entity": "madrid_plant",
                    "period": "2024-01-15",
                    "external_key": "erp:dup-1",
                    "value": 1,
                    "unit": "m³",
                },
                {
                    "concept": "csrd:E3_5",
                    "entity": "madrid_plant",
                    "period": "2024-01-16",
                    "external_key": "erp:dup-1",
                    "value": 2,
                    "unit": "m³",
                },
            ]
        },
    )
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["committed"] is False
    assert body["rejected_rows"] == 1
    assert "duplicate external_key" in body["items"][1]["message"].lower()


def test_values_import_job_json_completes_and_can_be_polled(client, data_manager_token):
    submit = client.post(
        "/api/v1/values/import-jobs",
        headers={**_auth(data_manager_token), "Idempotency-Key": "values-job-json-1"},
        json={
            "items": [
                {
                    "concept": "csrd:E3_5",
                    "entity": "madrid_plant",
                    "period": "2024-01-15",
                    "external_key": "erp:job-json-1",
                    "value": 1000,
                    "unit": "L",
                }
            ]
        },
    )
    assert submit.status_code == 202, submit.text
    created = submit.json()
    assert created["status"] in {"pending", "running", "completed"}

    job = client.get(
        f"/api/v1/values/import-jobs/{created['id']}",
        headers=_auth(data_manager_token),
    )
    assert job.status_code == 200, job.text
    payload = job.json()
    assert payload["status"] == "completed"
    assert payload["committed"] is True
    assert payload["result_body"]["accepted_rows"] == 1

    replay = client.post(
        "/api/v1/values/import-jobs",
        headers={**_auth(data_manager_token), "Idempotency-Key": "values-job-json-1"},
        json={
            "items": [
                {
                    "concept": "csrd:E3_5",
                    "entity": "madrid_plant",
                    "period": "2024-01-15",
                    "external_key": "erp:job-json-1",
                    "value": 1000,
                    "unit": "L",
                }
            ]
        },
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == created["id"]


def test_values_import_job_csv_completes_and_can_be_polled(client, data_manager_token):
    submit = client.post(
        "/api/v1/values/import-csv-jobs",
        headers=_auth(data_manager_token),
        files={
            "file": (
                "values.csv",
                (
                    "concept,entity,period,external_key,value,unit,metadata_json\n"
                    'csrd:E3_5,madrid_plant,2024-01-15,erp:job-csv-1,1000,L,"{""source"": ""csv-job""}"\n'
                ).encode("utf-8"),
                "text/csv",
            )
        },
    )
    assert submit.status_code == 202, submit.text
    created = submit.json()
    assert created["source_format"] == "csv"
    assert created["source_filename"] == "values.csv"

    job = client.get(
        f"/api/v1/values/import-jobs/{created['id']}",
        headers=_auth(data_manager_token),
    )
    assert job.status_code == 200, job.text
    payload = job.json()
    assert payload["status"] == "completed"
    assert payload["committed"] is True
    assert payload["result_body"]["accepted_rows"] == 1


def test_values_import_job_visibility_is_limited_to_submitter_or_admin(
    client, data_manager_token, viewer_token
):
    submit = client.post(
        "/api/v1/values/import-jobs",
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
    assert submit.status_code == 202, submit.text
    job_id = submit.json()["id"]

    forbidden = client.get(
        f"/api/v1/values/import-jobs/{job_id}",
        headers=_auth(viewer_token),
    )
    assert forbidden.status_code == 403


def test_values_import_job_json_replays_with_same_idempotency_key(
    client, data_manager_token
):
    headers = {**_auth(data_manager_token), "Idempotency-Key": "values-job-json-replay"}
    payload = {
        "items": [
            {
                "concept": "csrd:E3_5",
                "entity": "madrid_plant",
                "period": "2024-01-15",
                "external_key": "erp:job-replay-1",
                "value": 1,
                "unit": "m³",
            }
        ]
    }

    first = client.post("/api/v1/values/import-jobs", headers=headers, json=payload)
    assert first.status_code == 202, first.text

    second = client.post("/api/v1/values/import-jobs", headers=headers, json=payload)
    assert second.status_code == 202, second.text
    assert second.json()["id"] == first.json()["id"]


def test_values_import_job_json_rejects_same_idempotency_key_with_different_payload(
    client, data_manager_token
):
    first = client.post(
        "/api/v1/values/import-jobs",
        headers={
            **_auth(data_manager_token),
            "Idempotency-Key": "values-job-json-conflict",
        },
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
    assert first.status_code == 202, first.text

    second = client.post(
        "/api/v1/values/import-jobs",
        headers={
            **_auth(data_manager_token),
            "Idempotency-Key": "values-job-json-conflict",
        },
        json={
            "items": [
                {
                    "concept": "csrd:E3_5",
                    "entity": "madrid_plant",
                    "period": "2024-01-16",
                    "value": 2,
                    "unit": "m³",
                }
            ]
        },
    )
    assert second.status_code == 409


def test_values_import_job_failure_is_persisted(
    client, monkeypatch, data_manager_token
):
    from src.api.routers import values as values_router
    from src.services.value_import_job_store import InMemoryValueImportJobStore

    job_store = app.state.value_import_job_store = InMemoryValueImportJobStore()

    def _failing_runner(*, app, job_id, items, submitted_by, company_id):
        job_store.mark_running(job_id)
        job_store.fail(
            job_id=job_id,
            error_message="Forced runner failure",
            result_body={
                "total_rows": len(items),
                "accepted_rows": 0,
                "rejected_rows": len(items),
                "committed": False,
                "items": [{"message": "forced failure"}],
            },
            total_rows=len(items),
            accepted_rows=0,
            rejected_rows=len(items),
            committed=False,
        )

    monkeypatch.setattr(values_router, "run_value_import_job", _failing_runner)
    try:
        submit = client.post(
            "/api/v1/values/import-jobs",
            headers=_auth(data_manager_token),
            json={
                "items": [
                    {
                        "concept": "syg:DoesNotExist",
                        "entity": "madrid_plant",
                        "period": "2024-01-15",
                        "value": 1.5,
                        "unit": "m³",
                    }
                ]
            },
        )
        assert submit.status_code == 202, submit.text
        job_id = submit.json()["id"]

        job = client.get(
            f"/api/v1/values/import-jobs/{job_id}", headers=_auth(data_manager_token)
        )
        assert job.status_code == 200, job.text
        payload = job.json()
        assert payload["status"] == "failed"
        assert payload["committed"] is False
        assert payload["rejected_rows"] == 1
        assert payload["error_message"] == "Forced runner failure"
        assert payload["result_body"]["items"][0]["message"] == "forced failure"
    finally:
        app.state.value_import_job_store = None
