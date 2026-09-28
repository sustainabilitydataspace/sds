"""VARCH-2 contract tests — the atomization coverage gate.

Layers: (1) metadata drift for the new closed-enum exemption table + the frozen subject-key
matrix; (2) migration-038 text; (3) pure deterministic coverage-core logic covering the
discovery backlog (M1-M7); (4) disposable-DB smoke for the exemption table guardrails
(skipped unless SDS_MIGRATION_TEST_DATABASE_URL + SDS_MIGRATION_TEST_ALLOW_RESET=true).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import UniqueConstraint, create_engine, text

from src.database import models as _models  # noqa: F401 - register metadata
from src.database.base import Base
from src.semantic.coverage import (
    EVAL_PUBLIC,
    EVAL_SHARED,
    EVAL_TENANT,
    PATH_EXEMPTION,
    PUBLIC_TENANT,
    SCOPE_PUBLIC,
    SCOPE_SHARED,
    SCOPE_TENANT_PRIVATE,
    SUBJECT_KEY_MATRIX,
    SUBJECT_KINDS,
    BitemporalRow,
    CoverageGateError,
    CoverageSlice,
    ExemptionRow,
    Subject,
    assert_full_coverage,
    compute_coverage,
)

API_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_038 = API_ROOT / "alembic" / "versions" / "038_add_closed_enum_exemptions.py"
TABLE = "semantic_closed_enum_exemptions"

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 1, tzinfo=timezone.utc)
T2 = datetime(2026, 12, 1, tzinfo=timezone.utc)
FAR = datetime(2030, 1, 1, tzinfo=timezone.utc)


def _slice(**kw) -> CoverageSlice:
    base = dict(
        valid_as_of=T1,
        decision_commit_id=100,
        as_of_timestamp=T1,
        scope_class=EVAL_SHARED,
    )
    base.update(kw)
    return CoverageSlice(**base)


def _provider(kind, ref, **kw) -> BitemporalRow:
    base = dict(
        kind=kind,
        ref=ref,
        valid_from=T0,
        decision_commit_id=50,
        path="atomization_contract",
    )
    base.update(kw)
    return BitemporalRow(**base)


def _exemption(kind, ref, **kw) -> ExemptionRow:
    base = dict(
        kind=kind,
        ref=ref,
        valid_from=T0,
        decision_commit_id=50,
        path=PATH_EXEMPTION,
        expires_at=FAR,
    )
    base.update(kw)
    return ExemptionRow(**base)


# --- Layer 1: metadata + matrix -------------------------------------------------------


def test_exemption_table_registered_and_bitemporal() -> None:
    assert TABLE in Base.metadata.tables
    cols = Base.metadata.tables[TABLE].c
    for c in (
        "decision_commit_id",
        "valid_from",
        "valid_to",
        "status",
        "expires_at",
        "scope_kind",
        "tenant_id",
        "subject_kind",
        "subject_ref",
        "exemption_version",
        "previous_version_id",
        "superseded_by_version_id",
    ):
        assert c in cols, c
    assert cols["expires_at"].nullable is False
    assert cols["scope_kind"].nullable is False
    assert cols["tenant_id"].nullable is False


def test_exemption_key_version_unique_and_scope_checks() -> None:
    table = Base.metadata.tables[TABLE]
    uniques = {
        tuple(c.name for c in con.columns)
        for con in table.constraints
        if isinstance(con, UniqueConstraint)
    }
    assert ("subject_kind", "subject_ref", "tenant_id", "exemption_version") in uniques
    checks = {c.name for c in table.constraints if c.name and c.name.startswith("ck_")}
    assert "ck_semantic_closed_enum_exemptions_scope_kind" in checks
    assert "ck_semantic_closed_enum_exemptions_subject_kind" in checks
    assert "ck_semantic_closed_enum_exemptions_expiry" in checks


def test_subject_key_matrix_frozen() -> None:
    assert len(SUBJECT_KEY_MATRIX) == 8
    assert set(SUBJECT_KINDS) == set(SUBJECT_KEY_MATRIX)
    # M4: calculation subjects only coverable by exemption at VARCH-2.
    for kind in (
        "calculation_contract",
        "calculation_component",
        "calculation_dimension",
    ):
        assert SUBJECT_KEY_MATRIX[kind].eligible_paths == (PATH_EXEMPTION,)
    # M5: predicates frozen, not empty.
    for spec in SUBJECT_KEY_MATRIX.values():
        assert spec.membership_predicate
        assert spec.ref_scheme
        assert PATH_EXEMPTION in spec.eligible_paths


# --- Layer 2: migration text ----------------------------------------------------------


def test_migration_038_text() -> None:
    assert MIGRATION_038.exists()
    body = MIGRATION_038.read_text(encoding="utf-8")
    for snippet in [
        'revision = "038_add_closed_enum_exemptions"',
        'down_revision = "038_merge_demo_cleanup_with_evs"',
        "subject_kind WITH =, subject_ref WITH =, tenant_id WITH =",
        "CONSTRAINT ck_semantic_closed_enum_exemptions_scope_kind",
        "expires_at TIMESTAMPTZ NOT NULL",
        "CHECK (expires_at > valid_from)",
        "FOR EACH ROW EXECUTE FUNCTION sds_reject_versioned_mutation()",
    ]:
        assert snippet in body, snippet


def test_migration_038_downgrade_keeps_shared_function() -> None:
    body = MIGRATION_038.read_text(encoding="utf-8")
    downgrade = body.split("def downgrade", 1)[1]
    assert f"DROP TABLE IF EXISTS {TABLE}" in downgrade
    assert "DROP FUNCTION IF EXISTS sds_reject_versioned_mutation" not in downgrade


# --- Layer 3: pure coverage core ------------------------------------------------------


def test_full_coverage_passes() -> None:
    subjects = [Subject("concept", "u1"), Subject("concept", "u2")]
    providers = [_provider("concept", "u1"), _provider("concept", "u2")]
    result = compute_coverage(subjects, providers, [], _slice())
    assert result.is_full
    assert result.coverage_fraction == 1.0
    assert_full_coverage(result)  # no raise


def test_uncovered_fails_with_sorted_list() -> None:
    subjects = [Subject("concept", "z"), Subject("concept", "a")]
    result = compute_coverage(subjects, [_provider("concept", "a")], [], _slice())
    assert result.uncovered == (("concept", "z"),)
    assert result.covered == 1 and result.total == 2
    with pytest.raises(CoverageGateError):
        assert_full_coverage(result)


def test_m3_no_retroactive_exposure_valid_time() -> None:
    # Provider only becomes valid AFTER the slice -> does not cover.
    subjects = [Subject("concept", "u1")]
    late = _provider("concept", "u1", valid_from=T2)
    assert compute_coverage(subjects, [late], [], _slice()).uncovered == (
        ("concept", "u1"),
    )


def test_m3_no_retroactive_exposure_decision_time() -> None:
    # Provider published by a LATER decision commit than the slice -> does not cover.
    subjects = [Subject("concept", "u1")]
    later = _provider("concept", "u1", decision_commit_id=200)
    assert compute_coverage(
        subjects, [later], [], _slice(decision_commit_id=100)
    ).uncovered
    earlier = _provider("concept", "u1", decision_commit_id=100)
    assert compute_coverage(
        subjects, [earlier], [], _slice(decision_commit_id=100)
    ).is_full


def test_m3_superseded_provider_does_not_cover() -> None:
    subjects = [Subject("concept", "u1")]
    superseded = _provider("concept", "u1", status="superseded")
    assert compute_coverage(subjects, [superseded], [], _slice()).uncovered


def test_m7_unexpired_exemption_covers_expired_does_not() -> None:
    subjects = [Subject("calculation_contract", "h1")]
    unexpired = _exemption("calculation_contract", "h1", expires_at=FAR)
    res_ok = compute_coverage(subjects, [], [unexpired], _slice(as_of_timestamp=T1))
    assert res_ok.is_full
    assert res_ok.exempted == (("calculation_contract", "h1"),)
    expired = _exemption("calculation_contract", "h1", expires_at=T0)
    res_bad = compute_coverage(subjects, [], [expired], _slice(as_of_timestamp=T1))
    assert res_bad.uncovered == (("calculation_contract", "h1"),)


def test_m4_calculation_subject_only_exemption_covers() -> None:
    subjects = [Subject("calculation_dimension", "h1:d1")]
    # No exemption -> uncovered (no contract path exists for calc subjects at VARCH-2).
    assert compute_coverage(subjects, [], [], _slice()).uncovered
    exemption = _exemption("calculation_dimension", "h1:d1")
    assert compute_coverage(subjects, [], [exemption], _slice()).is_full


def test_m4_forbidden_provider_path_does_not_cover_calc_subject() -> None:
    # A non-exemption provider row with matching kind/ref/tenant must NOT cover a calc
    # subject: eligible_paths is ENFORCED, not just metadata.
    subjects = [Subject("calculation_contract", "h1")]
    from src.semantic.coverage import (
        PATH_ATOMIZATION_CONTRACT,
        PATH_COMPONENT_BINDING,
        PATH_SYGRIS_BINDING,
    )

    for forbidden in (
        PATH_ATOMIZATION_CONTRACT,
        PATH_SYGRIS_BINDING,
        PATH_COMPONENT_BINDING,
    ):
        row = _provider("calculation_contract", "h1", path=forbidden)
        assert compute_coverage(subjects, [row], [], _slice()).uncovered == (
            ("calculation_contract", "h1"),
        ), forbidden
    # And an eligible exemption still covers it.
    assert compute_coverage(
        subjects, [], [_exemption("calculation_contract", "h1")], _slice()
    ).is_full


def test_m6_tenant_private_excluded_from_shared_eval() -> None:
    subjects = [
        Subject("concept", "pub", SCOPE_PUBLIC),
        Subject("concept", "priv", SCOPE_TENANT_PRIVATE, tenant_id="tenantA"),
    ]
    providers = [_provider("concept", "pub")]
    result = compute_coverage(subjects, providers, [], _slice(scope_class=EVAL_SHARED))
    assert result.total == 1  # private subject not admitted
    assert result.is_full
    assert result.redacted_tenant_private is True
    assert SCOPE_TENANT_PRIVATE not in result.by_scope


def test_m2_tenant_eval_includes_and_matches_tenant() -> None:
    subjects = [Subject("concept", "priv", SCOPE_TENANT_PRIVATE, tenant_id="tenantA")]
    # A different tenant's provider must not cover it.
    other = _provider("concept", "priv", scope_kind=SCOPE_TENANT_PRIVATE, tenant_id="B")
    slc = _slice(scope_class=EVAL_TENANT, tenant_id="tenantA")
    assert compute_coverage(subjects, [other], [], slc).uncovered
    same = _provider(
        "concept", "priv", scope_kind=SCOPE_TENANT_PRIVATE, tenant_id="tenantA"
    )
    assert compute_coverage(subjects, [same], [], slc).is_full


def test_public_eval_excludes_shared_scope() -> None:
    subjects = [Subject("concept", "s", SCOPE_SHARED)]
    providers = [_provider("concept", "s", scope_kind=SCOPE_SHARED)]
    # EVAL_PUBLIC admits only public-scope subjects.
    res = compute_coverage(subjects, providers, [], _slice(scope_class=EVAL_PUBLIC))
    assert res.total == 0
    assert res.coverage_fraction == 1.0  # vacuously full


def test_tenant_eval_requires_tenant_id() -> None:
    with pytest.raises(ValueError):
        compute_coverage([], [], [], _slice(scope_class=EVAL_TENANT, tenant_id=None))


def test_determinism_same_inputs_same_result() -> None:
    subjects = [Subject("concept", f"u{i}") for i in range(5)]
    providers = [_provider("concept", f"u{i}") for i in (0, 2, 4)]
    a = compute_coverage(subjects, providers, [], _slice())
    b = compute_coverage(list(subjects), list(providers), [], _slice())
    assert a == b
    assert a.uncovered == (("concept", "u1"), ("concept", "u3"))


def test_empty_denominator_is_vacuously_full() -> None:
    res = compute_coverage([], [], [], _slice())
    assert res.is_full and res.coverage_fraction == 1.0 and res.total == 0


# --- Layer 4: disposable-DB smoke -----------------------------------------------------


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


_H = "a" * 64


def test_disposable_db_exemption_append_only() -> None:
    engine = _disposable_engine()
    try:
        from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError

        db_errors = (IntegrityError, InternalError, ProgrammingError)

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO semantic_stewards (id, steward_id, role) "
                    "VALUES ('s1', 's1', 'steward')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO decision_commit_epochs (epoch_number, fence_token) "
                    "VALUES (1, 'f1')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO decision_commit_fences (id, fence_token, epoch_number) "
                    "VALUES ('fen1', 'f1', 1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO decision_commit_sequence "
                    "(fence_token, epoch_number, commit_hash) VALUES ('f1', 1, :h)"
                ),
                {"h": _H},
            )
            cid = connection.execute(
                text("SELECT commit_id FROM decision_commit_sequence LIMIT 1")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO semantic_closed_enum_exemptions "
                    "(id, subject_kind, subject_ref, scope_kind, closed_enum_ref, "
                    "exemption_reason, expires_at, exemption_version, decision_commit_id, "
                    "valid_from) VALUES "
                    "('e1', 'calculation_contract', 'h1', 'public', 'axis:x', "
                    "'closed_enum_complete', '2030-01-01', 1, :cid, '2026-01-01')"
                ),
                {"cid": cid},
            )

        # expires_at <= valid_from rejected.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO semantic_closed_enum_exemptions "
                        "(id, subject_kind, subject_ref, scope_kind, closed_enum_ref, "
                        "exemption_reason, expires_at, exemption_version, "
                        "decision_commit_id, valid_from) VALUES "
                        "('e2', 'concept', 'u1', 'public', 'axis:x', 'other', "
                        "'2025-01-01', 1, :cid, '2026-01-01')"
                    ),
                    {"cid": cid},
                )

        # DELETE forbidden by the shared 033 guard.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM semantic_closed_enum_exemptions WHERE id = 'e1'")
                )
    finally:
        engine.dispose()
