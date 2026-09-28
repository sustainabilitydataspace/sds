"""VARCH-8b contract tests — immutable bitemporal pagination cursor.

Pure core: encode/decode a tamper-evident cursor binding all ten contract fields; reject tamper /
wrong key / malformation; supersession status (pinned_ok vs superseded_stale, never silent
advance); cross-request replay guard. No HTTP/DB layer.
"""

from __future__ import annotations

import base64
import json

import pytest

from src.semantic.read_cursor import (
    CURSOR_BINDS,
    CURSOR_PINNED_OK,
    CURSOR_SUPERSEDED_STALE,
    BitemporalCursor,
    CursorError,
    assert_cursor_matches_request,
    cursor_content_hash,
    cursor_status,
    decode_cursor,
    encode_cursor,
)

_SECRET = b"x" * 32
_EVS = "a" * 64
_RM = "b" * 64


def _cursor(**over):
    kwargs = dict(
        endpoint="/api/v1/semantic-dimensions",
        query_filters={"taxonomy": "Sygris"},
        valid_time_selector="2026-01-01T00:00:00+00:00",
        decision_commit_id=10,
        effective_version_set_hash=_EVS,
        replay_manifest_hash=_RM,
        sort_keys=("uri",),
        page_boundary={"uri": "syg:WastePlastic"},
        authorization_scope_class="public",
        projection_version="cat:v1",
    )
    kwargs.update(over)
    return BitemporalCursor(**kwargs)


def _token(**over):
    return encode_cursor(_cursor(**over), secret=_SECRET, key_id="k1")


# --- binds + round trip ---------------------------------------------------------------


def test_binds_match_contract() -> None:
    assert CURSOR_BINDS == (
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


def test_encode_decode_round_trip() -> None:
    c = _cursor()
    got = decode_cursor(encode_cursor(c, secret=_SECRET, key_id="k1"), secret=_SECRET)
    assert got == c


def test_content_hash_deterministic() -> None:
    assert cursor_content_hash(_cursor()) == cursor_content_hash(_cursor())
    assert cursor_content_hash(_cursor()) != cursor_content_hash(
        _cursor(decision_commit_id=11)
    )


# --- tamper / key / malformation ------------------------------------------------------


def test_decode_rejects_wrong_secret() -> None:
    with pytest.raises(CursorError, match="signature verification failed"):
        decode_cursor(_token(), secret=b"y" * 32)


def test_decode_rejects_tampered_payload() -> None:
    token = _token()
    raw = json.loads(base64.urlsafe_b64decode(token).decode("utf-8"))
    raw["p"]["decision_commit_id"] = 999  # widen the read
    tampered = base64.urlsafe_b64encode(json.dumps(raw).encode("utf-8")).decode("ascii")
    with pytest.raises(CursorError, match="signature verification failed"):
        decode_cursor(tampered, secret=_SECRET)


def test_decode_rejects_garbage() -> None:
    with pytest.raises(CursorError, match="malformed cursor token"):
        decode_cursor("!!!not-base64-json!!!", secret=_SECRET)


def test_decode_rejects_extra_envelope_fields() -> None:
    # M1: an extra unsigned top-level field must NOT pass (HMAC covers only {key_id, payload}).
    token = _token()
    raw = json.loads(base64.urlsafe_b64decode(token).decode("utf-8"))
    raw["evil"] = "injected"  # unsigned junk
    doctored = base64.urlsafe_b64encode(json.dumps(raw).encode("utf-8")).decode("ascii")
    with pytest.raises(CursorError, match="envelope must be exactly"):
        decode_cursor(doctored, secret=_SECRET)


def test_decode_rejects_non_dict_payload() -> None:
    raw = {"k": "k1", "p": "not-a-dict", "s": "deadbeef"}
    doctored = base64.urlsafe_b64encode(json.dumps(raw).encode("utf-8")).decode("ascii")
    with pytest.raises(CursorError):
        decode_cursor(doctored, secret=_SECRET)


def test_encode_validates_fields() -> None:
    with pytest.raises(CursorError, match="decision_commit_id must be positive"):
        _token(decision_commit_id=0)
    with pytest.raises(CursorError, match="64-lowercase-hex"):
        _token(effective_version_set_hash="xyz")
    with pytest.raises(CursorError, match="authorization_scope_class is required"):
        _token(authorization_scope_class="")
    with pytest.raises(CursorError, match="projection_version is required"):
        _token(projection_version="")


def test_encode_requires_strong_secret_and_key_id() -> None:
    with pytest.raises(CursorError, match="secret must be"):
        encode_cursor(_cursor(), secret=b"short", key_id="k1")
    with pytest.raises(CursorError, match="key_id is required"):
        encode_cursor(_cursor(), secret=_SECRET, key_id="")


# --- supersession (never silent advance) ----------------------------------------------


def test_cursor_pinned_ok_when_hashes_match() -> None:
    assert (
        cursor_status(
            _cursor(),
            current_effective_version_set_hash=_EVS,
            current_replay_manifest_hash=_RM,
        )
        == CURSOR_PINNED_OK
    )


def test_cursor_superseded_when_evs_drifts() -> None:
    assert (
        cursor_status(
            _cursor(),
            current_effective_version_set_hash="c" * 64,
            current_replay_manifest_hash=_RM,
        )
        == CURSOR_SUPERSEDED_STALE
    )


def test_cursor_superseded_when_manifest_drifts() -> None:
    assert (
        cursor_status(
            _cursor(),
            current_effective_version_set_hash=_EVS,
            current_replay_manifest_hash="d" * 64,
        )
        == CURSOR_SUPERSEDED_STALE
    )


# --- cross-request replay guard -------------------------------------------------------


def test_cursor_matches_request_passes() -> None:
    assert_cursor_matches_request(
        _cursor(),
        endpoint="/api/v1/semantic-dimensions",
        authorization_scope_class="public",
        query_filters={"taxonomy": "Sygris"},
    )


def test_cursor_rejects_endpoint_mismatch() -> None:
    with pytest.raises(CursorError, match="endpoint"):
        assert_cursor_matches_request(
            _cursor(),
            endpoint="/api/v1/concepts",
            authorization_scope_class="public",
            query_filters={"taxonomy": "Sygris"},
        )


def test_cursor_rejects_scope_widening() -> None:
    with pytest.raises(CursorError, match="authorization_scope_class"):
        assert_cursor_matches_request(
            _cursor(),
            endpoint="/api/v1/semantic-dimensions",
            authorization_scope_class="tenant",
            query_filters={"taxonomy": "Sygris"},
        )


def test_cursor_rejects_filter_mismatch() -> None:
    with pytest.raises(CursorError, match="query_filters"):
        assert_cursor_matches_request(
            _cursor(),
            endpoint="/api/v1/semantic-dimensions",
            authorization_scope_class="public",
            query_filters={"taxonomy": "GRI"},
        )
