#!/usr/bin/env python3
"""Create, verify, or explicitly rotate one application user from stdin."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO

from sqlalchemy import create_engine, func
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
os.chdir(API_ROOT)
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from src.auth.jwt_handler import jwt_handler  # noqa: E402
from src.auth.models import UserBase, UserCreate, UserRole  # noqa: E402
from src.database.models import UserAccount  # noqa: E402
from src.database.session import SessionLocal  # noqa: E402


class ProvisioningInputError(Exception):
    """A command input is unsafe or does not satisfy the operational contract."""


class ProvisioningError(Exception):
    """A provisioning operation failed with a deliberately non-sensitive code."""


@dataclass(frozen=True)
class ProvisionRequest:
    """The exact non-secret account fields that must already match on verify."""

    username: str
    email: str
    full_name: str | None
    company_id: str | None
    role: UserRole
    is_active: bool


@dataclass(frozen=True)
class ProvisionResult:
    """Machine-readable output that never contains account secrets or hashes."""

    status: str
    code: str | None = None

    def as_dict(self) -> dict[str, str]:
        result = {"status": self.status}
        if self.code is not None:
            result["code"] = self.code
        return result


_PASSWORD_MINIMUM_LENGTH = 12
_BCRYPT_MAXIMUM_BYTES = 72
_PLACEHOLDER_PASSWORD_MARKERS = frozenset(
    {
        "changeme",
        "placeholder",
        "password",
        "replace",
        "replacepassword",
        "secret",
    }
)


def _password_maximum_length() -> int:
    """Read the maximum from the existing application password model."""
    for metadata in UserCreate.model_fields["password"].metadata:
        maximum = getattr(metadata, "max_length", None)
        if isinstance(maximum, int):
            return maximum
    raise RuntimeError("application password maximum is unavailable")


def _password_from_single_stdin_record(value: str) -> str:
    """Accept at most one terminal newline and reject every other record marker."""
    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    if "\r" in value or "\n" in value:
        raise ProvisioningInputError("PASSWORD_INVALID")
    return value


def validate_password(password: str) -> None:
    """Reject unusable secrets before any database session is opened."""
    normalized_placeholder = re.sub(r"[\s_-]+", "", password.casefold())
    character_classes = sum(
        (
            any(character.islower() for character in password),
            any(character.isupper() for character in password),
            any(character.isdigit() for character in password),
            any(not character.isalnum() for character in password),
        )
    )
    if (
        len(password) < _PASSWORD_MINIMUM_LENGTH
        or len(password) > _password_maximum_length()
        or len(password.encode("utf-8")) > _BCRYPT_MAXIMUM_BYTES
        or any(
            marker in normalized_placeholder for marker in _PLACEHOLDER_PASSWORD_MARKERS
        )
        or character_classes < 3
    ):
        raise ProvisioningInputError("PASSWORD_INVALID")


def read_password_from_stdin(stdin: TextIO) -> str:
    """Read one bounded password value from stdin without echoing it."""
    password = _password_from_single_stdin_record(
        stdin.read(_password_maximum_length() + 3)
    )
    validate_password(password)
    return password


def validate_request(request: ProvisionRequest) -> None:
    """Apply the existing application's public field constraints without mutation."""
    if request.company_id == "":
        raise ProvisioningInputError("INPUT_INVALID")
    try:
        UserBase(
            username=request.username,
            email=request.email,
            full_name=request.full_name,
            company_id=request.company_id,
            role=request.role,
            is_active=request.is_active,
        )
    except Exception as exc:
        raise ProvisioningInputError("INPUT_INVALID") from exc


def _profile_matches(record: UserAccount, request: ProvisionRequest) -> bool:
    return (
        record.username == request.username
        and record.email == request.email
        and record.full_name == request.full_name
        and record.company_id == request.company_id
        and record.role == request.role.value
        and record.is_active is request.is_active
    )


def _new_user_id(username: str) -> str:
    """Preserve the existing database-backed user-store identity convention."""
    return f"user_{username}"


def _safe_session_factory(
    session_factory: Callable[[], Session],
) -> Callable[[], Session]:
    """Clone a session factory onto a CLI-only, non-logging engine.

    The clone shares the configured pool so its database behavior remains the
    application's behavior, but it never changes the shared engine's echo or
    logger configuration. ``hide_parameters`` is a second guard if an external
    SQLAlchemy logger is enabled for this process.
    """
    session_options = getattr(session_factory, "kw", None)
    if not isinstance(session_options, dict):
        raise ProvisioningError("DATABASE_UNAVAILABLE")
    source_engine = session_options.get("bind")
    if not isinstance(source_engine, Engine):
        raise ProvisioningError("DATABASE_UNAVAILABLE")

    safe_engine = create_engine(
        source_engine.url,
        pool=source_engine.pool,
        echo=False,
        hide_parameters=True,
        logging_name="provision_application_user",
    )
    safe_engine.logger.disabled = True
    safe_session_options = dict(session_options)
    safe_session_options["bind"] = safe_engine
    return sessionmaker(**safe_session_options)


