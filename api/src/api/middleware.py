"""
Middleware for FastAPI application.
"""

import asyncio
import threading
import time
import uuid
from typing import Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram
from starlette.types import ASGIApp, Receive, Scope, Send

import structlog
from src.config.settings import settings

logger = structlog.get_logger(__name__)


class RequestBodyLimitMiddleware:
    """Validate and bound the full request body before the app can send a response."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int | None = None,
        max_concurrent_bodies: int = 8,
        read_timeout_seconds: float = 30.0,
    ):
        self.app = app
        self.max_bytes = max_bytes or settings.request_max_body_bytes
        if self.max_bytes <= 0:
            raise ValueError("max_bytes must be greater than zero")
        if max_concurrent_bodies <= 0 or read_timeout_seconds <= 0:
            raise ValueError("request body admission limits must be positive")
        self.max_concurrent_bodies = max_concurrent_bodies
        self.read_timeout_seconds = read_timeout_seconds
        self._active_bodies = 0
        self._retained_body_bytes = 0
        self._inflight_body_bytes = 0
        self._pending_reads: set[asyncio.Future] = set()
        self._admission_lock = threading.Lock()
        self._health_probe_slots = threading.BoundedSemaphore(2)

    def _forget_pending_read(self, task: asyncio.Future) -> None:
        if not task.cancelled():
            task.exception()  # Consume late errors from an expired receive.
        with self._admission_lock:
            self._pending_reads.discard(task)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_lengths = [
            value
            for name, value in scope.get("headers", [])
            if name.lower() == b"content-length"
        ]
        if content_lengths and any(
            name.lower() == b"transfer-encoding" for name, _ in scope.get("headers", [])
        ):
            await self._reject(scope, receive, send, 400, "ambiguous_request_framing")
            return
        parsed_length = None
        if content_lengths:
            if len(content_lengths) != 1 or not content_lengths[0].isdigit():
                await self._reject(scope, receive, send, 400, "invalid_content_length")
                return
            try:
                parsed_length = int(content_lengths[0])
            except ValueError:
                await self._reject(scope, receive, send, 400, "invalid_content_length")
                return
            if parsed_length > self.max_bytes:
                await self._reject(scope, receive, send, 413, "request_body_too_large")
                return

        # Liveness cannot depend on upload admission: a set of slow clients
        # must not make a healthy process appear dead to its load balancer.
        # Body-framed health requests still pass through the normal guard.
        if (
            scope.get("path") == "/healthz"
            and scope.get("method") in {"GET", "HEAD"}
            and parsed_length in (None, 0)
            and not any(
                name.lower() == b"transfer-encoding"
                for name, _ in scope.get("headers", [])
            )
        ):
            # An unframed GET may still carry an ASGI body. Check one bounded
            # event without consuming an upload slot, even under saturation.
            if not self._health_probe_slots.acquire(blocking=False):
                await self._reject(
                    scope, receive, send, 503, "health_probe_admission_full"
                )
                return
            try:
                probe = asyncio.ensure_future(receive())
            except BaseException:
                self._health_probe_slots.release()
                raise
            probe.add_done_callback(
                lambda done: (
                    done.exception() if not done.cancelled() else None,
                    self._health_probe_slots.release(),
                )
            )
            try:
                done, _ = await asyncio.wait(
                    {probe}, timeout=min(1.0, self.read_timeout_seconds)
                )
            except BaseException:
                probe.cancel()
                raise
            if not done:
                probe.cancel()
                await self._reject(scope, receive, send, 408, "request_body_timeout")
                return
            message = probe.result()
            if message.get("type") == "http.disconnect":
                return
            if message.get("type") != "http.request":
                await self._reject(scope, receive, send, 400, "invalid_request_body")
                return
            if message.get("body") or message.get("more_body", False):
                status = 413 if len(message.get("body", b"")) > self.max_bytes else 400
                await self._reject(
                    scope, receive, send, status, "unexpected_health_body"
                )
                return

            replayed = False

            async def replay_health_receive():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return message
                return await receive()

            await self.app(scope, replay_health_receive, send)
            return

        with self._admission_lock:
            admitted = self._active_bodies < self.max_concurrent_bodies and (
                parsed_length is None
                or self._retained_body_bytes + self._inflight_body_bytes + parsed_length
                <= self.max_bytes * self.max_concurrent_bodies
            )
            if admitted:
                self._active_bodies += 1
        if not admitted:
            await self._reject(scope, receive, send, 503, "request_body_admission_full")
            return

        slot_owned = True
        retained_size = 0
        inflight_size = 0
        try:
            body = bytearray()
            deadline = time.monotonic() + self.read_timeout_seconds
            empty_chunks = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    await self._reject(
                        scope, receive, send, 408, "request_body_timeout"
                    )
                    return
                read_task = None
                with self._admission_lock:
                    if len(self._pending_reads) < self.max_concurrent_bodies:
                        read_task = asyncio.ensure_future(receive())
                        self._pending_reads.add(read_task)
                if read_task is None:
                    await self._reject(
                        scope, receive, send, 503, "request_body_admission_full"
                    )
                    return
                read_task.add_done_callback(self._forget_pending_read)
                try:
                    done, _pending = await asyncio.wait({read_task}, timeout=remaining)
                except BaseException:
                    read_task.cancel()
                    raise
                if not done or time.monotonic() >= deadline:
                    read_task.cancel()
                    # Give a cooperative ASGI receive one scheduling turn to
                    # settle; a cancellation-suppressing receive remains
                    # counted until its own eventual completion.
                    await asyncio.sleep(0)
                    if read_task.done():
                        self._forget_pending_read(read_task)
                    await self._reject(
                        scope, receive, send, 408, "request_body_timeout"
                    )
                    return
                self._forget_pending_read(read_task)
                message = read_task.result()
                if message["type"] == "http.disconnect":
                    return
                if message["type"] != "http.request":
                    raise RuntimeError("unexpected ASGI request event")
                chunk = message.get("body", b"")
                if len(chunk) > self.max_bytes - len(body):
                    await self._reject(
                        scope, receive, send, 413, "request_body_too_large"
                    )
                    return
                with self._admission_lock:
                    chunk_admitted = (
                        self._retained_body_bytes
                        + self._inflight_body_bytes
                        + len(chunk)
                        <= self.max_bytes * self.max_concurrent_bodies
                    )
                    if chunk_admitted:
                        self._inflight_body_bytes += len(chunk)
                        inflight_size += len(chunk)
                if not chunk_admitted:
                    await self._reject(
                        scope, receive, send, 503, "request_body_admission_full"
                    )
                    return
                body.extend(chunk)
                if not message.get("more_body", False):
                    break
                if not chunk:
                    empty_chunks += 1
                    if empty_chunks > 1024:
                        await self._reject(
                            scope, receive, send, 413, "request_body_too_large"
                        )
                        return

            if parsed_length is not None and len(body) != parsed_length:
                await self._reject(scope, receive, send, 400, "invalid_content_length")
                return

            body_bytes = bytes(body)
            body.clear()
            replayed = False

            async def replay_receive():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {
                        "type": "http.request",
                        "body": body_bytes,
                        "more_body": False,
                    }
                return await receive()

            with self._admission_lock:
                self._inflight_body_bytes -= inflight_size
                inflight_size = 0
                retained_size = len(body_bytes)
                self._retained_body_bytes += retained_size
                self._active_bodies -= 1
                slot_owned = False
            await self.app(scope, replay_receive, send)
        finally:
            with self._admission_lock:
                if slot_owned:
                    self._active_bodies -= 1
                self._inflight_body_bytes -= inflight_size
                self._retained_body_bytes -= retained_size

    @staticmethod
    async def _reject(scope, receive, send, status_code: int, error_code: str) -> None:
        response = JSONResponse(
            status_code=status_code,
            content={"detail": error_code},
        )
        await response(scope, receive, send)


class IndicatorJobAdmissionMiddleware:
    """Reject DB indicator submissions before body reads or job-state access."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and scope["path"]
            in {
                "/api/v1/indicators/import-csv-jobs",
                "/api/v1/indicators/import-csv-jobs/",
            }
            and settings.require_database
        ):
            response = JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "Database-backed async indicator import jobs are unsupported pending H15"
                    )
                },
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class ValueJobAdmissionMiddleware:
    """Reject unsupported DB value jobs without receiving or parsing the body."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and (
                (
                    scope["method"] == "POST"
                    and scope["path"]
                    in {
                        "/api/v1/values/import-jobs",
                        "/api/v1/values/import-csv-jobs",
                    }
                )
                or (
                    scope["method"] == "GET"
                    and scope["path"].startswith("/api/v1/values/import-jobs/")
                )
            )
            and settings.require_database
        ):
            response = JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "Database-backed async value import jobs are unsupported pending H15"
                    )
                },
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


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
        # Client headers are untrusted and may contain secrets, control bytes or
        # unbounded identifiers. Correlation IDs are always server-issued.
        request_id = str(uuid.uuid4())

        # Start timer
        start_time = time.time()

        # Log request
        logger.info(
            "Request started",
            request_id=request_id,
            method=request.method,
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
                path=path,
                status_code=response.status_code,
                duration_ms=round(duration * 1000, 2),
            )

            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id

            return response

        except Exception as exc:
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
                error_type=type(exc).__name__,
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
