#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path


def bootstrap_repo() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    sds_core_src = repo_root / "packages" / "sds_core" / "src"

    if str(sds_core_src) not in sys.path:
        sys.path.insert(0, str(sds_core_src))

    os.chdir(repo_root)
    return repo_root


def main() -> int:
    bootstrap_repo()

    import pytest

    return pytest.main(["tests/contract", "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
