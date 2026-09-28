"""Run the SDS mocked Terraform plans in an isolated disposable module copy."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

MODULE = Path(__file__).resolve().parent / "terraform"
REQUIRED_VERSION = "1.16.3"


def main() -> None:
    binary = os.environ.get("SDS_TERRAFORM_BIN", "terraform")
    version = subprocess.run(
        [binary, "version", "-json"], check=True, capture_output=True, text=True,
        timeout=30,
    )
    if json.loads(version.stdout).get("terraform_version") != REQUIRED_VERSION:
        raise RuntimeError(f"SDS AWS mock gate requires Terraform {REQUIRED_VERSION}")
    cases = (MODULE / "activation.tftest.hcl").read_text(encoding="utf-8")
    if 'mock_provider "aws"' not in cases:
        raise RuntimeError("Terraform tests must use a mock AWS provider")
    runs = re.findall(r'run "[^"]+"\s*\{\s*command\s*=\s*(\w+)', cases)
    declarations = re.findall(r'run\s+"[^"]+"\s*\{', cases)
    if not runs or len(runs) != len(declarations) or any(command != "plan" for command in runs):
        raise RuntimeError("Every Terraform mock test must explicitly plan only")
    env = {**os.environ, "AWS_EC2_METADATA_DISABLED": "true", "TF_IN_AUTOMATION": "1", "TF_INPUT": "0"}
    with tempfile.TemporaryDirectory(prefix="sds-terraform-offline-") as workspace:
        destination = Path(workspace) / "terraform"
        shutil.copytree(MODULE, destination, ignore=shutil.ignore_patterns(".terraform"))
        for args in (
            ("fmt", "-check", "-recursive"),
            ("init", "-backend=false", "-input=false", "-lockfile=readonly"),
            ("validate",),
            ("test", "-filter=activation.tftest.hcl", "-no-color"),
        ):
            subprocess.run(
                [binary, f"-chdir={destination}", *args], env=env, check=True,
                timeout=300,
            )


if __name__ == "__main__":
    main()
