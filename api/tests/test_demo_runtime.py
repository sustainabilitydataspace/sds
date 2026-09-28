from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from scripts import demo_runtime as demo
from src.services.value_csv_import import load_values_from_csv


def _rows(payload):
    return [
        row for use_case in payload["use_cases"].values() for row in use_case["rows"]
    ]


@pytest.fixture(autouse=True)
def revision_write_environment(monkeypatch):
    """Model the Make target's host-process environment for demo unit tests."""
    for name, value in demo.REQUIRED_REVISION_WRITE_ENV.items():
        monkeypatch.setenv(name, value)


def test_public_demo_manifest_is_complete_and_synthetic():
    manifest = demo.verify_manifest()

    assert manifest["synthetic_public_data"] is True
    assert set(manifest["files"]) == {"NOTICE.md", "use_cases.json", "values.csv"}


def test_demo_fixture_proves_domain_specific_and_bounded_claims():
    payload = json.loads(demo.USE_CASES_PATH.read_text(encoding="utf-8"))
    rows = _rows(payload)

    assert payload["synthetic_public_data"] is True
    assert set(payload["use_cases"]) == {f"UC-0{number}" for number in range(1, 7)}
    assert payload["use_cases"]["UC-01"]["rows"][0]["concept"].endswith("e1_5_01")
    assert payload["use_cases"]["UC-02"]["rows"][0]["source_unit"] == "kg CO2e"
    assert payload["use_cases"]["UC-02"]["rows"][0]["target_unit"] == "t CO2e"
    assert len(payload["use_cases"]["UC-03"]["rows"]) == 2
    for row in payload["use_cases"]["UC-03"]["rows"]:
        assert {
            "water_source",
            "water_destination",
            "water_stress_status",
            "evidence_reference",
        } <= row["metadata"].keys()
    for name in ("UC-04", "UC-05", "UC-06"):
        item = payload["use_cases"][name]
        assert item["kind"] == "bounded_data_contract"
        assert "No " in item["rows"][0]["prohibited_conclusion"]
    assert all(row["metadata"]["source"] == "synthetic_public_demo" for row in rows)


def test_demo_values_follow_strict_csv_contract_and_match_fixture():
    payload = json.loads(demo.USE_CASES_PATH.read_text(encoding="utf-8"))
    parsed = load_values_from_csv(demo.VALUES_PATH)

    assert {row.external_key for row in parsed} == {
        row["external_key"] for row in _rows(payload)
    }
    assert {row.concept for row in parsed} >= {
        "urn:sds:reg:esrs:e1_5_01",
        "urn:sds:reg:esrs:e3_4_11",
        "urn:sds:reg:esrs:e3_4_12",
        "urn:sds:reg:ghg:ghg_corporate_reporting_ghg_scope1_gross_emissions_tco2e",
    }


def test_demo_water_rows_preserve_uc03_conversion_and_uc06_stored_unit_contract():
    payload = json.loads(demo.USE_CASES_PATH.read_text(encoding="utf-8"))
    parsed = {row.external_key: row for row in load_values_from_csv(demo.VALUES_PATH)}

    uc03 = payload["use_cases"]["UC-03"]
    assert uc03["kind"] == "functional_unit_conversion"
    for row_spec in uc03["rows"]:
        csv_row = parsed[row_spec["external_key"]]
        assert row_spec["verification"] == "unit_conversion"
        assert row_spec["source_unit"] == csv_row.unit == "L"
        assert row_spec["target_unit"] == csv_row.expected_unit == "m³"

    uc06 = payload["use_cases"]["UC-06"]
    uc06_spec = uc06["rows"][0]
    uc06_csv_row = parsed[uc06_spec["external_key"]]
    assert uc06["kind"] == "bounded_data_contract"
    assert uc06_spec["verification"] == "bounded_data_contract"
    assert uc06_spec["stored_unit"] == uc06_csv_row.unit == "m³"
    assert uc06_csv_row.expected_unit == uc06_csv_row.unit


