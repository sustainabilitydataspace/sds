"""Private commitment construction & verification (VARCH-4d).

Step 4 (write validation) of candidate-v14, sub-slice (d): the deterministic HMAC private-
commitment construction/verification for tenant-private values, implementing the ratified
``sds:profile:private-commitment:v1`` profile. A tenant-private value is never stored or
projected in plaintext to a shared/public surface; instead a value is committed via an HMAC
over its CANONICAL serialization bound to a pinned key lineage, so it is reproducible
(replayable) yet unrecoverable once the key is crypto-shredded.

Profile bindings (from the ratified schema):

* ``algorithm`` ∈ {HMAC-SHA-256, HMAC-SHA-512}.
* ``canonicalization_binding`` == ``sds-canonical-json-v1`` — the commitment message is the
  canonical serialization of an envelope binding the algorithm, key lineage, salt and payload,
  so two look-alike inputs cannot collide and the construction is byte-reproducible.
* key lineage: the commitment key is independent from the data-encryption key, and ``key_id`` +
  ``key_generation`` are pinned INTO the commitment, so a commitment replays only under the
  exact key lineage that produced it.
* ``verification_after_key_shred`` == ``unverifiable_fail_closed`` — once the key is gone,
  verification MUST fail closed (no silent pass, no recovery).

Like the sibling cores this is PURE + deterministic (it takes the key material from the caller;
real key storage/shred is the VARCH-5/6 KMS seam). It also enforces the no-plaintext / private
non-commitment rule for public/shared projections.

Gates implemented here (mechanism; CI wiring in VARCH-10):

* ``commitment-construction-replay-gate`` — :func:`construct_commitment` is deterministic: the
  same (key, key_id, key_generation, salt, payload, algorithm) always yields the same hex
  commitment; :func:`verify_commitment` reconstructs and compares in constant time.
* ``commitment-key-replay-gate`` — ``key_id`` + ``key_generation`` are bound into the message,
  so a commitment cannot be verified under a different key lineage; once the key is shredded
  (``key=None``) verification fails closed.
* ``private-value-non-commitment-gate`` — :func:`assert_no_private_leak` rejects any
  shared/public projection that exposes the plaintext value, the commitment, key id/generation,
  salt, payload id, or exact private timing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Mapping

from src.semantic.profiles import canonical_json
from src.semantic.profiles.registry import SPECS_DIR

PROFILE_ID = "sds:profile:private-commitment:v1"
_SCHEMA_FILE = "private_commitment_profile.schema.json"

CANONICAL_BINDING = "sds-canonical-json-v1"

# Supported algorithms (must equal the ratified schema's enum — guarded by a test).
ALGORITHMS: Mapping[str, Any] = {
    "HMAC-SHA-256": hashlib.sha256,
    "HMAC-SHA-512": hashlib.sha512,
}
DEFAULT_ALGORITHM = "HMAC-SHA-256"

# A commitment over a low-entropy input is brute-forceable; require a real salt.
MIN_SALT_BYTES = 16

# Weak HMAC keys undermine the commitment; require a full-strength key (>= SHA-256 block out).
MIN_KEY_BYTES = 32

# Default object_type bound into the typed payload hash (closes Decimal/str look-alikes).
DEFAULT_OBJECT_TYPE = "private_value"

# Fields that must NEVER appear in a shared/public projection of a tenant-private value.
# Includes the persisted ``tenant_private_value_payloads`` vocabulary so a leaked column name
# from that model is caught, not just the abstract commitment fields.
FORBIDDEN_PUBLIC_FIELDS = frozenset(
    {
        # abstract commitment / private-value fields
        "private_value",
        "plaintext",
        "value_plaintext",
        "commitment",
        "key_id",
        "key_generation",
        "salt",
        "payload_id",
        "private_timestamp",
        "private_committed_at",
        # persisted tenant_private_value_payloads column vocabulary
        "payload_key",
        "payload_ciphertext",
        "dek_ref",
        "commitment_key_id",
        "commitment_profile_ref",
        "private_input_schema_ref",
        "payload_hash",
        "value_context_ref",
    }
)


class CommitmentError(ValueError):
    """Raised when a private commitment cannot be constructed or verified."""


@dataclass(frozen=True)
class CommitmentRecord:
    """A persistable private commitment. Carries NO key material and NO plaintext payload."""

    commitment: str  # hex digest
    algorithm: str
    canonicalization_binding: str
    key_id: str
    key_generation: str
    salt: str  # hex
    object_type: str  # the type bound into the payload's typed hash


def _commitment_message(
    payload: Any,
    *,
    algorithm: str,
    key_id: str,
    key_generation: str,
    salt: bytes,
    object_type: str,
) -> bytes:
    """Canonical bytes the HMAC is computed over (binds lineage + salt + TYPED payload).

    The payload is bound via ``typed_content_hash`` (not the raw value) so two look-alike
    private values — e.g. ``Decimal('1.0')`` and the string ``'1.0'`` — never produce the same
    commitment under the same lineage/salt/key (raw canonical bytes are not type-injective).
    """
    payload_typed_hash = canonical_json.typed_content_hash(
        payload, object_type=object_type
    )
    envelope = {
        "profile_id": PROFILE_ID,
        "algorithm": algorithm,
        "canonicalization_binding": CANONICAL_BINDING,
        "key_id": key_id,
        "key_generation": key_generation,
        "salt": salt.hex(),
        "object_type": object_type,
        "payload_typed_hash": payload_typed_hash,
    }
    return canonical_json.canonicalize(envelope)


def construct_commitment(
    key: bytes,
    payload: Any,
    *,
    key_id: str,
    key_generation: str,
    salt: bytes,
    algorithm: str = DEFAULT_ALGORITHM,
    object_type: str = DEFAULT_OBJECT_TYPE,
) -> CommitmentRecord:
    """Construct a deterministic HMAC commitment over ``payload``'s TYPED canonical hash.

    The returned record is safe to persist in a tenant-private context; it never contains the
    key or the plaintext payload. Raises :class:`CommitmentError` on a weak/invalid input.
    """
    if algorithm not in ALGORITHMS:
        raise CommitmentError(f"unsupported algorithm {algorithm!r}")
    if len(key) < MIN_KEY_BYTES:
        raise CommitmentError(
            f"commitment key must be >= {MIN_KEY_BYTES} bytes (weak-key guard)"
        )
    if not key_id or not key_generation:
        raise CommitmentError("key_id and key_generation must be pinned (non-empty)")
    if not object_type:
        raise CommitmentError("object_type must be a non-empty string")
    if len(salt) < MIN_SALT_BYTES:
        raise CommitmentError(
            f"salt must be >= {MIN_SALT_BYTES} bytes (low-entropy commitment guard)"
        )

    message = _commitment_message(
        payload,
        algorithm=algorithm,
        key_id=key_id,
        key_generation=key_generation,
        salt=salt,
        object_type=object_type,
    )
    digest = hmac.new(key, message, ALGORITHMS[algorithm]).hexdigest()
    return CommitmentRecord(
        commitment=digest,
        algorithm=algorithm,
        canonicalization_binding=CANONICAL_BINDING,
        key_id=key_id,
        key_generation=key_generation,
        salt=salt.hex(),
        object_type=object_type,
    )


def verify_commitment(
    record: CommitmentRecord, key: bytes | None, payload: Any
) -> None:
    """Re-derive the commitment and reject a mismatch (constant-time).

    ``key=None`` models a crypto-shredded key: verification is unrecoverable and MUST fail
    closed (``verification_after_key_shred = unverifiable_fail_closed``).
    """
    if key is None:
        raise CommitmentError("key shredded: commitment is unverifiable (fail closed)")
    try:
        salt = bytes.fromhex(record.salt)
    except ValueError as exc:
        raise CommitmentError(f"malformed salt in record: {exc}") from exc
    expected = construct_commitment(
        key,
        payload,
        key_id=record.key_id,
        key_generation=record.key_generation,
        salt=salt,
        algorithm=record.algorithm,
        object_type=record.object_type,
    )
    if not hmac.compare_digest(expected.commitment, record.commitment):
        raise CommitmentError("commitment verification failed")


def assert_no_private_leak(
    projection: Mapping[str, Any],
    *,
    forbidden: frozenset[str] = FORBIDDEN_PUBLIC_FIELDS,
    allowed: frozenset[str] | None = None,
) -> None:
    """Reject a shared/public projection that exposes any tenant-private field.

    Walks the projection RECURSIVELY (nested dicts/lists), so a private field buried inside a
    sub-object is still caught. ``forbidden`` is a denylist of private field names (defaults to
    the abstract commitment fields + the persisted ``tenant_private_value_payloads`` columns).
    If ``allowed`` is given it is enforced as an ALLOWLIST at every dict level — any key not in
    it is rejected (defense-in-depth for a known-shape public projection).
    """

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                here = f"{path}{key}"
                if key in forbidden:
                    raise CommitmentError(
                        f"private-value-non-commitment: public projection leaks private "
                        f"field {here!r}"
                    )
                if allowed is not None and key not in allowed:
                    raise CommitmentError(
                        f"private-value-non-commitment: field {here!r} is not in the public "
                        "allowlist"
                    )
                walk(value, f"{here}.")
        elif isinstance(node, (list, tuple)):
            for index, item in enumerate(node):
                walk(item, f"{path}{index}.")

    walk(projection, "")


def ratified_algorithm_enum() -> list[str]:
    """The ``algorithm`` enum from the ratified profile schema (for the drift-guard test)."""
    schema = json.loads((SPECS_DIR / _SCHEMA_FILE).read_text(encoding="utf-8"))
    return schema["properties"]["algorithm"]["enum"]


# --- DB / KMS seam (documented) -------------------------------------------------------


def construct_and_store_commitment(
    session, payload, **kwargs
):  # pragma: no cover - seam
    """Construct a commitment with KMS-held key material and persist it (VARCH-5/6 seam)."""
    raise NotImplementedError(
        "construct_and_store_commitment is the VARCH-5/6 KMS/write-path seam; the VARCH-4d "
        "deliverable is the pure HMAC commitment construction/verification core."
    )
