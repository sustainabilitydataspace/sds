"""Immutable bitemporal pagination cursor for public temporal reads (VARCH-8b).

The ratified ``sds:profile:public-temporal-read:v1`` contract requires an IMMUTABLE cursor that
BINDS exactly: endpoint, query_filters, valid_time_selector, decision_commit_id,
effective_version_set_hash, replay_manifest_hash, sort_keys, page_boundary,
authorization_scope_class, projection_version — and whose ``superseded_behavior`` is
``continue_pinned_or_return_stale_superseded_status`` (a cursor NEVER silently advances to latest).

The token is tamper-evident: an HMAC over the canonical-JSON payload (the
:mod:`src.semantic.profiles.canonical_json` profile, mirroring the ``export_signing`` precedent),
so a client cannot edit the bound decision_commit_id / scope class / hashes to widen a read. Decode
fails closed on any signature mismatch.

Gates contributed here (mechanism; CI wiring in VARCH-10): cursor-immutability-binding (all ten
fields bound + content-addressed), cursor-tamper-evidence (HMAC verify), cursor-no-silent-advance
(supersession check returns pinned/stale, never re-resolves to latest).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Mapping

from src.semantic.profiles import canonical_json

# the exact bound field set (must equal the contract cursor_contract.binds const).
CURSOR_BINDS = (
    "endpoint",
    "query_filters",
    "valid_time_selector",
    "decision_commit_id",
    "effective_version_set_hash",
    "replay_manifest_hash",
    "sort_keys",
    "page_boundary",
    "authorization_scope_class",
    "projection_version",
)

# supersession statuses (cursor never silently advances to latest).
CURSOR_PINNED_OK = "pinned_ok"
CURSOR_SUPERSEDED_STALE = "superseded_stale"

_MIN_SECRET_BYTES = 16


class CursorError(ValueError):
    """Raised when a bitemporal cursor is malformed, tampered, or inconsistent."""


@dataclass(frozen=True)
class BitemporalCursor:
    """The fully-bound bitemporal read cursor (one page boundary of a pinned read)."""

    endpoint: str
    query_filters: Mapping[str, Any]
    valid_time_selector: str | None
    decision_commit_id: int
    effective_version_set_hash: str
    replay_manifest_hash: str
    sort_keys: tuple[str, ...]
    page_boundary: Mapping[str, Any]
    authorization_scope_class: str
    projection_version: str


def _payload(cursor: BitemporalCursor) -> dict:
    """The canonical, deterministically-ordered bound payload (exactly CURSOR_BINDS)."""
    return {
        "endpoint": cursor.endpoint,
        "query_filters": dict(cursor.query_filters),
        "valid_time_selector": cursor.valid_time_selector,
        "decision_commit_id": cursor.decision_commit_id,
        "effective_version_set_hash": cursor.effective_version_set_hash,
        "replay_manifest_hash": cursor.replay_manifest_hash,
        "sort_keys": list(cursor.sort_keys),
        "page_boundary": dict(cursor.page_boundary),
        "authorization_scope_class": cursor.authorization_scope_class,
        "projection_version": cursor.projection_version,
    }


def cursor_content_hash(cursor: BitemporalCursor) -> str:
    """Content address of the cursor's full bound payload (immutability witness)."""
    return canonical_json.content_hash(_payload(cursor))


