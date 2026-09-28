from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for candidate in (
    REPO_ROOT,
    REPO_ROOT / "cli" / "sds_cli" / "src",
    REPO_ROOT / "packages" / "sds_core" / "src",
    REPO_ROOT / "scripts",
):
    candidate_str = str(candidate)
    if candidate.exists() and candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from sds_cli import __main__ as sds_cli_main  # noqa: E402

from scripts import catalog_build  # noqa: E402


def test_advertised_sds_cli_passthrough_targets_resolve():
    assert set(sds_cli_main.PASSTHROUGH_COMMANDS) == {
        "export-ngsi",
        "build-catalog",
        "edc-bundle",
        "semantics-bundle",
    }

    for command in sds_cli_main.PASSTHROUGH_COMMANDS.values():
        assert (REPO_ROOT / command.script_path).exists()
        module = importlib.import_module(command.module)
        assert callable(getattr(module, "main", None))


def test_sds_cli_passes_option_arguments_to_target(monkeypatch):
    observed_args: list[str] = []

    def fake_main() -> int:
        observed_args[:] = sys.argv[1:]
        return 0

    monkeypatch.setattr(sds_cli_main, "_load_main", lambda command: fake_main)

    exit_code = sds_cli_main.main(
        ["build-catalog", "--input", "register.csv", "--out", "catalog.jsonld"]
    )

    assert exit_code == 0
    assert observed_args == ["--input", "register.csv", "--out", "catalog.jsonld"]


def test_catalog_build_writes_fixture_backed_dcat_catalog(tmp_path):
    register = tmp_path / "register.csv"
    register.write_text(
        "\n".join(
            [
                "identifier,title,description,dimension,owner,purpose,region,policyId",
                "DS:1,Dataset One,Fixture dataset,E,SDS Team,reporting,EU,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    policy_registry = tmp_path / "policy_registry.json"
    policy_registry.write_text(
        json.dumps(
            {
                "denyByDefault": True,
                "defaultDenyPolicy": "policy-deny-all",
                "purposeVocabulary": {
                    "reporting": {"defaultPolicyId": "policy-reporting"}
                },
                "policies": [
                    {"uid": "policy-deny-all", "regions": []},
                    {"uid": "policy-reporting", "regions": ["EU"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "catalog.jsonld"

    exit_code = catalog_build.main(
        [
            "--input",
            str(register),
            "--policy-registry",
            str(policy_registry),
            "--base-url",
            "https://example.test/data-products",
            "--out",
            str(output),
        ]
    )

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["@type"] == "dcat:Catalog"
    assert len(payload["dcat:dataset"]) == 1

    dataset = payload["dcat:dataset"][0]
    assert dataset["dct:identifier"] == "DS:1"
    assert dataset["odrl:hasPolicy"]["@id"] == "urn:sds:policy:policy-reporting"
    assert (
        dataset["dcat:distribution"][0]["dcat:accessURL"]["@id"]
        == "https://example.test/data-products/asset-DS-1"
    )
