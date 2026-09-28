"""VARCH-4a contract tests — bitemporal temporal write-integrity core.

Two layers: (1) the pure deterministic core (no DB) for window integrity, published-row
immutability, and successor-publication planning; (2) a disposable-DB smoke that proves
the planner agrees with the live DB backstop — the same atomic supersede+insert the
planner emits is ACCEPTED by the 033 append-only trigger and the published-only
no-overlap EXCLUDE on ``semantic_axes``, while an illegal column mutation is REJECTED by
both ``assert_published_immutable`` and the trigger (skipped unless
SDS_MIGRATION_TEST_DATABASE_URL + SDS_MIGRATION_TEST_ALLOW_RESET=true).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

from src.semantic.write_temporal import (
    MUTABLE_ON_SUPERSEDE_PUBLICATION,
    MUTABLE_ON_SUPERSEDE_VERSIONED,
    STATUS_PUBLISHED,
    STATUS_SUPERSEDED,
    InsertOp,
    ProposedVersion,
    PublishedVersion,
    SuccessorPlan,
    SupersedeOp,
    WriteValidationError,
    assert_published_immutable,
    plan_successor_publication,
    validate_temporal_window,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 1, tzinfo=timezone.utc)
T2 = datetime(2026, 9, 1, tzinfo=timezone.utc)


# --- Layer 1: pure core --------------------------------------------------------------


def test_window_integrity_rejects_naive_datetime() -> None:
    bad = ProposedVersion("v1", "k", datetime(2026, 1, 1), 1)  # naive
    with pytest.raises(WriteValidationError, match="timezone-aware"):
        validate_temporal_window(bad)


def test_window_integrity_rejects_inverted_interval() -> None:
    bad = ProposedVersion("v1", "k", valid_from=T1, valid_to=T0, decision_commit_id=1)
    with pytest.raises(WriteValidationError, match="strictly after"):
        validate_temporal_window(bad)


def test_window_integrity_accepts_open_and_closed() -> None:
    validate_temporal_window(ProposedVersion("v1", "k", T0, 1))  # open valid_to
    validate_temporal_window(ProposedVersion("v1", "k", T0, 1, valid_to=T1))


def test_immutability_allows_only_status_and_pointer() -> None:
    old = {
        "status": STATUS_PUBLISHED,
        "axis_key": "k",
        "valid_to": None,
        "superseded_by_version_id": None,
    }
    new = {
        "status": STATUS_SUPERSEDED,
        "axis_key": "k",
        "valid_to": None,
        "superseded_by_version_id": "v2",
    }
    assert_published_immutable(old, new)  # legal supersede


def test_immutability_rejects_valid_to_edit() -> None:
    # valid_to is immutable: supersession is decision-time, never valid-time abutment.
    old = {
        "status": STATUS_PUBLISHED,
        "valid_to": None,
        "superseded_by_version_id": None,
    }
    new = {
        "status": STATUS_SUPERSEDED,
        "valid_to": T1,
        "superseded_by_version_id": "v2",
    }
    with pytest.raises(WriteValidationError, match="immutable fields changed"):
        assert_published_immutable(old, new)


def test_immutability_rejects_payload_edit() -> None:
    old = {
        "status": STATUS_PUBLISHED,
        "axis_key": "k",
        "superseded_by_version_id": None,
    }
    new = {
        "status": STATUS_SUPERSEDED,
        "axis_key": "OTHER",
        "superseded_by_version_id": "v2",
    }
    with pytest.raises(WriteValidationError, match="immutable fields changed"):
        assert_published_immutable(old, new)


def test_immutability_requires_superseded_status_and_pointer() -> None:
    old = {"status": STATUS_PUBLISHED, "superseded_by_version_id": None}
    with pytest.raises(WriteValidationError, match="only transition to 'superseded'"):
        assert_published_immutable(
            old, {"status": STATUS_PUBLISHED, "superseded_by_version_id": "v2"}
        )
    with pytest.raises(WriteValidationError, match="successor pointer"):
        assert_published_immutable(
            old, {"status": STATUS_SUPERSEDED, "superseded_by_version_id": None}
        )


def test_immutability_rejects_already_superseded_row() -> None:
    old = {"status": STATUS_SUPERSEDED, "superseded_by_version_id": "v2"}
    new = {"status": STATUS_SUPERSEDED, "superseded_by_version_id": "v3"}
    with pytest.raises(WriteValidationError, match="immutable once"):
        assert_published_immutable(old, new)


def test_plan_first_version_is_insert_only() -> None:
    proposed = ProposedVersion("v1", "k", T0, decision_commit_id=10)
    plan = plan_successor_publication(None, proposed)
    assert plan.is_first_version
    assert plan == SuccessorPlan(insert=InsertOp(proposed), supersede=None)


def test_plan_successor_emits_supersede_then_insert() -> None:
    current = PublishedVersion("v1", "k", T0, decision_commit_id=10)
    proposed = ProposedVersion("v2", "k", T1, decision_commit_id=11)
    plan = plan_successor_publication(current, proposed)
    assert not plan.is_first_version
    assert plan.insert == InsertOp(proposed)
    assert plan.supersede == SupersedeOp("v1", STATUS_SUPERSEDED, "v2")
    # the supersede op carries NO valid_to (decision-time, not valid-time abutment).
    assert not hasattr(plan.supersede, "new_valid_to")


def test_plan_rejects_non_increasing_decision_commit() -> None:
    current = PublishedVersion("v1", "k", T0, decision_commit_id=11)
    proposed = ProposedVersion("v2", "k", T1, decision_commit_id=11)
    with pytest.raises(WriteValidationError, match="reproducibility"):
        plan_successor_publication(current, proposed)


def test_plan_rejects_non_published_predecessor() -> None:
    current = PublishedVersion("v1", "k", T0, 10, status=STATUS_SUPERSEDED)
    proposed = ProposedVersion("v2", "k", T1, decision_commit_id=11)
    with pytest.raises(WriteValidationError, match="non-published predecessor"):
        plan_successor_publication(current, proposed)


def test_plan_rejects_logical_key_mismatch() -> None:
    current = PublishedVersion("v1", "k", T0, 10)
    proposed = ProposedVersion("v2", "OTHER", T1, decision_commit_id=11)
    with pytest.raises(WriteValidationError, match="logical_key"):
        plan_successor_publication(current, proposed)


def test_plan_rejects_overlap_with_published_sibling() -> None:
    current = PublishedVersion("v1", "k", T0, 10)
    proposed = ProposedVersion("v2", "k", T1, decision_commit_id=11, valid_to=T2)
    sibling = PublishedVersion(
        "sX", "k", valid_from=T1, valid_to=None, decision_commit_id=5
    )
    with pytest.raises(WriteValidationError, match="overlaps published sibling"):
        plan_successor_publication(current, proposed, published_siblings=[sibling])


def test_plan_excludes_predecessor_from_overlap_check() -> None:
    # The predecessor may overlap because the same-tx supersede removes it from published.
    current = PublishedVersion("v1", "k", T0, decision_commit_id=10)  # open window
    proposed = ProposedVersion("v2", "k", T0, decision_commit_id=11)  # same valid_from
    plan = plan_successor_publication(current, proposed, published_siblings=[current])
    assert plan.supersede == SupersedeOp("v1", STATUS_SUPERSEDED, "v2")


def test_immutability_requires_full_row_images() -> None:
    # M4: a partial patch dict (missing axis_key) would misclassify omitted columns as
    # unchanged; the trigger compares whole rows, so the core requires full row images.
    old = {
        "status": STATUS_PUBLISHED,
        "axis_key": "k",
        "superseded_by_version_id": None,
    }
    new = {"status": STATUS_SUPERSEDED, "superseded_by_version_id": "v2"}
    with pytest.raises(WriteValidationError, match="full row images"):
        assert_published_immutable(old, new)


def test_immutability_publication_chain_mutable_set() -> None:
    # M3: the semantic_publication_chains family mutates status + superseded_by_commit_id.
    old = {
        "status": STATUS_PUBLISHED,
        "subject_id": "s",
        "valid_to": None,
        "superseded_by_commit_id": None,
    }
    new = {
        "status": STATUS_SUPERSEDED,
        "subject_id": "s",
        "valid_to": None,
        "superseded_by_commit_id": 99,
    }
    assert_published_immutable(
        old, new, mutable_fields=MUTABLE_ON_SUPERSEDE_PUBLICATION
    )  # legal


def test_immutability_publication_chain_rejects_valid_to_edit() -> None:
    # M3: valid_to is immutable on the publication-chain family too.
    old = {
        "status": STATUS_PUBLISHED,
        "valid_to": None,
        "superseded_by_commit_id": None,
    }
    new = {"status": STATUS_SUPERSEDED, "valid_to": T1, "superseded_by_commit_id": 99}
    with pytest.raises(WriteValidationError, match="immutable fields changed"):
        assert_published_immutable(
            old, new, mutable_fields=MUTABLE_ON_SUPERSEDE_PUBLICATION
        )


def test_immutability_publication_chain_requires_commit_pointer() -> None:
    # M3: under the publication mutable set, the commit pointer must be set on supersede.
    old = {"status": STATUS_PUBLISHED, "superseded_by_commit_id": None}
    new = {"status": STATUS_SUPERSEDED, "superseded_by_commit_id": None}
    with pytest.raises(WriteValidationError, match="successor pointer"):
        assert_published_immutable(
            old, new, mutable_fields=MUTABLE_ON_SUPERSEDE_PUBLICATION
        )


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
    init_db_for_engine(engine)  # migrations to head, incl 039 genesis decision commit
    return engine


_HASH = "a" * 64


def _genesis_commit(conn):
    """The seeded genesis decision commit (epoch/fence/commit) from migration 039."""
    return conn.execute(
        text(
            "SELECT commit_id, epoch_number, fence_token "
            "FROM decision_commit_sequence ORDER BY commit_id LIMIT 1"
        )
    ).one()


def test_disposable_db_atomic_overlapping_restatement() -> None:
    # M1: the central VARCH-4a contract — an atomic decision-time restatement over an
    # OVERLAPPING valid window (v2 restates v1's identical [T0, open) window under a later
    # commit). This is only executable because migration 040 made the
    # superseded_by_version_id self-FK DEFERRABLE INITIALLY DEFERRED: the supersede UPDATE
    # sets the pointer to a successor that does not exist until the INSERT later in the
    # same transaction (checked at COMMIT). The published-only EXCLUDE is satisfied because
    # the supersede flips v1 out of 'published' before v2 is inserted.
    engine = _disposable_engine()
    try:
        from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError

        db_errors = (IntegrityError, InternalError, ProgrammingError)

        with engine.begin() as conn:
            genesis = _genesis_commit(conn)
            conn.execute(
                text(
                    "INSERT INTO semantic_stewards (id, steward_id, role) "
                    "VALUES ('t4a-steward', 't4a-steward', 'author')"
                )
            )
            # A successor decision commit (strictly later than genesis).
            c2 = conn.execute(
                text(
                    "INSERT INTO decision_commit_sequence "
                    "(epoch_number, fence_token, previous_commit_id, commit_hash) "
                    "VALUES (:e, :f, :prev, :h) RETURNING commit_id"
                ),
                {
                    "e": genesis.epoch_number,
                    "f": genesis.fence_token,
                    "prev": genesis.commit_id,
                    "h": "b" * 64,
                },
            ).scalar_one()

            # v1: first published version of an axis (open valid window).
            conn.execute(
                text(
                    "INSERT INTO semantic_axes "
                    "(id, axis_key, axis_version, valid_from, status, created_by, "
                    " decision_commit_id, axis_hash) VALUES "
                    "('t4a-ax-v1', 't4a:axis', 1, :vf, 'published', 't4a-steward', "
                    " :dc, :h)"
                ),
                {"vf": T0, "dc": genesis.commit_id, "h": _HASH},
            )

        current = PublishedVersion(
            "t4a-ax-v1", "t4a:axis", valid_from=T0, decision_commit_id=genesis.commit_id
        )
        # v2 restates the IDENTICAL [T0, open) valid window -> fully overlaps v1.
        proposed = ProposedVersion(
            "t4a-ax-v2", "t4a:axis", valid_from=T0, decision_commit_id=c2
        )
        plan = plan_successor_publication(
            current, proposed, published_siblings=[current]
        )

        # Apply the plan as ONE transaction: supersede predecessor, then insert
        # successor. The DB backstop (trigger + published-only EXCLUDE + deferred self-FK)
        # must accept it.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE semantic_axes SET status = :st, "
                    "superseded_by_version_id = :succ WHERE id = :vid"
                ),
                {
                    "st": plan.supersede.new_status,
                    "succ": plan.supersede.successor_version_id,
                    "vid": plan.supersede.version_id,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO semantic_axes "
                    "(id, axis_key, axis_version, valid_from, status, created_by, "
                    " decision_commit_id, previous_version_id, axis_hash) VALUES "
                    "(:id, 't4a:axis', 2, :vf, 'published', 't4a-steward', :dc, "
                    " 't4a-ax-v1', :h)"
                ),
                {"id": proposed.version_id, "vf": T0, "dc": c2, "h": "c" * 64},
            )

        # The chain is now v1(superseded)->v2(published).
        with engine.begin() as conn:
            states = dict(
                conn.execute(
                    text(
                        "SELECT id, status FROM semantic_axes "
                        "WHERE axis_key = 't4a:axis'"
                    )
                ).all()
            )
            assert states == {"t4a-ax-v1": "superseded", "t4a-ax-v2": "published"}

        # Negative: an illegal column mutation on the published v2 is rejected by the
        # trigger AND by assert_published_immutable -> planner agrees with the DB.
        with pytest.raises(db_errors):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE semantic_axes SET axis_key = 'hijacked' "
                        "WHERE id = 't4a-ax-v2'"
                    )
                )
        with pytest.raises(WriteValidationError, match="immutable fields changed"):
            assert_published_immutable(
                {
                    "status": STATUS_PUBLISHED,
                    "axis_key": "t4a:axis",
                    "superseded_by_version_id": None,
                },
                {
                    "status": STATUS_SUPERSEDED,
                    "axis_key": "hijacked",
                    "superseded_by_version_id": "x",
                },
                mutable_fields=MUTABLE_ON_SUPERSEDE_VERSIONED,
            )
    finally:
        engine.dispose()


def test_disposable_db_publication_chain_supersede_needs_no_deferral() -> None:
    # M3 / adjudication (b): semantic_publication_chains.superseded_by_commit_id references
    # decision_commit_sequence (a commit allocated FIRST), not the successor row, so an
    # atomic overlapping restatement here works with the IMMEDIATE FK: UPDATE the
    # predecessor (status->superseded, superseded_by_commit_id=c2) then INSERT the
    # successor published over the same subject/window. No deferrable FK is required.
    engine = _disposable_engine()
    try:
        with engine.begin() as conn:
            genesis = _genesis_commit(conn)
            conn.execute(
                text(
                    "INSERT INTO semantic_stewards (id, steward_id, role) "
                    "VALUES ('t4a-pub-steward', 't4a-pub-steward', 'author')"
                )
            )
            c2 = conn.execute(
                text(
                    "INSERT INTO decision_commit_sequence "
                    "(epoch_number, fence_token, previous_commit_id, commit_hash) "
                    "VALUES (:e, :f, :prev, :h) RETURNING commit_id"
                ),
                {
                    "e": genesis.epoch_number,
                    "f": genesis.fence_token,
                    "prev": genesis.commit_id,
                    "h": "d" * 64,
                },
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO semantic_publication_chains "
                    "(id, subject_kind, subject_id, valid_from, decision_commit_id, "
                    " status, created_by) VALUES "
                    "('t4a-pub-1', 'axis', 't4a:pub', :vf, :dc, 'published', "
                    " 't4a-pub-steward')"
                ),
                {"vf": T0, "dc": genesis.commit_id},
            )

        # Atomic overlapping restatement with the IMMEDIATE commit FK.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE semantic_publication_chains SET status = 'superseded', "
                    "superseded_by_commit_id = :c2 WHERE id = 't4a-pub-1'"
                ),
                {"c2": c2},
            )
            conn.execute(
                text(
                    "INSERT INTO semantic_publication_chains "
                    "(id, subject_kind, subject_id, valid_from, decision_commit_id, "
                    " supersedes_publication_id, status, created_by) VALUES "
                    "('t4a-pub-2', 'axis', 't4a:pub', :vf, :dc, 't4a-pub-1', "
                    " 'published', 't4a-pub-steward')"
                ),
                {"vf": T0, "dc": c2},
            )

        with engine.begin() as conn:
            states = dict(
                conn.execute(
                    text(
                        "SELECT id, status FROM semantic_publication_chains "
                        "WHERE subject_id = 't4a:pub'"
                    )
                ).all()
            )
            assert states == {"t4a-pub-1": "superseded", "t4a-pub-2": "published"}
    finally:
        engine.dispose()
