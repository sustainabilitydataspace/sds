#!/usr/bin/env python3
"""Import a complete SDS package into SDS.

This neutral entrypoint delegates to the legacy importer so existing automation
keeps working while public docs can refer to the SDS package contract directly.
"""

from __future__ import annotations

from import_atomizer_sds_package import main as _legacy_main


def main(argv: list[str] | None = None) -> int:
    return _legacy_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