def provision_user(
    *,
    session_factory: Callable[[], Session],
    request: ProvisionRequest,
    password: str,
    rotate_password: bool = False,
) -> ProvisionResult:
    """Create, verify, or explicitly rotate one account in one transaction."""
    try:
        validate_request(request)
        validate_password(password)
    except ProvisioningInputError as exc:
        return ProvisionResult(status="error", code=str(exc))

    try:
        with session_factory() as db:
            with db.begin():
                record = (
                    db.query(UserAccount)
                    .filter(UserAccount.username == request.username)
                    .with_for_update()
                    .one_or_none()
                )
                if record is None:
                    db.add(
                        UserAccount(
                            id=_new_user_id(request.username),
                            username=request.username,
                            email=request.email,
                            full_name=request.full_name,
                            company_id=request.company_id,
                            role=request.role.value,
                            is_active=request.is_active,
                            password_hash=jwt_handler.hash_password(password),
                            auth_version=0,
                        )
                    )
                    db.flush()
                    return ProvisionResult(status="created")

                if not _profile_matches(record, request):
                    raise ProvisioningError("PROFILE_DRIFT")

                if jwt_handler.verify_password(password, record.password_hash):
                    return ProvisionResult(status="verified")

                if not rotate_password:
                    raise ProvisioningError("PASSWORD_MISMATCH")

                rotated = (
                    db.query(UserAccount)
                    .filter(
                        UserAccount.id == record.id,
                        UserAccount.password_hash == record.password_hash,
                        UserAccount.auth_version == record.auth_version,
                    )
                    .update(
                        {
                            "password_hash": jwt_handler.hash_password(password),
                            "auth_version": func.coalesce(UserAccount.auth_version, 0)
                            + 1,
                            "updated_at": func.now(),
                        },
                        synchronize_session=False,
                    )
                )
                if rotated != 1:
                    raise ProvisioningError("CONCURRENT_MODIFICATION")
                return ProvisionResult(status="rotated")
    except ProvisioningError as exc:
        return ProvisionResult(status="error", code=str(exc))
    except IntegrityError:
        return ProvisionResult(status="error", code="CONCURRENT_MODIFICATION")
    except SQLAlchemyError:
        return ProvisionResult(status="error", code="DATABASE_UNAVAILABLE")
    except Exception:
        return ProvisionResult(status="error", code="DATABASE_UNAVAILABLE")


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ProvisioningInputError("INPUT_INVALID")


def _parse_active(value: str) -> bool:
    normalized = value.casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("invalid active value")


def build_parser() -> argparse.ArgumentParser:
    """Build the non-secret CLI contract; passwords deliberately have no flag."""
    parser = _SafeArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--full-name", required=True)
    parser.add_argument("--company-id")
    parser.add_argument(
        "--role", required=True, choices=[role.value for role in UserRole]
    )
    parser.add_argument("--active", required=True, type=_parse_active)
    parser.add_argument("--rotate-password", action="store_true")
    return parser


def _request_from_args(args: argparse.Namespace) -> ProvisionRequest:
    return ProvisionRequest(
        username=args.username,
        email=args.email,
        full_name=args.full_name,
        company_id=args.company_id,
        role=UserRole(args.role),
        is_active=args.active,
    )


def _write_result(stdout: TextIO, result: ProvisionResult) -> None:
    stdout.write(json.dumps(result.as_dict(), separators=(",", ":")) + "\n")


def main(
    argv: list[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Execute the provisioning command with fixed, secret-free output."""
    output = stdout or sys.stdout
    try:
        args = build_parser().parse_args(argv)
        password = read_password_from_stdin(stdin or sys.stdin)
        result = provision_user(
            session_factory=_safe_session_factory(SessionLocal),
            request=_request_from_args(args),
            password=password,
            rotate_password=args.rotate_password,
        )
    except ProvisioningInputError as exc:
        result = ProvisionResult(status="error", code=str(exc))
    except Exception:
        result = ProvisionResult(status="error", code="DATABASE_UNAVAILABLE")
    _write_result(output, result)
    return 0 if result.status != "error" else 2


if __name__ == "__main__":
    raise SystemExit(main())
