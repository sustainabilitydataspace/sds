"""VARCH-3 contract tests — seed VARCH-0 profiles + the Waste_Plastic atomization fixture.

Layers: (1) embedded-payload freeze + routing + demo-free + no-runtime-import (import the
migration module, no DB); (2) migration-038/039 text; (3) disposable-DB smoke that runs the
real migration to head and proves the seeded registry hashes match the frozen set AND the
seeded Waste_Plastic subject is covered by the seeded contract under the VARCH-2 coverage gate
(skipped unless SDS_MIGRATION_TEST_DATABASE_URL + SDS_MIGRATION_TEST_ALLOW_RESET=true).
"""

from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.test_varch0_profile_registry import FROZEN_PROFILE_HASHES

API_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_039 = (
    API_ROOT / "alembic" / "versions" / "039_seed_varch0_profiles_and_waste_plastic.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("m039", MIGRATION_039)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- Layer 1: embedded payload freeze / routing / demo-free ---------------------------


def test_embedded_hashes_equal_frozen_set() -> None:
    m = _load_migration()
    embedded = {pid: p["hash"] for pid, p in m._PROFILE_PAYLOADS.items()}
    assert embedded == FROZEN_PROFILE_HASHES


def test_routing_two_implemented_seven_ratified() -> None:
    m = _load_migration()
    impl = {k for k, v in m._PROFILE_PAYLOADS.items() if v["kind"] == "implemented"}
    rat = {k for k, v in m._PROFILE_PAYLOADS.items() if v["kind"] == "ratified"}
    assert impl == {"sds-canonical-json-v1", "sds-computation-profile-v1"}
    assert len(rat) == 7
    assert set(m._IMPLEMENTED_ROUTE) == impl
    assert m._IMPLEMENTED_ROUTE["sds-canonical-json-v1"] == "canonical_hash_profiles"
    assert m._IMPLEMENTED_ROUTE["sds-computation-profile-v1"] == "computation_profiles"
    # M1: the replay-manifest entry is a ratified SCHEMA (-> contract_schema_versions),
    # NOT a manifest instance.
    assert m._PROFILE_PAYLOADS["sds:profile:replay-manifest:v1"]["kind"] == "ratified"


def test_no_runtime_import_of_profiles_package() -> None:
    # Condition: the migration must NOT import src.semantic.profiles at Alembic runtime.
    # (The docstring may *mention* it; assert no actual import statement.)
    for line in MIGRATION_039.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith(("import ", "from ")):
            assert "src.semantic" not in s, s


def _code_only(body: str) -> str:
    """Strip the module docstring + comment lines so doc text isn't scanned for tokens."""
    parts = body.split('"""')
    # parts[0] = before module docstring, parts[1] = module docstring, parts[2:] = rest.
    rest = '"""'.join(parts[2:]) if len(parts) > 2 else body
    return "\n".join(ln for ln in rest.splitlines() if not ln.lstrip().startswith("#"))


def test_seed_ids_are_demo_free() -> None:
    code = _code_only(MIGRATION_039.read_text(encoding="utf-8"))
    forbidden = [
        "urn:sds:reg:demo:",
        "SDS demo",
        "fastapi_demo_seed",
        "portal_demo_seed",
        "swagger-golden-paths",
        "fastapi_demo_sample_load_v1",
        "demo://",
    ]
    for token in forbidden:
        assert token not in code, token


# --- Layer 2: migration text ----------------------------------------------------------


def test_migration_039_text() -> None:
    assert MIGRATION_039.exists()
    body = MIGRATION_039.read_text(encoding="utf-8")
    for snippet in [
        'revision = "039_seed_varch0_profiles_and_waste_plastic"',
        'down_revision = "038_add_closed_enum_exemptions"',
        "INSERT INTO {table}",
        "INSERT INTO contract_schema_versions",
        "INSERT INTO canonical_concepts",
        "INSERT INTO semantic_axes",
        "INSERT INTO semantic_terms",
        "INSERT INTO partition_sets",
        "INSERT INTO partition_set_members",
        "INSERT INTO concept_atomization_contracts",
        "INSERT INTO contract_required_axes",
        "WHERE NOT EXISTS",
        "VARCH-3 seed drift",  # fail-closed M4
    ]:
        assert snippet in body, snippet


def test_migration_039_downgrade_is_documented_noop() -> None:
    body = MIGRATION_039.read_text(encoding="utf-8")
    downgrade = body.split("def downgrade", 1)[1]
    assert "pass" in downgrade
    assert "DELETE FROM" not in downgrade  # append-only: cannot reverse by DELETE


# --- Layer 3: disposable-DB smoke -----------------------------------------------------


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
    init_db_for_engine(engine)  # runs migrations to head, including 039 seed
    return engine


def test_disposable_db_seed_and_coverage() -> None:
    engine = _disposable_engine()
    try:
        with engine.begin() as conn:
            # Registry seed: 1 + 1 + 7 rows with frozen hashes.
            ch = conn.execute(
                text(
                    "SELECT profile_hash FROM canonical_hash_profiles "
                    "WHERE profile_id = 'sds-canonical-json-v1'"
                )
            ).scalar_one()
            assert ch == FROZEN_PROFILE_HASHES["sds-canonical-json-v1"]
            comp = conn.execute(
                text(
                    "SELECT profile_hash FROM computation_profiles "
                    "WHERE profile_id = 'sds-computation-profile-v1'"
                )
            ).scalar_one()
            assert comp == FROZEN_PROFILE_HASHES["sds-computation-profile-v1"]
            n_schema = conn.execute(
                text(
                    "SELECT count(*) FROM contract_schema_versions "
                    "WHERE schema_id LIKE 'sds:profile:%'"
                )
            ).scalar_one()
            assert n_schema == 7

            # Waste_Plastic denominator subject + atomization contract + vocabulary.
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM canonical_concepts "
                        "WHERE canonical_uri = 'syg:WastePlastic' AND effective_to IS NULL"
                    )
                ).scalar_one()
                == 1
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM semantic_axes "
                        "WHERE id LIKE 'sds:seed:axis:%'"
                    )
                ).scalar_one()
                == 2
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM partition_set_members "
                        "WHERE partition_set_id = 'sds:seed:pset:waste_hazard_status'"
                    )
                ).scalar_one()
                == 2
            )

            row = conn.execute(
                text(
                    "SELECT subject_kind, subject_ref, valid_from, decision_commit_id "
                    "FROM concept_atomization_contracts "
                    "WHERE id = 'sds:seed:contract:waste_plastic'"
                )
            ).one()

        # M2 coverage tie-in: build provider + subject FROM the seeded rows and prove the
        # seeded Waste_Plastic subject is covered by the seeded contract under the gate.
        from src.semantic.coverage import (
            EVAL_SHARED,
            PATH_ATOMIZATION_CONTRACT,
            SCOPE_PUBLIC,
            BitemporalRow,
            CoverageSlice,
            Subject,
            compute_coverage,
        )

        subject = Subject(
            kind=row.subject_kind, ref=row.subject_ref, scope_kind=SCOPE_PUBLIC
        )
        provider = BitemporalRow(
            kind=row.subject_kind,
            ref=row.subject_ref,
            valid_from=row.valid_from,
            decision_commit_id=row.decision_commit_id,
            path=PATH_ATOMIZATION_CONTRACT,
        )
        slc = CoverageSlice(
            valid_as_of=datetime(2026, 6, 1, tzinfo=timezone.utc),
            decision_commit_id=row.decision_commit_id,
            as_of_timestamp=datetime(2026, 6, 1, tzinfo=timezone.utc),
            scope_class=EVAL_SHARED,
        )
        result = compute_coverage([subject], [provider], [], slc)
        assert result.is_full, result.uncovered
    finally:
        engine.dispose()
