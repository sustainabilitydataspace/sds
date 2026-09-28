from __future__ import annotations


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_values_resolve_endpoint_returns_direct_value_with_unit_conversion(
    client, data_manager_token, analyst_token
):
    created = client.post(
        "/api/v1/values",
        headers=_auth(data_manager_token),
        json={
            "concept": "syg:Water_Industrial",
            "entity": "resolve_api_plant",
            "period": "2024-01-15",
            "value": 1500,
            "unit": "L",
            "metadata": {"source": "manual-swagger-test"},
        },
    )
    assert created.status_code == 201, created.text

    response = client.post(
        "/api/v1/values/resolve",
        headers=_auth(analyst_token),
        json={
            "target_concept": "syg:Water_Industrial",
            "entity": "resolve_api_plant",
            "period": "2024",
            "granularity": "annual",
            "target_unit": "m3",
            "include_trace": True,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "resolved"
    assert body["method"] in {"direct_stored", "converted"}
    assert body["target_concept"] == "syg:Water_Industrial"
    assert body["value"] == 1.5
    assert body["unit"] in {"m3", "m³"}
    assert body["trace"]["source_value_id"] == created.json()["id"]


def test_values_resolve_endpoint_refuses_unsupported_energy_to_emissions_conversion(
    client, data_manager_token, analyst_token
):
    created = client.post(
        "/api/v1/values",
        headers=_auth(data_manager_token),
        json={
            "concept": "syg:TotalEnergyConsumptionWithinOrganization",
            "entity": "resolve_api_plant",
            "period": "2024-01-15",
            "value": 5,
            "unit": "GJ",
        },
    )
    assert created.status_code == 201, created.text

    response = client.post(
        "/api/v1/values/resolve",
        headers=_auth(analyst_token),
        json={
            "target_concept": "syg:TotalEnergyConsumptionWithinOrganization",
            "entity": "resolve_api_plant",
            "period": "2024",
            "granularity": "annual",
            "target_unit": "t CO2e",
            "include_trace": True,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "unsupported_conversion"
    assert body["value"] is None
    assert body["trace"]["conversion"]["from_unit"] == "GJ"
    assert body["trace"]["conversion"]["to_unit"] == "t CO2e"


def test_values_resolve_endpoint_converts_energy_units_without_emissions_factor(
    client, data_manager_token, analyst_token
):
    created = client.post(
        "/api/v1/values",
        headers=_auth(data_manager_token),
        json={
            "concept": "syg:TotalEnergyConsumptionWithinOrganization",
            "entity": "resolve_api_energy_plant",
            "period": "2024-01-15",
            "value": 1000,
            "unit": "kWh",
        },
    )
    assert created.status_code == 201, created.text

    response = client.post(
        "/api/v1/values/resolve",
        headers=_auth(analyst_token),
        json={
            "target_concept": "syg:TotalEnergyConsumptionWithinOrganization",
            "entity": "resolve_api_energy_plant",
            "period": "2024",
            "granularity": "annual",
            "target_unit": "MWh",
            "include_trace": True,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "resolved"
    assert body["method"] == "converted"
    assert body["value"] == 1
    assert body["unit"] == "MWh"
    assert body["trace"]["conversion"]["from_unit"] == "kWh"
    assert body["trace"]["conversion"]["to_unit"] == "MWh"
