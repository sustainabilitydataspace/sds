"""VARCH-0: conformance for sds-canonical-json-v1 deterministic serialization.

Covers the agreed adversarial corpus (backlog ACC-1..ACC-6, ACC-12):
NFC drift, decimal scale / integer-vs-decimal / negative-zero / large / small,
non-ASCII key ordering, duplicate-key-after-NFC rejection, null/absent/empty
distinctness, nested set ordering, timestamp precision / naive rejection.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.semantic.profiles import canonical_json as cj


def test_dict_key_order_and_nested_set_are_stable_under_reordering():
    a = {"b": Decimal("1.50"), "a": [1, 2, {3, 1, 2}], "z": None}
    b = {"z": None, "a": [1, 2, {2, 1, 3}], "b": Decimal("1.50")}
    assert cj.content_hash(a) == cj.content_hash(b)


def test_float_is_forbidden():
    with pytest.raises(cj.CanonicalSerializationError):
        cj.canonicalize(1.5)
    with pytest.raises(cj.CanonicalSerializationError):
        cj.canonicalize({"x": [Decimal("1"), 2.0]})


def test_decimal_scale_is_semantic_and_hash_distinct():
    assert cj.canonicalize(Decimal("1.0")) == b'"1.0"'
    assert cj.canonicalize(Decimal("1.00")) == b'"1.00"'
    assert cj.content_hash(Decimal("1.0")) != cj.content_hash(Decimal("1.00"))


def test_decimal_no_scientific_notation_for_large_and_small():
    assert cj.canonicalize(Decimal("1E+9")) == b'"1000000000"'
    assert cj.canonicalize(Decimal("1.5E-3")) == b'"0.0015"'


def test_negative_zero_normalizes_but_preserves_scale():
    assert cj.canonicalize(Decimal("-0")) == b'"0"'
    assert cj.canonicalize(Decimal("-0.00")) == b'"0.00"'


def test_nan_and_infinity_decimals_rejected():
    for bad in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
        with pytest.raises(cj.CanonicalSerializationError):
            cj.canonicalize(bad)


def test_int_is_bare_number_and_bool_is_distinct():
    assert cj.canonicalize(5) == b"5"
    assert cj.canonicalize(True) == b"true"
    assert cj.canonicalize(False) == b"false"
    # bool must not collapse to int 1/0
    assert cj.content_hash(True) != cj.content_hash(1)
    assert cj.content_hash(False) != cj.content_hash(0)


def test_nfc_equivalent_strings_hash_identically():
    composed = "é"  # é precomposed
    decomposed = "é"  # e + combining acute
    assert composed != decomposed
    assert unicodedata.normalize("NFC", decomposed) == composed
    assert cj.content_hash({"k": composed}) == cj.content_hash({"k": decomposed})


def test_non_ascii_keys_sort_by_utf8_bytes():
    # "z" (0x7a) sorts before "ä" (0xc3 0xa4 in utf-8)
    out = cj.canonicalize({"ä": 1, "z": 2})
    assert out == '{"z":2,"ä":1}'.encode("utf-8")


def test_duplicate_keys_after_nfc_are_rejected():
    composed = "é"
    decomposed = "é"
    with pytest.raises(cj.CanonicalSerializationError):
        cj.canonicalize({composed: 1, decomposed: 2})


def test_non_string_keys_rejected():
    with pytest.raises(cj.CanonicalSerializationError):
        cj.canonicalize({1: "a"})


def test_null_absent_empty_are_all_distinct():
    h_null = cj.content_hash({"a": None})
    h_absent = cj.content_hash({})
    h_empty_str = cj.content_hash({"a": ""})
    h_empty_list = cj.content_hash({"a": []})
    h_empty_obj = cj.content_hash({"a": {}})
    assert len({h_null, h_absent, h_empty_str, h_empty_list, h_empty_obj}) == 5


def test_list_order_is_significant_but_set_order_is_not():
    assert cj.content_hash([1, 2, 3]) != cj.content_hash([3, 2, 1])
    assert cj.content_hash({1, 2, 3}) == cj.content_hash({3, 2, 1})


def test_datetime_normalized_to_utc_microseconds():
    aware = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc)
    assert cj.canonicalize(aware) == b'"2026-01-02T03:04:05.678901Z"'


def test_naive_datetime_rejected():
    with pytest.raises(cj.CanonicalSerializationError):
        cj.canonicalize(datetime(2026, 1, 1))


def test_unsupported_type_rejected():
    with pytest.raises(cj.CanonicalSerializationError):
        cj.canonicalize(object())


def test_unicode_drift_fails_closed(monkeypatch):
    monkeypatch.setattr(cj.unicodedata, "unidata_version", "99.0.0")
    with pytest.raises(cj.CanonicalSerializationError):
        cj.canonicalize({"a": 1})


def test_profile_descriptor_and_hash_are_consistent():
    descriptor = cj.profile_descriptor()
    assert descriptor["profile_id"] == "sds-canonical-json-v1"
    assert cj.profile_hash() == cj.content_hash(descriptor)


def test_raw_content_hash_collides_across_types_by_design():
    # Documented limitation that motivates typed_content_hash.
    assert cj.content_hash(Decimal("1.0")) == cj.content_hash("1.0")


def test_typed_content_hash_distinguishes_types_and_object_types():
    # Same canonical bytes, different declared object types -> different hash.
    as_quantity = cj.typed_content_hash(Decimal("1.0"), object_type="quantity")
    as_label = cj.typed_content_hash("1.0", object_type="label")
    assert as_quantity != as_label
    # Same payload + same object_type is stable.
    assert as_quantity == cj.typed_content_hash(Decimal("1.0"), object_type="quantity")
    # schema id/version participate in the binding.
    assert (
        cj.typed_content_hash(
            Decimal("1.0"), object_type="quantity", schema_id="s", schema_version="1"
        )
        != as_quantity
    )


def test_typed_content_hash_is_leaf_type_injective_under_identical_metadata():
    # The exact collision codex flagged: identical object_type + schema, different
    # leaf type. Leaf type tags must keep these distinct.
    as_decimal = cj.typed_content_hash(
        Decimal("1.0"), object_type="x", schema_id="s", schema_version="1"
    )
    as_string = cj.typed_content_hash(
        "1.0", object_type="x", schema_id="s", schema_version="1"
    )
    assert as_decimal != as_string
    # datetime vs look-alike string also stay distinct.
    ts = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc)
    assert cj.typed_content_hash(ts, object_type="x") != cj.typed_content_hash(
        "2026-01-02T03:04:05.678901Z", object_type="x"
    )


def test_typed_content_hash_nested_leaf_types_are_distinct():
    a = cj.typed_content_hash({"v": Decimal("1.0")}, object_type="x")
    b = cj.typed_content_hash({"v": "1.0"}, object_type="x")
    assert a != b
    # set membership stays order-independent under typed hashing.
    assert cj.typed_content_hash({1, 2, 3}, object_type="x") == cj.typed_content_hash(
        {3, 2, 1}, object_type="x"
    )


def test_typed_content_hash_handles_list_and_rejects_float_and_unsupported():
    # list path + int/null leaves exercised
    assert cj.typed_content_hash(
        [1, None, True], object_type="x"
    ) == cj.typed_content_hash([1, None, True], object_type="x")
    with pytest.raises(cj.CanonicalSerializationError):
        cj.typed_content_hash([1.5], object_type="x")
    with pytest.raises(cj.CanonicalSerializationError):
        cj.typed_content_hash(object(), object_type="x")


def test_typed_content_hash_requires_object_type():
    with pytest.raises(cj.CanonicalSerializationError):
        cj.typed_content_hash(Decimal("1.0"), object_type="")


def test_fixture_corpus_is_bound_into_descriptor():
    descriptor = cj.profile_descriptor()
    assert descriptor["fixture_corpus_sha256"] == cj.fixture_corpus_sha256()
    assert len(descriptor["fixture_corpus_sha256"]) == 64
