from __future__ import annotations

import asyncio

from starlette.responses import JSONResponse


def _scope(*, content_length: int | None = None) -> dict:
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/upload",
        "raw_path": b"/upload",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 10000),
        "server": ("testserver", 80),
        "root_path": "",
    }


def _run_request(*, chunks: list[bytes], max_bytes: int, content_length=None):
    from src.api.middleware import RequestBodyLimitMiddleware

    called = False

    async def app(scope, receive, send):
        nonlocal called
        body = bytearray()
        while True:
            message = await receive()
            body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        called = True
        response = JSONResponse({"size": len(body)})
        await response(scope, receive, send)

    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]
    sent = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    asyncio.run(
        RequestBodyLimitMiddleware(app, max_bytes=max_bytes)(
            _scope(content_length=content_length), receive, send
        )
    )
    status = next(
        message["status"]
        for message in sent
        if message["type"] == "http.response.start"
    )
    return status, called


def test_streaming_body_over_limit_is_rejected_before_handler_completion():
    status, called = _run_request(chunks=[b"12345678", b"9"], max_bytes=8)

    assert status == 413
    assert called is False


def test_content_length_over_limit_is_rejected_without_receiving_body():
    status, called = _run_request(chunks=[b"not-read"], max_bytes=8, content_length=9)

    assert status == 413
    assert called is False


def test_body_at_exact_limit_is_accepted():
    status, called = _run_request(chunks=[b"1234", b"5678"], max_bytes=8)

    assert status == 200
    assert called is True


def test_body_length_must_match_declared_content_length():
    for declared, actual in ((1, b"abc"), (3, b"a")):
        status, called = _run_request(
            chunks=[actual], max_bytes=8, content_length=declared
        )
        assert status == 400
        assert called is False


def test_ambiguous_content_length_is_rejected_before_body_or_handler():
    from src.api.middleware import RequestBodyLimitMiddleware

    for values in ((b"2", b"2"), (b"+2",), (b" 2",), (b"2", b"3")):
        scope = _scope()
        scope["headers"] = [(b"content-length", value) for value in values]
        sent = []

        async def app(_scope, _receive, _send):
            raise AssertionError("ambiguous length must not reach handler")

        async def receive():
            raise AssertionError("ambiguous length must not read body")

        async def send(message):
            sent.append(message)

        asyncio.run(RequestBodyLimitMiddleware(app, max_bytes=8)(scope, receive, send))
        assert (
            next(
                message["status"]
                for message in sent
                if message["type"] == "http.response.start"
            )
            == 400
        )


def test_transfer_encoding_with_content_length_is_rejected_before_handler():
    from src.api.middleware import RequestBodyLimitMiddleware

    scope = _scope(content_length=2)
    scope["headers"].append((b"transfer-encoding", b"chunked"))
    sent = []

    async def app(_scope, _receive, _send):
        raise AssertionError("conflicting framing must not reach handler")

    async def receive():
        raise AssertionError("conflicting framing must not read body")

    async def send(message):
        sent.append(message)

    asyncio.run(RequestBodyLimitMiddleware(app, max_bytes=8)(scope, receive, send))
    assert (
        next(
            message["status"]
            for message in sent
            if message["type"] == "http.response.start"
        )
        == 400
    )


def test_late_body_over_limit_never_commits_an_early_success_response():
    from src.api.middleware import RequestBodyLimitMiddleware

    sent = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await receive()
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b"123456789", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(RequestBodyLimitMiddleware(app, max_bytes=8)(_scope(), receive, send))
    assert [
        message["status"]
        for message in sent
        if message["type"] == "http.response.start"
    ] == [413]


