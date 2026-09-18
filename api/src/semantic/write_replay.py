"""Deterministic computation, canonical-hash & replay-manifest write checks (VARCH-4c).

Step 4 (write validation) of candidate-v14, sub-slice (c): the deterministic, application-side
checks that bind every content-addressable write to the VARCH-0 profile layer so a stored hash,
computed value, or replay manifest is reproducible forever. This module COMPOSES the pinned
profiles (it does not re-implement them):

* :mod:`src.semantic.profiles.canonical_json` — ``sds-canonical-json-v1`` byte-exact
  serialization + ``content_hash`` / ``typed_content_hash``.
* :mod:`src.semantic.profiles.computation` — ``sds-computation-profile-v1`` deterministic
  decimal arithmetic + ``canonicalize_decimal_key``.
* :mod:`src.semantic.profiles.registry` — the content-addressable ``{profile_id: hash}`` map
  and the ratified ``sds:profile:replay-manifest:v1`` JSON Schema under ``specs/``.

Unlike VARCH-4a/4b there is no DB backstop unique to this slice: the guarantee is byte-level
reproducibility, enforced here and (in VARCH-5/6) at every persist. Like the sibling cores it
is PURE + deterministic; the only "seam" is that callers supply the recomputation closure.

Gates implemented here (mechanism; CI wiring in VARCH-10):

* ``canonical-hash-serialization-gate`` / ``normalization-determinism-gate`` —
  :func:`verify_content_hash` / :func:`verify_typed_content_hash` recompute the canonical hash
  and reject a stored hash that does not match (NFC / decimal-scale / key-order determinism is
  inherited from the profile).
* ``computation-determinism-gate`` — :func:`verify_recomputation` compares a stored decimal to
  a recomputation using the computation profile's scale-sensitive canonical key
  (``"1.0"`` != ``"1.00"``); :func:`verify_computation_profile_pin` ties the artifact to the
  frozen computation profile hash.
* ``profile-contract-backward-replay-gate`` — :func:`assert_profile_hash_pinned` rejects a
  pinned profile hash that has drifted from the registry's current frozen hash, so an artifact
  pinned to a profile version replays only under that exact frozen contract.
* ``semantic-replay-manifest-gate`` / ``profile-bundle-order-replay-gate`` —
  :func:`validate_replay_manifest` validates a manifest against the ratified Draft-7 schema
  (which fixes the evaluation_order const and the profile_tuple shape) AND checks every bound
  ``profileRef.hash`` against the registry (fail-closed), so a manifest can only bind real,
  current, correctly-ordered profile versions.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Mapping

from jsonschema import Draft7Validator

from src.semantic.profiles import canonical_json, computation, registry
from src.semantic.profiles.registry import SPECS_DIR

REPLAY_MANIFEST_PROFILE_ID = "sds:profile:replay-manifest:v1"
REPLAY_MANIFEST_SCHEMA_FILE = "replay_manifest.schema.json"

# The pinned bundle evaluation order (mirrors the ratified schema's ``const``); exported so
# callers/tests can build a conforming manifest without re-deriving it.
EVALUATION_ORDER = (
    "canonical_serialization_profile",
    "computation_profile",
    "private_commitment_profile",
    "aggregate_disclosure_profile",
    "license_rights_window",
)

_PROFILE_REF_KEYS = frozenset({"profile_id", "version", "hash"})


class ReplayValidationError(ValueError):
    """Raised when a content hash, computation, or replay manifest fails validation."""


# --- canonical-hash-serialization / normalization-determinism -------------------------


def verify_content_hash(payload: Any, claimed_hash: str) -> str:
    """Recompute ``content_hash(payload)`` and reject a mismatch. Returns the actual hash."""
    actual = canonical_json.content_hash(payload)
    if actual != claimed_hash:
        raise ReplayValidationError(
            f"canonical content hash mismatch: recomputed {actual} != stored {claimed_hash}"
        )
    return actual


def verify_typed_content_hash(
    payload: Any,
    claimed_hash: str,
    *,
    object_type: str,
    schema_id: str | None = None,
    schema_version: str | None = None,
) -> str:
    """Recompute the type-bound content hash (traces/replays/pins) and reject a mismatch."""
    actual = canonical_json.typed_content_hash(
        payload,
        object_type=object_type,
        schema_id=schema_id,
        schema_version=schema_version,
    )
    if actual != claimed_hash:
        raise ReplayValidationError(
            f"typed content hash mismatch for object_type={object_type!r}: "
            f"recomputed {actual} != stored {claimed_hash}"
        )
    return actual


# --- computation-determinism ----------------------------------------------------------


def verify_recomputation(claimed: object, recomputed: object) -> None:
    """Reject unless ``claimed`` and ``recomputed`` are equal under the computation profile.

    Comparison uses ``computation.canonicalize_decimal_key`` so scale is significant
    (``Decimal('1.0')`` != ``Decimal('1.00')``), matching how computed values are hashed.
    """
    claimed_d = computation.to_decimal(claimed)
    recomputed_d = computation.to_decimal(recomputed)
    if computation.canonicalize_decimal_key(
        claimed_d
    ) != computation.canonicalize_decimal_key(recomputed_d):
        raise ReplayValidationError(
            f"computation non-determinism: stored {claimed_d} != recomputed {recomputed_d}"
        )


def verify_computation_profile_pin(pinned_hash: str) -> None:
    """Reject unless ``pinned_hash`` equals the frozen ``sds-computation-profile-v1`` hash."""
    assert_profile_hash_pinned("sds-computation-profile-v1", pinned_hash)


# --- profile-contract-backward-replay -------------------------------------------------


def assert_profile_hash_pinned(profile_id: str, pinned_hash: str) -> None:
    """Reject an unknown profile or a pinned hash that has drifted from the registry."""
    frozen = registry.profile_hashes().get(profile_id)
    if frozen is None:
        raise ReplayValidationError(f"unknown profile {profile_id!r}")
    if frozen != pinned_hash:
        raise ReplayValidationError(
            f"profile {profile_id!r} hash drift: pinned {pinned_hash} != frozen {frozen}"
        )


def assert_profile_ref_pinned(ref: Mapping[str, Any]) -> None:
    """Reject a manifest ``profileRef`` whose full ``(profile_id, version, hash)`` tuple does
    not match the registry. The registry models a profile as a stable tuple, so checking only
    the hash would let an internally inconsistent version/hash pair pass (M1).
    """
    profile_id = ref["profile_id"]
    entry = registry.list_profiles().get(profile_id)
    if entry is None:
        raise ReplayValidationError(f"unknown profile {profile_id!r}")
    if ref["version"] != entry["version"]:
        raise ReplayValidationError(
            f"profile {profile_id!r} version drift: pinned {ref['version']} != frozen "
            f"{entry['version']}"
        )
    if ref["hash"] != entry["hash"]:
        raise ReplayValidationError(
            f"profile {profile_id!r} hash drift: pinned {ref['hash']} != frozen "
            f"{entry['hash']}"
        )


# --- semantic-replay-manifest / profile-bundle-order-replay ---------------------------


def _replay_manifest_schema() -> dict[str, Any]:
    return json.loads(
        (SPECS_DIR / REPLAY_MANIFEST_SCHEMA_FILE).read_text(encoding="utf-8")
    )


def compute_manifest_hash(manifest: Mapping[str, Any]) -> str:
    """The content-addressable hash of a manifest under the canonical serialization profile."""
    return canonical_json.content_hash(manifest)


def validate_replay_manifest(
    manifest: Mapping[str, Any], *, check_registry_pins: bool = True
) -> None:
    """Validate a replay manifest fail-closed.

    1. Structural: validate against the ratified Draft-7 ``replay-manifest`` schema, which
       fixes the ``evaluation_order`` const, the required keys, and the ``profileRef`` shape.
    2. Semantic (``check_registry_pins``): every bound ``profileRef`` in ``profile_tuple`` must
       match the registry's full frozen ``(profile_id, version, hash)`` tuple — so a manifest
       can only bind real, current profile versions (backward-replay/drift guard).

    ``check_registry_pins=False`` runs schema-only validation and is for test/inspection use;
    the fail-closed write gate ALWAYS leaves it True.
    """
    errors = sorted(
        Draft7Validator(_replay_manifest_schema()).iter_errors(manifest),
        key=lambda e: list(e.path),
    )
    if errors:
        preview = "; ".join(e.message for e in errors[:5])
        raise ReplayValidationError(f"replay manifest schema violations: {preview}")

    if check_registry_pins:
        for ref in manifest["profile_tuple"].values():
            if isinstance(ref, Mapping) and _PROFILE_REF_KEYS <= set(ref):
                assert_profile_ref_pinned(ref)


# --- DB adapter (documented seam) -----------------------------------------------------


def verify_persisted_artifact(session, artifact):  # pragma: no cover - DB seam
    """Re-verify a persisted content-addressable artifact's hash + profile pins (VARCH-5/6)."""
    raise NotImplementedError(
        "verify_persisted_artifact is the VARCH-5/6 write-path seam; the VARCH-4c deliverable "
        "is the pure canonical-hash / computation / replay-manifest validation core."
    )
