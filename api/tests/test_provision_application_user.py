"""Behavioral tests for the application-user provisioning CLI."""

from __future__ import annotations

import importlib
import io
import json
import logging
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from src.auth.jwt_handler import jwt_handler
from src.auth.models import UserRole
from src.database.models import UserAccount


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    UserAccount.__table__.create(engine)
    return sessionmaker(bind=engine)


def _request(**overrides):
    values = {
        "username": "service_user",
        "email": "service-user@example.com",
        "full_name": "Service User",
        "company_id": "company_a",
        "role": UserRole.ANALYST,
        "is_active": True,
    }
    values.update(overrides)
    return values


def _seed_user(session_factory, *, password, auth_version=0, **overrides):
    values = _request(**overrides)
    with session_factory.begin() as db:
        db.add(
            UserAccount(
                id=f"user_{values['username']}",
                username=values["username"],
                email=values["email"],
                full_name=values["full_name"],
                company_id=values["company_id"],
                role=values["role"].value,
                is_active=values["is_active"],
                password_hash=jwt_handler.hash_password(password),
                auth_version=auth_version,
            )
        )


def test_provision_creates_a_missing_user_with_requested_fields():
    """A missing requested user must become one exact active account."""
    module = importlib.import_module("scripts.provision_application_user")
    session_factory = _session_factory()
    request = module.ProvisionRequest(**_request())

    result = module.provision_user(
        session_factory=session_factory,
        request=request,
        password="Synthetic-A1b2-C3d4!",
    )

    assert result == module.ProvisionResult(status="created")
    with session_factory() as db:
        record = db.query(UserAccount).filter_by(username="service_user").one()
        assert record.email == "service-user@example.com"
        assert record.full_name == "Service User"
        assert record.company_id == "company_a"
        assert record.role == UserRole.ANALYST.value
        assert record.is_active is True
        assert record.auth_version == 0
        assert record.password_hash != "Synthetic-A1b2-C3d4!"


def test_provision_verifies_an_exact_existing_user_without_writes():
    """Removing the no-op branch would rewrite an already verified account."""
    module = importlib.import_module("scripts.provision_application_user")
    session_factory = _session_factory()
    password = "Synthetic-A1b2-C3d4!"
    _seed_user(session_factory, password=password, auth_version=3)

    result = module.provision_user(
        session_factory=session_factory,
        request=module.ProvisionRequest(**_request()),
        password=password,
    )

    assert result == module.ProvisionResult(status="verified")
    with session_factory() as db:
        record = db.query(UserAccount).filter_by(username="service_user").one()
        assert record.auth_version == 3
        assert jwt_handler.verify_password(password, record.password_hash)


def test_password_mismatch_without_rotation_leaves_the_user_unchanged():
    """Removing the mismatch guard would silently change an account password."""
    module = importlib.import_module("scripts.provision_application_user")
    session_factory = _session_factory()
    existing_password = "Synthetic-A1b2-C3d4!"
    _seed_user(session_factory, password=existing_password, auth_version=3)
    with session_factory() as db:
        before = db.query(UserAccount).filter_by(username="service_user").one()
        original_hash = before.password_hash

    result = module.provision_user(
        session_factory=session_factory,
        request=module.ProvisionRequest(**_request()),
        password="Synthetic-Q9r8-S7t6!",
    )

    assert result == module.ProvisionResult(status="error", code="PASSWORD_MISMATCH")
    with session_factory() as db:
        after = db.query(UserAccount).filter_by(username="service_user").one()
        assert after.password_hash == original_hash
        assert after.auth_version == 3


def test_profile_drift_leaves_the_user_unchanged_before_password_action():
    """Removing profile comparison could grant an unreviewed privilege change."""
    module = importlib.import_module("scripts.provision_application_user")
    session_factory = _session_factory()
    password = "Synthetic-A1b2-C3d4!"
    _seed_user(session_factory, password=password, auth_version=3)

    result = module.provision_user(
        session_factory=session_factory,
        request=module.ProvisionRequest(**_request(role=UserRole.DATA_MANAGER)),
        password=password,
        rotate_password=True,
    )

    assert result == module.ProvisionResult(status="error", code="PROFILE_DRIFT")
    with session_factory() as db:
        after = db.query(UserAccount).filter_by(username="service_user").one()
        assert after.role == UserRole.ANALYST.value
        assert after.auth_version == 3
        assert jwt_handler.verify_password(password, after.password_hash)


