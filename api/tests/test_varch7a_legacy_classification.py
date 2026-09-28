"""VARCH-7a contract tests — legacy value classification + deterministic defaults.

Layer 1 (pure core): deterministic 4-state classification (classified / auto_classified_pending_
review / legacy_grandfathered / not_dimension_required); deterministic legacy valid/decision/
sunset defaults; content-addressed classification hash.
Layer 2 (disposable-DB smoke): migration 041 applies and the legacy_value_classifications CHECK
constraints (state enum, grandfathered<=>sunset) hold. Gated on the disposable PG env vars.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from src.semantic import write_replay
from src.semantic.legacy_dimensions import (
    CLASS_AUTO_PENDING,
    CLASS_CLASSIFIED,
    CLASS_GRANDFATHERED,
    CLASS_NOT_REQUIRED,
    REVIEW_NONE,
    REVIEW_QUEUED,
    LegacyClassificationError,
    LegacyDefaults,
    classify_legacy_value,
    deterministic_legacy_defaults,
)

_DEFAULTS = LegacyDefaults(
    valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
    decision_commit_id=1,
    sunset_date=date(2031, 1, 1),
)
_ASSIGN = [("ax:fuel", "diesel"), ("ax:scope", "s1")]


def _classify(**over):
    kwargs = dict(
        contract_requires_dimensions=True,
        candidate_assignment=_ASSIGN,
        confidence="0.9",
        confidence_threshold="0.8",
        defaults=_DEFAULTS,
    )
    kwargs.update(over)
    return classify_legacy_value(**kwargs)


# --- classification decision tree -----------------------------------------------------


def test_not_dimension_required() -> None:
    c = _classify(contract_requires_dimensions=False)
    assert c.state == CLASS_NOT_REQUIRED
    assert c.assigned_dimensions_hash is None and c.sunset_date is None
    assert c.review_status == REVIEW_NONE


def test_classified_at_or_above_threshold() -> None:
    c = _classify(confidence="0.8", confidence_threshold="0.8")
    assert c.state == CLASS_CLASSIFIED
    assert c.assigned_dimensions_hash == write_replay.canonical_json.content_hash(
        sorted(_ASSIGN)
    )
    assert c.sunset_date is None


def test_auto_pending_below_threshold() -> None:
    c = _classify(confidence="0.5", confidence_threshold="0.8")
    assert c.state == CLASS_AUTO_PENDING
    assert c.assigned_dimensions_hash is not None  # tentative assignment retained
    assert c.review_status == REVIEW_QUEUED  # enters the review queue


def test_grandfathered_when_no_candidate() -> None:
    c = _classify(candidate_assignment=None)
    assert c.state == CLASS_GRANDFATHERED
    assert c.sunset_date == date(2031, 1, 1)
    assert c.assigned_dimensions_hash is None


def test_grandfathered_when_empty_candidate() -> None:
    assert _classify(candidate_assignment=[]).state == CLASS_GRANDFATHERED


def test_classification_hash_deterministic_and_state_sensitive() -> None:
    a = _classify()
    b = _classify()
    assert a.classification_hash == b.classification_hash
    grand = _classify(candidate_assignment=None)
    assert a.classification_hash != grand.classification_hash


def test_confidence_is_decimal_not_float() -> None:
    assert _classify(confidence="0.81").confidence == Decimal("0.81")
    with pytest.raises(LegacyClassificationError):
        _classify(confidence=0.81)  # float forbidden by the computation profile


def test_confidence_out_of_unit_range_rejected() -> None:
    with pytest.raises(LegacyClassificationError, match="in \\[0, 1\\]"):
        _classify(confidence="1.5")
    with pytest.raises(LegacyClassificationError, match="in \\[0, 1\\]"):
        _classify(confidence_threshold="-0.1")


# --- deterministic defaults -----------------------------------------------------------


def test_defaults_from_period_fields() -> None:
    d = deterministic_legacy_defaults(
        period_start=date(2022, 4, 1),
        period_close_date=date(2023, 3, 31),
        legacy_decision_commit_id=7,
        grandfather_years=8,
    )
    assert d.valid_from == datetime(2022, 4, 1, tzinfo=timezone.utc)
    assert d.decision_commit_id == 7
    assert d.sunset_date == date(2031, 3, 31)  # close + 8 years


def test_defaults_sunset_falls_back_to_period_start() -> None:
    d = deterministic_legacy_defaults(
        period_start=date(2022, 1, 1),
        legacy_decision_commit_id=1,
        grandfather_years=5,
    )
    assert d.sunset_date == date(2027, 1, 1)


def test_defaults_sunset_leap_day_clamped() -> None:
    # M1: Feb 29 + non-leap target year must not raise; clamp to Feb 28.
    d = deterministic_legacy_defaults(
        period_start=date(2024, 1, 1),
        period_close_date=date(2024, 2, 29),
        legacy_decision_commit_id=1,
        grandfather_years=7,  # 2031 is not a leap year
    )
    assert d.sunset_date == date(2031, 2, 28)


def test_defaults_reject_nonpositive_decision() -> None:
    with pytest.raises(LegacyClassificationError, match="positive committed id"):
        deterministic_legacy_defaults(
            period_start=date(2022, 1, 1),
            legacy_decision_commit_id=0,
            grandfather_years=5,
        )


def test_defaults_reject_nonpositive_grandfather_years() -> None:
    with pytest.raises(LegacyClassificationError, match="grandfather_years"):
        deterministic_legacy_defaults(
            period_start=date(2022, 1, 1),
            legacy_decision_commit_id=1,
            grandfather_years=0,
        )


# --- Layer 2: disposable-DB smoke (migration 041 + CHECKs) ----------------------------


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
    init_db_for_engine(engine)  # migrations to head, incl 041
    return engine


def _insert_value_context(conn, ctx_id: int) -> int:
    conn.execute(
        text(
            "INSERT INTO value_contexts (tenant_id, context_hash_recipe_version, "
            "context_hash, entity_id, reporting_period_id, period_type, "
            "reporting_boundary_id, indicator_identifier, standard_release_id, "
            "standard_datapoint_id, value_kind) VALUES "
            "('t7a', 'v1', :h, 'e1', 'rp1', 'annual', 'rb1', 'ind1', 'rel1', 'dp1', "
            "'numeric')"
        ),
        {"h": f"h{ctx_id}"},
    )
    return conn.execute(
        text("SELECT id FROM value_contexts WHERE context_hash = :h"),
        {"h": f"h{ctx_id}"},
    ).scalar_one()


def test_disposable_db_classification_checks() -> None:
    engine = _disposable_engine()
    try:
        from sqlalchemy.exc import IntegrityError

        h = "a" * 64
        with engine.begin() as conn:
            vc1 = _insert_value_context(conn, 1)
            # classified row with no sunset is OK (classification_hash is mandatory).
            conn.execute(
                text(
                    "INSERT INTO legacy_value_classifications (id, value_context_id, "
                    "classification_state, confidence, contract_requires_dimensions, "
                    "default_valid_from, default_decision_commit_id, classification_hash) "
                    "VALUES ('lc1', :vc, 'classified', '0.9', true, now(), 1, :h)"
                ),
                {"vc": vc1, "h": h},
            )

        # grandfathered WITHOUT a sunset_date violates the CHECK.
        with engine.begin() as conn:
            vc2 = _insert_value_context(conn, 2)
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO legacy_value_classifications (id, value_context_id, "
                        "classification_state, confidence, contract_requires_dimensions, "
                        "default_valid_from, default_decision_commit_id, "
                        "classification_hash) VALUES "
                        "('lc2', :vc, 'legacy_grandfathered', '0.1', true, now(), 1, :h)"
                    ),
                    {"vc": vc2, "h": h},
                )

        # M2: classification_hash is mandatory (NOT NULL) for replayable content-addressing.
        with engine.begin() as conn:
            vc3 = _insert_value_context(conn, 3)
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO legacy_value_classifications (id, value_context_id, "
                        "classification_state, confidence, contract_requires_dimensions, "
                        "default_valid_from, default_decision_commit_id) VALUES "
                        "('lc3', :vc, 'classified', '0.9', true, now(), 1)"
                    ),
                    {"vc": vc3},
                )
    finally:
        engine.dispose()
