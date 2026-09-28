"""VARCH-0: profile registry — append-only immutability + ratified-schema validity.

Covers backlog ACC-8, ACC-9: every profile (implemented + ratified) is
content-addressable; the v1 hashes are frozen (a rule change must become a new
version, never an in-place edit); ratified schemas are valid Draft-7 schemas.
"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft7Validator

from src.semantic.profiles import registry

# Append-only pins. Changing any rule/schema is a NEW profile version, never an edit
# to v1; if one of these fails, the v1 contract was mutated in place.
FROZEN_PROFILE_HASHES = {
    "sds-canonical-json-v1": "6e0a325f9584cff46c0b5f20adaf45a59dd2e377ee3d565be79655187fbcfb8f",
    "sds-computation-profile-v1": "6f567eb457f39f6bff5700d2ba53bf2320256a4da1271251213157e037f0b050",
    "sds:profile:aggregate-disclosure:v1": "9ee75e23a702503dc48518959432a286c33ad8bfb26c505d3185f739dd4c361b",
    "sds:profile:consolidation-composition:v1": "494f68b562c6ebb96e67d90e979d7a4bbb64b6128d5107ca9726450bd038469f",
    "sds:profile:factor-vintage-selection:v1": "62e621eee49da393fabbcb9c45318ecb48be1a3e8450400cc34a01cf24e48610",
    "sds:profile:license-rights-window:v1": "6fa789ff3cab7d143c33571376ed65dadf6164b7ac160b73f6287c43c86cd358",
    "sds:profile:private-commitment:v1": "47aabe17688c90ea48c644ae55d4a643494c40eb27f82d5b60357e0768c41052",
    "sds:profile:public-temporal-read:v1": "c8adbd78333dd443876907540cb51d1b141fd3156a50b3590c742b1b7deb1018",
    "sds:profile:replay-manifest:v1": "c00242c23dd03bc166f7f2a5bf2d6e0c5c74693b72ef17ab61d998c587ad5c8a",
}


def test_registry_lists_all_implemented_and_ratified_profiles():
    profiles = registry.list_profiles()
    kinds = {pid: entry["kind"] for pid, entry in profiles.items()}
    assert kinds["sds-canonical-json-v1"] == "implemented"
    assert kinds["sds-computation-profile-v1"] == "implemented"
    ratified = [pid for pid, kind in kinds.items() if kind == "ratified"]
    assert len(ratified) == 7


def test_profile_hashes_are_frozen():
    assert registry.profile_hashes() == FROZEN_PROFILE_HASHES


def test_every_profile_has_id_version_and_hash():
    for entry in registry.list_profiles().values():
        assert entry["profile_id"]
        assert entry["version"]
        assert len(entry["hash"]) == 64


def test_ratified_schemas_are_valid_draft7():
    for entry in registry.list_profiles().values():
        if entry["kind"] != "ratified":
            continue
        path = registry.SPECS_DIR / entry["schema_file"]
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft7Validator.check_schema(schema)


def test_ratified_schemas_are_satisfiable():
    # Guard against the M7 defect class: a key in `required` that is absent from
    # `properties` while `additionalProperties` is false makes the schema unsatisfiable
    # (no instance can ever validate).
    for entry in registry.list_profiles().values():
        if entry["kind"] != "ratified":
            continue
        schema = json.loads(
            (registry.SPECS_DIR / entry["schema_file"]).read_text("utf-8")
        )
        if schema.get("additionalProperties", True) is False:
            missing = set(schema.get("required", [])) - set(
                (schema.get("properties") or {}).keys()
            )
            assert (
                not missing
            ), f"{entry['schema_file']} unsatisfiable: required-not-in-properties {sorted(missing)}"


def test_get_profile_and_missing_profile():
    entry = registry.get_profile("sds-canonical-json-v1")
    assert entry["kind"] == "implemented"
    with pytest.raises(KeyError):
        registry.get_profile("sds:profile:does-not-exist:v1")


def test_malformed_ratified_schema_is_rejected(tmp_path, monkeypatch):
    bad = tmp_path / "broken.schema.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(registry, "SPECS_DIR", tmp_path)
    with pytest.raises(registry.ProfileRegistryError):
        registry.list_profiles()


def test_non_object_ratified_schema_is_rejected(tmp_path, monkeypatch):
    not_obj = tmp_path / "arr.schema.json"
    not_obj.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(registry, "SPECS_DIR", tmp_path)
    with pytest.raises(registry.ProfileRegistryError):
        registry.list_profiles()


def test_ratified_schema_missing_id_is_rejected(tmp_path, monkeypatch):
    no_id = tmp_path / "noid.schema.json"
    no_id.write_text(json.dumps({"x-profile-version": "v1"}), encoding="utf-8")
    monkeypatch.setattr(registry, "SPECS_DIR", tmp_path)
    with pytest.raises(registry.ProfileRegistryError):
        registry.list_profiles()


def test_ratified_schema_missing_version_is_rejected(tmp_path, monkeypatch):
    no_ver = tmp_path / "nover.schema.json"
    no_ver.write_text(json.dumps({"$id": "sds:profile:x:v1"}), encoding="utf-8")
    monkeypatch.setattr(registry, "SPECS_DIR", tmp_path)
    with pytest.raises(registry.ProfileRegistryError):
        registry.list_profiles()