@pytest.mark.parametrize("requested_active", [True, False])
def test_null_active_state_is_profile_drift_without_rotation_writes(requested_active):
    """A NULL active state is never equivalent to either requested boolean."""
    module = importlib.import_module("scripts.provision_application_user")
    session_factory = _session_factory()
    old_password = "Synthetic-A1b2-C3d4!"
    new_password = "Synthetic-Q9r8-S7t6!"
    _seed_user(
        session_factory,
        password=old_password,
        auth_version=3,
    )
    with session_factory.begin() as db:
        db.query(UserAccount).filter_by(username="service_user").update(
            {"is_active": None}
        )
    with session_factory() as db:
        before = db.query(UserAccount).filter_by(username="service_user").one()
        original_hash = before.password_hash
        original_updated_at = before.updated_at
        assert before.is_active is None

    result = module.provision_user(
        session_factory=session_factory,
        request=module.ProvisionRequest(**_request(is_active=requested_active)),
        password=new_password,
        rotate_password=True,
    )

    assert result == module.ProvisionResult(status="error", code="PROFILE_DRIFT")
    with session_factory() as db:
        after = db.query(UserAccount).filter_by(username="service_user").one()
        assert after.is_active is None
        assert after.password_hash == original_hash
        assert after.auth_version == 3
        assert after.updated_at == original_updated_at
        assert jwt_handler.verify_password(old_password, after.password_hash)


def test_explicit_rotation_replaces_only_the_credential_epoch():
    """Removing the compare-and-increment path would leave sessions valid."""
    module = importlib.import_module("scripts.provision_application_user")
    session_factory = _session_factory()
    old_password = "Synthetic-A1b2-C3d4!"
    new_password = "Synthetic-Q9r8-S7t6!"
    _seed_user(session_factory, password=old_password, auth_version=3)
    fixed_updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    with session_factory.begin() as db:
        db.query(UserAccount).filter_by(username="service_user").update(
            {"updated_at": fixed_updated_at}
        )
    with session_factory() as db:
        original_updated_at = (
            db.query(UserAccount).filter_by(username="service_user").one().updated_at
        )

    result = module.provision_user(
        session_factory=session_factory,
        request=module.ProvisionRequest(**_request()),
        password=new_password,
        rotate_password=True,
    )

    assert result == module.ProvisionResult(status="rotated")
    with session_factory() as db:
        record = db.query(UserAccount).filter_by(username="service_user").one()
        assert record.auth_version == 4
        assert record.updated_at != original_updated_at
        assert jwt_handler.verify_password(new_password, record.password_hash)
        assert record.email == "service-user@example.com"
        assert record.full_name == "Service User"
        assert record.company_id == "company_a"
        assert record.role == UserRole.ANALYST.value
        assert record.is_active is True


@pytest.mark.parametrize("stdin_value", ["", "weak", "Placeholder-Value-42!"])
def test_cli_refuses_missing_or_weak_stdin_password_without_echo(stdin_value):
    """Removing stdin validation would permit absent or weak credentials."""
    module = importlib.import_module("scripts.provision_application_user")
    output = io.StringIO()

    exit_code = module.main(
        [
            "--username",
            "service_user",
            "--email",
            "service-user@example.com",
            "--full-name",
            "Service User",
            "--role",
            UserRole.ANALYST.value,
            "--active",
            "true",
        ],
        stdin=io.StringIO(stdin_value),
        stdout=output,
    )

    assert exit_code == 2
    assert json.loads(output.getvalue()) == {
        "status": "error",
        "code": "PASSWORD_INVALID",
    }
    if stdin_value:
        assert stdin_value not in output.getvalue()


@pytest.mark.parametrize(
    ("password", "is_valid"),
    [
        ("Aa1!" + "x" * 68, True),
        ("Aa1!" + "x" * 69, False),
        ("Aa1!" + "é" * 34, True),
        ("Aa1!" + "é" * 34 + "x", False),
    ],
)
def test_password_stdin_enforces_bcrypt_utf8_byte_boundary(password, is_valid):
    """The bcrypt limit is bytes, including multi-byte UTF-8 input."""
    module = importlib.import_module("scripts.provision_application_user")
    stream = io.StringIO(password + "\n")

    if is_valid:
        assert module.read_password_from_stdin(stream) == password
    else:
        with pytest.raises(module.ProvisioningInputError, match="PASSWORD_INVALID"):
            module.read_password_from_stdin(stream)


