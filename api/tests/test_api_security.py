"""
Tests for API security and authorization rules.
Tests that all endpoints are properly protected with authentication and authorization.
"""

import pytest

from src.auth.models import UserRole


class TestAPISecurityBase:
    """Base class for API security tests."""


class TestPublicEndpoints(TestAPISecurityBase):
    """Test that public endpoints don't require authentication."""

    def test_root_endpoint_public(self, client):
        """Test root endpoint is public."""
        response = client.get("/")
        assert response.status_code == 200

    def test_health_endpoint_public(self, client):
        """Test health endpoint is public."""
        response = client.get("/healthz")
        assert response.status_code == 200

    def test_ready_endpoint_requires_system_health_permission(self, client):
        """Readiness includes catalog state and is not a public probe."""
        response = client.get("/ready")
        assert response.status_code == 403

    def test_docs_endpoint_public(self, client):
        """Test docs endpoint is public."""
        response = client.get("/docs")
        assert response.status_code == 200

    def test_openapi_endpoint_public(self, client):
        """Test OpenAPI spec endpoint is public."""
        response = client.get("/openapi.json")
        assert response.status_code == 200

    def test_login_endpoint_public(self, client):
        """Test login endpoint is public."""
        response = client.post(
            "/auth/login", json={"username": "admin", "password": "admin123"}
        )
        assert response.status_code == 200


class TestOntologyEndpointsSecurity(TestAPISecurityBase):
    """Test security for ontology endpoints."""

    def test_list_concepts_requires_auth(self, client):
        """Test that listing concepts requires authentication."""
        response = client.get("/api/v1/concepts")
        assert response.status_code == 403  # No auth header

    def test_list_concepts_viewer_allowed(self, client, viewer_token):
        """Test that VIEWER can list concepts."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.get("/api/v1/concepts", headers=headers)
        assert response.status_code == 200

    def test_get_equivalences_requires_auth(self, client):
        """Test that getting equivalences requires authentication."""
        response = client.get("/api/v1/equivalences")
        assert response.status_code == 403

    def test_get_equivalences_viewer_allowed(self, client, viewer_token):
        """Test that VIEWER can get equivalences."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.get("/api/v1/equivalences", headers=headers)
        assert response.status_code == 200

    def test_execute_sparql_requires_auth(self, client):
        """Test that executing SPARQL requires authentication."""
        response = client.post(
            "/api/v1/sparql", json={"query": "SELECT * WHERE { ?s ?p ?o } LIMIT 10"}
        )
        assert response.status_code == 403

    def test_execute_sparql_viewer_forbidden(self, client, viewer_token):
        """Test that VIEWER cannot execute SPARQL."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.post(
            "/api/v1/sparql",
            json={"query": "SELECT * WHERE { ?s ?p ?o } LIMIT 10"},
            headers=headers,
        )
        assert response.status_code == 403

    def test_execute_sparql_analyst_allowed(self, client, analyst_token):
        """Test that ANALYST can execute SPARQL."""
        headers = {"Authorization": f"Bearer {analyst_token}"}
        response = client.post(
            "/api/v1/sparql",
            json={"query": "SELECT * WHERE { ?s ?p ?o } LIMIT 10"},
            headers=headers,
        )
        # Should succeed or return 500 if SPARQL service unavailable
        assert response.status_code in [200, 500]

    def test_get_taxonomies_requires_auth(self, client):
        """Test that getting taxonomies requires authentication."""
        response = client.get("/api/v1/taxonomies")
        assert response.status_code == 403

    def test_get_taxonomies_viewer_allowed(self, client, viewer_token):
        """Test that VIEWER can get taxonomies."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.get("/api/v1/taxonomies", headers=headers)
        assert response.status_code == 200


