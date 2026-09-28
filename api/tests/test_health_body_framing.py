"""Unframed liveness requests cannot bypass request-body admission."""

import asyncio

from starlette.responses import JSONResponse

from src.api.middleware import RequestBodyLimitMiddleware


def test_unframed_health_rejects_actual_body_before_success():
    called = False
    sent = []

    async def app(scope, receive, send):
        nonlocal called
        called = True
        await JSONResponse({"ok": True})(scope, receive, send)

    async def receive():
        return {"type": "http.request", "body": b"x" * 32, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/healthz",
        "headers": [],
        "http_version": "1.1",
    }
    asyncio.run(RequestBodyLimitMiddleware(app, max_bytes=8)(scope, receive, send))
    assert not called
    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [413]


def test_unframed_health_rejects_chunked_or_malformed_events():
    async def app(scope, receive, send):
        raise AssertionError("body with ambiguous framing must not reach health")

    async def scenario(message, expected):
        sent = []

        async def receive():
            return message

        async def send(event):
            sent.append(event)

        await RequestBodyLimitMiddleware(app, max_bytes=8)(
            {"type": "http", "method": "GET", "path": "/healthz", "headers": []},
            receive,
            send,
        )
        assert [m["status"] for m in sent if m["type"] == "http.response.start"] == (
            [] if expected == 0 else [expected]
        )

    asyncio.run(scenario({"type": "http.request", "body": b"x"}, 400))
    asyncio.run(scenario({"type": "http.request", "more_body": True}, 400))
    asyncio.run(scenario({"type": "invalid"}, 400))
    asyncio.run(scenario({"type": "http.disconnect"}, 0))


def test_health_probe_timeout_and_capacity_are_bounded():
    async def app(scope, receive, send):
        raise AssertionError("slow health request must not reach handler")

    async def scenario():
        middleware = RequestBodyLimitMiddleware(
            app, max_bytes=8, read_timeout_seconds=0.1
        )
        started = asyncio.Event()
        count = 0

        async def slow_receive():
            nonlocal count
            count += 1
            if count == 2:
                started.set()
            await asyncio.Event().wait()
            return {"type": "http.request", "body": b"", "more_body": False}

        async def request():
            sent = []

            async def send(event):
                sent.append(event)

            await middleware(
                {"type": "http", "method": "GET", "path": "/healthz", "headers": []},
                slow_receive,
                send,
            )
            return [e["status"] for e in sent if e["type"] == "http.response.start"]

        first, second = asyncio.create_task(request()), asyncio.create_task(request())
        await started.wait()
        assert await request() == [503]
        assert await first == [408]
        assert await second == [408]

    asyncio.run(scenario())


def test_health_probe_cancellation_releases_capacity():
    async def app(scope, receive, send):
        raise AssertionError("cancelled probe must not dispatch")

    async def scenario():
        middleware = RequestBodyLimitMiddleware(app, max_bytes=8)
        started = asyncio.Event()

        async def receive():
            started.set()
            await asyncio.Event().wait()
            return {"type": "http.request", "body": b""}

        async def send(event):
            pass

        task = asyncio.create_task(
            middleware(
                {"type": "http", "method": "GET", "path": "/healthz", "headers": []},
                receive,
                send,
            )
        )
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)
        assert middleware._health_probe_slots.acquire(blocking=False)
        assert middleware._health_probe_slots.acquire(blocking=False)
        middleware._health_probe_slots.release()
        middleware._health_probe_slots.release()

    asyncio.run(scenario())


def test_health_probe_start_failure_releases_capacity(monkeypatch):
    import src.api.middleware as module

    async def app(scope, receive, send):
        raise AssertionError("scheduler failure must not dispatch")

    middleware = RequestBodyLimitMiddleware(app, max_bytes=8)

    def fail_start(*args):
        args[0].close()
        raise RuntimeError("probe scheduler unavailable")

    monkeypatch.setattr(module.asyncio, "ensure_future", fail_start)

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(event):
        pass

    try:
        asyncio.run(
            middleware(
                {"type": "http", "method": "GET", "path": "/healthz", "headers": []},
                receive,
                send,
            )
        )
    except RuntimeError as exc:
        assert "probe scheduler unavailable" in str(exc)
    else:
        raise AssertionError("scheduler failure must propagate")
    assert middleware._health_probe_slots.acquire(blocking=False)
    assert middleware._health_probe_slots.acquire(blocking=False)
    middleware._health_probe_slots.release()
    middleware._health_probe_slots.release()
