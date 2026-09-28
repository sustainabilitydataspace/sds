"""VARCH-4e contract tests — disclosure suppression pins + deterministic outcomes.

Two layers: (1) the pure deterministic core (no DB) — k-anonymity + complementary suppression,
deterministic replay, profile-pin drift, uniform redaction (trace-existence side channel), and
the longitudinal-differencing guard; (2) a disposable-DB smoke proving the disclosure profile
pin matches the persisted ratified schema (skipped unless SDS_MIGRATION_TEST_DATABASE_URL +
SDS_MIGRATION_TEST_ALLOW_RESET=true).
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

from src.semantic.profiles import registry
from src.semantic.write_disclosure import (
    DISCLOSURE_PROFILE_ID,
    REASON_BELOW_K,
    REASON_COMPLEMENTARY,
    REDACTION_MARKER,
    Cell,
    DisclosureError,
    DisclosureProfilePin,
    assert_disclosure_profile_pinned,
    assert_suppression_stable,
    decide_suppression,
    redact_projection,
)


def _pin() -> DisclosureProfilePin:
    e = registry.list_profiles()[DISCLOSURE_PROFILE_ID]
    return DisclosureProfilePin(DISCLOSURE_PROFILE_ID, e["version"], e["hash"])


def _decide(cells, k=5, **over):
    return decide_suppression(cells, k_threshold=k, profile_pin=_pin(), **over)


# --- Layer 1: pure core --------------------------------------------------------------


def test_below_k_cells_are_suppressed() -> None:
    cells = [Cell("a", 3, "g"), Cell("b", 10, "g"), Cell("c", 20, "g")]
    res = _decide(cells)
    assert res.suppressed_keys() == {
        "a",
        "b",
    }  # a below k; b complementary (group of 3)


def test_complementary_suppression_protects_group_total() -> None:
    # Only one cell below k in a 3-cell group -> a second (smallest remaining) is suppressed.
    cells = [Cell("a", 2, "g"), Cell("b", 50, "g"), Cell("c", 80, "g")]
    res = _decide(cells)
    keys = res.suppressed_keys()
    assert "a" in keys and "b" in keys and "c" not in keys
    by = {d.cell_key: d.reason for d in res.decisions}
    assert by["a"] == REASON_BELOW_K and by["b"] == REASON_COMPLEMENTARY


def test_no_complementary_when_two_already_suppressed() -> None:
    cells = [Cell("a", 1, "g"), Cell("b", 2, "g"), Cell("c", 99, "g")]
    res = _decide(cells)
    assert res.suppressed_keys() == {"a", "b"}  # already >=2 suppressed; c stays


def test_singleton_group_flagged_and_total_non_publishable() -> None:
    res = _decide([Cell("solo", 1, "g")])
    assert res.suppressed_keys() == {"solo"}
    assert res.decisions[0].reason == "below_k_singleton"
    # M2: machine-checkable — the group's total must not be published.
    assert res.non_publishable_group_totals == frozenset({"g"})


def test_complemented_group_total_is_publishable() -> None:
    # A properly complemented (>=2 suppressed) multi-cell group can publish its total.
    res = _decide([Cell("a", 2, "g"), Cell("b", 50, "g"), Cell("c", 80, "g")])
    assert res.non_publishable_group_totals == frozenset()


def test_partition_contract_is_declared() -> None:
    from src.semantic.write_disclosure import PARTITION_CONTRACT

    res = _decide([Cell("a", 2, "g"), Cell("b", 9, "g")])
    assert res.partition_contract == PARTITION_CONTRACT == "disjoint_groups"


def test_redact_projection_strict_domain() -> None:
    res = _decide([Cell("a", 2, "g"), Cell("b", 50, "g"), Cell("c", 80, "g")])
    with pytest.raises(DisclosureError, match="exactly the decision domain"):
        redact_projection(res, {"a": 2, "b": 50})  # missing 'c'
    with pytest.raises(DisclosureError, match="exactly the decision domain"):
        redact_projection(res, {"a": 2, "b": 50, "c": 80, "x": 1})  # extra 'x'


def test_suppression_is_deterministic_replay() -> None:
    cells = [Cell("c", 2, "g"), Cell("a", 99, "g"), Cell("b", 80, "g")]
    r1 = _decide(cells)
    r2 = _decide(list(reversed(cells)))
    assert r1.decisions == r2.decisions  # order-independent, sorted by cell_key


def test_rejects_duplicate_cell_key() -> None:
    with pytest.raises(DisclosureError, match="duplicate cell_key"):
        _decide([Cell("a", 1, "g"), Cell("a", 2, "g")])


def test_rejects_bad_k_threshold() -> None:
    with pytest.raises(DisclosureError, match="k_threshold must be"):
        _decide([Cell("a", 1, "g")], k=0)


def test_profile_pin_drift() -> None:
    good = _pin()
    assert_disclosure_profile_pinned(good)
    with pytest.raises(DisclosureError, match="hash drift"):
        assert_disclosure_profile_pinned(
            DisclosureProfilePin(good.profile_id, good.version, "0" * 64)
        )
    with pytest.raises(DisclosureError, match="version drift"):
        assert_disclosure_profile_pinned(
            DisclosureProfilePin(good.profile_id, "v9", good.hash)
        )
    with pytest.raises(DisclosureError, match="unknown disclosure profile"):
        assert_disclosure_profile_pinned(DisclosureProfilePin("nope", "v1", "x"))


def test_redaction_is_uniform_no_side_channel() -> None:
    cells = [Cell("a", 2, "g"), Cell("b", 4, "g"), Cell("c", 99, "g")]
    res = _decide(cells)
    proj = redact_projection(res, {"a": 2, "b": 4, "c": 99})
    # both suppressed cells get the SAME marker; neither count nor reason leaks.
    assert proj["a"] == REDACTION_MARKER and proj["b"] == REDACTION_MARKER
    assert proj["c"] == 99
    assert "reason" not in proj["a"] and "count" not in proj["a"]


def test_longitudinal_differencing_guard() -> None:
    cells_a = [Cell("a", 2, "g"), Cell("b", 50, "g"), Cell("c", 80, "g")]
    prev = _decide(cells_a)
    # next release: 'a' now has a publishable count -> would un-suppress -> differencing leak.
    cells_b = [Cell("a", 50, "g"), Cell("b", 50, "g"), Cell("c", 80, "g")]
    curr = _decide(cells_b)
    with pytest.raises(DisclosureError, match="differencing"):
        assert_suppression_stable(prev, curr)
    # allowed when the cell is explicitly restated.
    assert_suppression_stable(prev, curr, restated_cells=frozenset({"a", "b"}))


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


def test_disposable_db_disclosure_pin_matches_seed() -> None:
    engine = _disposable_engine()
    try:
        with engine.begin() as conn:
            schema_hash = conn.execute(
                text(
                    "SELECT schema_hash FROM contract_schema_versions WHERE schema_id = :s"
                ),
                {"s": DISCLOSURE_PROFILE_ID},
            ).scalar_one()
        # A pin built from the PERSISTED seed hash validates under 4e; a tamper fails.
        assert_disclosure_profile_pinned(
            DisclosureProfilePin(DISCLOSURE_PROFILE_ID, "v1", schema_hash)
        )
        assert schema_hash == registry.profile_hashes()[DISCLOSURE_PROFILE_ID]
        with pytest.raises(DisclosureError, match="hash drift"):
            assert_disclosure_profile_pinned(
                DisclosureProfilePin(DISCLOSURE_PROFILE_ID, "v1", "f" * 64)
            )
    finally:
        engine.dispose()