def test_install_uses_supported_synchronous_import_and_verifies(monkeypatch):
    calls = []
    bootstrapped = []

    class FakeDb:
        def close(self):
            return None

    monkeypatch.setattr(demo, "_engine", lambda _url: object())
    monkeypatch.setattr(demo, "sessionmaker", lambda **_kwargs: lambda: FakeDb())
    monkeypatch.setattr(
        demo, "ensure_value_import_gate_hierarchy", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(demo, "_purge_demo_rows", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        demo, "_bootstrap_unit_catalog", lambda db: bootstrapped.append(db) or {}
    )
    monkeypatch.setattr(
        demo, "import_values_csv", lambda **kwargs: calls.append(kwargs) or 7
    )
    monkeypatch.setattr(demo, "verify_database", lambda _url: {"persisted_rows": 7})

    result = demo.install("postgresql://synthetic")

    assert result == {"persisted_rows": 7, "imported_rows": 7}
    assert len(bootstrapped) == 1
    assert calls[0]["tenant_id"] == demo.TENANT_ID
    assert calls[0]["csv_path"] == demo.VALUES_PATH


def test_demo_database_commands_fail_closed_without_explicit_revision_config(
    monkeypatch,
):
    monkeypatch.delenv("VALUE_REVISION_DUAL_WRITE_ENABLED")

    with pytest.raises(ValueError, match="require explicit revision-write"):
        demo.require_revision_write_configuration()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("VALUE_REVISION_API_ENABLED", "false"),
        ("VALUE_REVISION_DUAL_WRITE_ENABLED", "false"),
        ("VALUE_REVISION_PRIMARY_READ_PATH", "legacy"),
    ],
)
def test_demo_database_commands_reject_false_revision_config(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match="invalid or unavailable"):
        demo.require_revision_write_configuration()


def test_database_dependency_load_checks_config_before_cached_imports(monkeypatch):
    monkeypatch.delenv("VALUE_REVISION_API_ENABLED")
    # A populated dependency cache must not make a later DB command bypass the
    # process-level contract.
    monkeypatch.setattr(demo, "_engine", object())

    with pytest.raises(ValueError):
        demo._load_database_dependencies()


def test_database_dependency_load_does_not_import_retired_gate_purge(monkeypatch):
    monkeypatch.setattr(demo, "_engine", None)
    demo._load_database_dependencies()
    assert callable(demo._engine)
    assert callable(demo.import_values_csv)


def test_verify_database_rejects_missing_demo_rows(monkeypatch):
    class FakeQuery:
        def filter(self, *_args):
            return self

        def all(self):
            return []

    class FakeDb:
        def query(self, *_args):
            return FakeQuery()

        def close(self):
            return None

    monkeypatch.setattr(demo, "_engine", lambda _url: object())
    monkeypatch.setattr(demo, "sessionmaker", lambda **_kwargs: lambda: FakeDb())
    monkeypatch.setattr(
        demo,
        "ESGValue",
        type(
            "FakeValue",
            (),
            {
                "external_key": type("Key", (), {"in_": lambda *_: None})(),
                "created_by": object(),
            },
        ),
    )

    try:
        demo.verify_database("postgresql://synthetic")
    except ValueError as error:
        assert "persistence keys mismatch" in str(error)
    else:  # pragma: no cover - regression assertion
        raise AssertionError("missing demo rows must fail closed")


def test_verify_api_checks_only_tenant_data_manager_rows_and_conversions(monkeypatch):
    payload = json.loads(demo.USE_CASES_PATH.read_text(encoding="utf-8"))
    rows = _rows(payload)
    calls = []

    def fake_http_json(url, *, method="GET", payload=None, token=None):
        calls.append((url, method, payload, token))
        if url.endswith("/auth/me"):
            return {"company_id": demo.TENANT_ID, "role": "data_manager"}
        if url.endswith("/values?entity=gate_facility&limit=1000"):
            return {"items": [{"external_key": row["external_key"]} for row in rows]}
        assert url.endswith("/api/v1/convert")
        return {
            "converted_value": next(
                row["target_value"]
                for row in rows
                if row["verification"] == "unit_conversion"
                and row["source_value"] == str(payload["value"])
            ),
            "converted_unit": payload["to_unit"],
        }

    monkeypatch.setattr(demo, "_http_json", fake_http_json)

    result = demo.verify_api(
        "http://synthetic-api",
        tenant_principal_token="synthetic-data-manager-token",
    )

    assert result["api_visible_rows"] == 7
    assert len(result["api_conversions"]) == 4
    assert all(call[3] == "synthetic-data-manager-token" for call in calls)


