"""The Makefile start path must preserve the socket peer for app-level proxy trust."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("extra", [[], ["--reload"]])
def test_ensure_runtime_start_disables_uvicorn_proxy_header_rewrite(
    monkeypatch: pytest.MonkeyPatch, extra: list[str]
) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "ensure_runtime.py"
    namespace = runpy.run_path(str(script))
    globals_ = namespace["main"].__globals__
    monkeypatch.setitem(globals_, "ensure_runtime", lambda: Path(sys.executable))
    calls: list[list[str]] = []
    monkeypatch.setattr(
        globals_["subprocess"],
        "call",
        lambda command, **_kwargs: calls.append(command) or 0,
    )
    monkeypatch.setattr(sys, "argv", [str(script), "--start", *extra])

    assert namespace["main"]() == 0
    assert len(calls) == 1
    assert "--no-proxy-headers" in calls[0]
