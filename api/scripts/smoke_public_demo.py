#!/usr/bin/env python3
"""Probe the public Docker profile without relying on curl or shell-specific tools."""

from __future__ import annotations

import argparse
import json
import time
from urllib.error import URLError
from urllib.request import urlopen


def fetch(url: str) -> dict:
    with urlopen(url, timeout=5) as response:  # noqa: S310 -- localhost fixed by caller
        if response.status != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    deadline = time.monotonic() + args.timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            health = fetch(f"{args.base_url}/healthz")
            ready = fetch(f"{args.base_url}/ready")
            if health.get("status") == "healthy" and ready.get("status") == "ready":
                print("Public demo smoke: PASS")
                return 0
            last_error = RuntimeError(f"unexpected health/readiness payload: {health}, {ready}")
        except (URLError, OSError, ValueError, RuntimeError) as exc:
            last_error = exc
        time.sleep(2)
    raise SystemExit(f"Public demo smoke: FAIL after {args.timeout:.0f}s: {last_error}")


if __name__ == "__main__":
    raise SystemExit(main())