def test_slow_body_read_times_out_and_releases_admission_slot():
    from src.api.middleware import RequestBodyLimitMiddleware

    async def app(scope, receive, send):
        await JSONResponse({"ok": True})(scope, receive, send)

    middleware = RequestBodyLimitMiddleware(
        app, max_bytes=8, max_concurrent_bodies=1, read_timeout_seconds=0.01
    )

    async def scenario():
        sent = []

        async def slow_receive():
            await asyncio.Event().wait()

        async def send(message):
            sent.append(message)

        await middleware(_scope(), slow_receive, send)
        assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [
            408
        ]

        sent.clear()

        async def complete_receive():
            return {"type": "http.request", "body": b"a", "more_body": False}

        await middleware(_scope(), complete_receive, send)
        assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [
            200
        ]

    asyncio.run(scenario())


def test_cancel_suppressing_receive_cannot_make_expired_body_succeed():
    from src.api.middleware import RequestBodyLimitMiddleware

    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True
        await JSONResponse({"ok": True})(scope, receive, send)

    async def receive():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.1)
            return {"type": "http.request", "body": b"a", "more_body": False}
        raise AssertionError("receive wait unexpectedly completed")

    async def scenario():
        sent = []

        async def send(message):
            sent.append(message)

        start = asyncio.get_running_loop().time()
        await RequestBodyLimitMiddleware(
            app, max_bytes=8, max_concurrent_bodies=1, read_timeout_seconds=0.005
        )(_scope(), receive, send)
        elapsed = asyncio.get_running_loop().time() - start
        assert elapsed < 0.06
        assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [
            408
        ]

    asyncio.run(scenario())
    assert called is False


def test_concurrent_buffered_body_budget_rejects_excess_without_reading():
    from src.api.middleware import RequestBodyLimitMiddleware

    entered = asyncio.Event()
    release = asyncio.Event()

    async def app(scope, receive, send):
        entered.set()
        await release.wait()
        await JSONResponse({"ok": True})(scope, receive, send)

    middleware = RequestBodyLimitMiddleware(
        app, max_bytes=8, max_concurrent_bodies=1, read_timeout_seconds=1
    )

    async def scenario():
        first_sent, second_sent = [], []

        async def first_receive():
            return {"type": "http.request", "body": b"12345678", "more_body": False}

        async def forbidden_receive():
            raise AssertionError("saturated request must not read another body")

        async def first_send(message):
            first_sent.append(message)

        async def second_send(message):
            second_sent.append(message)

        first = asyncio.create_task(
            middleware(_scope(content_length=8), first_receive, first_send)
        )
        await entered.wait()
        await middleware(_scope(content_length=1), forbidden_receive, second_send)
        assert [
            m["status"] for m in second_sent if m["type"] == "http.response.start"
        ] == [503]
        release.set()
        await first
        assert [
            m["status"] for m in first_sent if m["type"] == "http.response.start"
        ] == [200]

    asyncio.run(scenario())


def test_finished_body_read_does_not_hold_upload_admission_during_handler():
    from src.api.middleware import RequestBodyLimitMiddleware

    entered = asyncio.Event()
    release = asyncio.Event()

    async def app(scope, receive, send):
        if scope["path"] == "/upload":
            entered.set()
            await release.wait()
        await JSONResponse({"ok": True})(scope, receive, send)

    middleware = RequestBodyLimitMiddleware(
        app, max_bytes=8, max_concurrent_bodies=1, read_timeout_seconds=0.01
    )

    async def scenario():
        first_sent, second_sent = [], []

        async def upload_receive():
            return {"type": "http.request", "body": b"a", "more_body": False}

        async def empty_receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def first_send(message):
            first_sent.append(message)

        async def second_send(message):
            second_sent.append(message)

        first = asyncio.create_task(middleware(_scope(), upload_receive, first_send))
        await entered.wait()
        empty = _scope()
        empty["method"] = "GET"
        empty["path"] = "/healthz"
        await middleware(empty, empty_receive, second_send)
        assert [
            m["status"] for m in second_sent if m["type"] == "http.response.start"
        ] == [200]
        release.set()
        await first
        assert [
            m["status"] for m in first_sent if m["type"] == "http.response.start"
        ] == [200]

    asyncio.run(scenario())


