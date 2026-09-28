from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "e6_check_governance.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("e6_check_governance", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


e6_check_governance = _load_module()


def _result_map(report):
    return {result.name: result for result in report.results}


def test_checker_default_paths_are_repo_relative_from_foreign_cwd(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [str(MODULE_PATH), "--json"])
    e6_check_governance.main()
    results = {
        entry["name"]: entry for entry in json.loads(capsys.readouterr().out)["results"]
    }
    assert results["policy_registry_exists"]["passed"]
    assert results["edc_policies_exists"]["passed"]


def _p256_coordinate(value: int) -> str:
    return (
        base64.urlsafe_b64encode(value.to_bytes(32, "big")).decode("ascii").rstrip("=")
    )


def test_schema_dependency_failure_is_an_error(tmp_path, monkeypatch):
    document_path = tmp_path / "registry.json"
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    monkeypatch.setattr(e6_check_governance, "jsonschema", None)
    report = e6_check_governance.ValidationReport()

    assert (
        e6_check_governance._load_schema(
            document_path, {"$schema": "./schema.json"}, report, "registry"
        )
        is None
    )

    result = _result_map(report)["registry_schema_validated"]
    assert not result.passed
    assert result.severity == "error"
    assert "Required dependency jsonschema is unavailable" in result.message
    assert not report.passed
    assert report.error_count == 1
    assert report.warning_count == 0


@pytest.mark.parametrize("malformed", [["not-a-policy"], {"uid": "wrong"}])
def test_registry_rejects_malformed_policy_collection_without_crashing(
    tmp_path, malformed
):
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "$schema": "./missing.json",
                "denyByDefault": True,
                "defaultDenyPolicy": "policy-deny-all",
                "policies": malformed,
            }
        ),
        encoding="utf-8",
    )
    report = e6_check_governance.ValidationReport()
    assert e6_check_governance.check_policy_registry(path, report) is None
    assert not _result_map(report)["policy_registry_structure"].passed


def test_markdown_ref_check_ignores_fenced_commands_and_accepts_globs(tmp_path):
    repo_root = tmp_path / "repo"
    doc_path = repo_root / "docs" / "governance" / "operating_model_raci.md"
    trust_doc = repo_root / "docs" / "governance" / "trust_issuers.md"
    edc_dir = repo_root / "configs" / "edc"

    trust_doc.parent.mkdir(parents=True, exist_ok=True)
    edc_dir.mkdir(parents=True, exist_ok=True)
    trust_doc.write_text("# Trust Issuers\n", encoding="utf-8")
    (edc_dir / "assets.json").write_text("[]", encoding="utf-8")

    doc_path.write_text(
        "\n".join(
            [
                "# Operating Model",
                "- Trust Registry: `docs/governance/trust_issuers.md`",
                "- EDC Bundle: `configs/edc/*`",
                "",
                "```powershell",
                "python scripts\\e6_check_governance.py",
                "```",
                "",
                "- Historical source: `\\\\wsl.localhost\\Ubuntu-24.04\\home\\alice\\dataspaces`",
            ]
        ),
        encoding="utf-8",
    )

    report = e6_check_governance.ValidationReport()
    e6_check_governance._check_markdown_refs(
        doc_path,
        report,
        check_prefix="operating_model_raci",
        repo_root=repo_root,
    )

    results = _result_map(report)
    assert results["operating_model_raci_exists"].passed
    assert results["operating_model_raci_local_refs"].passed