class TestConceptDetailsSecurity(TestAPISecurityBase):
    """Test security for current concept detail endpoints."""

    def test_get_concept_detail_requires_auth(self, client):
        """Test that concept detail requires authentication."""
        response = client.get("/api/v1/concepts/urn:sds:disclosure:csrd:e3-5")
        assert response.status_code == 403

    def test_get_concept_detail_viewer_allowed(self, client, viewer_token):
        """Test that VIEWER can retrieve concept detail."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.get(
            "/api/v1/concepts/urn:sds:disclosure:csrd:e3-5", headers=headers
        )
        assert response.status_code in [200, 404, 500, 503]


class TestCalculationsEndpointsSecurity(TestAPISecurityBase):
    """Test security for calculations endpoints."""

    def test_calculate_requires_auth(self, client):
        """Test that calculations require authentication."""
        response = client.post(
            "/api/v1/calculate",
            json={
                "concept": "urn:sds:disclosure:csrd:e3-5",
                "entity": "madrid_plant",
                "period": "2024",
                "granularity": "annual",
            },
        )
        assert response.status_code == 403

    def test_calculate_viewer_forbidden(self, client, viewer_token):
        """Test that VIEWER cannot execute calculations."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.post(
            "/api/v1/calculate",
            json={
                "concept": "urn:sds:disclosure:csrd:e3-5",
                "entity": "madrid_plant",
                "period": "2024",
                "granularity": "annual",
            },
            headers=headers,
        )
        assert response.status_code == 403

    def test_calculate_analyst_allowed(self, client, analyst_token):
        """Test that ANALYST can execute calculations."""
        headers = {"Authorization": f"Bearer {analyst_token}"}
        response = client.post(
            "/api/v1/calculate",
            json={
                "concept": "urn:sds:disclosure:csrd:e3-5",
                "entity": "madrid_plant",
                "period": "2024",
                "granularity": "annual",
            },
            headers=headers,
        )
        # Missing input values should return 404; infra issues may return 500.
        assert response.status_code in [200, 404, 500]

    def test_batch_calculate_requires_auth(self, client):
        """Test that batch calculations require authentication."""
        response = client.get(
            "/api/v1/calculate/batch?entity=madrid_plant&period=2024&concepts=urn:sds:disclosure:csrd:e3-5"
        )
        assert response.status_code == 403

    def test_batch_calculate_analyst_allowed(self, client, analyst_token):
        """Test that ANALYST can execute batch calculations."""
        headers = {"Authorization": f"Bearer {analyst_token}"}
        response = client.get(
            "/api/v1/calculate/batch?entity=madrid_plant&period=2024&concepts=urn:sds:disclosure:csrd:e3-5",
            headers=headers,
        )
        # Should succeed or return 500 if service unavailable
        assert response.status_code in [200, 500]

    def test_batch_calculate_rejects_unbounded_concept_lists(
        self, client, analyst_token
    ):
        headers = {"Authorization": f"Bearer {analyst_token}"}
        concepts = ",".join(f"urn:test:{index}" for index in range(101))

        response = client.get(
            "/api/v1/calculate/batch",
            params={
                "entity": "madrid_plant",
                "period": "2024",
                "concepts": concepts,
            },
            headers=headers,
        )

        assert response.status_code == 400
        assert "At most 100 concepts are allowed" in response.text

    def test_get_dependencies_requires_auth(self, client):
        """Test that getting dependencies requires authentication."""
        response = client.get(
            "/api/v1/calculate/dependencies/urn:sds:disclosure:csrd:e3-5"
        )
        assert response.status_code == 403

    def test_get_dependencies_viewer_allowed(self, client, viewer_token):
        """Test that VIEWER can get dependencies."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.get(
            "/api/v1/calculate/dependencies/urn:sds:disclosure:csrd:e3-5",
            headers=headers,
        )
        assert response.status_code in [200, 404, 500]


class TestHierarchiesEndpointsSecurity(TestAPISecurityBase):
    """Test security for hierarchies endpoints."""

    def test_list_hierarchies_requires_auth(self, client):
        """Test that listing hierarchies requires authentication."""
        response = client.get("/api/v1/hierarchies")
        assert response.status_code == 403

    def test_list_hierarchies_viewer_allowed(self, client, viewer_token):
        """Test that VIEWER can list hierarchies."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.get("/api/v1/hierarchies", headers=headers)
        assert response.status_code == 200

    def test_create_hierarchy_requires_auth(self, client):
        """Test that creating hierarchies requires authentication."""
        response = client.post(
            "/api/v1/hierarchies",
            json={
                "company_id": "test_company",
                "hierarchy_type": "organizational",
                "name": "Test Hierarchy",
                "levels": [],
            },
        )
        assert response.status_code == 403

    def test_create_hierarchy_viewer_forbidden(self, client, viewer_token):
        """Test that VIEWER cannot create hierarchies."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.post(
            "/api/v1/hierarchies",
            json={
                "company_id": "test_company",
                "hierarchy_type": "organizational",
                "name": "Test Hierarchy",
                "levels": [],
            },
            headers=headers,
        )
        assert response.status_code == 403

    def test_create_hierarchy_analyst_forbidden(self, client, analyst_token):
        """Test that ANALYST cannot create hierarchies."""
        headers = {"Authorization": f"Bearer {analyst_token}"}
        response = client.post(
            "/api/v1/hierarchies",
            json={
                "company_id": "test_company",
                "hierarchy_type": "organizational",
                "name": "Test Hierarchy",
                "levels": [],
            },
            headers=headers,
        )
        assert response.status_code == 403

    def test_create_hierarchy_data_manager_allowed(self, client, data_manager_token):
        """Test that DATA_MANAGER can create hierarchies."""
        headers = {"Authorization": f"Bearer {data_manager_token}"}
        response = client.post(
            "/api/v1/hierarchies",
            json={
                "company_id": "test_company",
                "hierarchy_type": "organizational",
                "name": "Test Hierarchy",
                "levels": [],
                "active": True,
            },
            headers=headers,
        )
        # Should succeed or return 400/422/500 for validation/service errors
        assert response.status_code in [201, 400, 422, 500]

    def test_update_hierarchy_requires_auth(self, client):
        """Test that updating hierarchies requires authentication."""
        response = client.put(
            "/api/v1/hierarchies/test_id",
            json={
                "company_id": "test_company",
                "hierarchy_type": "organizational",
                "name": "Updated Hierarchy",
                "levels": [],
                "active": True,
            },
        )
        assert response.status_code == 403

    def test_update_hierarchy_viewer_forbidden(self, client, viewer_token):
        """Test that VIEWER cannot update hierarchies."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.put(
            "/api/v1/hierarchies/test_id",
            json={
                "company_id": "test_company",
                "hierarchy_type": "organizational",
                "name": "Updated Hierarchy",
                "levels": [],
                "active": True,
            },
            headers=headers,
        )
        assert response.status_code == 403

    def test_delete_hierarchy_requires_auth(self, client):
        """Test that deleting hierarchies requires authentication."""
        response = client.delete("/api/v1/hierarchies/test_id")
        assert response.status_code == 403

    def test_delete_hierarchy_viewer_forbidden(self, client, viewer_token):
        """Test that VIEWER cannot delete hierarchies."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.delete("/api/v1/hierarchies/test_id", headers=headers)
        assert response.status_code == 403


class TestUnitsEndpointsSecurity(TestAPISecurityBase):
    """Test security for units endpoints."""

    def test_convert_units_requires_auth(self, client):
        """Test that unit conversion requires authentication."""
        response = client.post(
            "/api/v1/convert", json={"value": 1000, "from_unit": "kg", "to_unit": "t"}
        )
        assert response.status_code == 403

    def test_convert_units_viewer_allowed(self, client, viewer_token):
        """Test that VIEWER can convert units."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.post(
            "/api/v1/convert",
            json={"value": 1000, "from_unit": "kg", "to_unit": "t"},
            headers=headers,
        )
        # Should succeed or return 400/500/503 for validation/service errors
        assert response.status_code in [200, 400, 500, 503]

    def test_list_units_requires_auth(self, client):
        """Test that listing units requires authentication."""
        response = client.get("/api/v1/units")
        assert response.status_code == 403

    def test_list_units_viewer_allowed(self, client, viewer_token):
        """Test that VIEWER can list units."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.get("/api/v1/units", headers=headers)
        # Should succeed or return 503 if service unavailable
        assert response.status_code in [200, 503]

    def test_validate_units_requires_auth(self, client):
        """Test that validating units requires authentication."""
        response = client.get("/api/v1/units/validate?from_unit=kg&to_unit=t")
        assert response.status_code == 403

    def test_validate_units_viewer_allowed(self, client, viewer_token):
        """Test that VIEWER can validate units."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.get(
            "/api/v1/units/validate?from_unit=kg&to_unit=t", headers=headers
        )
        # Should succeed or return 400/500/503 for validation/service errors
        assert response.status_code in [200, 400, 500, 503]