@pytest.mark.parametrize(
    "stdin_value",
    [
        "Synthetic-A1b2-C3d4!\ntrailing-record",
        "Synthetic-A1b2-C3d4!\n\n",
        "Synthetic-A1b2-\nC3d4!",
    ],
)
def test_password_stdin_rejects_embedded_newlines_and_trailing_records(stdin_value):
    """One stdin record may end with one newline but may not contain another."""
    module = importlib.import_module("scripts.provision_application_user")

    with pytest.raises(module.ProvisioningInputError, match="PASSWORD_INVALID"):
        module.read_password_from_stdin(io.StringIO(stdin_value))


def test_duplicate_or_concurrent_create_failure_is_sanitized_and_rolled_back():
    """Removing transaction rollback could leave a partial duplicate account."""
    module = importlib.import_module("scripts.provision_application_user")
    session_factory = _session_factory()
    _seed_user(
        session_factory,
        password="Synthetic-A1b2-C3d4!",
        username="other_user",
        email="shared@example.com",
    )

    result = module.provision_user(
        session_factory=session_factory,
        request=module.ProvisionRequest(**_request(email="shared@example.com")),
        password="Synthetic-Q9r8-S7t6!",
    )

    assert result == module.ProvisionResult(
        status="error", code="CONCURRENT_MODIFICATION"
    )
    with session_factory() as db:
        assert db.query(UserAccount).count() == 1


def test_database_failure_is_sanitized():
    """Removing error sanitization would expose database implementation details."""
    module = importlib.import_module("scripts.provision_application_user")

    def failing_session_factory():
        raise OperationalError("SELECT", {}, RuntimeError("unavailable"))

    result = module.provision_user(
        session_factory=failing_session_factory,
        request=module.ProvisionRequest(**_request()),
        password="Synthetic-Q9r8-S7t6!",
    )

    assert result == module.ProvisionResult(status="error", code="DATABASE_UNAVAILABLE")


def test_cli_has_no_password_option_and_emits_only_sanitized_json(monkeypatch, capsys):
    """Adding argv password input or echoing it would expose a credential."""
    module = importlib.import_module("scripts.provision_application_user")
    session_factory = _session_factory()
    monkeypatch.setattr(module, "SessionLocal", session_factory)
    candidate = "Synthetic-A1b2-C3d4!"
    output = io.StringIO()

    exit_code = module.main(
        [
            "--username",
            "service_user",
            "--email",
            "service-user@example.com",
            "--full-name",
            "Service User",
            "--company-id",
            "company_a",
            "--role",
            UserRole.ANALYST.value,
            "--active",
            "true",
        ],
        stdin=io.StringIO(candidate + "\n"),
        stdout=output,
    )

    option_strings = {
        option
        for action in module.build_parser()._actions
        for option in action.option_strings
    }
    assert "--password" not in option_strings
    assert exit_code == 0
    assert json.loads(output.getvalue()) == {"status": "created"}
    assert candidate not in output.getvalue()
    captured = capsys.readouterr()
    assert candidate not in captured.out
    assert candidate not in captured.err


def test_cli_suppresses_sql_parameters_for_an_echo_enabled_shared_engine(
    monkeypatch, caplog, capsys
):
    """CLI SQL must not expose credentials or requested profile fields."""
    module = importlib.import_module("scripts.provision_application_user")
    engine = create_engine("sqlite:///:memory:", echo="debug")
    UserAccount.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(module, "SessionLocal", session_factory)
    caplog.set_level(logging.DEBUG, logger="sqlalchemy.engine")
    password = "Synthetic-A1b2-C3d4!"
    username = "leak_test_user"
    email = "leak-test@example.com"
    full_name = "Leak Test User"
    company_id = "company_leak_test"
    _seed_user(
        session_factory,
        password=password,
        username=username,
        email=email,
        full_name=full_name,
        company_id=company_id,
    )
    with session_factory() as db:
        password_hash = (
            db.query(UserAccount).filter_by(username=username).one().password_hash
        )
    caplog.clear()
    capsys.readouterr()

    exit_code = module.main(
        [
            "--username",
            username,
            "--email",
            email,
            "--full-name",
            full_name,
            "--company-id",
            company_id,
            "--role",
            UserRole.ANALYST.value,
            "--active",
            "true",
        ],
        stdin=io.StringIO(password + "\n"),
        stdout=io.StringIO(),
    )

    captured = capsys.readouterr()
    sql_output = caplog.text + captured.out + captured.err

    assert exit_code == 0
    for secret_or_profile_value in (
        password,
        password_hash,
        username,
        email,
        full_name,
        company_id,
    ):
        assert secret_or_profile_value not in sql_output
