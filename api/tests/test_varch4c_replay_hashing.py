"""VARCH-4c contract tests — canonical-hash / computation / replay-manifest write checks.

Two layers: (1) the pure deterministic core (binds to the VARCH-0 profile layer, no DB) for
content-hash verification, scale-sensitive computation determinism, profile-pin backward
replay, and replay-manifest validation against the ratified Draft-7 schema; (2) a disposable-DB
smoke proving the persisted VARCH-3 seed registry hashes still validate under the 4c registry-
pin check (skipped unless SDS_MIGRATION_TEST_DATABASE_URL + SDS_MIGRATION_TEST_ALLOW_RESET).
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from src.semantic.profiles import canonical_json, computation, registry
from src.semantic.write_replay import (
    EVALUATION_ORDER,
    ReplayValidationError,
    assert_profile_hash_pinned,
    assert_profile_ref_pinned,
    compute_manifest_hash,
    validate_replay_manifest,
    verify_computation_profile_pin,
    verify_content_hash,
    verify_recomputation,
    verify_typed_content_hash,
)


def _ref(pid: str, hashes: dict[str, str]) -> dict[str, str]:
    return {"profile_id": pid, "version": "v1", "hash": hashes[pid]}


def _valid_manifest(hashes: dict[str, str] | None = None) -> dict:
    h = hashes if hashes is not None else registry.profile_hashes()
    return {
        "manifest_id": "m1",
        "manifest_version": "v1",
        "evaluation_order": list(EVALUATION_ORDER),
        "profile_tuple": {
            "canonical_serialization_profile": _ref("sds-canonical-json-v1", h),
            "computation_profile": _ref("sds-computation-profile-v1", h),
            "private_commitment_profile": _ref("sds:profile:private-commitment:v1", h),
            "aggregate_disclosure_profile": _ref(
                "sds:profile:aggregate-disclosure:v1", h
            ),
            "license_rights_window": _ref("sds:profile:license-rights-window:v1", h),
        },
        "transition_policy": "fail_closed_require_explicit_transition",
    }


# --- Layer 1: pure core --------------------------------------------------------------


def test_verify_content_hash_roundtrip_and_mismatch() -> None:
    payload = {"a": Decimal("1.0"), "b": ["x", None]}
    h = canonical_json.content_hash(payload)
    assert verify_content_hash(payload, h) == h
    with pytest.raises(ReplayValidationError, match="content hash mismatch"):
        verify_content_hash(payload, "0" * 64)


def test_verify_typed_content_hash_roundtrip_and_mismatch() -> None:
    payload = {"v": Decimal("2.50")}
    h = canonical_json.typed_content_hash(payload, object_type="trace")
    assert verify_typed_content_hash(payload, h, object_type="trace") == h
    with pytest.raises(ReplayValidationError, match="typed content hash mismatch"):
        verify_typed_content_hash(payload, "0" * 64, object_type="trace")


def test_verify_recomputation_is_scale_sensitive() -> None:
    verify_recomputation(
        Decimal("3.00"), computation.deterministic_sum(["1.00", "2.00"])
    )
    with pytest.raises(ReplayValidationError, match="non-determinism"):
        verify_recomputation(Decimal("1.0"), Decimal("1.00"))


def test_verify_computation_profile_pin() -> None:
    verify_computation_profile_pin(computation.profile_hash())
    with pytest.raises(ReplayValidationError, match="hash drift"):
        verify_computation_profile_pin("0" * 64)


def test_assert_profile_hash_pinned_unknown_and_drift() -> None:
    h = registry.profile_hashes()
    assert_profile_hash_pinned("sds-canonical-json-v1", h["sds-canonical-json-v1"])
    with pytest.raises(ReplayValidationError, match="unknown profile"):
        assert_profile_hash_pinned("sds-not-a-profile", "x")
    with pytest.raises(ReplayValidationError, match="hash drift"):
        assert_profile_hash_pinned("sds-canonical-json-v1", "0" * 64)


def test_validate_replay_manifest_accepts_valid() -> None:
    validate_replay_manifest(_valid_manifest())


def test_validate_replay_manifest_rejects_bad_evaluation_order() -> None:
    m = _valid_manifest()
    m["evaluation_order"] = list(reversed(EVALUATION_ORDER))
    with pytest.raises(ReplayValidationError, match="schema violations"):
        validate_replay_manifest(m)


def test_validate_replay_manifest_rejects_missing_required() -> None:
    m = _valid_manifest()
    del m["transition_policy"]
    with pytest.raises(ReplayValidationError, match="schema violations"):
        validate_replay_manifest(m)


def test_validate_replay_manifest_rejects_bad_profile_ref_shape() -> None:
    m = _valid_manifest()
    del m["profile_tuple"]["computation_profile"]["hash"]  # profileRef requires hash
    with pytest.raises(ReplayValidationError, match="schema violations"):
        validate_replay_manifest(m)


def test_validate_replay_manifest_rejects_drifted_pin() -> None:
    m = _valid_manifest()
    m["profile_tuple"]["canonical_serialization_profile"]["hash"] = "a" * 64
    with pytest.raises(ReplayValidationError, match="hash drift"):
        validate_replay_manifest(m)


def test_validate_replay_manifest_rejects_drifted_version() -> None:
    # M1: a profileRef with the correct hash but a tampered version must be rejected (the
    # registry models a profile as a full (profile_id, version, hash) tuple).
    m = _valid_manifest()
    m["profile_tuple"]["computation_profile"]["version"] = "v2"
    with pytest.raises(ReplayValidationError, match="version drift"):
        validate_replay_manifest(m)


def test_assert_profile_ref_pinned_full_tuple() -> None:
    profiles = registry.list_profiles()
    entry = profiles["sds-canonical-json-v1"]
    assert_profile_ref_pinned(
        {
            "profile_id": "sds-canonical-json-v1",
            "version": entry["version"],
            "hash": entry["hash"],
        }
    )
    with pytest.raises(ReplayValidationError, match="version drift"):
        assert_profile_ref_pinned(
            {
                "profile_id": "sds-canonical-json-v1",
                "version": "v9",
                "hash": entry["hash"],
            }
        )


def test_validate_replay_manifest_can_skip_registry_pins() -> None:
    # Schema-only validation still passes with a (structurally valid) fake hash.
    m = _valid_manifest()
    m["profile_tuple"]["canonical_serialization_profile"]["hash"] = "a" * 64
    validate_replay_manifest(m, check_registry_pins=False)


def test_compute_manifest_hash_is_canonical_and_deterministic() -> None:
    m = _valid_manifest()
    assert compute_manifest_hash(m) == canonical_json.content_hash(m)
    assert compute_manifest_hash(m) == compute_manifest_hash(_valid_manifest())


# --- Layer 2: disposable-DB smoke ----------------------------------------------------


def _disposable_engine():
    database_url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    from src.database.init_db import init_db_for_engine

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    init_db_for_engine(engine)
    return engine


def test_disposable_db_seeded_hashes_validate_under_4c() -> None:
    engine = _disposable_engine()
    try:
        with engine.begin() as conn:
            db_hashes: dict[str, str] = {}
            db_hashes.update(
                conn.execute(
                    text("SELECT profile_id, profile_hash FROM canonical_hash_profiles")
                ).all()
            )
            db_hashes.update(
                conn.execute(
                    text("SELECT profile_id, profile_hash FROM computation_profiles")
                ).all()
            )
            db_hashes.update(
                conn.execute(
                    text("SELECT schema_id, schema_hash FROM contract_schema_versions")
                ).all()
            )

        # The persisted VARCH-3 seed hashes equal the live registry (seed integrity).
        assert db_hashes == registry.profile_hashes()

        # A manifest built from the PERSISTED hashes validates fail-closed under 4c.
        validate_replay_manifest(_valid_manifest(db_hashes))

        # Tampering any persisted hash makes the manifest fail the registry-pin check.
        tampered = dict(db_hashes)
        tampered["sds-computation-profile-v1"] = "b" * 64
        with pytest.raises(ReplayValidationError, match="hash drift"):
            validate_replay_manifest(_valid_manifest(tampered))
    finally:
        engine.dispose()
