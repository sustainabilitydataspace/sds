"""Profile registry for the SDS semantic-atomization contract layer.

Binds every VARCH profile to a stable ``(profile_id, version, hash)`` so later
sub-slices (migrations, resolver, manifests) can reference an immutable contract.

Two kinds of profile:
- ``implemented``: behavior lives in code (canonical serialization, computation).
  Their hash is taken over the in-code descriptor.
- ``ratified``: a frozen JSON Schema under ``specs/`` with no runtime yet. Their hash
  is taken over the parsed schema object via the canonical serialization profile, so a
  ratified profile is already content-addressable and validatable before VARCH-1.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

from src.semantic.profiles import canonical_json, computation

SPECS_DIR = Path(__file__).resolve().parent / "specs"


class ProfileRegistryError(RuntimeError):
    """Raised when a ratified profile schema is malformed or non-conforming."""


def _implemented_profiles() -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    for module in (canonical_json, computation):
        descriptor = module.profile_descriptor()
        profile_id = descriptor["profile_id"]
        profiles[profile_id] = {
            "profile_id": profile_id,
            "version": descriptor["version"],
            "kind": "implemented",
            "descriptor": descriptor,
            "hash": module.profile_hash(),
        }
    return profiles


def _load_ratified_schema(path: Path) -> dict[str, Any]:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileRegistryError(
            f"unreadable ratified schema {path.name}: {exc}"
        ) from exc
    if not isinstance(schema, dict):
        raise ProfileRegistryError(f"ratified schema {path.name} is not a JSON object")
    profile_id = schema.get("$id")
    if not profile_id:
        raise ProfileRegistryError(f"ratified schema {path.name} is missing $id")
    if "x-profile-version" not in schema:
        raise ProfileRegistryError(
            f"ratified schema {path.name} is missing x-profile-version"
        )
    # The schema must itself be a valid Draft-7 schema.
    Draft7Validator.check_schema(schema)
    return schema


def _ratified_profiles() -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    for path in sorted(SPECS_DIR.glob("*.schema.json")):
        schema = _load_ratified_schema(path)
        profile_id = schema["$id"]
        if profile_id in profiles:
            raise ProfileRegistryError(f"duplicate ratified profile id: {profile_id}")
        profiles[profile_id] = {
            "profile_id": profile_id,
            "version": schema["x-profile-version"],
            "kind": "ratified",
            "schema_file": path.name,
            "hash": canonical_json.content_hash(schema),
        }
    return profiles


def list_profiles() -> dict[str, dict[str, Any]]:
    """Return all registered profiles keyed by profile id, with content hashes."""
    profiles = _implemented_profiles()
    for profile_id, entry in _ratified_profiles().items():
        if profile_id in profiles:
            raise ProfileRegistryError(
                f"ratified profile id collides with implemented profile: {profile_id}"
            )
        profiles[profile_id] = entry
    return profiles


def get_profile(profile_id: str) -> dict[str, Any]:
    """Return a single registered profile, or raise ``KeyError``."""
    profiles = list_profiles()
    if profile_id not in profiles:
        raise KeyError(profile_id)
    return profiles[profile_id]


def profile_hashes() -> dict[str, str]:
    """Return ``{profile_id: hash}`` for every registered profile."""
    return {pid: entry["hash"] for pid, entry in list_profiles().items()}
