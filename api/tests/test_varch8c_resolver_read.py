"""VARCH-8c contract tests — resolver read-path DB wiring.

Layer 1 (pure adapter): resolve_read_context orchestrates the resolver cores over an injected
ResolverRepo — tested with a FakeRepo (no DB) for the SNAPSHOT_REQUIRED path and a full
end-to-end EVS resolution.
Layer 2 (disposable-DB smoke): SemanticResolverRepository runs the real bitemporal queries —
the migration-039 genesis commit chain loads, an absent EVS yields SNAPSHOT_REQUIRED, and an
unwired subject kind fails closed. Gated on the disposable PG env vars.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from src.semantic.profiles import registry
from src.semantic.write_evs import EffectiveVersionSet, EVSMember, canonical_evs_digest
from src.semantic.write_replay import EVALUATION_ORDER
from src.services.resolver_read import (
    ResolverReadError,
    SemanticResolverRepository,
    resolve_read_context,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = T0 + timedelta(days=200)
T2 = T0 + timedelta(days=730)
_SCOPE = "public"
_MANIFEST_ID = "rm:evs:1"
_SUBJECT = ("canonical_concept", "syg:WastePlastic")
_CHAIN = [(1, None), (2, 1), (3, 2)]


def _manifest():
    h = registry.profile_hashes()

    def ref(pid):
        return {"profile_id": pid, "version": "v1", "hash": h[pid]}

    return {
        "manifest_id": _MANIFEST_ID,
        "manifest_version": "v1",
        "evaluation_order": list(EVALUATION_ORDER),
        "profile_tuple": {
            "canonical_serialization_profile": ref("sds-canonical-json-v1"),
            "computation_profile": ref("sds-computation-profile-v1"),
            "private_commitment_profile": ref("sds:profile:private-commitment:v1"),
            "aggregate_disclosure_profile": ref("sds:profile:aggregate-disclosure:v1"),
            "license_rights_window": ref("sds:profile:license-rights-window:v1"),
        },
        "transition_policy": "fail_closed_require_explicit_transition",
    }


def _member():
    return EVSMember(
        effective_version_set_id="evs1",
        scope_context=_SCOPE,
        subject_kind="canonical_concept",
        subject_ref="syg:WastePlastic",
        resolved_version_id="ax-v1",
    )


def _evs():
    base = EffectiveVersionSet(
        id="evs1",
        evs_key="evsk:1",
        scope_context=_SCOPE,
        replay_manifest_ref=_MANIFEST_ID,
        valid_from=T0,
        valid_to=T2,
        decision_commit_id=2,
    )
    return replace(base, evs_hash=canonical_evs_digest(base, [_member()]))


class _FakeRepo:
    def __init__(self, *, evs=None):
        self._evs = evs

    def load_commit_chain(self):
        return list(_CHAIN)

    def latest_committed_commit_id(self):
        return 3

    def load_evs_slice(self, evs_key, scope_context, valid_as_of, decision_commit_id):
        return self._evs

    def load_evs_members(self, evs_id):
        return [_member()]

    def load_replay_manifest(self, manifest_ref):
        return _manifest()

    def build_resolved_version_index(self, members, valid_as_of, decision_commit_id):
        return {_SUBJECT: frozenset({"ax-v1"})}


# --- Layer 1: pure adapter via FakeRepo -----------------------------------------------


def test_snapshot_required_when_no_evs() -> None:
    res = resolve_read_context(
        _FakeRepo(evs=None),
        evs_key="evsk:1",
        scope_context=_SCOPE,
        reporting_period=T1,
        trace_decision_commit_id=2,
    )
    assert res.snapshot_required is True
    assert res.effective_version_set is None
    assert res.context.valid_as_of == T1
    assert res.context.decision_commit_id == 2


def test_resolves_effective_version_set_end_to_end() -> None:
    res = resolve_read_context(
        _FakeRepo(evs=_evs()),
        evs_key="evsk:1",
        scope_context=_SCOPE,
        reporting_period=T1,
        trace_decision_commit_id=2,
    )
    assert res.snapshot_required is False
    assert res.effective_version_set is not None
    assert res.effective_version_set.id == "evs1"
    assert res.replay_manifest["manifest_id"] == _MANIFEST_ID
    assert len(res.members) == 1


def test_resolution_rejects_uncommitted_future_decision() -> None:
    # trace pins commit 3 but latest committed is materialized at 3 in the fake; request 99 absent.
    with pytest.raises(Exception):  # ResolverError (not a member)
        resolve_read_context(
            _FakeRepo(evs=_evs()),
            evs_key="evsk:1",
            scope_context=_SCOPE,
            reporting_period=T1,
            trace_decision_commit_id=99,
        )


# --- Layer 2: disposable-DB smoke (real repository) -----------------------------------


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


def test_disposable_db_repository_reads() -> None:
    engine = _disposable_engine()
    from sqlalchemy.orm import Session

    try:
        with Session(engine) as session:
            repo = SemanticResolverRepository(session)

            # migration 039 seeds the genesis decision commit -> chain non-empty, has a genesis.
            chain = repo.load_commit_chain()
            assert chain, "expected a seeded genesis decision commit"
            assert any(
                prev is None for _, prev in chain
            ), "expected a genesis (prev=None)"
            assert repo.latest_committed_commit_id() is not None

            # no EVS materialized for this slice -> SNAPSHOT_REQUIRED end-to-end via the real repo.
            res = resolve_read_context(
                repo,
                evs_key="evsk:does-not-exist",
                scope_context="public",
                reporting_period=T1,
                request_decision_commit_id=chain[0][0],
            )
            assert res.snapshot_required is True

            # an unwired subject kind fails closed.
            with pytest.raises(ResolverReadError, match="not wired for subject_kind"):
                repo.build_resolved_version_index(
                    [
                        EVSMember(
                            effective_version_set_id="x",
                            scope_context="public",
                            subject_kind="standard_datapoint",
                            subject_ref="rel1:dp1",
                            resolved_version_id="v1",
                        )
                    ],
                    T1,
                    chain[0][0],
                )

            # M1: canonical_concept version selection is INSTANT-based, not wall-clock.
            # effective_from = 2026-06-01 12:00 UTC (stored naive). A read at 09:00 in UTC-4
            # is the 13:00 UTC instant (>= 12:00) -> the revision IS effective; a naive strip
            # would wrongly compare 09:00 < 12:00 and miss it.
            session.execute(
                text(
                    "INSERT INTO canonical_concepts (canonical_uri, revision, effective_from, "
                    "label, taxonomy, concept_type) VALUES "
                    "('syg:TzProbe', 1, '2026-06-01 12:00:00', 'Tz Probe', 'Sygris', 'metric')"
                )
            )
            session.commit()
            cid = session.execute(
                text(
                    "SELECT id FROM canonical_concepts WHERE canonical_uri = 'syg:TzProbe'"
                )
            ).scalar_one()
            member = EVSMember(
                effective_version_set_id="x",
                scope_context="public",
                subject_kind="canonical_concept",
                subject_ref="syg:TzProbe",
                resolved_version_id=str(cid),
            )
            from datetime import timezone as _tz

            non_utc = datetime(
                2026, 6, 1, 9, 0, tzinfo=_tz(timedelta(hours=-4))
            )  # = 13:00 UTC
            index = repo.build_resolved_version_index([member], non_utc, chain[0][0])
            assert str(cid) in index[("canonical_concept", "syg:TzProbe")]

            # and an instant BEFORE effective_from excludes it.
            before = datetime(
                2026, 6, 1, 7, 0, tzinfo=_tz(timedelta(hours=-4))
            )  # = 11:00 UTC
            index_before = repo.build_resolved_version_index(
                [member], before, chain[0][0]
            )
            assert index_before[("canonical_concept", "syg:TzProbe")] == frozenset()
    finally:
        engine.dispose()