@pytest.mark.parametrize(
    "principal",
    [
        None,
        "",
        {"company_id": None, "role": "data_manager"},
        {"company_id": demo.TENANT_ID, "role": "admin"},
    ],
)
def test_verify_api_refuses_non_tenant_bound_principal(monkeypatch, principal):
    calls = []

    def fake_http_json(url, *, method="GET", payload=None, token=None):
        calls.append((url, method, payload, token))
        return principal

    monkeypatch.setattr(demo, "_http_json", fake_http_json)

    with pytest.raises(
        ValueError,
        match="tenant-bound|principal bound to the demo tenant",
    ):
        demo.verify_api("http://synthetic-api", tenant_principal_token="token")

    assert calls == [("http://synthetic-api/auth/me", "GET", None, "token")]


def test_verify_api_refuses_a_request_without_a_tenant_principal(monkeypatch):
    calls = []

    def fake_http_json(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("a missing principal must fail before HTTP")

    monkeypatch.setattr(demo, "_http_json", fake_http_json)

    with pytest.raises(ValueError, match="tenant-bound"):
        demo.verify_api("http://synthetic-api", tenant_principal_token=None)

    assert calls == []


def test_ci_api_principal_path_uses_admin_only_for_user_creation(monkeypatch):
    payload = json.loads(demo.USE_CASES_PATH.read_text(encoding="utf-8"))
    rows = _rows(payload)
    calls = []

    def fake_http_json(url, *, method="GET", payload=None, token=None):
        calls.append((url, method, payload, token))
        if url.endswith("/auth/login"):
            if payload["username"] == "admin":
                assert payload["password"] == "bootstrap-secret"
                return {"access_token": "admin-token"}
            assert payload["username"].startswith("demo_ci_dm_")
            assert payload["password"]
            return {"access_token": "data-manager-token"}
        if url.endswith("/auth/users"):
            assert token == "admin-token"
            assert payload["company_id"] == demo.TENANT_ID
            assert payload["role"] == "data_manager"
            assert payload["username"].startswith("demo_ci_dm_")
            return {"company_id": demo.TENANT_ID, "role": "data_manager"}
        if url.endswith("/auth/me"):
            assert token == "data-manager-token"
            return {"company_id": demo.TENANT_ID, "role": "data_manager"}
        if url.endswith("/values?entity=gate_facility&limit=1000"):
            assert token == "data-manager-token"
            return {"items": [{"external_key": row["external_key"]} for row in rows]}
        assert url.endswith("/api/v1/convert")
        assert token == "data-manager-token"
        return {
            "converted_value": next(
                row["target_value"]
                for row in rows
                if row["verification"] == "unit_conversion"
                and row["source_value"] == str(payload["value"])
            ),
            "converted_unit": payload["to_unit"],
        }

    monkeypatch.setattr(demo, "_http_json", fake_http_json)

    result = demo.verify_ci_api_principal_path(
        "http://synthetic-api",
        admin_username="admin",
        admin_password="bootstrap-secret",
    )

    assert result["api_visible_rows"] == 7
    assert "disposable-volume removal" in result["api_principal_boundary"]
    assert all(
        token != "admin-token"
        for url, _method, _payload, token in calls
        if "/api/v1/" in url
    )


def test_cli_refuses_implicit_api_verification(monkeypatch):
    monkeypatch.setattr(demo, "verify_manifest", lambda: {})
    monkeypatch.setattr(demo, "verify_database", lambda _url: {"persisted_rows": 7})

    assert demo.main(["verify", "--api-url", "http://synthetic-api"]) == 1


def test_benchmark_uses_fixed_regression_budget_without_public_report(monkeypatch):
    monkeypatch.setattr(demo, "_engine", lambda _url: object())

    def fake_run_gate(**kwargs):
        assert kwargs["row_count"] == 1000
        assert kwargs["max_seconds"] == 30.0
        assert kwargs["tenant_id"] == demo.TENANT_ID
        assert "sds_public_demo_benchmark_" in str(kwargs["report_path"])
        return {"passed": True}, []

    monkeypatch.setitem(
        sys.modules,
        "gate_value_import_performance",
        SimpleNamespace(run_gate=fake_run_gate),
    )
    assert demo.benchmark("postgresql://synthetic") == {"passed": True}


def test_reset_refuses_to_erase_immutable_manifest_owned_demo_revisions(monkeypatch):
    class Field:
        def __init__(self, owner, name):
            self.owner = owner
            self.name = name

        def in_(self, values):
            allowed = set(values)
            return lambda row: getattr(row, self.name) in allowed

        def __eq__(self, value):
            return lambda row: getattr(row, self.name) == value

    def model(name, fields):
        result = type(name, (), {})
        for field_name in fields:
            setattr(result, field_name, Field(result, field_name))
        return result

    Revision = model(
        "Revision", ("id", "context_id", "external_key", "tenant_id", "created_by")
    )
    Context = model("Context", ("id", "tenant_id", "created_by"))
    Event = model("Event", ("revision_id",))
    Current = model("Current", ("revision_id",))
    Reported = model("Reported", ("revision_id",))
    Value = model("Value", ("external_key", "created_by"))

    class FakeQuery:
        def __init__(self, db, targets):
            self.db = db
            self.targets = targets
            self.row_type = (
                targets[0].owner if isinstance(targets[0], Field) else targets[0]
            )
            self.selected = list(db.rows[self.row_type])

        def filter(self, *predicates):
            for predicate in predicates:
                self.selected = [row for row in self.selected if predicate(row)]
            return self

        def distinct(self):
            return self

        def all(self):
            if isinstance(self.targets[0], Field):
                return [
                    tuple(getattr(row, field.name) for field in self.targets)
                    for row in self.selected
                ]
            return list(self.selected)

        def delete(self, **_kwargs):
            selected_ids = {id(row) for row in self.selected}
            self.db.rows[self.row_type] = [
                row
                for row in self.db.rows[self.row_type]
                if id(row) not in selected_ids
            ]
            return len(selected_ids)

    class FakeDb:
        def __init__(self, rows):
            self.rows = rows
            self.committed = False

        def query(self, *targets):
            return FakeQuery(self, targets)

        def commit(self):
            self.committed = True

        def close(self):
            return None

    owned_keys = demo._demo_external_keys()
    owned_revisions = [
        SimpleNamespace(
            id=f"owned-{index}",
            context_id=index,
            external_key=key,
            tenant_id=demo.TENANT_ID,
            created_by=demo.CREATED_BY,
        )
        for index, key in enumerate(sorted(owned_keys), start=1)
    ]
    namespace_collision = SimpleNamespace(
        id="non-demo-namespace",
        context_id=100,
        external_key="demo-wave1:customer-observation",
        tenant_id=demo.TENANT_ID,
        created_by=demo.CREATED_BY,
    )
    exact_key_collision = SimpleNamespace(
        id="non-demo-exact-key",
        context_id=101,
        external_key=next(iter(owned_keys)),
        tenant_id="other-tenant",
        created_by="other-provenance",
    )
    db = FakeDb(
        {
            Revision: owned_revisions + [namespace_collision, exact_key_collision],
            Context: [
                *[
                    SimpleNamespace(
                        id=row.context_id,
                        tenant_id=demo.TENANT_ID,
                        created_by=demo.CREATED_BY,
                    )
                    for row in owned_revisions
                ],
                SimpleNamespace(
                    id=100, tenant_id=demo.TENANT_ID, created_by=demo.CREATED_BY
                ),
                SimpleNamespace(
                    id=101,
                    tenant_id="other-tenant",
                    created_by="other-provenance",
                ),
            ],
            Event: [SimpleNamespace(revision_id=row.id) for row in owned_revisions],
            Current: [SimpleNamespace(revision_id=row.id) for row in owned_revisions],
            Reported: [SimpleNamespace(revision_id=row.id) for row in owned_revisions],
            Value: [
                *[
                    SimpleNamespace(external_key=key, created_by=demo.CREATED_BY)
                    for key in owned_keys
                ],
                SimpleNamespace(
                    external_key="demo-wave1:customer-observation",
                    created_by=demo.CREATED_BY,
                ),
                SimpleNamespace(
                    external_key=next(iter(owned_keys)),
                    created_by="other-provenance",
                ),
            ],
        }
    )
    monkeypatch.setattr(demo, "_engine", lambda _url: object())
    monkeypatch.setattr(demo, "sessionmaker", lambda **_kwargs: lambda: db)
    monkeypatch.setattr(demo, "ValueRevision", Revision)
    monkeypatch.setattr(demo, "ValueContext", Context)
    monkeypatch.setattr(demo, "ValueRevisionEvent", Event)
    monkeypatch.setattr(demo, "CurrentValuePointer", Current)
    monkeypatch.setattr(demo, "ReportedValuePointer", Reported)
    monkeypatch.setattr(demo, "ESGValue", Value)

    before = {model: list(rows) for model, rows in db.rows.items()}
    with pytest.raises(ValueError, match="immutable demo revision history"):
        demo.reset("postgresql://synthetic")
    assert db.rows == before
    assert db.committed is False