class TestLegacySyncSurfaceRemoved(TestAPISecurityBase):
    """Test that the legacy sync plane is no longer part of the public API."""

    def test_sync_routes_return_not_found(self, client, admin_token):
        """Legacy sync routes should no longer be mounted at all."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        for method, endpoint in [
            ("GET", "/api/v1/sync/status"),
            ("POST", "/api/v1/sync/trigger"),
            ("POST", "/api/v1/sync/auto-sync/enable"),
        ]:
            response = client.request(method, endpoint, headers=headers)
            assert response.status_code == 404

    def test_openapi_does_not_publish_sync_routes(self, client):
        """OpenAPI should not expose the removed sync endpoints."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]
        assert not any(path.startswith("/api/v1/sync") for path in paths)


class TestValuesEndpointsSecurity(TestAPISecurityBase):
    """Test security for values endpoints (already protected)."""

    def test_create_value_requires_auth(self, client):
        """Test that creating values requires authentication."""
        response = client.post(
            "/api/v1/values",
            json={
                "concept": "syg:Water_Cooling",
                "entity": "madrid_plant",
                "period": "2024-01-15",
                "value": 1500.5,
                "unit": "L",
            },
        )
        assert response.status_code == 403

    def test_create_value_viewer_forbidden(self, client, viewer_token):
        """Test that VIEWER cannot create values."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.post(
            "/api/v1/values",
            json={
                "concept": "syg:Water_Cooling",
                "entity": "madrid_plant",
                "period": "2024-01-15",
                "value": 1500.5,
                "unit": "L",
            },
            headers=headers,
        )
        assert response.status_code == 403

    def test_create_value_analyst_forbidden(self, client, analyst_token):
        """Test that ANALYST cannot create values."""
        headers = {"Authorization": f"Bearer {analyst_token}"}
        response = client.post(
            "/api/v1/values",
            json={
                "concept": "syg:Water_Cooling",
                "entity": "madrid_plant",
                "period": "2024-01-15",
                "value": 1500.5,
                "unit": "L",
            },
            headers=headers,
        )
        assert response.status_code == 403

    def test_create_value_data_manager_allowed(self, client, data_manager_token):
        """Test that DATA_MANAGER can create values."""
        headers = {"Authorization": f"Bearer {data_manager_token}"}
        response = client.post(
            "/api/v1/values",
            json={
                "concept": "syg:Water_Cooling",
                "entity": "madrid_plant",
                "period": "2024-01-15",
                "value": 1500.5,
                "unit": "L",
            },
            headers=headers,
        )
        # Should succeed or return 400/500 for validation/service errors
        assert response.status_code in [201, 400, 500]

    def test_list_values_requires_auth(self, client):
        """Test that listing values requires authentication."""
        response = client.get("/api/v1/values")
        assert response.status_code == 403

    def test_list_values_viewer_allowed(self, client, viewer_token):
        """Test that VIEWER can list values."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.get("/api/v1/values", headers=headers)
        assert response.status_code == 200

    def test_import_values_requires_auth(self, client):
        """Test that bulk import requires authentication."""
        response = client.post("/api/v1/values/import", json={"items": []})
        assert response.status_code == 403

    def test_import_values_viewer_forbidden(self, client, viewer_token):
        """Test that VIEWER cannot bulk import values."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.post(
            "/api/v1/values/import", headers=headers, json={"items": []}
        )
        assert response.status_code == 403

    def test_import_values_csv_requires_auth(self, client):
        """Test that CSV import requires authentication."""
        response = client.post(
            "/api/v1/values/import-csv", files={"file": ("values.csv", b"", "text/csv")}
        )
        assert response.status_code == 403

    def test_import_values_csv_viewer_forbidden(self, client, viewer_token):
        """Test that VIEWER cannot import values from CSV."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.post(
            "/api/v1/values/import-csv",
            headers=headers,
            files={"file": ("values.csv", b"", "text/csv")},
        )
        assert response.status_code == 403

    def test_export_values_requires_auth(self, client):
        """Test that exporting values requires authentication."""
        response = client.get("/api/v1/values/export")
        assert response.status_code == 403

    def test_export_values_viewer_allowed(self, client, viewer_token):
        """Test that VIEWER can export values."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.get("/api/v1/values/export", headers=headers)
        assert response.status_code == 200

    def test_delete_value_requires_auth(self, client):
        """Test that deleting values requires authentication."""
        response = client.delete("/api/v1/values/test_id")
        assert response.status_code == 403

    def test_delete_value_viewer_forbidden(self, client, viewer_token):
        """Test that VIEWER cannot delete values."""
        headers = {"Authorization": f"Bearer {viewer_token}"}
        response = client.delete("/api/v1/values/test_id", headers=headers)
        assert response.status_code == 403


class TestRoleHierarchy(TestAPISecurityBase):
    """Test role hierarchy and permission inheritance."""

    def test_admin_has_all_permissions(self, client, admin_token):
        """Test that ADMIN has access to all endpoints."""
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Test various endpoints
        endpoints = [
            ("GET", "/api/v1/concepts"),
            ("GET", "/api/v1/equivalences"),
            ("GET", "/api/v1/hierarchies"),
            ("GET", "/api/v1/units"),
        ]

        for method, endpoint in endpoints:
            if method == "GET":
                response = client.get(endpoint, headers=headers)
            elif method == "POST":
                response = client.post(endpoint, headers=headers, json={})

            # Should not return 403 (Forbidden)
            assert (
                response.status_code != 403
            ), f"Admin should have access to {endpoint}"

    def test_viewer_limited_to_read_only(self, client, viewer_token):
        """Test that VIEWER can only read, not write."""
        headers = {"Authorization": f"Bearer {viewer_token}"}

        # Should be able to read
        read_endpoints = [
            "/api/v1/concepts",
            "/api/v1/equivalences",
            "/api/v1/hierarchies",
            "/api/v1/units",
            "/api/v1/values",
        ]

        for endpoint in read_endpoints:
            response = client.get(endpoint, headers=headers)
            assert (
                response.status_code != 403
            ), f"Viewer should be able to read {endpoint}"

        # Should NOT be able to write
        write_tests = [
            (
                "POST",
                "/api/v1/values",
                {
                    "concept": "test",
                    "entity": "test",
                    "period": "2024-01-01",
                    "value": 100,
                    "unit": "L",
                },
            ),
            (
                "POST",
                "/api/v1/calculate",
                {
                    "concept": "test",
                    "entity": "test",
                    "period": "2024",
                    "granularity": "annual",
                },
            ),
            (
                "POST",
                "/api/v1/hierarchies",
                {
                    "company_id": "test",
                    "hierarchy_type": "organizational",
                    "name": "test",
                    "levels": [],
                    "active": True,
                },
            ),
        ]

        for method, endpoint, data in write_tests:
            response = client.post(endpoint, headers=headers, json=data)
            assert (
                response.status_code == 403
            ), f"Viewer should NOT be able to write to {endpoint}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
