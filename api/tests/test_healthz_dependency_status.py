"""Extra /healthz branch coverage (dependency matrix)."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from src.api import main


@pytest.mark.asyncio
async def test_slow_health_dependency_does_not_block_other_requests(monkeypatch):
    def slow_probe():
        time.sleep(0.12)
        return 200, {"status": "healthy"}

    monkeypatch.setattr(main, "_dependency_health", slow_probe)
    request = SimpleNamespace(state=SimpleNamespace(request_id="probe"))
    started = asyncio.get_running_loop().time()
    probe = asyncio.create_task(main.healthz(request))
    await asyncio.sleep(0.01)
    elapsed = asyncio.get_running_loop().time() - started
    await probe
    assert elapsed < 0.08


@pytest.mark.asyncio
async def test_slow_readiness_dependency_does_not_block_event_loop(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def slow_readiness():
        entered.set()
        assert release.wait(timeout=0.4)
        return 200, {"status": "ready"}

    monkeypatch.setattr(main, "_readiness_status", slow_readiness)
    started = asyncio.get_running_loop().time()
    request = asyncio.create_task(main.readiness_check())
    try:
        await asyncio.sleep(0.01)
        assert asyncio.get_running_loop().time() - started < 0.15
        assert entered.is_set()
    finally:
        release.set()
        await request


@pytest.mark.asyncio
async def test_slow_metrics_readiness_dependency_does_not_block_event_loop(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def slow_readiness():
        entered.set()
        assert release.wait(timeout=0.4)
        return 200, {"status": "ready"}

    monkeypatch.setattr(main, "_readiness_status", slow_readiness)
    started = asyncio.get_running_loop().time()
    request = asyncio.create_task(main.metrics())
    try:
        await asyncio.sleep(0.01)
        assert asyncio.get_running_loop().time() - started < 0.15
        assert entered.is_set()
    finally:
        release.set()
        await request


@pytest.mark.asyncio
async def test_health_probe_saturation_rejects_and_releases_after_cancel(monkeypatch):
    entered = threading.Semaphore(0)
    release = threading.Event()

    def slow_probe():
        entered.release()
        assert release.wait(timeout=3)
        return 200, {"status": "healthy"}

    monkeypatch.setattr(main, "_dependency_health", slow_probe)
    request = SimpleNamespace(state=SimpleNamespace(request_id="probe"))
    first = asyncio.create_task(main.healthz(request))
    second = asyncio.create_task(main.healthz(request))
    try:
        for _ in range(2):
            assert await asyncio.to_thread(entered.acquire, True, 2)
        unavailable = await main.healthz(request)
        assert unavailable.status_code == 503
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await main.healthz(request)).status_code == 503
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)
    assert (await main.healthz(request)).status_code == 200


def test_healthz_unhealthy_when_database_required_and_ping_fails(client, monkeypatch):
    from src.config.settings import settings

    monkeypatch.setattr(settings, "require_database", True)

    import src.database.init_db as init_db_mod

    def _boom() -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(init_db_mod, "ping_db", _boom)

    response = client.get("/healthz")
    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "unhealthy"
    assert payload["dependencies"]["database"]["status"] == "unhealthy"


def test_healthz_reports_only_database_dependency(client, monkeypatch):
    from src.config.settings import settings

    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr("src.database.init_db.ping_db", lambda: None)

    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload["dependencies"]) == {"database"}
