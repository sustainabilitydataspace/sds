"""
Middleware for FastAPI application.
"""

import time
import uuid
from typing import Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram

import structlog
from src.config.settings import settings

logger = structlog.get_logger(__name__)


def setup_security_headers_middleware(app: FastAPI):
    """Add conservative defensive security headers to every response."""

    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next: Callable):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        # HSTS only in DB-required (production-like) mode so local http dev and
        # offline mode are not forced onto HTTPS.
        if getattr(settings, "require_database", False):
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


REQUEST_COUNT = Counter(
    "sds_api_requests_total",
    "Total HTTP requests processed by the SDS API",
    ["method", "path", "status_code"],
)
REQUEST_DURATION = Histogram(
    "sds_api_request_duration_seconds",
    "HTTP request duration in seconds for the SDS API",
    ["method", "path"],
)


def _metric_path(request: Request) -> str:
    """Low-cardinality metric label: the matched route template, not the raw URL.

    Using ``request.url.path`` would put per-request identifiers (entity ids,
    periods, cursors) into Prometheus label values and explode cardinality.
    The matched route template (e.g. ``/api/v1/values/{id}``) is bounded.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    return template or "unmatched"


def setup_logging_middleware(app: FastAPI):
    """Setup logging middleware for request/response logging."""

    @app.middleware("http")
    async def logging_middleware(request: Request, call_next: Callable):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        # Start timer
        start_time = time.time()

        # Log request
        logger.info(
            "Request started",
            request_id=request_id,
            method=request.method,
            url=str(request.url),
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

        # Add request ID to request state
        request.state.request_id = request_id

        try:
            # Process request
            response = await call_next(request)

            # Calculate duration
            duration = time.time() - start_time
            path = _metric_path(request)

            REQUEST_COUNT.labels(
                method=request.method,
                path=path,
                status_code=str(response.status_code),
            ).inc()
            REQUEST_DURATION.labels(method=request.method, path=path).observe(duration)

            # Log response
            logger.info(
                "Request completed",
                request_id=request_id,
                status_code=response.status_code,
                duration_ms=round(duration * 1000, 2),
            )

            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id

            return response

        except Exception as e:
            # Calculate duration
            duration = time.time() - start_time
            path = _metric_path(request)

            REQUEST_COUNT.labels(
                method=request.method,
                path=path,
                status_code="500",
            ).inc()
            REQUEST_DURATION.labels(method=request.method, path=path).observe(duration)

            # Log error
            logger.error(
                "Request failed",
                request_id=request_id,
                error=str(e),
                duration_ms=round(duration * 1000, 2),
            )

            raise


def setup_error_handling(app: FastAPI):
    """Setup global error handling."""

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """Handle HTTP exceptions."""
        request_id = getattr(request.state, "request_id", "unknown")

        logger.warning(
            "HTTP exception",
            request_id=request_id,
            status_code=exc.status_code,
            detail=exc.detail,
        )

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.status_code,
                    "message": exc.detail,
                    "request_id": request_id,
                }
            },
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """Handle general exceptions."""
        request_id = getattr(request.state, "request_id", "unknown")

        logger.error(
            "Unhandled exception",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )

        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": 500,
                    "message": "Internal server error",
                    "request_id": request_id,
                }
            },
        )


class RequestContextMiddleware:
    """Middleware to add request context to structured logging."""

    def __init__(self, app: FastAPI):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            # Add request context to structlog
            request_id = str(uuid.uuid4())

            # Bind context to logger
            structlog.contextvars.clear_contextvars()
            structlog.contextvars.bind_contextvars(
                request_id=request_id,
                method=scope.get("method"),
                path=scope.get("path"),
            )

        await self.app(scope, receive, send)