def test_trust_issuer_check_flags_schema_expiry_and_placeholder_keys(tmp_path):
    repo_root = tmp_path / "repo"
    issuers_stem = repo_root / "docs" / "governance" / "trust_issuers"
    issuers_stem.parent.mkdir(parents=True, exist_ok=True)

    (issuers_stem.with_suffix(".json")).write_text(
        json.dumps(
            {
                "$schema": "./trust_issuers_schema.json",
                "version": "2026-01-27",
                "statusListStandard": "BitstringStatusList2025",
                "issuers": [
                    {
                        "did": "did:web:example.org:issuer:test",
                        "name": "Test Issuer",
                        "status": "active",
                        "validFrom": "2025-01-01",
                        "validUntil": "2025-12-31",
                        "statusListCredential": "https://example.org/status/1",
                        "statusListType": "BitstringStatusListCredential",
                        "publicKeyJwk": {
                            "kty": "EC",
                            "x": "placeholder-x",
                            "y": "placeholder-y",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (issuers_stem.with_suffix(".md")).write_text(
        "# Trust Issuers v2026-01-27\n",
        encoding="utf-8",
    )

    report = e6_check_governance.ValidationReport()
    e6_check_governance.check_trust_issuers(
        issuers_stem,
        report,
        repo_root=repo_root,
        current_date=date(2026, 4, 13),
    )

    results = _result_map(report)
    assert not results["trust_issuers_schema_exists"].passed
    assert not results["trust_issuers_validity_window"].passed
    assert not results["trust_issuers_placeholder_keys"].passed


def test_trust_enforcement_hold_requires_no_issuer_records(tmp_path):
    repo_root = tmp_path / "repo"
    issuers_stem = repo_root / "docs" / "governance" / "trust_issuers"
    issuers_stem.parent.mkdir(parents=True, exist_ok=True)
    (issuers_stem.with_suffix(".json")).write_text(
        json.dumps(
            {
                "$schema": "./trust_issuers_schema.json",
                "version": "2026-09-12",
                "statusListStandard": "BitstringStatusList2025",
                "runtimeEnforcement": {
                    "status": "not_activated",
                    "reason": "No verified custody evidence",
                    "activationPrerequisite": "Verified issuer evidence and runtime verifier",
                },
                "issuers": [],
            }
        ),
        encoding="utf-8",
    )
    (issuers_stem.with_suffix(".md")).write_text(
        "# Trust enforcement status v2026-09-12\n", encoding="utf-8"
    )

    report = e6_check_governance.ValidationReport()
    e6_check_governance.check_trust_issuers(
        issuers_stem,
        report,
        repo_root=repo_root,
        current_date=date(2026, 9, 12),
    )

    results = _result_map(report)
    assert results["trust_issuers_runtime_enforcement_invariant"].passed
    assert results["trust_issuers_public_key_jwk_usable"].passed


def test_trust_enforcement_hold_rejects_published_issuer_records(tmp_path):
    repo_root = tmp_path / "repo"
    issuers_stem = repo_root / "docs" / "governance" / "trust_issuers"
    issuers_stem.parent.mkdir(parents=True, exist_ok=True)
    (issuers_stem.with_suffix(".json")).write_text(
        json.dumps(
            {
                "$schema": "./trust_issuers_schema.json",
                "version": "2026-09-12",
                "statusListStandard": "BitstringStatusList2025",
                "runtimeEnforcement": {
                    "status": "not_activated",
                    "reason": "No verified custody evidence",
                    "activationPrerequisite": "Verified issuer evidence and runtime verifier",
                },
                "issuers": [{"did": "did:web:example.org:issuer:forbidden"}],
            }
        ),
        encoding="utf-8",
    )
    (issuers_stem.with_suffix(".md")).write_text(
        "# Trust enforcement status v2026-09-12\n", encoding="utf-8"
    )

    report = e6_check_governance.ValidationReport()
    e6_check_governance.check_trust_issuers(
        issuers_stem,
        report,
        repo_root=repo_root,
        current_date=date(2026, 9, 12),
    )

    results = _result_map(report)
    assert not results["trust_issuers_runtime_enforcement_invariant"].passed


def test_trust_enforcement_hold_rejects_non_list_issuers(tmp_path):
    repo_root = tmp_path / "repo"
    issuers_stem = repo_root / "docs" / "governance" / "trust_issuers"
    issuers_stem.parent.mkdir(parents=True, exist_ok=True)
    (issuers_stem.with_suffix(".json")).write_text(
        json.dumps(
            {
                "$schema": "./trust_issuers_schema.json",
                "version": "2026-09-12",
                "statusListStandard": "BitstringStatusList2025",
                "runtimeEnforcement": {
                    "status": "not_activated",
                    "reason": "No verified custody evidence",
                    "activationPrerequisite": "Verified issuer evidence and runtime verifier",
                },
                "issuers": {"hiddenIssuer": "not-a-list"},
            }
        ),
        encoding="utf-8",
    )
    (issuers_stem.with_suffix(".md")).write_text(
        "# Trust enforcement status v2026-09-12\n", encoding="utf-8"
    )

    report = e6_check_governance.ValidationReport()
    e6_check_governance.check_trust_issuers(
        issuers_stem,
        report,
        repo_root=repo_root,
        current_date=date(2026, 9, 12),
    )

    assert not _result_map(report)["trust_issuers_runtime_enforcement_invariant"].passed


def test_trust_hold_schema_rejects_unrecognized_trust_material():
    schema_path = REPO_ROOT / "docs" / "governance" / "trust_issuers_schema.json"
    registry_path = REPO_ROOT / "docs" / "governance" / "trust_issuers.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    assert not list(
        e6_check_governance.jsonschema.Draft202012Validator(schema).iter_errors(
            registry
        )
    )

    invalid_registry = {**registry, "trustAnchors": ["unverified"]}
    assert list(
        e6_check_governance.jsonschema.Draft202012Validator(schema).iter_errors(
            invalid_registry
        )
    )

    for container, field_name in (
        ("rotationPolicy", "publicKeyJwk"),
        ("verificationRequirements", "trustAnchors"),
    ):
        invalid_registry = json.loads(json.dumps(registry))
        invalid_registry[container][field_name] = {"did": "did:web:forbidden"}
        assert list(
            e6_check_governance.jsonschema.Draft202012Validator(schema).iter_errors(
                invalid_registry
            )
        )


def test_trust_hold_checker_rejects_nested_trust_material(tmp_path):
    repo_root = tmp_path / "repo"
    issuers_stem = repo_root / "docs" / "governance" / "trust_issuers"
    issuers_stem.parent.mkdir(parents=True, exist_ok=True)
    (issuers_stem.with_suffix(".json")).write_text(
        json.dumps(
            {
                "$schema": "./trust_issuers_schema.json",
                "version": "2026-09-12",
                "statusListStandard": "BitstringStatusList2025",
                "runtimeEnforcement": {
                    "status": "not_activated",
                    "reason": "No verified custody evidence",
                    "activationPrerequisite": "Verified issuer evidence and runtime verifier",
                },
                "issuers": [],
                "rotationPolicy": {"nested": {"publicKeyJwk": {"kty": "EC"}}},
                "verificationRequirements": {
                    "nested": {"trustAnchors": [{"did": "did:web:forbidden"}]}
                },
            }
        ),
        encoding="utf-8",
    )
    (issuers_stem.with_suffix(".md")).write_text(
        "# Trust enforcement status v2026-09-12\n", encoding="utf-8"
    )

    report = e6_check_governance.ValidationReport()
    e6_check_governance.check_trust_issuers(
        issuers_stem,
        report,
        repo_root=repo_root,
        current_date=date(2026, 9, 12),
    )

    assert not _result_map(report)["trust_issuers_no_hidden_trust_material"].passed


def test_trust_issuer_check_rejects_unusable_p256_public_jwk(tmp_path):
    repo_root = tmp_path / "repo"
    issuers_stem = repo_root / "docs" / "governance" / "trust_issuers"
    issuers_stem.parent.mkdir(parents=True, exist_ok=True)

    generator_x = int(
        "6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296",
        16,
    )
    generator_y = int(
        "4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5",
        16,
    )

    (issuers_stem.with_suffix(".json")).write_text(
        json.dumps(
            {
                "$schema": "./trust_issuers_schema.json",
                "version": "2026-01-27",
                "statusListStandard": "BitstringStatusList2025",
                "issuers": [
                    {
                        "did": "did:web:example.org:issuer:valid",
                        "name": "Valid Test Issuer",
                        "status": "active",
                        "validFrom": "2026-01-01",
                        "validUntil": "2026-12-31",
                        "statusListCredential": "https://example.org/status/valid",
                        "statusListType": "BitstringStatusListCredential",
                        "publicKeyJwk": {
                            "kty": "EC",
                            "crv": "P-256",
                            "x": _p256_coordinate(generator_x),
                            "y": _p256_coordinate(generator_y),
                        },
                    },
                    {
                        "did": "did:web:example.org:issuer:off-curve",
                        "name": "Off Curve Test Issuer",
                        "status": "active",
                        "validFrom": "2026-01-01",
                        "validUntil": "2026-12-31",
                        "statusListCredential": "https://example.org/status/off-curve",
                        "statusListType": "BitstringStatusListCredential",
                        "publicKeyJwk": {
                            "kty": "EC",
                            "crv": "P-256",
                            "x": _p256_coordinate(generator_x),
                            "y": _p256_coordinate(generator_y + 1),
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (issuers_stem.with_suffix(".md")).write_text(
        "# Trust Issuers v2026-01-27\n",
        encoding="utf-8",
    )

    report = e6_check_governance.ValidationReport()
    e6_check_governance.check_trust_issuers(
        issuers_stem,
        report,
        repo_root=repo_root,
        current_date=date(2026, 4, 13),
    )

    results = _result_map(report)
    assert results["trust_issuers_placeholder_keys"].passed
    assert not results["trust_issuers_public_key_jwk_usable"].passed
    assert "did:web:example.org:issuer:off-curve" in (
        results["trust_issuers_public_key_jwk_usable"].message
    )
    assert (
        "unusable P-256 JWK" in results["trust_issuers_public_key_jwk_usable"].message
    )


def test_edc_bundle_check_flags_register_count_drift(tmp_path):
    repo_root = tmp_path / "repo"
    edc_dir = repo_root / "configs" / "edc"
    register_meta = repo_root / "data" / "processed" / "e1_dataset_register.meta.json"
    register_csv = repo_root / "data" / "processed" / "e1_dataset_register.csv"

    edc_dir.mkdir(parents=True, exist_ok=True)
    register_meta.parent.mkdir(parents=True, exist_ok=True)

    (edc_dir / "policies.json").write_text(
        json.dumps(
            [
                {
                    "uid": "policy-reporting-365d-retention",
                    "policy": {
                        "permissions": [
                            {
                                "action": "use",
                                "constraints": [
                                    {
                                        "leftOperand": "purpose",
                                        "operator": "eq",
                                        "rightOperand": "reporting",
                                    }
                                ],
                            }
                        ],
                        "prohibitions": [],
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    (edc_dir / "assets.json").write_text(
        json.dumps([{"id": "a1"}, {"id": "a2"}]), encoding="utf-8"
    )
    (edc_dir / "contract-definitions.json").write_text(
        json.dumps([{"id": "c1"}, {"id": "c2"}]),
        encoding="utf-8",
    )
    register_meta.write_text(json.dumps({"count": 3}), encoding="utf-8")
    register_csv.write_text("id\nrow-1\nrow-2\nrow-3\n", encoding="utf-8")

    report = e6_check_governance.ValidationReport()
    e6_check_governance.check_edc_bundle(
        edc_dir,
        report,
        register_meta_path=register_meta,
        register_csv_path=register_csv,
    )

    results = _result_map(report)
    assert results["edc_register_count_available"].passed
    assert not results["edc_assets_match_register_count"].passed
    assert not results["edc_contract_definitions_match_register_count"].passed


@pytest.mark.parametrize("malformed", [{"unexpected": True}, ["not-a-policy"]])
def test_edc_bundle_rejects_malformed_policy_structure(tmp_path, malformed):
    edc_dir = tmp_path / "edc"
    edc_dir.mkdir()
    (edc_dir / "policies.json").write_text(json.dumps(malformed), encoding="utf-8")
    for name in ("assets.json", "contract-definitions.json"):
        (edc_dir / name).write_text("[]", encoding="utf-8")
    report = e6_check_governance.ValidationReport()
    e6_check_governance.check_edc_bundle(
        edc_dir,
        report,
        register_meta_path=tmp_path / "register.meta.json",
        register_csv_path=tmp_path / "register.csv",
    )
    assert not _result_map(report)["edc_bundle_structure"].passed


@pytest.mark.parametrize(
    "constraints",
    [[], [{}], [{"leftExpression": "purpose", "operator": "EQ"}]],
)
def test_edc_permission_without_constraints_fails_even_with_prohibitions(
    tmp_path, constraints
):
    edc_dir = tmp_path / "edc"
    edc_dir.mkdir()
    (edc_dir / "policies.json").write_text(
        json.dumps(
            [
                {
                    "uid": "policy-reporting-only",
                    "policy": {
                        "permissions": [{"action": "USE", "constraints": constraints}],
                        "prohibitions": [{"action": "ANALYZE", "constraints": []}],
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    for name in ("assets.json", "contract-definitions.json"):
        (edc_dir / name).write_text("[]", encoding="utf-8")
    report = e6_check_governance.ValidationReport()
    e6_check_governance.check_edc_bundle(
        edc_dir,
        report,
        register_meta_path=tmp_path / "register.meta.json",
        register_csv_path=tmp_path / "register.csv",
    )
    assert not _result_map(report)["edc_no_allow_all"].passed


@pytest.mark.parametrize(
    "constraints",
    [[], [{}], [{"leftOperand": "purpose", "operator": "eq"}]],
)
def test_registry_permission_without_constraint_fails_even_with_prohibition(
    tmp_path, constraints
):
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "$schema": "./missing.json",
                "denyByDefault": True,
                "defaultDenyPolicy": "policy-deny-all",
                "policies": [
                    {
                        "uid": "policy-deny-all",
                        "status": "active",
                        "legalBasis": "test",
                        "odrl": {
                            "permission": [
                                {"action": "use", "constraint": constraints}
                            ],
                            "prohibition": [{"action": "analyze"}],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report = e6_check_governance.ValidationReport()
    e6_check_governance.check_policy_registry(path, report)
    assert not _result_map(report)["no_allow_all_policies"].passed


@pytest.mark.parametrize(
    "case,check",
    [
        ("missing_policy", "edc_references_resolve"),
        ("missing_asset", "edc_references_resolve"),
        ("duplicate_policy", "edc_unique_ids"),
        ("duplicate_asset", "edc_unique_ids"),
        ("duplicate_contract", "edc_unique_ids"),
    ],
)
def test_edc_bundle_rejects_broken_references_and_duplicate_ids(tmp_path, case, check):
    edc_dir = tmp_path / "edc"
    edc_dir.mkdir()
    policies = [
        {
            "uid": "policy-a",
            "policy": {"permissions": [], "prohibitions": []},
        }
    ]
    assets = [{"asset": {"properties": {"asset:prop:id": "asset-1"}}}]
    contracts = [
        {
            "id": "contract-1",
            "accessPolicyId": "policy-a",
            "contractPolicyId": "policy-a",
            "additionalPolicies": [],
            "assetsSelector": [
                {
                    "operandLeft": "asset:prop:id",
                    "operator": "=",
                    "operandRight": "asset-1",
                }
            ],
        }
    ]
    if case == "missing_policy":
        contracts[0]["additionalPolicies"] = ["policy-not-found"]
    elif case == "missing_asset":
        contracts[0]["assetsSelector"][0]["operandRight"] = "missing"
    elif case == "duplicate_policy":
        policies.append(policies[0].copy())
    elif case == "duplicate_asset":
        assets.append(assets[0].copy())
        contracts.append({**contracts[0], "id": "contract-2"})
    elif case == "duplicate_contract":
        contracts.append(contracts[0].copy())
        assets.append({"asset": {"properties": {"asset:prop:id": "asset-2"}}})
    for name, contents in (
        ("policies.json", policies),
        ("assets.json", assets),
        ("contract-definitions.json", contracts),
    ):
        (edc_dir / name).write_text(json.dumps(contents), encoding="utf-8")
    report = e6_check_governance.ValidationReport()
    e6_check_governance.check_edc_bundle(
        edc_dir,
        report,
        register_meta_path=tmp_path / "missing.json",
        register_csv_path=tmp_path / "missing.csv",
    )
    assert not _result_map(report)[check].passed


def test_json_reader_and_edc_check_reject_oversized_artifact(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    edc_dir = repo_root / "configs" / "edc"
    edc_dir.mkdir(parents=True)
    for filename in ("policies.json", "assets.json", "contract-definitions.json"):
        (edc_dir / filename).write_text("[{}]", encoding="utf-8")
    (edc_dir / "assets.json").write_text("[" + " " * 16 + "]", encoding="utf-8")
    register_meta = repo_root / "register.meta.json"
    register_csv = repo_root / "register.csv"
    register_csv.write_text("id\nrow-1\n", encoding="utf-8")
    monkeypatch.setattr(e6_check_governance, "MAX_JSON_FILE_BYTES", 8, raising=False)

    with pytest.raises(ValueError, match="JSON file exceeds size limit"):
        e6_check_governance._read_json(edc_dir / "assets.json")

    report = e6_check_governance.ValidationReport()
    e6_check_governance.check_edc_bundle(
        edc_dir,
        report,
        register_meta_path=register_meta,
        register_csv_path=register_csv,
    )

    results = _result_map(report)
    assert not results["edc_bundle_valid_json"].passed
    assert "exceeds size limit" in results["edc_bundle_valid_json"].message


def test_json_reader_uses_nonblocking_nofollow_descriptor(monkeypatch, tmp_path):
    json_path = tmp_path / "payload.json"
    json_path.write_text('{"ok": true}\n', encoding="utf-8")
    original_open = e6_check_governance.os.open
    observed = False

    def assert_secure_open(path, flags, *args, **kwargs):
        nonlocal observed
        if Path(path) == json_path:
            observed = True
            assert flags & e6_check_governance.os.O_NONBLOCK
            assert flags & e6_check_governance.os.O_NOFOLLOW
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(e6_check_governance.os, "open", assert_secure_open)

    assert e6_check_governance._read_json(json_path) == {"ok": True}
    assert observed


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO test")
def test_json_reader_rejects_fifo_without_blocking(tmp_path):
    json_path = tmp_path / "payload.json"
    os.mkfifo(json_path)

    with pytest.raises(
        e6_check_governance.JsonFileReadError,
        match="not a regular file",
    ):
        e6_check_governance._read_json(json_path)


def test_explicit_register_csv_is_authoritative_over_stale_local_metadata(tmp_path):
    register_meta = tmp_path / "e1_dataset_register.meta.json"
    register_csv = tmp_path / "sds_dataset_register.csv"
    register_meta.write_text(json.dumps({"count": 999}), encoding="utf-8")
    register_csv.write_text("id\nrow-1\nrow-2\n", encoding="utf-8")

    assert e6_check_governance._load_register_count(register_meta, register_csv) == 2


def test_explicit_register_csv_has_an_independent_byte_limit(monkeypatch, tmp_path):
    register_meta = tmp_path / "e1_dataset_register.meta.json"
    register_csv = tmp_path / "sds_dataset_register.csv"
    register_csv.write_text("id\n" + "x" * 32 + "\n", encoding="utf-8")
    monkeypatch.setattr(
        e6_check_governance,
        "MAX_REGISTER_CSV_BYTES",
        16,
        raising=False,
    )

    with pytest.raises(ValueError, match="register CSV exceeds size limit"):
        e6_check_governance._load_register_count(register_meta, register_csv)


def test_explicit_register_csv_is_opened_nonblocking(monkeypatch, tmp_path):
    register_meta = tmp_path / "e1_dataset_register.meta.json"
    register_csv = tmp_path / "sds_dataset_register.csv"
    register_csv.write_text("id\nrow-1\n", encoding="utf-8")
    original_open = e6_check_governance.os.open
    observed = False

    def assert_nonblocking_open(path, flags, *args, **kwargs):
        nonlocal observed
        if Path(path) == register_csv:
            observed = True
            assert flags & e6_check_governance.os.O_NONBLOCK
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(e6_check_governance.os, "open", assert_nonblocking_open)

    assert e6_check_governance._load_register_count(register_meta, register_csv) == 1
    assert observed


def test_explicit_register_csv_enforces_field_limit_in_bytes(monkeypatch, tmp_path):
    register_meta = tmp_path / "e1_dataset_register.meta.json"
    register_csv = tmp_path / "sds_dataset_register.csv"
    register_csv.write_text(
        "id,title\nrow-1," + ("😀" * 9) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(e6_check_governance, "MAX_CSV_FIELD_BYTES", 32)

    with pytest.raises(
        e6_check_governance.RegisterFileReadError,
        match="CSV field exceeds byte limit",
    ):
        e6_check_governance._load_register_count(register_meta, register_csv)


def test_missing_explicit_register_csv_never_falls_back_to_local_metadata(tmp_path):
    register_meta = tmp_path / "e1_dataset_register.meta.json"
    register_csv = tmp_path / "missing-register.csv"
    register_meta.write_text(json.dumps({"count": 1805}), encoding="utf-8")

    assert (
        e6_check_governance._load_register_count(
            register_meta,
            register_csv,
            allow_metadata_fallback=False,
        )
        is None
    )


def test_data_product_terms_flags_generic_wsl_home_paths(tmp_path):
    repo_root = tmp_path / "repo"
    terms_path = repo_root / "docs" / "policies" / "data_product_terms.md"
    terms_path.parent.mkdir(parents=True, exist_ok=True)
    terms_path.write_text(
        "# Terms\n\n- Historical source: `/home/alice/sds/source-package`\n",
        encoding="utf-8",
    )

    report = e6_check_governance.ValidationReport()
    e6_check_governance.check_data_product_terms(
        terms_path,
        {},
        report,
        repo_root=repo_root,
    )

    results = _result_map(report)
    assert not results["data_product_terms_no_wsl_refs"].passed
