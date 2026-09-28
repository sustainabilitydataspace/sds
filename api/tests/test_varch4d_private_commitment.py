"""VARCH-4d contract tests — private (HMAC) commitment construction & verification.

Two layers: (1) the pure deterministic core (no DB) — deterministic HMAC construction bound to
the canonical serialization + key lineage, constant-time verification, fail-closed after key
shred, low-entropy/weak-input guards, and the private-value-non-commitment leak guard;
(2) a disposable-DB smoke proving the supported algorithm set + canonicalization binding match
the PERSISTED ratified private-commitment profile schema (skipped unless
SDS_MIGRATION_TEST_DATABASE_URL + SDS_MIGRATION_TEST_ALLOW_RESET=true).
"""

from __future__ import annotations

import json
import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from src.semantic.write_commitment import (
    ALGORITHMS,
    CANONICAL_BINDING,
    DEFAULT_ALGORITHM,
    FORBIDDEN_PUBLIC_FIELDS,
    MIN_SALT_BYTES,
    PROFILE_ID,
    CommitmentError,
    CommitmentRecord,
    assert_no_private_leak,
    construct_commitment,
    ratified_algorithm_enum,
    verify_commitment,
)

_KEY = b"k" * 32
_SALT = b"s" * MIN_SALT_BYTES
_PAYLOAD = {"value": Decimal("123.45"), "unit": "kg"}


def _commit(**over):
    kwargs = dict(key_id="kid-1", key_generation="g1", salt=_SALT)
    kwargs.update(over)
    return construct_commitment(_KEY, _PAYLOAD, **kwargs)


# --- Layer 1: pure core --------------------------------------------------------------


def test_construct_is_deterministic_replay() -> None:
    assert _commit().commitment == _commit().commitment


def test_verify_roundtrip_and_wrong_key_payload() -> None:
    rec = _commit()
    verify_commitment(rec, _KEY, _PAYLOAD)
    with pytest.raises(CommitmentError, match="verification failed"):
        verify_commitment(rec, b"other-key" * 4, _PAYLOAD)
    with pytest.raises(CommitmentError, match="verification failed"):
        verify_commitment(rec, _KEY, {"value": Decimal("999.99"), "unit": "kg"})


def test_commitment_is_bound_to_key_lineage() -> None:
    base = _commit()
    assert _commit(key_id="kid-2").commitment != base.commitment
    assert _commit(key_generation="g2").commitment != base.commitment


def test_commitment_is_bound_to_salt() -> None:
    assert _commit(salt=b"t" * MIN_SALT_BYTES).commitment != _commit().commitment


def test_record_carries_no_key_or_plaintext() -> None:
    rec = _commit()
    fields = set(CommitmentRecord.__dataclass_fields__)
    assert "key" not in fields and "payload" not in fields and "plaintext" not in fields
    # the plaintext value must not be reconstructable from the record's public fields
    assert "123.45" not in json.dumps(rec.__dict__)


def test_rejects_short_salt() -> None:
    with pytest.raises(CommitmentError, match="low-entropy"):
        _commit(salt=b"short")


def test_rejects_weak_key() -> None:
    # M2: empty and short keys are both rejected by the weak-key guard.
    for weak in (b"", b"short-key"):
        with pytest.raises(CommitmentError, match="weak-key guard"):
            construct_commitment(
                weak, _PAYLOAD, key_id="k", key_generation="g", salt=_SALT
            )


def test_commitment_is_type_bound() -> None:
    # M1: a Decimal payload and a look-alike string payload must NOT collide.
    dec = construct_commitment(
        _KEY, Decimal("1.0"), key_id="k1", key_generation="g1", salt=_SALT
    )
    txt = construct_commitment(
        _KEY, "1.0", key_id="k1", key_generation="g1", salt=_SALT
    )
    assert dec.commitment != txt.commitment


def test_commitment_is_bound_to_object_type() -> None:
    base = _commit()
    assert _commit(object_type="other_kind").commitment != base.commitment


def test_rejects_unpinned_lineage() -> None:
    with pytest.raises(CommitmentError, match="pinned"):
        _commit(key_id="")


def test_rejects_unsupported_algorithm() -> None:
    with pytest.raises(CommitmentError, match="unsupported algorithm"):
        _commit(algorithm="MD5")


def test_rejects_non_canonical_payload() -> None:
    # float ingress is forbidden by the canonical serialization profile.
    with pytest.raises(Exception):
        construct_commitment(
            _KEY, {"value": 1.5}, key_id="k", key_generation="g", salt=_SALT
        )


def test_verify_fails_closed_after_key_shred() -> None:
    rec = _commit()
    with pytest.raises(CommitmentError, match="fail closed"):
        verify_commitment(rec, None, _PAYLOAD)


def test_assert_no_private_leak_denylist_incl_model_vocab() -> None:
    assert_no_private_leak({"concept_uri": "syg:X", "label": "ok", "count": 3})
    # abstract fields AND the persisted tenant_private_value_payloads vocabulary (M3).
    for field in (
        "commitment",
        "key_id",
        "salt",
        "private_value",
        "payload_id",
        "payload_ciphertext",
        "dek_ref",
        "commitment_key_id",
        "commitment_profile_ref",
        "private_input_schema_ref",
        "payload_hash",
    ):
        with pytest.raises(CommitmentError, match="leaks private"):
            assert_no_private_leak({"concept_uri": "syg:X", field: "leak"})


def test_assert_no_private_leak_is_recursive() -> None:
    # M3: a private field nested inside a sub-object / list is still caught.
    with pytest.raises(CommitmentError, match="leaks private"):
        assert_no_private_leak({"ok": {"nested": {"dek_ref": "x"}}})
    with pytest.raises(CommitmentError, match="leaks private"):
        assert_no_private_leak({"rows": [{"label": "a"}, {"commitment": "h"}]})


def test_assert_no_private_leak_allowlist_mode() -> None:
    allowed = frozenset({"concept_uri", "label"})
    assert_no_private_leak({"concept_uri": "syg:X", "label": "ok"}, allowed=allowed)
    with pytest.raises(CommitmentError, match="not in the public allowlist"):
        assert_no_private_leak({"concept_uri": "syg:X", "extra": 1}, allowed=allowed)


def test_supported_algorithms_match_ratified_enum() -> None:
    assert sorted(ALGORITHMS) == sorted(ratified_algorithm_enum())
    assert DEFAULT_ALGORITHM in ALGORITHMS
    assert "private_value" in FORBIDDEN_PUBLIC_FIELDS


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


def test_disposable_db_profile_schema_matches_core() -> None:
    engine = _disposable_engine()
    try:
        with engine.begin() as conn:
            schema_json = conn.execute(
                text(
                    "SELECT schema FROM contract_schema_versions WHERE schema_id = :sid"
                ),
                {"sid": PROFILE_ID},
            ).scalar_one()
        schema = (
            schema_json if isinstance(schema_json, dict) else json.loads(schema_json)
        )
        # The persisted ratified profile's algorithm enum + canonical binding agree with the
        # VARCH-4d core, so construction binds to exactly the seeded contract.
        assert sorted(schema["properties"]["algorithm"]["enum"]) == sorted(ALGORITHMS)
        assert (
            schema["properties"]["canonicalization_binding"]["const"]
            == CANONICAL_BINDING
        )
    finally:
        engine.dispose()
