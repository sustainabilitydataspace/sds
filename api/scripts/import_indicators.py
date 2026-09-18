#!/usr/bin/env python3
"""
Import E1 indicators from CSV to PostgreSQL database.

Versión: 1.2
Changelog:
  - v1.2: Removed tqdm (adds dependency), use simple progress
  - v1.1: Añadido batch transactions, --resume, validación URN
  - v1.0: Versión inicial

Uso:
    python scripts/import_indicators.py
    python scripts/import_indicators.py --dry-run
    python scripts/import_indicators.py --resume
"""

import argparse
import os
import signal
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add parent to path for imports
SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
REPO_ROOT = API_ROOT.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src.database.init_db import init_db_for_engine
from src.database.models import Indicator
from src.database.repositories.dataset_snapshot_repository import (
    DatasetSnapshotRepository,
)
from src.database.repositories.indicator_repository import IndicatorRepository
from src.services.dataset_history import file_sha256, persist_dataset_snapshot
from src.services.dataset_manifest import build_dataset_manifest
from src.services.dataset_serialization import serialize_indicator
from src.services.indicator_import import COLUMN_MAP, REQUIRED_REGISTER_COLUMNS
from src.services.indicator_import import (
    canonicalize_identifier_for_row as shared_canonicalize_identifier_for_row,
)
from src.services.indicator_import import (
    collect_legacy_identifier_migrations as shared_collect_legacy_identifier_migrations,
)
from src.services.indicator_import import (
    migrate_legacy_identifiers as shared_migrate_legacy_identifiers,
)
from src.services.indicator_import import (
    normalize_indicator_row as shared_normalize_indicator_row,
)
from src.services.indicator_import import validate_urn as shared_validate_urn
from src.services.semantic_concept_projector import SemanticConceptProjector

DEFAULT_CSV_PATH = REPO_ROOT / "data" / "processed" / "e1_dataset_register.csv"
# Use the platform temp directory so resume works on Windows and Unix.
RESUME_FILE = Path(tempfile.gettempdir()) / ".sds_import_resume"
BATCH_SIZE = 50  # Reduced for memory constrained environments


def safe_print(*args, sep=" ", end="\n", flush=False):
    """Print without failing on non-UTF-8 Windows code pages."""
    message = sep.join(str(arg) for arg in args)
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        print(message, end=end, flush=flush)
    except UnicodeEncodeError:
        safe_message = message.encode(encoding, errors="replace").decode(
            encoding, errors="replace"
        )
        stream.write(safe_message + end)
        if flush:
            stream.flush()


def get_default_database_url() -> str:
    """Build a sensible DB URL when DATABASE_URL is not explicitly set."""
    env_database_url = os.getenv("DATABASE_URL")
    if env_database_url:
        return env_database_url

    postgres_user = os.getenv("POSTGRES_USER", "sds")
    postgres_password = os.getenv("POSTGRES_PASSWORD", "password")
    postgres_host = os.getenv("POSTGRES_HOST", "localhost")
    postgres_port = os.getenv("POSTGRES_PORT", "5432")
    postgres_db = os.getenv("POSTGRES_DB", "sds")
    return f"postgresql://{postgres_user}:{postgres_password}@{postgres_host}:{postgres_port}/{postgres_db}"


