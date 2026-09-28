"""Tests for main API application."""

from tests.legacy_guided_tokens import LEGACY_OVERLAY, LEGACY_QUERY


def test_root_endpoint(client):
    """Test root endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "name" in data
    assert "version" in data
    assert "status" in data
    assert data["status"] == "running"
    assert data["name"] == "SustainabilityDataSpace API"
    assert "portal" in data
    if data["portal"]:
        assert LEGACY_QUERY not in data["portal"]


def test_health_check(client):
    """Test health check endpoint."""
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_readiness_requires_system_health_permission(client):
    response = client.get("/ready")
    assert response.status_code == 403


def test_readiness_check(client, admin_token):
    """Test readiness check endpoint."""
    response = client.get("/ready", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"


def test_semantic_projection_readiness_does_not_disclose_exception_text(monkeypatch):
    from src.api import main
    from src.database import session as database_session
    from src.services.semantic_concept_projector import SemanticConceptProjector

    sentinel = "postgres" + "ql://private-user:***@private-host/internal"

    class _Session:
        def close(self):
            return None

    monkeypatch.setattr(database_session, "SessionLocal", lambda: _Session())

    def _fail(_self):
        raise RuntimeError(sentinel)

    monkeypatch.setattr(SemanticConceptProjector, "coverage_summary", _fail)

    payload = main._semantic_projection_readiness()

    assert payload == {
        "status": "unhealthy",
        "error_code": "semantic_projection_unavailable",
    }
    assert sentinel not in str(payload)


def test_readiness_fails_when_semantic_projection_is_incomplete(monkeypatch):
    """A healthy DB is not ready when active indicators lack concepts."""
    from src.config.settings import settings
    from src.services.semantic_concept_projector import SemanticConceptProjector

    class _Session:
        def close(self):
            pass

    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr("src.database.init_db.ping_db", lambda: None)
    monkeypatch.setattr("src.database.session.SessionLocal", lambda: _Session())
    monkeypatch.setattr(
        SemanticConceptProjector,
        "coverage_summary",
        lambda _self: {
            "ready": False,
            "active_indicators": 2,
            "projected_active_indicators": 1,
            "missing_active_indicator_identifiers": ["urn:sds:reg:esrs:e1_6_01"],
            "stale_active_indicator_identifiers": [],
        },
    )

    from src.api.main import _readiness_status

    status_code, data = _readiness_status()

    assert status_code == 503
    assert data["status"] == "unhealthy"
    assert set(data["dependencies"]) == {"database"}
    assert data["semantic_projection"]["status"] == "unhealthy"
    assert data["semantic_projection"]["active_indicators"] == 2
    assert data["semantic_projection"]["projected_active_indicators"] == 1


def test_readiness_fails_when_semantic_projection_is_stale(monkeypatch):
    """A healthy DB is not ready when active indicators have stale concepts."""
    from src.config.settings import settings
    from src.services.semantic_concept_projector import SemanticConceptProjector

    class _Session:
        def close(self):
            pass

    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr("src.database.init_db.ping_db", lambda: None)
    monkeypatch.setattr("src.database.session.SessionLocal", lambda: _Session())
    monkeypatch.setattr(
        SemanticConceptProjector,
        "coverage_summary",
        lambda _self: {
            "ready": False,
            "active_indicators": 2,
            "projected_active_indicators": 2,
            "missing_active_indicator_identifiers": [],
            "stale_active_indicator_identifiers": ["urn:sds:reg:esrs:e1_6_01"],
        },
    )

    from src.api.main import _readiness_status

    status_code, data = _readiness_status()

    assert status_code == 503
    assert data["status"] == "unhealthy"
    assert data["semantic_projection"]["status"] == "unhealthy"
    assert data["semantic_projection"]["active_indicators"] == 2
    assert data["semantic_projection"]["projected_active_indicators"] == 2
    assert data["semantic_projection"]["stale_active_indicator_identifiers"] == [
        "urn:sds:reg:esrs:e1_6_01"
    ]


def test_docs_uses_local_assets_offline_safe(client):
    """Swagger UI should not depend on external CDNs (offline-safe)."""
    response = client.get("/docs")
    assert response.status_code == 200
    assert "/static/swagger-ui/swagger-ui-bundle.js" in response.text
    assert "/static/swagger-ui/swagger-ui.css" in response.text
    assert LEGACY_OVERLAY not in response.text
    assert "cdn.jsdelivr.net" not in response.text


def test_auth_examples_guide_credentials_in_human_order(client):
    """OpenAPI examples should not invite swapped login fields or dummy refresh tokens."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    openapi = response.json()

    login_schema = openapi["components"]["schemas"]["UserLogin"]
    assert list(login_schema["properties"].keys()) == ["username", "password"]
    assert "example" not in login_schema

    login_examples = openapi["paths"]["/auth/login"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    login_value = login_examples["credentials"]["value"]
    assert list(login_value.keys()) == ["username", "password"]
    assert login_value == {"username": "your-account", "password": "[REDACTED]"}

    refresh_media = openapi["paths"]["/auth/refresh"]["post"]["requestBody"]["content"][
        "application/json"
    ]
    assert "examples" not in refresh_media
    assert "example" not in refresh_media
    refresh_schema = openapi["components"]["schemas"]["RefreshTokenRequest"]
    assert (
        "POST /auth/login"
        in refresh_schema["properties"]["refresh_token"]["description"]
    )

    bearer_scheme = openapi["components"]["securitySchemes"]["HTTPBearer"]
    assert "access_token" in bearer_scheme["description"]
    assert "Do not paste the refresh_token" in bearer_scheme["description"]


def test_redoc_uses_local_assets_offline_safe(client):
    """ReDoc should not depend on external networks (offline-safe)."""
    response = client.get("/redoc")
    assert response.status_code == 200
    assert "/static/redoc/redoc.standalone.js" in response.text
    assert "cdn.redoc.ly" not in response.text
    assert "fonts.googleapis.com" not in response.text
    assert "fastapi.tiangolo.com" not in response.text

    # Ensure referenced assets are actually served.
    js_response = client.get("/static/redoc/redoc.standalone.js")
    assert js_response.status_code == 200
    assert len(js_response.text) > 10_000

    openapi_response = client.get("/openapi.json")
    assert openapi_response.status_code == 200
    assert openapi_response.json().get("openapi")


def test_vendored_documentation_license_notices_are_served(client):
    for path, marker in (
        ("/static/swagger-ui/LICENSE", "Apache License"),
        ("/static/redoc/LICENSE", "The MIT License (MIT)"),
        ("/static/redoc/redoc.standalone.js.LICENSE.txt", "@license DOMPurify"),
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert marker in response.text


def test_versioned_jsonld_context_is_served_offline_safe(client):
    leaked_linux_home = "/".join(["", "home", "alice"])
    leaked_unc_prefix = "\\\\" + "ws" + "l.localhost"
    for path in ("/contexts/sds/v1.0.jsonld", "/context/sds/v1.0.jsonld"):
        response = client.get(path)
        assert response.status_code == 200
        payload = response.json()
        assert (
            payload["@context"]["@vocab"]
            == "https://sustainabilitydataspace.com/ontology#"
        )
        assert leaked_linux_home not in response.text
        assert leaked_unc_prefix not in response.text


def test_openapi_excludes_removed_legacy_paths(client):
    """Deprecated public aliases should be absent from the published OpenAPI contract."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    assert "/health" not in paths
    assert "/api/v1/units/convert" not in paths
    assert "/api/v1/mappings/esrs/{code}" not in paths
    assert "/api/v1/mappings/gri/{code}" not in paths
    assert not any(path.startswith("/api/v1/crosswalks") for path in paths)


def test_removed_legacy_paths_return_not_found(client):
    """Removed legacy aliases should no longer resolve at runtime."""
    assert client.get("/health").status_code == 404
    assert client.get("/api/v1/crosswalks/esrs/E1").status_code == 404
    assert client.get("/api/v1/crosswalks/gri/305").status_code == 404
    assert (
        client.post(
            "/api/v1/units/convert",
            json={"value": 1, "from_unit": "kg", "to_unit": "g"},
        ).status_code
        == 404
    )
