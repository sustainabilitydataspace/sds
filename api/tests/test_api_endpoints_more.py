"""Additional API endpoint tests to raise router coverage (offline-safe)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_units_convert_list_and_validate_endpoints(client, admin_token):
    headers = _auth(admin_token)

    ok = client.post(
        "/api/v1/convert",
        headers=headers,
        json={"value": 1, "from_unit": "kg", "to_unit": "g"},
    )
    assert ok.status_code == 200
    assert float(ok.json()["converted_value"]) == 1000.0

    bad = client.post(
        "/api/v1/convert",
        headers=headers,
        json={"value": 1, "from_unit": "kg", "to_unit": "L"},
    )
    assert bad.status_code == 400

    listing = client.get("/api/v1/units?category=mass", headers=headers)
    assert listing.status_code == 200
    assert any(u["symbol"] == "kg" for u in listing.json())

    invalid_category = client.get(
        "/api/v1/units?category=not-a-category", headers=headers
    )
    assert invalid_category.status_code == 400

    compat = client.get(
        "/api/v1/units/validate?from_unit=kg&to_unit=g", headers=headers
    )
    assert compat.status_code == 200
    assert compat.json()["compatible"] is True

    incompat = client.get(
        "/api/v1/units/validate?from_unit=kg&to_unit=L", headers=headers
    )
    assert incompat.status_code == 200
    assert incompat.json()["compatible"] is False


def test_openapi_does_not_publish_legacy_sync_endpoints(client, admin_token):
    headers = _auth(admin_token)

    schema = client.get("/openapi.json", headers=headers)
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    assert not any(path.startswith("/api/v1/sync") for path in paths)

    removed = client.get("/api/v1/sync/status", headers=headers)
    assert removed.status_code == 404


def test_calculations_endpoints_calculate_batch_and_dependencies(
    monkeypatch, client, admin_token
):
    headers = _auth(admin_token)

    import src.calculation.engine as eng

    class FakeEngine:
        def calculate_indicator(self, indicator_uri: str, context, **_kwargs):
            if indicator_uri == "bad":
                raise RuntimeError("bad")
            return SimpleNamespace(
                value=1.0,
                unit="L",
                formula_used="x",
                confidence=1.0,
                calculation_timestamp=datetime.now(timezone.utc),
                variables_used=["v1"],
                dependencies_resolved=["v1"],
                execution_time_ms=1.0,
            )

    monkeypatch.setattr(eng, "CalculationEngine", FakeEngine)

    single = client.post(
        "/api/v1/calculate",
        headers=headers,
        json={
            "concept": "urn:sds:disclosure:csrd:e3-5",
            "entity": "madrid_plant",
            "period": "2024",
            "granularity": "annual",
            "include_trace": True,
        },
    )
    assert single.status_code == 200
    assert single.json()["trace"] is not None

    batch = client.get(
        "/api/v1/calculate/batch?entity=madrid_plant&period=2024&concepts=urn:sds:disclosure:csrd:e3-5,bad&granularity=annual",
        headers=headers,
    )
    assert batch.status_code == 200
    batch_items = batch.json()
    assert batch_items[0]["concept"] == "urn:sds:disclosure:csrd:e3-5"
    assert batch_items[0]["status"] == "success"
    assert batch_items[0]["result"]["concept"] == "urn:sds:disclosure:csrd:e3-5"
    assert batch_items[1]["concept"] == "bad"
    assert batch_items[1]["status"] == "failed"
    assert "bad" in batch_items[1]["error"]

    all_failed = client.get(
        "/api/v1/calculate/batch?entity=madrid_plant&period=2024&concepts=bad&granularity=annual",
        headers=headers,
    )
    assert all_failed.status_code == 200
    failed_item = all_failed.json()[0]
    assert failed_item["concept"] == "bad"
    assert failed_item["status"] == "failed"
    assert failed_item["result"] is None
    assert "bad" in failed_item["error"]

    deps = client.get(
        "/api/v1/calculate/dependencies/urn:sds:disclosure:csrd:e3-5",
        headers=headers,
    )
    assert deps.status_code == 200
    assert deps.json()["concept"] == "urn:sds:disclosure:csrd:e3-5"
    assert deps.json()["dependency_source"] == "ontology_graph"

    deps_missing = client.get("/api/v1/calculate/dependencies/unknown", headers=headers)
    assert deps_missing.status_code == 404
