from __future__ import annotations


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_interoperability_readiness_endpoint_reports_runtime_checks(
    client, analyst_token
):
    response = client.get(
        "/api/v1/interoperability/readiness",
        headers=_auth(analyst_token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] in {"ready", "not_ready"}
    check_names = {check["name"] for check in body["checks"]}
    assert "semantic_catalogues_loaded" in check_names
    assert "l_to_m3_unit_conversion" in check_names
    assert "kwh_to_mwh_energy_conversion" in check_names
    assert "gj_to_tco2e_requires_emission_factor" in check_names
    assert "csrd_gri_exact_mapping_loaded" in check_names
    assert "csrd_disclosure_e3_5_calculation_contract_loaded" in check_names

    gj_check = next(
        check
        for check in body["checks"]
        if check["name"] == "gj_to_tco2e_requires_emission_factor"
    )
    assert gj_check["ready"] is True
