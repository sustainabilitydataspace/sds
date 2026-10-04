"""Sanitized, durable diagnostics for server errors, readable by admins.

A record holds exception TYPES, in-repository frame locations, a fixed
classification and allow-listed PostgreSQL diagnostic fields. Exception
messages and arguments, request headers/body/query, SQL text and parameters
are never stored or logged; the only free text kept is a trigger message that
exactly matches a static ``RAISE EXCEPTION`` literal from the migrations.

Retention: 14 days and the newest 500 rows, enforced under an advisory lock
by every capture and before every read (``purge_error_diagnostics``).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session

import structlog
from src.database.models import AdminErrorDiagnostic

logger = structlog.get_logger(__name__)

RETENTION_DAYS = 14
RETENTION_ROWS = 500
MAX_CHAIN = 8
MAX_FRAMES = 30
MAX_FRAMES_PER_EXCEPTION = 10
DIAGNOSTICS_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"sds-error-diagnostics").digest()[:8], "big", signed=True
)
SRC_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = SRC_ROOT.parent
_MIGRATIONS = API_ROOT / "alembic" / "versions"
_STATIC_RAISE = re.compile(r"RAISE EXCEPTION\s+'([^'%\n]+)'\s*;")

_PGCODE_CLASSES = {
    "23505": "db_unique_violation",
    "23503": "db_foreign_key_violation",
    "23502": "db_not_null_violation",
    "23514": "db_check_violation",
    "P0001": "db_trigger_exception",
    "40001": "db_serialization_failure",
    "40P01": "db_serialization_failure",
    "57014": "db_statement_timeout",
}

# Tests point this at a disposable database; production uses SessionLocal.
session_factory: Optional[Callable[[], Session]] = None


def _static_trigger_messages() -> frozenset[str]:
    """Literal, parameter-free RAISE EXCEPTION texts defined by migrations."""
    messages: set[str] = set()
    for path in sorted(_MIGRATIONS.glob("*.py")):
        try:
            messages.update(_STATIC_RAISE.findall(path.read_text(encoding="utf-8")))
        except OSError:
            continue
    return frozenset(messages)


TRIGGER_MESSAGES = _static_trigger_messages()


# --------------------------------------------------------------------------
# Building a sanitized record
# --------------------------------------------------------------------------


def _chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen and len(chain) < MAX_CHAIN:
        seen.add(id(current))
        chain.append(current)
        if current.__cause__ is not None:
            current = current.__cause__
        elif not current.__suppress_context__:
            current = current.__context__
        else:
            current = None
    return chain


def _type_name(exc: BaseException) -> str:
    cls = type(exc)
    return f"{cls.__module__}.{cls.__qualname__}"


def _frames(chain: list[BaseException]) -> list[dict[str, Any]]:
    """File/line/function of frames under api/src only; no source, no locals."""
    frames: list[dict[str, Any]] = []
    for index, exc in enumerate(chain):
        mine = []
        tb = exc.__traceback__
        while tb is not None:
            code = tb.tb_frame.f_code
            try:
                path = Path(code.co_filename).resolve()
                if path.is_relative_to(SRC_ROOT):
                    mine.append(
                        {
                            "exception": index,
                            "file": path.relative_to(API_ROOT).as_posix(),
                            "line": tb.tb_lineno,
                            "function": code.co_name[:100],
                        }
                    )
            except (OSError, ValueError):
                pass
            tb = tb.tb_next
        frames.extend(mine[-MAX_FRAMES_PER_EXCEPTION:])
    return frames[:MAX_FRAMES]


def _db_fields(chain: list[BaseException]) -> Optional[dict[str, Any]]:
    for exc in chain:
        orig = exc.orig if isinstance(exc, DBAPIError) else exc
        pgcode = getattr(orig, "pgcode", None)
        diag = getattr(orig, "diag", None)
        if pgcode is None and diag is None and not isinstance(exc, DBAPIError):
            continue
        primary = getattr(diag, "message_primary", None)
        return {
            "pgcode": pgcode if isinstance(pgcode, str) else None,
            "constraint_name": getattr(diag, "constraint_name", None),
            "table_name": getattr(diag, "table_name", None),
            "column_name": getattr(diag, "column_name", None),
            "trigger_message": primary if primary in TRIGGER_MESSAGES else None,
        }
    return None


def _classify(chain: list[BaseException], db: Optional[dict[str, Any]]) -> str:
    from src.calculation.unit_converter import UnitConversionError
    from src.services.unit_service import UnitConversionError as ServiceUnitError
    from src.services.value_ingest import ValueIngestError

    if any(isinstance(exc, PoolTimeoutError) for exc in chain):
        return "db_connection_error"
    if db is not None:
        pgcode = db.get("pgcode") or ""
        if pgcode in _PGCODE_CLASSES:
            return _PGCODE_CLASSES[pgcode]
        if pgcode.startswith("08") or (
            not pgcode and any(isinstance(e, SAOperationalError) for e in chain)
        ):
            return "db_connection_error"
        return "db_other"
    for kinds, label in (
        ((UnitConversionError, ServiceUnitError), "unit_conversion_error"),
        ((ValueIngestError,), "value_ingest_error"),
        ((ValidationError, ValueError), "validation_error"),
        ((TimeoutError, asyncio.TimeoutError), "timeout"),
    ):
        if any(isinstance(exc, kinds) for exc in chain):
            return label
    return "unknown"


def build_record(request: Any, exc: BaseException, status_code: int) -> dict:
    chain = _chain(exc)
    db = _db_fields(chain)
    route = request.scope.get("route") if request is not None else None
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    return {
        "request_id": str(request_id or f"unknown-{uuid.uuid4().hex}")[:100],
        "method": (getattr(request, "method", None) or "")[:10] or None,
        "route_template": (getattr(route, "path", None) or "")[:300] or None,
        "status_code": int(status_code),
        "classification": _classify(chain, db),
        "exception_types": [_type_name(item) for item in chain],
        "frames": _frames(chain),
        "db": db,
    }


# --------------------------------------------------------------------------
# Durable store
# --------------------------------------------------------------------------


def _new_session() -> Optional[Session]:
    if session_factory is not None:
        return session_factory()
    from src.config.settings import settings

    if not getattr(settings, "require_database", False):
        return None
    from src.database.session import SessionLocal

    return SessionLocal()


def _lock(db: Session) -> None:
    db.execute(text("SET LOCAL statement_timeout = '2s'"))
    db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": DIAGNOSTICS_LOCK_KEY}
    )


def purge_error_diagnostics(db: Session) -> None:
    """Delete expired rows and rows beyond the newest 500 (caller commits).

    Run inside a transaction holding the diagnostics advisory lock.
    """
    db.execute(
        text(
            "DELETE FROM admin_error_diagnostics "
            f"WHERE created_at < now() - interval '{RETENTION_DAYS} days'"
        )
    )
    db.execute(
        text(
            "DELETE FROM admin_error_diagnostics WHERE id IN ("
            "SELECT id FROM admin_error_diagnostics "
            "ORDER BY created_at DESC, id DESC OFFSET :keep)"
        ),
        {"keep": RETENTION_ROWS},
    )


def store_record(record: dict[str, Any]) -> bool:
    """Insert the record and apply retention in ONE locked transaction.

    Fail-open: any error is logged by type only and the caller's response is
    unaffected.
    """
    db = None
    try:
        db = _new_session()
        if db is None or db.get_bind().dialect.name != "postgresql":
            return False
        _lock(db)
        db.execute(
            insert(AdminErrorDiagnostic)
            .values(**record)
            .on_conflict_do_nothing(index_elements=["request_id"])
        )
        purge_error_diagnostics(db)
        db.commit()
        return True
    except Exception as exc:
        if db is not None:
            db.rollback()
        logger.warning("diagnostics_capture_failed", error_type=type(exc).__name__)
        return False
    finally:
        if db is not None:
            db.close()


def record_error_diagnostic(
    request: Any, exc: BaseException, *, status_code: int = 500
) -> Optional[dict[str, Any]]:
    """Log and durably store a sanitized record; never raises."""
    try:
        record = build_record(request, exc, status_code)
    except Exception as failure:
        logger.warning("diagnostics_capture_failed", error_type=type(failure).__name__)
        return None
    logger.error("error_diagnostic", **record)
    if record["classification"] != "db_connection_error":
        # A pool or connection failure would make the capture wait on the
        # same database; the sanitized log line above is kept either way.
        store_record(record)
    return record


# --------------------------------------------------------------------------
# Admin reads
# --------------------------------------------------------------------------


def _purge_before_read(db: Session) -> None:
    try:
        _lock(db)
        purge_error_diagnostics(db)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("diagnostics_purge_failed", error_type=type(exc).__name__)


def _as_dict(row: AdminErrorDiagnostic) -> dict[str, Any]:
    return {
        "request_id": row.request_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "method": row.method,
        "route_template": row.route_template,
        "status_code": row.status_code,
        "classification": row.classification,
        "exception_types": row.exception_types,
        "frames": row.frames,
        "db": row.db,
    }


def _fresh(query):
    return query.filter(
        AdminErrorDiagnostic.created_at
        >= text(f"now() - interval '{RETENTION_DAYS} days'")
    )


def get_error_diagnostic(db: Session, request_id: str) -> Optional[dict[str, Any]]:
    _purge_before_read(db)
    row = _fresh(
        db.query(AdminErrorDiagnostic).filter(
            AdminErrorDiagnostic.request_id == request_id
        )
    ).first()
    result = _as_dict(row) if row is not None else None
    db.rollback()
    return result


def list_error_diagnostics(db: Session, *, limit: int) -> list[dict[str, Any]]:
    _purge_before_read(db)
    rows = (
        _fresh(db.query(AdminErrorDiagnostic))
        .order_by(
            AdminErrorDiagnostic.created_at.desc(), AdminErrorDiagnostic.id.desc()
        )
        .limit(max(1, min(limit, 50)))
        .all()
    )
    result = [_as_dict(row) for row in rows]
    db.rollback()
    return result
