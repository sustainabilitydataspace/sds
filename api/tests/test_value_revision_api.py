from __future__ import annotations

import importlib.metadata
import importlib.util
import sys
import types
from datetime import date, datetime
from decimal import Decimal

import pydantic.networks as pydantic_networks
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

email_validator_stub = types.ModuleType("email_validator")


class EmailNotValidError(ValueError):
    pass


def _validate_email(email, **_kwargs):
    local_part, _, domain = email.partition("@")
    return types.SimpleNamespace(
        normalized=email,
        local_part=local_part,
        domain=domain,
        ascii_email=email,
        smtputf8=False,
    )


email_validator_stub.ALLOW_SMTPUTF8 = True
email_validator_stub.ALLOW_EMPTY_LOCAL = False
email_validator_stub.ALLOW_QUOTED_LOCAL = False
email_validator_stub.ALLOW_DOMAIN_LITERAL = False
email_validator_stub.ALLOW_DISPLAY_NAME = False
email_validator_stub.CHECK_DELIVERABILITY = False
email_validator_stub.STRICT = False
email_validator_stub.DEFAULT_TIMEOUT = 15
email_validator_stub.GLOBALLY_DELIVERABLE = True
email_validator_stub.SPECIAL_USE_DOMAIN_NAMES = [
    "arpa",
    "invalid",
    "local",
    "localhost",
    "onion",
    "test",
]
email_validator_stub.TEST_ENVIRONMENT = False
email_validator_stub.TYPE_CHECKING = False
email_validator_stub.EmailNotValidError = EmailNotValidError
email_validator_stub.validate_email = _validate_email
_patches = pytest.MonkeyPatch()
_original_version = importlib.metadata.version


def _version(name: str) -> str:
    if name == "email-validator":
        return "2.0.0"
    return _original_version(name)


# Only stub email_validator when the real package is genuinely unavailable. Installing a
# spec-less ModuleType stub into sys.modules at import time persists through the rest of
# pytest collection (teardown_module runs far too late), so later app-importing tests see
# email_validator.__spec__ is None and pydantic raises during /openapi + TestClient
# collection. When the real dependency is present, use it and patch nothing (codex F14).
if importlib.util.find_spec("email_validator") is None:
    _patches.setitem(sys.modules, "email_validator", email_validator_stub)
    _patches.setattr(importlib.metadata, "version", _version)
    _patches.setattr(pydantic_networks, "version", _version)

from src.api.routers import values as values_router  # noqa: E402
from src.auth.models import Permission, User, UserRole  # noqa: E402
from src.database.models import (  # noqa: E402
    CurrentValuePointer,
    ReportedValuePointer,
    ValueContext,
    ValueRevision,
    ValueRevisionEvent,
)
from src.services.value_revision_store import (  # noqa: E402
    ValueRevisionInput,
    ValueRevisionStore,
)
from src.services.value_versioning import ValueContextIdentity  # noqa: E402


def teardown_module(_module):
    _patches.undo()


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


def _session():
    engine = create_engine("sqlite:///:memory:")
    for table in (
        ValueContext.__table__,
        ValueRevision.__table__,
        ValueRevisionEvent.__table__,
        CurrentValuePointer.__table__,
        ReportedValuePointer.__table__,
    ):
        table.create(engine)
    return sessionmaker(bind=engine)()


def _identity() -> ValueContextIdentity:
    return ValueContextIdentity(
        tenant_id="tenant-a",
        entity_id="entity-1",
        reporting_period_id="FY2026",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        period_close_date=None,
        period_type="annual",
        reporting_boundary_id="operational_control",
        sds_indicator_id="indicator-1",
        indicator_identifier="urn:sds:reg:esrs:e1_6_07",
        standard_release_id="ESRS_SET1_2023_12_22",
        standard_datapoint_id="E1-6_07",
        dimensions={"scope": "scope1"},
        expected_unit="tCO2e",
        expected_currency=None,
        value_kind="numeric",
    )


def _user(
    *,
    role: UserRole = UserRole.VIEWER,
    company_id: str | None = "tenant-a",
) -> User:
    return User(
        id="user-1",
        username="reader",
        email="reader@example.com",
        company_id=company_id,
        role=role,
        permissions=[Permission.READ_VALUES],
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )


