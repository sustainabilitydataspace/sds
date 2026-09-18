"""Settings should be robust to extra env vars in .env / runtime env."""

from __future__ import annotations

from src.config.settings import Settings


def test_settings_ignores_extra_env_vars(monkeypatch):
    monkeypatch.setenv("SDS_OFFLINE", "0")
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy")

    Settings()
