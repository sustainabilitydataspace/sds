from __future__ import annotations

import argparse
import importlib
import sys
from dataclasses import dataclass
from typing import Callable, List


@dataclass(frozen=True)
class PassthroughCommand:
    name: str
    help: str
    module: str
    script_path: str


PASSTHROUGH_COMMANDS: dict[str, PassthroughCommand] = {
    "export-ngsi": PassthroughCommand(
        name="export-ngsi",
        help="Export NGSI-LD entities from E1 register",
        module="scripts.e3_export_ngsi_ld",
        script_path="scripts/e3_export_ngsi_ld.py",
    ),
    "build-catalog": PassthroughCommand(
        name="build-catalog",
        help="Build DCAT-AP catalog from E1 register",
        module="scripts.catalog_build",
        script_path="scripts/catalog_build.py",
    ),
    "edc-bundle": PassthroughCommand(
        name="edc-bundle",
        help="Generate EDC assets/policies/contracts bundle from E1 register",
        module="scripts.edc_bundle_from_register",
        script_path="scripts/edc_bundle_from_register.py",
    ),
    "semantics-bundle": PassthroughCommand(
        name="semantics-bundle",
        help="Build the SDS semantics bundle (ZIP + manifest)",
        module="scripts.semantics_bundle_build",
        script_path="scripts/semantics_bundle_build.py",
    ),
}


def _load_main(command: PassthroughCommand) -> Callable[[], int]:
    module = importlib.import_module(command.module)
    main_fn = getattr(module, "main", None)
    if not callable(main_fn):
        raise RuntimeError(f"{command.module} does not expose a callable main()")
    return main_fn


def _run_script(main_fn: Callable[[], int], passthrough_args: List[str]) -> int:
    old_argv = sys.argv
    sys.argv = [old_argv[0], *passthrough_args]
    try:
        return int(main_fn() or 0)
    finally:
        sys.argv = old_argv


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sds-cli", description="SDS CLI (thin wrappers)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for command in PASSTHROUGH_COMMANDS.values():
        parser = sub.add_parser(command.name, help=command.help)
        parser.epilog = (
            f"Arguments after '{command.name}' are passed through to "
            f"{command.script_path}."
        )

    args, passthrough_args = ap.parse_known_args(argv)

    command = PASSTHROUGH_COMMANDS.get(args.cmd)
    if command is None:
        raise AssertionError(f"Unknown command: {args.cmd}")
    return _run_script(_load_main(command), passthrough_args)


if __name__ == "__main__":
    raise SystemExit(main())