def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully."""
    safe_print("\n\n⚠️  Interrupted by user. Progress saved. Use --resume to continue.")
    sys.exit(1)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def validate_urn(identifier):
    """Valida que el identificador sea un URN válido."""
    return shared_validate_urn(identifier)


def resolve_csv_input_path(csv_path: Path | None) -> Path | None:
    """Resolve a CSV path against repo-root conventions when launched from other cwd values."""
    if csv_path is None:
        return None
    if csv_path.exists():
        return csv_path.resolve()
    if csv_path.is_absolute():
        return csv_path

    repo_candidate = (REPO_ROOT / csv_path).resolve()
    if repo_candidate.exists():
        return repo_candidate
    return csv_path


def canonicalize_identifier_for_row(row: dict) -> str:
    """Return the canonical indicator URN for an E1 register row."""
    return shared_canonicalize_identifier_for_row(row)


def get_resume_point():
    """Obtiene el punto de resumen si existe."""
    if RESUME_FILE.exists():
        try:
            with open(RESUME_FILE, "r") as f:
                return int(f.read().strip())
        except (ValueError, IOError):
            return 0
    return 0


def save_resume_point(offset):
    """Guarda el punto de resumen."""
    try:
        with open(RESUME_FILE, "w") as f:
            f.write(str(offset))
    except IOError:
        pass  # Continue even if resume file can't be written


def clear_resume_point():
    """Limpia el punto de resumen tras éxito."""
    if RESUME_FILE.exists():
        try:
            RESUME_FILE.unlink()
        except IOError:
            pass


def load_csv(csv_path: Path, skip_rows=0) -> pd.DataFrame:
    """Load and validate the E1 dataset register CSV."""
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    df = pd.read_csv(
        csv_path, dtype=str, skiprows=range(1, skip_rows + 1) if skip_rows > 0 else None
    )
    df = df.fillna("")

    required = list(REQUIRED_REGISTER_COLUMNS)
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    safe_print(f"✓ Loaded {len(df)} rows from {csv_path}")
    if skip_rows > 0:
        safe_print(f"  (Resuming from row {skip_rows})")

    return df


def normalize_indicator_row(row: dict) -> dict:
    """Map a CSV row to a canonical Indicator payload plus normalization metadata."""
    return shared_normalize_indicator_row(row)


def migrate_legacy_identifiers(
    db, legacy_to_canonical: dict[str, str]
) -> tuple[int, int]:
    """Rename legacy malformed indicator URNs in-place before upserting canonical rows."""
    return shared_migrate_legacy_identifiers(db, legacy_to_canonical)


def collect_legacy_identifier_migrations(
    db, analyzed_rows: list[dict]
) -> dict[str, str]:
    """Collect legacy->canonical identifier rewrites from both input rows and the existing DB."""
    return shared_collect_legacy_identifier_migrations(db, analyzed_rows)


def print_progress(current, total, start_time):
    """Print simple progress bar without external dependencies."""
    percent = (current / total) * 100
    bar_length = 40
    filled = int(bar_length * current // total)
    bar = "#" * filled + "-" * (bar_length - filled)

    elapsed = (datetime.now() - start_time).total_seconds()
    if current > 0:
        eta = (elapsed / current) * (total - current)
        eta_str = f"ETA: {int(eta)}s"
    else:
        eta_str = "ETA: --"

    safe_print(
        f"\r{bar} {percent:.1f}% ({current}/{total}) {eta_str}", end="", flush=True
    )


def retire_missing_indicators_by_prefix(
    db,
    source_records: list[dict],
    retire_prefixes: list[str] | None = None,
) -> int:
    """Deactivate active indicators in closed scopes but absent from the source."""
    retirement_plan = plan_missing_indicators_by_prefix(
        db,
        source_records,
        retire_prefixes,
    )
    source_ids = {
        record["identifier"]
        for record in source_records
        if isinstance(record.get("identifier"), str)
    }
    retired = 0
    for prefix in retirement_plan:
        candidates = (
            db.query(Indicator)
            .filter(Indicator.is_active.is_(True))
            .filter(Indicator.identifier.startswith(prefix))
            .all()
        )
        for indicator in candidates:
            if indicator.identifier not in source_ids:
                indicator.is_active = False
                retired += 1

    if retired:
        db.commit()
    return retired


def plan_missing_indicators_by_prefix(
    db,
    source_records: list[dict],
    retire_prefixes: list[str] | None = None,
) -> dict[str, int]:
    """Count active closed-scope indicators absent from the source register."""
    prefixes = [prefix for prefix in retire_prefixes or [] if prefix]
    if not prefixes:
        return {}

    source_ids = {
        record["identifier"]
        for record in source_records
        if isinstance(record.get("identifier"), str)
    }
    plan: dict[str, int] = {}
    for prefix in prefixes:
        candidates = (
            db.query(Indicator)
            .filter(Indicator.is_active.is_(True))
            .filter(Indicator.identifier.startswith(prefix))
            .all()
        )
        plan[prefix] = sum(
            1 for indicator in candidates if indicator.identifier not in source_ids
        )
    return plan


def parse_retirement_impact_approvals(values: list[str] | None) -> dict[str, int]:
    """Parse PREFIX=COUNT approvals for closed-scope retirement."""
    approvals: dict[str, int] = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(
                "retirement approval must use PREFIX=COUNT, " f"got {value!r}"
            )
        prefix, count_text = value.rsplit("=", 1)
        prefix = prefix.strip()
        count_text = count_text.strip()
        if not prefix:
            raise ValueError("retirement approval prefix cannot be blank")
        if prefix in approvals:
            raise ValueError(f"duplicate retirement approval for prefix {prefix}")
        try:
            count = int(count_text)
        except ValueError as exc:
            raise ValueError(
                f"retirement approval count for {prefix} must be an integer"
            ) from exc
        if count < 0:
            raise ValueError(
                f"retirement approval count for {prefix} must be non-negative"
            )
        approvals[prefix] = count
    return approvals


def validate_retirement_approval_counts(
    planned_counts: dict[str, int],
    approved_counts: dict[str, int] | None,
) -> None:
    """Fail closed unless every retirement plan has an exact count approval."""
    approvals = approved_counts or {}
    missing = [
        f"{prefix}={count}"
        for prefix, count in planned_counts.items()
        if prefix not in approvals
    ]
    if missing:
        raise ValueError(
            "closed-scope retirement requires explicit impact approval via "
            "--approve-retirement-impact "
            + ", --approve-retirement-impact ".join(missing)
        )

    mismatched = [
        (prefix, approvals[prefix], count)
        for prefix, count in planned_counts.items()
        if approvals[prefix] != count
    ]
    if mismatched:
        details = "; ".join(
            f"{prefix}: approved {approved}, planned {planned}"
            for prefix, approved, planned in mismatched
        )
        raise ValueError(f"retirement approval count mismatch: {details}")

    extra = sorted(set(approvals) - set(planned_counts))
    if extra:
        raise ValueError(
            "retirement approval provided for unrequested prefix: " + ", ".join(extra)
        )


def seed_indicators(
    csv_path: Path,
    db_url: str,
    dry_run: bool = False,
    resume: bool = False,
    batch_size: int = BATCH_SIZE,
    retire_prefixes: list[str] | None = None,
    retirement_impact_approvals: dict[str, int] | None = None,
) -> int:
    """Seed indicators from CSV into database with batching."""

    start_time = datetime.now()
    start_offset = get_resume_point() if resume else 0

    df = load_csv(csv_path, skip_rows=start_offset)

    if dry_run:
        safe_print(f"⚡ DRY RUN: Would upsert {len(df)} indicators")
        safe_print("   Validating all records without writing")
        analyzed_rows = [normalize_indicator_row(row) for _, row in df.iterrows()]
        invalid_urns = [
            (i, item["canonical_identifier"] or item["original_identifier"])
            for i, item in enumerate(analyzed_rows)
            if not item["valid"]
        ]
        if invalid_urns:
            safe_print(f"⚠️  Found {len(invalid_urns)} invalid URNs:")
            for idx, urn in invalid_urns[:5]:
                safe_print(f"   Row {idx}: {urn}")
            if len(invalid_urns) > 5:
                safe_print(f"   ... and {len(invalid_urns) - 5} more")
            raise ValueError(
                "Indicator import aborted because at least one URN remains invalid after normalization"
            )
        safe_print("   Sample first 3 records:")
        for i, (_, row) in enumerate(df.head(3).iterrows()):
            analysis = analyzed_rows[i]
            identifier = analysis["canonical_identifier"] or "N/A"
            valid = analysis["valid"]
            status = "✓" if valid else "✗ INVALID URN"
            safe_print(
                f"     [{status}] {identifier}: {row.get('title', 'N/A')[:50]}..."
            )
        return len(df)

    # Create engine with connection timeout
    engine = create_engine(
        db_url,
        connect_args={"connect_timeout": 10},
        pool_pre_ping=True,  # Verify connections before using
    )
    init_db_for_engine(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    total_imported = 0
    errors = []

    db = SessionLocal()
    try:
        repo = IndicatorRepository(db)

        # Pre-validate all records before starting
        safe_print("Pre-validating records...")
        analyzed_rows = [normalize_indicator_row(row) for _, row in df.iterrows()]
        all_records = [item["record"] for item in analyzed_rows]

        normalized_urns = [
            (i, item["original_identifier"], item["canonical_identifier"])
            for i, item in enumerate(analyzed_rows)
            if item["normalized"]
        ]
        invalid_urns = [
            (i, item["canonical_identifier"] or item["original_identifier"])
            for i, item in enumerate(analyzed_rows)
            if not item["valid"]
        ]

        if normalized_urns:
            safe_print(
                f"ℹ️  Canonicalized {len(normalized_urns)} legacy indicator URNs"
            )
            for idx, original, canonical in normalized_urns[:5]:
                safe_print(f"   Row {idx}: {original} -> {canonical}")
            if len(normalized_urns) > 5:
                safe_print(f"   ... and {len(normalized_urns) - 5} more")

        if invalid_urns:
            safe_print(f"⚠️  Found {len(invalid_urns)} invalid URNs:")
            for idx, urn in invalid_urns[:5]:
                safe_print(f"   Row {idx}: {urn}")
            if len(invalid_urns) > 5:
                safe_print(f"   ... and {len(invalid_urns) - 5} more")
            raise ValueError(
                "Indicator import aborted because at least one URN remains invalid after normalization"
            )

        retirement_plan = plan_missing_indicators_by_prefix(
            db,
            all_records,
            retire_prefixes,
        )
        validate_retirement_approval_counts(
            retirement_plan,
            retirement_impact_approvals,
        )

        migrated, removed_duplicates = migrate_legacy_identifiers(
            db,
            collect_legacy_identifier_migrations(db, analyzed_rows),
        )
        if migrated or removed_duplicates:
            safe_print(
                f"✓ Migrated {migrated} legacy indicator IDs"
                + (
                    f" and removed {removed_duplicates} stale duplicates"
                    if removed_duplicates
                    else ""
                )
            )

        # Process in batches
        total = len(all_records)
        safe_print(f"\nImporting {total} indicators in batches of {batch_size}...")
        safe_print("Press Ctrl+C to pause (resume with --resume)\n")

        for i in range(0, total, batch_size):
            batch = all_records[i : i + batch_size]
            actual_offset = start_offset + i

            try:
                count = repo.bulk_upsert(batch)
                total_imported += count

                # Save checkpoint
                save_resume_point(actual_offset + len(batch))

                # Print progress
                print_progress(
                    actual_offset + len(batch), start_offset + total, start_time
                )

            except Exception as e:
                db.rollback()
                error_msg = f"Batch at offset {actual_offset}: {str(e)}"
                errors.append(error_msg)
                safe_print(f"\n❌ Error: {error_msg}")
                safe_print(f"   Use --resume to retry from this point")
                raise

        # Success: clear resume
        clear_resume_point()
        safe_print()  # New line after progress bar

        retirement_plan = plan_missing_indicators_by_prefix(
            db,
            all_records,
            retire_prefixes,
        )
        validate_retirement_approval_counts(
            retirement_plan,
            retirement_impact_approvals,
        )
        retired = retire_missing_indicators_by_prefix(
            db,
            all_records,
            retire_prefixes,
        )
        if retired:
            safe_print(
                f"✓ Retired {retired} active indicators outside closed import scopes"
            )

        projection_result = SemanticConceptProjector(db).project(commit=True)
        safe_print(
            "✓ Semantic projection refreshed "
            f"({projection_result.as_dict()['projected_active_indicators']}/"
            f"{projection_result.active_indicators} active indicators)"
        )

        elapsed = (datetime.now() - start_time).total_seconds()
        safe_print(
            f"\n✓ Successfully imported {total_imported} indicators in {elapsed:.1f}s"
        )

        current_total = repo.count()
        current_indicators = repo.get_all(limit=max(current_total, 1), offset=0)
        snapshot_payload = [serialize_indicator(item) for item in current_indicators]
        manifest = build_dataset_manifest(
            dataset="indicators",
            items=snapshot_payload,
            last_modified=max(
                (
                    getattr(item, "updated_at", None)
                    or getattr(item, "created_at", None)
                    for item in current_indicators
                ),
                default=None,
            ),
        )
        snapshot = persist_dataset_snapshot(
            repo=DatasetSnapshotRepository(db),
            dataset="indicators",
            manifest=manifest,
            items=snapshot_payload,
            source_ref=str(csv_path),
            source_hash=file_sha256(csv_path),
            created_by="import_indicators.py",
        )
        safe_print(
            f"✓ Recorded indicators snapshot #{snapshot.id} ({manifest.manifest_hash[:12]})"
        )

        # Show sample
        safe_print(f"\n   Sample indicators:")
        for record in all_records[:3]:
            safe_print(
                f"     - {record['identifier']}: {record.get('title', 'N/A')[:50]}..."
            )

        return total_imported

    except Exception as e:
        db.rollback()
        safe_print(f"\n✗ Error during import: {e}")
        safe_print(f"   Progress saved to {RESUME_FILE}")
        safe_print(f"   Resume with: python {sys.argv[0]} --resume")
        raise
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Import E1 indicators to PostgreSQL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Environment:
  DATABASE_URL    PostgreSQL URL (default: from env or local fallback)

Examples:
  # Import normal
  python %(prog)s

  # Import direct from an Atomizer register-first package
  python %(prog)s --csv <path-to-atomizer-sds-package>/sds_dataset_register.csv

  # Dry run (validate without writing)
  python %(prog)s --dry-run

  # Reanudar tras fallo
  python %(prog)s --resume

  # Batch pequeño (sistemas con poca memoria)
  python %(prog)s --batch-size 25

Resume file location: {RESUME_FILE}
        """,
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help=f"CSV file (default: {DEFAULT_CSV_PATH})",
    )
    parser.add_argument(
        "--db-url", type=str, default=get_default_database_url(), help="PostgreSQL URL"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate without writing"
    )
    parser.add_argument(
        "--resume", action="store_true", help="Resume from last failure point"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"Records per transaction (default: {BATCH_SIZE})",
    )
    parser.add_argument(
        "--retire-prefix",
        action="append",
        default=[],
        help=(
            "Deactivate active indicators whose identifier starts with this prefix "
            "and is absent from the imported CSV. Use only for closed-scope imports."
        ),
    )
    parser.add_argument(
        "--approve-retirement-impact",
        action="append",
        default=[],
        help=(
            "Required for real closed-scope retirement. Use PREFIX=COUNT after "
            "reviewing the target DB impact, for example "
            "urn:sds:reg:ghg:=247."
        ),
    )

    args = parser.parse_args()
    args.csv = resolve_csv_input_path(args.csv)
    try:
        retirement_impact_approvals = parse_retirement_impact_approvals(
            args.approve_retirement_impact
        )
    except ValueError as e:
        safe_print(f"❌ Validation error: {e}")
        return 1

    if args.resume and not RESUME_FILE.exists():
        safe_print("⚠️  No resume point found. Starting from beginning.")
        args.resume = False

    if args.csv is None or not args.csv.exists():
        safe_print("❌ Error: CSV file not found.")
        safe_print("   Supported SDS inputs:")
        safe_print(
            "   - Register-first package: <path-to-atomizer-sds-package>/sds_dataset_register.csv"
        )
        safe_print(f"   - Processed register:      {DEFAULT_CSV_PATH}")
        safe_print("   Legacy fallback generation:")
        safe_print("   python scripts/prepare_atomizer_for_import.py")
        return 1

    try:
        count = seed_indicators(
            args.csv,
            args.db_url,
            args.dry_run,
            args.resume,
            args.batch_size,
            args.retire_prefix,
            retirement_impact_approvals,
        )
        safe_print(f"\n✅ Done! Total indicators: {count}")
        return 0
    except FileNotFoundError as e:
        safe_print(f"❌ File not found: {e}")
        return 1
    except ValueError as e:
        safe_print(f"❌ Validation error: {e}")
        return 1
    except Exception as e:
        safe_print(f"❌ Failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
