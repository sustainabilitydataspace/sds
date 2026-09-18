"""``sds-canonical-json-v1`` — deterministic, append-only canonical serialization.

This profile is the byte-level foundation every downstream SDS semantic-atomization
hash, trace, replay manifest, disclosure pin, and license decision binds to. Given
the same logical value it always produces the same bytes, on any conforming runtime,
forever (append-only: a rule change is a NEW profile version, never an edit to v1).

It is deliberately independent from:
- ``src.services.export_signing`` (legacy export-manifest signing, looser rules), and
- ``src.services.value_versioning`` numeric helpers.
Do not route those through this module without an explicit profile/manifest transition;
doing so would change historical hashes.

Pinned rules (v1):
- ``float`` is forbidden anywhere in the input graph (no IEEE-754 ingress). Use ``Decimal``.
- ``Decimal`` serializes to a JSON *string* in fixed-point form (no scientific notation),
  preserving its exact scale — trailing zeros are semantic and hash-distinct
  (``"1.0"`` != ``"1.00"``). Negative zero normalizes to zero at the same scale.
- ``int`` serializes as a bare JSON number (exact). ``bool`` is ``true``/``false`` and is
  never treated as an integer.
- ``str`` is NFC-normalized under the pinned Unicode version, then JSON-escaped.
- ``datetime`` must be timezone-aware; it normalizes to UTC and serializes as a JSON
  string with fixed microsecond precision: ``YYYY-MM-DDTHH:MM:SS.ffffffZ``. Naive
  datetimes are rejected.
- ``dict`` keys must be strings, are NFC-normalized, sorted by UTF-8 byte order, and any
  two keys that collide after normalization are rejected.
- ``set``/``frozenset`` serialize as a list sorted by each element's canonical UTF-8 bytes.
- ``list``/``tuple`` keep their given order (order is significant).
- ``None`` serializes as JSON ``null`` and is retained (never silently stripped); absent
  keys, ``null``, ``""``, ``[]`` and ``{}`` are all distinct.
- Output is compact UTF-8 (no insignificant whitespace).
- The running ``unicodedata.unidata_version`` must equal the pinned version or
  serialization fails closed (reproducibility guarantee).
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

CANONICAL_PROFILE_ID = "sds-canonical-json-v1"
CANONICAL_PROFILE_VERSION = "v1"
PINNED_UNICODE_VERSION = "13.0.0"
PINNED_NORMALIZATION_FORM = "NFC"
TIMESTAMP_PRECISION = "microseconds"

_FIXTURE_CORPUS_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "canonical_corpus.json"
)


class CanonicalSerializationError(ValueError):
    """Raised when a value cannot be canonicalized deterministically."""


def _require_pinned_unicode() -> None:
    actual = unicodedata.unidata_version
    if actual != PINNED_UNICODE_VERSION:
        raise CanonicalSerializationError(
            "Unicode data version drift: runtime "
            f"{actual!r} != pinned {PINNED_UNICODE_VERSION!r}; "
            f"{CANONICAL_PROFILE_ID} serialization is not reproducible here."
        )


def _nfc(text: str) -> str:
    return unicodedata.normalize(PINNED_NORMALIZATION_FORM, text)


def _encode_str(text: str) -> str:
    # json.dumps gives correct, deterministic JSON string escaping for the
    # NFC-normalized text (quotes, control chars, surrogate handling).
    return json.dumps(_nfc(text), ensure_ascii=False)


def _encode_decimal(value: Decimal) -> str:
    if value.is_nan() or value.is_infinite():
        raise CanonicalSerializationError(
            "NaN/Infinity Decimal cannot be canonically serialized"
        )
    if value == 0:
        # Collapse negative zero while preserving the declared scale (-0.00 -> 0.00).
        value = abs(value)
    # format(..., "f") is fixed-point: no scientific notation, exact scale preserved.
    return json.dumps(format(value, "f"))


def _encode_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise CanonicalSerializationError(
            "naive datetime is forbidden; supply a timezone-aware UTC datetime"
        )
    as_utc = value.astimezone(timezone.utc)
    text = as_utc.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    return json.dumps(text)


def _encode_mapping(value: dict) -> str:
    entries: list[tuple[bytes, str, Any]] = []
    seen: set[bytes] = set()
    for key, item in value.items():
        if not isinstance(key, str):
            raise CanonicalSerializationError(
                f"dict keys must be str, got {type(key).__name__}"
            )
        normalized = _nfc(key)
        key_bytes = normalized.encode("utf-8")
        if key_bytes in seen:
            raise CanonicalSerializationError(
                f"duplicate key after NFC normalization: {normalized!r}"
            )
        seen.add(key_bytes)
        entries.append((key_bytes, normalized, item))
    entries.sort(key=lambda entry: entry[0])
    parts = [
        json.dumps(normalized, ensure_ascii=False) + ":" + _encode(item)
        for _, normalized, item in entries
    ]
    return "{" + ",".join(parts) + "}"


def _encode_set(value: Any) -> str:
    encoded = [_encode(item) for item in value]
    encoded.sort(key=lambda text: text.encode("utf-8"))
    return "[" + ",".join(encoded) + "]"


def _encode(value: Any) -> str:
    # bool MUST precede int/Decimal: bool is a subclass of int.
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _encode_str(value)
    if isinstance(value, Decimal):
        return _encode_decimal(value)
    if isinstance(value, float):
        raise CanonicalSerializationError(
            "float is forbidden in canonical serialization; use Decimal"
        )
    if isinstance(value, int):
        return str(value)
    if isinstance(value, datetime):
        return _encode_datetime(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    if isinstance(value, (set, frozenset)):
        return _encode_set(value)
    if isinstance(value, dict):
        return _encode_mapping(value)
    raise CanonicalSerializationError(
        f"unsupported type for canonical serialization: {type(value).__name__}"
    )


def canonicalize(value: Any) -> bytes:
    """Return the canonical UTF-8 byte serialization of ``value``."""
    _require_pinned_unicode()
    return _encode(value).encode("utf-8")


def content_hash(value: Any) -> str:
    """Return the SHA-256 hex digest of ``value``'s canonical serialization.

    NOTE: raw ``content_hash`` is injective only WITHIN a single declared type. By
    design the bytes are not self-describing — e.g. ``Decimal('1.0')`` and the string
    ``'1.0'`` both serialize to ``"1.0"``. For any trace/replay/disclosure/license hash,
    use :func:`typed_content_hash`, which binds the profile, object type, and schema so
    typed values cannot collide with look-alike literals.
    """
    return hashlib.sha256(canonicalize(value)).hexdigest()


def _type_tag(value: Any) -> Any:
    """Return a leaf-type-annotated mirror of ``value`` for injective hashing.

    Each scalar is wrapped with its discriminating type so look-alike encodings cannot
    collide: ``Decimal('1.0')`` -> ``{"t":"decimal","v":Decimal('1.0')}`` while the
    string ``'1.0'`` -> ``{"t":"str","v":"1.0"}``; ``datetime`` and ``str`` likewise
    differ. Sets stay order-independent by sorting their tagged elements by canonical
    bytes.
    """
    if value is None:
        return {"t": "null"}
    if value is True or value is False:
        return {"t": "bool", "v": value}
    if isinstance(value, str):
        return {"t": "str", "v": value}
    if isinstance(value, Decimal):
        return {"t": "decimal", "v": value}
    if isinstance(value, float):
        raise CanonicalSerializationError(
            "float is forbidden in canonical serialization; use Decimal"
        )
    if isinstance(value, int):
        return {"t": "int", "v": value}
    if isinstance(value, datetime):
        return {"t": "datetime", "v": value}
    if isinstance(value, (list, tuple)):
        return {"t": "list", "v": [_type_tag(item) for item in value]}
    if isinstance(value, (set, frozenset)):
        tagged = [_type_tag(item) for item in value]
        tagged.sort(key=canonicalize)
        return {"t": "set", "v": tagged}
    if isinstance(value, dict):
        return {"t": "dict", "v": {key: _type_tag(item) for key, item in value.items()}}
    raise CanonicalSerializationError(
        f"unsupported type for canonical serialization: {type(value).__name__}"
    )


def typed_content_hash(
    payload: Any,
    *,
    object_type: str,
    schema_id: Optional[str] = None,
    schema_version: Optional[str] = None,
) -> str:
    """Return the type-bound content hash used for traces, replays, and pins.

    Wraps a leaf-type-annotated mirror of ``payload`` in a canonical envelope that also
    embeds the profile id/version, the declared ``object_type`` and optional schema
    id/version, then hashes the envelope. Because every leaf carries its type, two
    payloads with the same raw canonical bytes but different value types (e.g.
    ``Decimal('1.0')`` vs the string ``'1.0'``) hash differently even under identical
    object_type/schema metadata — closing the collision in :func:`content_hash`.
    """
    if not isinstance(object_type, str) or not object_type:
        raise CanonicalSerializationError("object_type must be a non-empty string")
    envelope = {
        "profile_id": CANONICAL_PROFILE_ID,
        "profile_version": CANONICAL_PROFILE_VERSION,
        "object_type": object_type,
        "schema_id": schema_id,
        "schema_version": schema_version,
        "payload": _type_tag(payload),
    }
    return content_hash(envelope)


def fixture_corpus_sha256() -> str:
    """Return the newline-normalized SHA-256 of the conformance fixture corpus.

    Bound into the profile descriptor so a behavior change that alters declared
    conformance flips the profile hash (append-only discipline covers behavior, not
    just descriptor strings).
    """
    text = _FIXTURE_CORPUS_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def profile_descriptor() -> dict:
    """Return the append-only, hashable descriptor of this profile (v1)."""
    return {
        "profile_id": CANONICAL_PROFILE_ID,
        "version": CANONICAL_PROFILE_VERSION,
        "kind": "canonical_serialization",
        "unicode_version": PINNED_UNICODE_VERSION,
        "normalization_form": PINNED_NORMALIZATION_FORM,
        "timestamp_precision": TIMESTAMP_PRECISION,
        "fixture_corpus_sha256": fixture_corpus_sha256(),
        "rules": [
            "float-forbidden",
            "decimal-as-fixed-point-string-preserving-scale",
            "negative-zero-normalized",
            "int-as-bare-number",
            "bool-distinct-from-int",
            "str-nfc-then-json-escaped",
            "datetime-utc-aware-microsecond-or-reject",
            "dict-keys-str-nfc-utf8-byte-order",
            "dict-duplicate-after-nfc-rejected",
            "set-as-list-sorted-by-element-utf8-bytes",
            "list-order-significant",
            "null-retained-distinct-from-absent-and-empty",
            "compact-utf8-no-insignificant-whitespace",
            "unicode-version-pinned-fail-closed",
            "typed-envelope-hash-binds-object-type-and-schema",
            "typed-envelope-uses-leaf-type-tags",
        ],
    }


def profile_hash() -> str:
    """Return the content-addressable hash of this profile's descriptor."""
    return content_hash(profile_descriptor())
