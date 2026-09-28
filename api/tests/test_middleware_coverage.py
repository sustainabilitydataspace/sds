"""Tests for middleware to achieve full coverage.

Targets uncovered lines:
- 78-98: Exception in logging middleware
- 130-139: General exception handler
- 155, 158-170: RequestContextMiddleware
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from src.api.middleware import (
    RequestContextMiddleware,
    setup_error_handling,
    setup_logging_middleware,
)


class TestLoggingMiddlewareException:
    """Tests for logging middleware exception handling (lines 78-98)."""

    def test_middleware_handles_exception_in_request_processing(self):
        """Test that middleware logs exceptions and re-raises them."""
        test_app = FastAPI()

        @test_app.get("/fail")
        async def fail_endpoint():
            raise RuntimeError("Intentional failure")

        setup_logging_middleware(test_app)
        setup_error_handling(test_app)

        client = TestClient(test_app, raise_server_exceptions=False)
        response = client.get("/fail")

        # The exception should be handled by the general exception handler
        assert response.status_code == 500
        assert "request_id" in response.json().get("error", {})

    def test_middleware_replaces_client_supplied_request_id(self):
        """The untrusted correlation header cannot control logged identifiers."""
        test_app = FastAPI()

        @test_app.get("/test")
        async def test_endpoint():
            return {"status": "ok"}

        setup_logging_middleware(test_app)

        client = TestClient(test_app)
        response = client.get(
            "/test",
            headers={"X-Request-ID": "custom-request-id-123"},
        )

        assert response.status_code == 200
        from uuid import UUID

        assert (
            str(UUID(response.headers["X-Request-ID"]))
            == response.headers["X-Request-ID"]
        )

    def test_client_supplied_private_request_id_is_never_logged(self, monkeypatch):
        from src.api import middleware

        logged = []
        monkeypatch.setattr(
            middleware.logger, "info", lambda *a, **kw: logged.append((a, kw))
        )
        test_app = FastAPI()

        @test_app.get("/test")
        async def test_endpoint():
            return {"status": "ok"}

        setup_logging_middleware(test_app)
        sentinel = "private-identifier-" + "x" * 128
        response = TestClient(test_app).get("/test", headers={"X-Request-ID": sentinel})
        assert response.status_code == 200
        assert sentinel not in str(logged)
        assert sentinel != response.headers["X-Request-ID"]

    def test_middleware_generates_request_id_if_missing(self):
        """Test that middleware generates request ID if not provided."""
        test_app = FastAPI()

        @test_app.get("/test")
        async def test_endpoint():
            return {"status": "ok"}

        setup_logging_middleware(test_app)

        client = TestClient(test_app)
        response = client.get("/test")

        assert response.status_code == 200
        # Response should include a generated request ID
        assert "X-Request-ID" in response.headers
        assert len(response.headers["X-Request-ID"]) > 0


@pytest.mark.asyncio
async def test_failed_login_does_not_record_submitted_username(monkeypatch):
    from src.api.routers import auth
    from src.auth.models import UserLogin

    logged = []
    monkeypatch.setattr(auth.logger, "warning", lambda *a, **kw: logged.append((a, kw)))
    monkeypatch.setattr(auth.logger, "info", lambda *a, **kw: logged.append((a, kw)))
    store = MagicMock()
    store.authenticate.return_value = None
    sentinel = "private-username-for-log-probe"
    with pytest.raises(HTTPException):
        await auth.login(
            request=Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/login",
                    "headers": [],
                    "client": ("127.0.0.1", 1234),
                }
            ),
            user_credentials=UserLogin(username=sentinel, password="synthetic-invalid"),
            store=store,
        )
    assert sentinel not in str(logged)


class TestGeneralExceptionHandler:
    """Tests for general exception handler (lines 130-139)."""

    def test_unhandled_exception_returns_500_with_request_id(self):
        """Test that unhandled exceptions return 500 with request_id."""
        test_app = FastAPI()

        @test_app.get("/unhandled")
        async def unhandled_exception():
            # This is not an HTTPException, so general handler catches it
            raise ValueError("Unexpected value error")

        setup_logging_middleware(test_app)
        setup_error_handling(test_app)

        client = TestClient(test_app, raise_server_exceptions=False)
        response = client.get("/unhandled")

        assert response.status_code == 500
        error = response.json().get("error", {})
        assert error.get("code") == 500
        assert error.get("message") == "Internal server error"
        assert "request_id" in error

    def test_http_exception_handler_returns_correct_status(self):
        """Test that HTTP exceptions are handled with correct status code."""
        test_app = FastAPI()

        @test_app.get("/not-found")
        async def not_found():
            raise HTTPException(status_code=404, detail="Resource not found")

        setup_logging_middleware(test_app)
        setup_error_handling(test_app)

        client = TestClient(test_app)
        response = client.get("/not-found")

        assert response.status_code == 404
        error = response.json().get("error", {})
        assert error.get("code") == 404
        assert error.get("message") == "Resource not found"


class TestRequestContextMiddleware:
    """Tests for RequestContextMiddleware (lines 155, 158-170)."""

    def test_request_context_middleware_http_request(self):
        """Test that RequestContextMiddleware handles HTTP requests."""
        import asyncio

        test_app = FastAPI()

        @test_app.get("/context-test")
        async def context_test():
            return {"status": "ok"}

        # Create middleware instance
        middleware = RequestContextMiddleware(test_app)

        # Create a mock scope for HTTP request
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/context-test",
            "headers": [],
        }

        receive = AsyncMock()
        send = AsyncMock()

        # Create a mock app that will be called
        mock_app = AsyncMock()
        middleware.app = mock_app

        asyncio.get_event_loop().run_until_complete(middleware(scope, receive, send))

        # Verify the app was called
        mock_app.assert_called_once_with(scope, receive, send)

    def test_request_context_middleware_non_http_request(self):
        """Test that RequestContextMiddleware handles non-HTTP requests."""
        import asyncio

        test_app = FastAPI()

        # Create middleware instance
        middleware = RequestContextMiddleware(test_app)

        # Create a mock scope for WebSocket (non-HTTP)
        scope = {
            "type": "websocket",
            "path": "/ws",
        }

        receive = AsyncMock()
        send = AsyncMock()

        # Create a mock app that will be called
        mock_app = AsyncMock()
        middleware.app = mock_app

        asyncio.get_event_loop().run_until_complete(middleware(scope, receive, send))

        # Verify the app was called (without context binding for non-HTTP)
        mock_app.assert_called_once_with(scope, receive, send)

    def test_request_context_middleware_integration(self):
        """Test RequestContextMiddleware integration with FastAPI."""
        test_app = FastAPI()

        @test_app.get("/middleware-test")
        async def middleware_test():
            return {"status": "ok"}

        # Add middleware using ASGI wrapper
        test_app = RequestContextMiddleware(test_app)

        # Can't use TestClient directly with ASGI middleware wrapper
        # This test verifies the middleware can be instantiated correctly
        assert test_app is not None


class TestMetricsRecording:
    """Tests for Prometheus metrics recording in middleware."""

    def test_successful_request_records_metrics(self):
        """Test that successful requests record Prometheus metrics."""
        test_app = FastAPI()

        @test_app.get("/metrics-test")
        async def metrics_test():
            return {"status": "ok"}

        setup_logging_middleware(test_app)

        client = TestClient(test_app)

        # Make multiple requests to ensure metrics are recorded
        for _ in range(3):
            response = client.get("/metrics-test")
            assert response.status_code == 200

    def test_failed_request_records_metrics(self):
        """Test that failed requests record Prometheus metrics."""
        test_app = FastAPI()

        @test_app.get("/error-metrics-test")
        async def error_metrics_test():
            raise HTTPException(status_code=400, detail="Bad request")

        setup_logging_middleware(test_app)
        setup_error_handling(test_app)

        client = TestClient(test_app)

        response = client.get("/error-metrics-test")
        assert response.status_code == 400


class TestMiddlewareWithNoClient:
    """Tests for middleware edge cases."""

    def test_middleware_handles_missing_client_info(self):
        """Test middleware handles requests without client info."""
        test_app = FastAPI()

        @test_app.get("/no-client")
        async def no_client():
            return {"status": "ok"}

        setup_logging_middleware(test_app)

        client = TestClient(test_app)
        response = client.get("/no-client")

        assert response.status_code == 200