def test_streaming_body_is_rejected_before_retained_plus_inflight_exceeds_budget():
    from src.api.middleware import RequestBodyLimitMiddleware

    entered = asyncio.Event()
    release = asyncio.Event()

    async def app(scope, receive, send):
        entered.set()
        await release.wait()
        await JSONResponse({"ok": True})(scope, receive, send)

    middleware = RequestBodyLimitMiddleware(
        app, max_bytes=8, max_concurrent_bodies=1, read_timeout_seconds=1
    )

    async def scenario():
        first_sent, second_sent = [], []

        async def first_receive():
            return {"type": "http.request", "body": b"12345678", "more_body": False}

        second_reads = 0

        async def second_receive():
            nonlocal second_reads
            second_reads += 1
            if second_reads > 1:
                raise AssertionError(
                    "another chunk read after aggregate budget exceeded"
                )
            return {"type": "http.request", "body": b"1234567", "more_body": True}

        async def first_send(message):
            first_sent.append(message)

        async def second_send(message):
            second_sent.append(message)

        first = asyncio.create_task(
            middleware(_scope(content_length=8), first_receive, first_send)
        )
        await entered.wait()
        await middleware(_scope(), second_receive, second_send)
        assert second_reads == 1
        assert [
            m["status"] for m in second_sent if m["type"] == "http.response.start"
        ] == [503]
        release.set()
        await first
        assert [
            m["status"] for m in first_sent if m["type"] == "http.response.start"
        ] == [200]

    asyncio.run(scenario())


def test_unending_empty_body_chunks_are_bounded_before_handler():
    from src.api.middleware import RequestBodyLimitMiddleware

    chunks = 0
    sent = []

    async def app(_scope, _receive, _send):
        raise AssertionError("empty-chunk flood must not reach handler")

    async def receive():
        nonlocal chunks
        chunks += 1
        return {"type": "http.request", "body": b"", "more_body": True}

    async def send(message):
        sent.append(message)

    asyncio.run(RequestBodyLimitMiddleware(app, max_bytes=8)(_scope(), receive, send))
    assert chunks == 1025
    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [413]


def test_bodyless_healthz_survives_eight_stalled_uploads_without_body_bypass():
    from src.api.middleware import RequestBodyLimitMiddleware

    release = asyncio.Event()
    all_started = asyncio.Event()
    started = 0

    async def app(scope, _receive, send):
        await JSONResponse({"ok": True})(scope, _receive, send)

    middleware = RequestBodyLimitMiddleware(
        app, max_bytes=8, max_concurrent_bodies=8, read_timeout_seconds=2
    )

    async def stalled_receive():
        nonlocal started
        started += 1
        if started == 8:
            all_started.set()
        await release.wait()
        return {"type": "http.request", "body": b"", "more_body": False}

    async def no_body_receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def scenario():
        uploads = []

        async def upload_send(_message):
            pass

        for _ in range(8):
            uploads.append(
                asyncio.create_task(middleware(_scope(), stalled_receive, upload_send))
            )
        try:
            await asyncio.wait_for(all_started.wait(), timeout=1)
            health_scope = _scope()
            health_scope.update(method="GET", path="/healthz")
            health_sent = []

            async def health_send(message):
                health_sent.append(message)

            await middleware(health_scope, no_body_receive, health_send)
            assert [
                m["status"] for m in health_sent if m["type"] == "http.response.start"
            ] == [200]

            # A health path with a declared body still uses normal admission.
            with_body = _scope(content_length=1)
            with_body.update(method="GET", path="/healthz")
            body_sent = []

            async def body_send(message):
                body_sent.append(message)

            await middleware(with_body, no_body_receive, body_send)
            assert [
                m["status"] for m in body_sent if m["type"] == "http.response.start"
            ] == [503]
        finally:
            release.set()
            await asyncio.gather(*uploads)

    asyncio.run(scenario())