def _append_revisions(db):
    store = ValueRevisionStore(db)
    first = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            unit="tCO2e",
            state="approved",
            source_system="manual",
        )
    )
    second = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("2"),
            value_kind="numeric",
            unit="tCO2e",
            state="corrected",
            source_system="manual",
            parent_revision_id=first.revision.id,
            input_revision_ids=[first.revision.id],
        )
    )
    return first, second


@pytest.mark.asyncio
async def test_value_revisions_endpoint_returns_revision_page() -> None:
    db = _session()
    _append_revisions(db)

    page = await values_router.get_value_revisions(
        tenant_id="tenant-a",
        context_id=None,
        state=None,
        limit=10,
        offset=0,
        db=db,
        current_user=_user(),
    )

    assert page.total == 2
    assert page.items[0].revision_number == 2
    assert page.items[0].canonical_value == "2"


@pytest.mark.asyncio
async def test_value_revision_lineage_endpoint_returns_parent_and_inputs() -> None:
    db = _session()
    first, second = _append_revisions(db)

    lineage = await values_router.get_value_revision_lineage(
        revision_id=second.revision.id,
        db=db,
        current_user=_user(),
    )

    assert lineage.revision.id == second.revision.id
    assert lineage.context.context_hash == second.context_hash
    assert lineage.parent_revision_id == first.revision.id
    assert lineage.input_revision_ids == [first.revision.id]


@pytest.mark.asyncio
async def test_value_revision_changes_endpoint_uses_monotonic_event_sequence() -> None:
    db = _session()
    _append_revisions(db)

    page = await values_router.get_value_revision_changes(
        tenant_id="tenant-a",
        after_event_seq=None,
        limit=1,
        db=db,
        current_user=_user(),
    )

    assert page.tenant_id == "tenant-a"
    assert page.items[0].event_seq == 1
    assert page.items[0].context_event_seq == 1
    assert page.next_after_event_seq == 1
    assert page.has_more is True


@pytest.mark.asyncio
async def test_value_revision_endpoints_reject_cross_tenant_reads() -> None:
    db = _session()
    _append_revisions(db)

    with pytest.raises(HTTPException) as revisions_exc:
        await values_router.get_value_revisions(
            tenant_id="tenant-b",
            context_id=None,
            state=None,
            limit=10,
            offset=0,
            db=db,
            current_user=_user(company_id="tenant-a"),
        )
    assert revisions_exc.value.status_code == 403

    with pytest.raises(HTTPException) as changes_exc:
        await values_router.get_value_revision_changes(
            tenant_id="tenant-b",
            after_event_seq=None,
            limit=10,
            db=db,
            current_user=_user(company_id="tenant-a"),
        )
    assert changes_exc.value.status_code == 403


@pytest.mark.asyncio
async def test_value_revision_lineage_hides_foreign_and_unknown_ids_identically() -> (
    None
):
    db = _session()
    _first, second = _append_revisions(db)
    errors = []
    for revision_id in (second.revision.id, "missing-revision-id"):
        with pytest.raises(HTTPException) as lineage_exc:
            await values_router.get_value_revision_lineage(
                revision_id=revision_id,
                db=db,
                current_user=_user(company_id="tenant-b"),
            )
        errors.append((lineage_exc.value.status_code, lineage_exc.value.detail))
    assert errors[0] == errors[1]
    assert errors[0][0] == 404


@pytest.mark.asyncio
async def test_value_context_lineage_hides_foreign_and_unknown_ids_identically() -> (
    None
):
    db = _session()
    first, _second = _append_revisions(db)
    errors = []
    for context_id in (first.context.id, first.context.id + 1000000):
        with pytest.raises(HTTPException) as lineage_exc:
            await values_router.get_value_context_lineage(
                context_id=context_id,
                db=db,
                current_user=_user(company_id="tenant-b"),
            )
        errors.append((lineage_exc.value.status_code, lineage_exc.value.detail))
    assert errors[0] == errors[1]
    assert errors[0][0] == 404


@pytest.mark.asyncio
async def test_bearer_admin_can_read_foreign_lineage() -> None:
    db = _session()
    first, second = _append_revisions(db)
    admin = _user(role=UserRole.ADMIN, company_id="tenant-b").model_copy(
        update={"auth_method": "bearer"}
    )
    revision = await values_router.get_value_revision_lineage(
        revision_id=second.revision.id, db=db, current_user=admin
    )
    context = await values_router.get_value_context_lineage(
        context_id=first.context.id, db=db, current_user=admin
    )
    assert revision.context.tenant_id == context.context.tenant_id == "tenant-a"
