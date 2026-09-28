"""VARCH-4b contract tests — fenced decision-time commit sequencer.

Two layers: (1) the pure deterministic core (no DB) for commit-allocation validation, epoch
failover planning, and replay ordering; (2) a disposable-DB smoke that proves the planner
agrees with the live migration-032 backbone — a continuous fenced allocation is ACCEPTED, a
fork is REJECTED by the fork-prevention partial unique, and an atomic epoch failover keeps the
one-active-epoch / one-held-fence invariants (skipped unless SDS_MIGRATION_TEST_DATABASE_URL +
SDS_MIGRATION_TEST_ALLOW_RESET=true).
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

from src.semantic.write_sequencer import (
    EPOCH_ACTIVE,
    EPOCH_SUPERSEDED,
    FENCE_HELD,
    FENCE_RELEASED,
    CommitHead,
    Epoch,
    EpochFailoverPlan,
    Fence,
    ProposedCommit,
    SequencerError,
    plan_epoch_failover,
    replay_order,
    validate_commit_allocation,
)


def _active():
    return Epoch(
        epoch_number=1, fence_token="f1", status=EPOCH_ACTIVE, commit_id_floor=0
    )


def _held():
    return Fence(fence_token="f1", epoch_number=1, status=FENCE_HELD)


# --- Layer 1: pure core --------------------------------------------------------------


def test_allocation_accepts_continuous_commit() -> None:
    validate_commit_allocation(
        _active(),
        _held(),
        CommitHead(commit_id=5, epoch_number=1),
        ProposedCommit(epoch_number=1, fence_token="f1", previous_commit_id=5),
    )


def test_allocation_accepts_genesis_with_no_predecessor() -> None:
    validate_commit_allocation(
        _active(),
        _held(),
        None,
        ProposedCommit(epoch_number=1, fence_token="f1", previous_commit_id=None),
    )


def test_allocation_rejects_genesis_with_predecessor() -> None:
    with pytest.raises(SequencerError, match="genesis commit must have"):
        validate_commit_allocation(
            _active(),
            _held(),
            None,
            ProposedCommit(epoch_number=1, fence_token="f1", previous_commit_id=3),
        )


def test_allocation_rejects_genesis_after_floor_established() -> None:
    # M1: head=None is only valid at the global start; once an epoch carries a floor (prior
    # commits exist) a genesis would create a second NULL-predecessor row the DB cannot stop.
    with pytest.raises(SequencerError, match="only valid at the global"):
        validate_commit_allocation(
            Epoch(2, "f2", status=EPOCH_ACTIVE, commit_id_floor=5),
            Fence("f2", 2, status=FENCE_HELD),
            None,
            ProposedCommit(epoch_number=2, fence_token="f2", previous_commit_id=None),
        )


def test_allocation_rejects_fork_off_non_head() -> None:
    with pytest.raises(SequencerError, match="continuity"):
        validate_commit_allocation(
            _active(),
            _held(),
            CommitHead(commit_id=5, epoch_number=1),
            ProposedCommit(epoch_number=1, fence_token="f1", previous_commit_id=4),
        )


def test_allocation_rejects_inactive_epoch() -> None:
    with pytest.raises(SequencerError, match="not active"):
        validate_commit_allocation(
            Epoch(1, "f1", status=EPOCH_SUPERSEDED),
            _held(),
            None,
            ProposedCommit(1, "f1", None),
        )


def test_allocation_rejects_unheld_fence() -> None:
    with pytest.raises(SequencerError, match="not held"):
        validate_commit_allocation(
            _active(),
            Fence("f1", 1, status=FENCE_RELEASED),
            None,
            ProposedCommit(1, "f1", None),
        )


def test_allocation_rejects_wrong_epoch() -> None:
    with pytest.raises(SequencerError, match="under the active"):
        validate_commit_allocation(
            _active(),
            _held(),
            CommitHead(5, 1),
            ProposedCommit(epoch_number=2, fence_token="f1", previous_commit_id=5),
        )


def test_allocation_rejects_wrong_fence_token() -> None:
    with pytest.raises(SequencerError, match="held fence_token"):
        validate_commit_allocation(
            _active(),
            _held(),
            CommitHead(5, 1),
            ProposedCommit(epoch_number=1, fence_token="OTHER", previous_commit_id=5),
        )


def test_allocation_rejects_fence_epoch_mismatch() -> None:
    with pytest.raises(SequencerError, match="different epoch"):
        validate_commit_allocation(
            _active(),
            Fence("f1", epoch_number=2, status=FENCE_HELD),
            None,
            ProposedCommit(1, "f1", None),
        )


def test_allocation_rejects_bad_commit_hash() -> None:
    with pytest.raises(SequencerError, match="hex"):
        validate_commit_allocation(
            _active(),
            _held(),
            None,
            ProposedCommit(1, "f1", None, commit_hash="nothex"),
        )


def test_failover_plan_sets_floor_and_supersession() -> None:
    plan = plan_epoch_failover(_active(), _held(), 2, "f2", current_max_commit_id=9)
    assert plan == EpochFailoverPlan(
        supersede_epoch=1,
        superseded_by_epoch=2,
        release_fence_token="f1",
        new_epoch=Epoch(2, "f2", EPOCH_ACTIVE, commit_id_floor=9),
        new_fence=Fence("f2", 2, FENCE_HELD),
    )


def test_failover_rejects_non_increasing_epoch() -> None:
    with pytest.raises(SequencerError, match="must exceed"):
        plan_epoch_failover(_active(), _held(), 1, "f2", current_max_commit_id=9)


def test_failover_rejects_reused_fence_token() -> None:
    with pytest.raises(SequencerError, match="must differ"):
        plan_epoch_failover(_active(), _held(), 2, "f1", current_max_commit_id=9)


def test_failover_rejects_inactive_source() -> None:
    with pytest.raises(SequencerError, match="currently active"):
        plan_epoch_failover(
            Epoch(1, "f1", status=EPOCH_SUPERSEDED), _held(), 2, "f2", 9
        )


def test_failover_rejects_unheld_old_fence() -> None:
    with pytest.raises(SequencerError, match="currently held"):
        plan_epoch_failover(
            _active(), Fence("f1", 1, status=FENCE_RELEASED), 2, "f2", 9
        )


def test_failover_rejects_old_fence_epoch_mismatch() -> None:
    with pytest.raises(SequencerError, match="different epoch"):
        plan_epoch_failover(
            _active(), Fence("f1", epoch_number=2, status=FENCE_HELD), 2, "f2", 9
        )


def test_failover_rejects_old_fence_token_mismatch() -> None:
    with pytest.raises(SequencerError, match="does not match"):
        plan_epoch_failover(_active(), Fence("fX", 1, status=FENCE_HELD), 2, "f2", 9)


def test_replay_order_walks_chain() -> None:
    assert replay_order([(2, 1), (1, None), (3, 2)]) == (1, 2, 3)


def test_replay_order_empty() -> None:
    assert replay_order([]) == ()


def test_replay_order_rejects_fork() -> None:
    with pytest.raises(SequencerError, match="fork"):
        replay_order([(1, None), (2, 1), (3, 1)])


def test_replay_order_rejects_missing_genesis() -> None:
    with pytest.raises(SequencerError, match="no genesis"):
        replay_order([(2, 1), (3, 2)])


def test_replay_order_rejects_gap() -> None:
    with pytest.raises(SequencerError, match="not contiguous"):
        replay_order([(1, None), (3, 2)])


def test_replay_order_rejects_genesisless_cycle() -> None:
    # A 2-cycle has no genesis (no commit with previous_commit_id=None); fork-prevention
    # guarantees a unique prev->commit map, so a genesis-reachable cycle cannot form and the
    # malformed chain is deterministically rejected at the missing-genesis check.
    with pytest.raises(SequencerError, match="no genesis"):
        replay_order([(1, 2), (2, 1)])


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


def _max_commit(conn) -> int:
    return conn.execute(
        text("SELECT max(commit_id) FROM decision_commit_sequence")
    ).scalar_one()


def _head(conn):
    # The head is the commit no other commit references as previous_commit_id.
    row = conn.execute(
        text(
            "SELECT commit_id, epoch_number FROM decision_commit_sequence s "
            "WHERE NOT EXISTS (SELECT 1 FROM decision_commit_sequence c "
            "                  WHERE c.previous_commit_id = s.commit_id) "
            "ORDER BY commit_id DESC LIMIT 1"
        )
    ).one()
    return CommitHead(commit_id=row.commit_id, epoch_number=row.epoch_number)


def test_disposable_db_fenced_allocation_and_failover() -> None:
    engine = _disposable_engine()
    try:
        from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError

        db_errors = (IntegrityError, InternalError, ProgrammingError)

        # The seed leaves epoch 0 superseded / fence released (commits 1->2). Promote an
        # operational active epoch 1 + held fence to allocate against.
        with engine.begin() as conn:
            floor = _max_commit(conn)  # 2
            conn.execute(
                text(
                    "INSERT INTO decision_commit_epochs "
                    "(epoch_number, fence_token, status, commit_id_floor) "
                    "VALUES (1, 'op-fence-1', 'active', :floor)"
                ),
                {"floor": floor},
            )
            conn.execute(
                text(
                    "INSERT INTO decision_commit_fences "
                    "(id, fence_token, epoch_number, status) "
                    "VALUES ('op-fence-1', 'op-fence-1', 1, 'held')"
                )
            )

        active = Epoch(1, "op-fence-1", EPOCH_ACTIVE, commit_id_floor=floor)
        held = Fence("op-fence-1", 1, FENCE_HELD)

        # Allocate a continuous commit in epoch 1 chained off the seeded head (commit 2).
        with engine.begin() as conn:
            head = _head(conn)
            proposed = ProposedCommit(
                1, "op-fence-1", previous_commit_id=head.commit_id
            )
            validate_commit_allocation(active, held, head, proposed)
            c3 = conn.execute(
                text(
                    "INSERT INTO decision_commit_sequence "
                    "(epoch_number, fence_token, previous_commit_id, commit_hash) "
                    "VALUES (1, 'op-fence-1', :prev, :h) RETURNING commit_id"
                ),
                {"prev": head.commit_id, "h": "a" * 64},
            ).scalar_one()
            assert c3 > head.commit_id  # global ordering via IDENTITY

        # Fork attempt: a second commit off the SAME predecessor is rejected by the DB
        # fork-prevention partial unique (and the pure validator would reject it pre-DB).
        with pytest.raises(db_errors):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO decision_commit_sequence "
                        "(epoch_number, fence_token, previous_commit_id) "
                        "VALUES (1, 'op-fence-1', :prev)"
                    ),
                    {"prev": head.commit_id},
                )
        with pytest.raises(SequencerError, match="continuity"):
            validate_commit_allocation(
                active,
                held,
                _head_after_c3(c3),
                ProposedCommit(1, "op-fence-1", previous_commit_id=head.commit_id),
            )

        # Atomic epoch failover: supersede epoch 1, release its fence, promote epoch 2 with
        # commit_id_floor = current max, hold a new fence. Order supersede-before-promote so
        # the one-active / one-held partial uniques never collide.
        with engine.begin() as conn:
            cur_max = _max_commit(conn)
            plan = plan_epoch_failover(active, held, 2, "op-fence-2", cur_max)
            conn.execute(
                text(
                    "UPDATE decision_commit_epochs SET status = 'superseded', "
                    "superseded_at = now(), superseded_by_epoch = :by "
                    "WHERE epoch_number = :old"
                ),
                {"by": plan.superseded_by_epoch, "old": plan.supersede_epoch},
            )
            conn.execute(
                text(
                    "UPDATE decision_commit_fences SET status = 'released', "
                    "revoked_at = now() WHERE fence_token = :tok"
                ),
                {"tok": plan.release_fence_token},
            )
            conn.execute(
                text(
                    "INSERT INTO decision_commit_epochs "
                    "(epoch_number, fence_token, status, commit_id_floor) "
                    "VALUES (:e, :tok, 'active', :floor)"
                ),
                {
                    "e": plan.new_epoch.epoch_number,
                    "tok": plan.new_epoch.fence_token,
                    "floor": plan.new_epoch.commit_id_floor,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO decision_commit_fences "
                    "(id, fence_token, epoch_number, status) "
                    "VALUES (:tok, :tok, :e, 'held')"
                ),
                {"tok": plan.new_fence.fence_token, "e": plan.new_fence.epoch_number},
            )

        with engine.begin() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM decision_commit_epochs WHERE status='active'"
                    )
                ).scalar_one()
                == 1
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM decision_commit_fences WHERE status='held'"
                    )
                ).scalar_one()
                == 1
            )
            # Allocate in the new epoch chained off the global head; floor < new commit.
            head2 = _head(conn)
            c4 = conn.execute(
                text(
                    "INSERT INTO decision_commit_sequence "
                    "(epoch_number, fence_token, previous_commit_id) "
                    "VALUES (2, 'op-fence-2', :prev) RETURNING commit_id"
                ),
                {"prev": head2.commit_id},
            ).scalar_one()
            assert c4 > plan.new_epoch.commit_id_floor

            # Replay reconstructs the single global order across both epochs.
            rows = conn.execute(
                text(
                    "SELECT commit_id, previous_commit_id FROM decision_commit_sequence"
                )
            ).all()
            order = replay_order([(r.commit_id, r.previous_commit_id) for r in rows])
            assert order[0] == 1 and order[-1] == c4 and len(order) == len(rows)
    finally:
        engine.dispose()


def _head_after_c3(c3: int) -> CommitHead:
    return CommitHead(commit_id=c3, epoch_number=1)