def _sign(message: bytes, secret: bytes) -> str:
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def encode_cursor(cursor: BitemporalCursor, *, secret: bytes, key_id: str) -> str:
    """Encode a tamper-evident, immutable cursor token (base64 of canonical payload + HMAC).

    Validates the cursor is complete (positive decision commit; hex EVS + manifest hashes; a
    non-empty scope class / projection version), binds ALL ten contract fields into the signed
    message, and returns an opaque URL-safe token.
    """
    if len(secret) < _MIN_SECRET_BYTES:
        raise CursorError(f"cursor secret must be >= {_MIN_SECRET_BYTES} bytes")
    if not key_id:
        raise CursorError("cursor key_id is required")
    if cursor.decision_commit_id <= 0:
        raise CursorError("cursor decision_commit_id must be positive")
    for name in ("effective_version_set_hash", "replay_manifest_hash"):
        if not _is_hex64(getattr(cursor, name)):
            raise CursorError(f"cursor {name} must be a 64-lowercase-hex hash")
    if not cursor.authorization_scope_class:
        raise CursorError("cursor authorization_scope_class is required")
    if not cursor.projection_version:
        raise CursorError("cursor projection_version is required")

    envelope = {"key_id": key_id, "payload": _payload(cursor)}
    message = canonical_json.canonicalize(envelope)
    signature = _sign(message, secret)
    token_obj = {"k": key_id, "p": _payload(cursor), "s": signature}
    raw = canonical_json.canonicalize(token_obj)
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(token: str, *, secret: bytes) -> BitemporalCursor:
    """Decode + verify a cursor token; fail closed on tamper or malformation."""
    if len(secret) < _MIN_SECRET_BYTES:
        raise CursorError(f"cursor secret must be >= {_MIN_SECRET_BYTES} bytes")
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        token_obj = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - any decode failure is a bad cursor
        raise CursorError(f"malformed cursor token: {exc}") from exc

    # the envelope must be EXACTLY {k, p, s} — extra/unsigned top-level fields are rejected so a
    # tampered envelope cannot pass (the HMAC covers only {key_id, payload}); fail closed.
    if not isinstance(token_obj, dict) or set(token_obj) != {"k", "p", "s"}:
        raise CursorError("cursor token envelope must be exactly {k, p, s}")
    key_id, payload, signature = token_obj["k"], token_obj["p"], token_obj["s"]
    if not isinstance(key_id, str) or not key_id:
        raise CursorError("cursor key_id must be a non-empty string")
    if not isinstance(signature, str) or not signature:
        raise CursorError("cursor signature must be a non-empty string")
    if not isinstance(payload, dict):
        raise CursorError("cursor payload must be an object")

    # recompute the signature over the SAME canonical envelope and compare in constant time.
    envelope = {"key_id": key_id, "payload": payload}
    expected = _sign(canonical_json.canonicalize(envelope), secret)
    if not hmac.compare_digest(expected, signature):
        raise CursorError(
            "cursor signature verification failed (tampered or wrong key)"
        )

    if set(payload) != set(CURSOR_BINDS):
        raise CursorError(
            f"cursor payload fields {sorted(payload)} != bound contract fields "
            f"{sorted(CURSOR_BINDS)}"
        )
    return BitemporalCursor(
        endpoint=payload["endpoint"],
        query_filters=payload["query_filters"],
        valid_time_selector=payload["valid_time_selector"],
        decision_commit_id=payload["decision_commit_id"],
        effective_version_set_hash=payload["effective_version_set_hash"],
        replay_manifest_hash=payload["replay_manifest_hash"],
        sort_keys=tuple(payload["sort_keys"]),
        page_boundary=payload["page_boundary"],
        authorization_scope_class=payload["authorization_scope_class"],
        projection_version=payload["projection_version"],
    )


def cursor_status(
    cursor: BitemporalCursor,
    *,
    current_effective_version_set_hash: str,
    current_replay_manifest_hash: str,
) -> str:
    """Supersession check: never silently advance to latest.

    Returns ``pinned_ok`` when the cursor's bound EVS + manifest hashes still match the current
    published slice (the read continues on the pinned ``decision_commit_id``), or
    ``superseded_stale`` when either has drifted (the underlying publication was superseded). The
    caller either continues on the pinned decision commit or surfaces the stale/superseded status —
    it MUST NOT re-resolve the cursor to the latest state.
    """
    if (
        cursor.effective_version_set_hash == current_effective_version_set_hash
        and cursor.replay_manifest_hash == current_replay_manifest_hash
    ):
        return CURSOR_PINNED_OK
    return CURSOR_SUPERSEDED_STALE


def assert_cursor_matches_request(
    cursor: BitemporalCursor,
    *,
    endpoint: str,
    authorization_scope_class: str,
    query_filters: Mapping[str, Any],
) -> None:
    """Reject a cursor replayed against a different endpoint / scope class / query filters.

    A cursor minted for one (endpoint, scope class, filters) cannot be reused on another — this
    prevents widening a read by carrying a public cursor into a tenant request, or vice versa.
    """
    if cursor.endpoint != endpoint:
        raise CursorError(
            f"cursor endpoint {cursor.endpoint!r} != request endpoint {endpoint!r}"
        )
    if cursor.authorization_scope_class != authorization_scope_class:
        raise CursorError(
            "cursor authorization_scope_class does not match the request scope class"
        )
    if dict(cursor.query_filters) != dict(query_filters):
        raise CursorError("cursor query_filters do not match the request filters")


_HEX = frozenset("0123456789abcdef")


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in _HEX for c in value)
