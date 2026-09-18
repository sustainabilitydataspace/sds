"""`/api/v1/semantic-dimensions` — public temporal-read dimension browse (VARCH-8d).

The canonical dimension/term browsing surface, implementing the ratified
``sds:profile:public-temporal-read:v1`` contract: optional bitemporal selectors
(``valid_as_of`` / ``decision_as_of`` / ``as_of_commit_id``) defaulting to latest published; every
response carries the seven response_pins needed to reproduce the read; pagination uses the
immutable bitemporal cursor (VARCH-8b); scope authorization is centralized (VARCH-8a); reads are
materialized through the resolver read path (VARCH-8c).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_active_user, require_permission
from src.auth.models import Permission, User
from src.config.settings import settings
from src.database.session import get_db_optional
from src.semantic import write_replay
from src.semantic.read_cursor import (
    CURSOR_PINNED_OK,
    BitemporalCursor,
    CursorError,
    assert_cursor_matches_request,
    cursor_status,
    decode_cursor,
    encode_cursor,
)
from src.semantic.read_scope import (
    ReadScopeError,
    assert_public_projection_clean,
    resolve_eval_scope,
)
from src.services.resolver_read import SemanticResolverRepository, resolve_read_context
from src.services.semantic_catalog import SemanticCatalogRepository

router = APIRouter()

# the catalog browse resolves through a catalog EVS key + a stable projection version.
CATALOG_EVS_KEY = "catalog:semantic-dimensions"
CATALOG_PROJECTION_VERSION = "sds-semantic-dimensions-v1"


class ReadPins(BaseModel):
    """The response_pins that make a public temporal read reproducible (contract)."""

    resolved_decision_commit_id: int
    valid_time_slice: str
    effective_version_set_hash: Optional[str] = None
    replay_manifest_id: Optional[str] = None
    replay_manifest_version: Optional[str] = None
    replay_manifest_hash: Optional[str] = None
    catalog_projection_version: str


class SemanticDimensionsResponse(BaseModel):
    items: List[Any]
    total: int
    limit: int
    offset: int
    has_more: bool
    next_cursor: Optional[str] = None
    snapshot_required: bool = Field(
        False,
        description="True when no materialized EVS snapshot backs this slice yet.",
    )
    pins: ReadPins


def _cursor_secret() -> bytes:
    secret = settings.export_signing_secret
    raw = (
        secret.get_secret_value()
        if secret is not None
        else settings.jwt_secret_key.get_secret_value()
    )
    return raw.encode("utf-8")


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def _cursor_query_filters(scope: str) -> dict[str, str]:
    return {"scope": scope}


def _cursor_valid_time(cursor: BitemporalCursor) -> datetime:
    selector = cursor.valid_time_selector
    if selector is None:
        raise CursorError("cursor valid_time_selector is required for this endpoint")
    try:
        return _aware(datetime.fromisoformat(selector.replace("Z", "+00:00")))
    except ValueError as exc:
        raise CursorError(
            f"cursor valid_time_selector is invalid: {selector!r}"
        ) from exc


def _cursor_offset(cursor: BitemporalCursor) -> int:
    raw_offset = dict(cursor.page_boundary).get("offset")
    if not isinstance(raw_offset, int) or raw_offset < 0:
        raise CursorError("cursor page_boundary.offset must be a non-negative integer")
    return raw_offset


def _validate_cursor_shape(cursor: BitemporalCursor) -> None:
    if cursor.projection_version != CATALOG_PROJECTION_VERSION:
        raise CursorError("cursor projection_version does not match this projection")
    if tuple(cursor.sort_keys) != ("axis_key",):
        raise CursorError("cursor sort_keys do not match this projection")


def _axis_keys_from_evs_members(members: Iterable[Any]) -> tuple[str, ...]:
    axis_keys: set[str] = set()
    for member in members:
        if getattr(member, "subject_kind", None) != "calculation_dimension":
            continue
        subject_ref = str(getattr(member, "subject_ref", "") or "")
        axis_key = subject_ref.rsplit(":", 1)[-1]
        if axis_key:
            axis_keys.add(axis_key)
    return tuple(sorted(axis_keys))


@router.get(
    "/semantic-dimensions",
    response_model=SemanticDimensionsResponse,
    summary="Browse published semantic dimensions (bitemporal public temporal read)",
    dependencies=[Depends(require_permission(Permission.QUERY_ONTOLOGY))],
)
def list_semantic_dimensions(
    valid_as_of: Optional[datetime] = Query(
        None, description="Valid-time selector; default latest."
    ),
    decision_as_of: Optional[int] = Query(
        None, description="Decision commit id selector; default latest committed."
    ),
    as_of_commit_id: Optional[int] = Query(
        None, description="Alias of decision_as_of."
    ),
    scope: str = Query(
        "shared", description="Authorization scope class: public|shared|tenant."
    ),
    cursor: Optional[str] = Query(
        None,
        description=(
            "Opaque continuation cursor returned as `next_cursor` by the previous "
            "page. Leave blank for the first page."
        ),
    ),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Optional[Session] = Depends(get_db_optional),
    current_user: User = Depends(get_current_active_user),
) -> SemanticDimensionsResponse:
    if db is None:
        raise HTTPException(
            status_code=503, detail="Canonical semantic data is required for this read."
        )

    # centralized scope authorization (VARCH-8a).
    try:
        eval_scope, _eval_tenant = resolve_eval_scope(
            requested_scope_class=scope,
            user_company_id=current_user.company_id,
            is_admin=current_user.role.value == "admin",
        )
    except ReadScopeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    repo = SemanticResolverRepository(db)

    # resolve + PIN the bitemporal coordinates (default latest published, but pinned so the read
    # is reproducible going forward — never an un-pinned "latest").
    if (
        decision_as_of is not None
        and as_of_commit_id is not None
        and decision_as_of != as_of_commit_id
    ):
        raise HTTPException(
            status_code=400,
            detail="decision_as_of and as_of_commit_id must match when both are supplied.",
        )

    decoded_cursor: BitemporalCursor | None = None
    effective_offset = offset
    request_decision = decision_as_of if decision_as_of is not None else as_of_commit_id
    cursor_token = cursor.strip() if cursor is not None else None
    if cursor_token == "":
        cursor_token = None

    if cursor_token is not None:
        try:
            decoded_cursor = decode_cursor(cursor_token, secret=_cursor_secret())
            _validate_cursor_shape(decoded_cursor)
            assert_cursor_matches_request(
                decoded_cursor,
                endpoint="/api/v1/semantic-dimensions",
                authorization_scope_class=eval_scope,
                query_filters=_cursor_query_filters(scope),
            )
            if (
                request_decision is not None
                and request_decision != decoded_cursor.decision_commit_id
            ):
                raise CursorError(
                    "cursor decision_commit_id does not match request selector"
                )
            valid_anchor = _cursor_valid_time(decoded_cursor)
            if valid_as_of is not None and _aware(valid_as_of) != valid_anchor:
                raise CursorError(
                    "cursor valid_time_selector does not match request valid_as_of"
                )
            request_decision = decoded_cursor.decision_commit_id
            effective_offset = _cursor_offset(decoded_cursor)
        except CursorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        if request_decision is None:
            request_decision = repo.latest_committed_commit_id()
            if request_decision is None:
                raise HTTPException(
                    status_code=503,
                    detail="No committed decision sequence is available.",
                )
        valid_anchor = (
            _aware(valid_as_of)
            if valid_as_of is not None
            else datetime.now(timezone.utc)
        )

    try:
        read = resolve_read_context(
            repo,
            evs_key=CATALOG_EVS_KEY,
            scope_context=eval_scope,
            reporting_period=valid_anchor,
            request_decision_commit_id=request_decision,
        )
    except (
        ValueError
    ) as exc:  # resolver / read errors -> 400 (bad temporal coordinates)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ctx = read.context
    catalog = SemanticCatalogRepository(db)
    evs = read.effective_version_set
    axis_keys = _axis_keys_from_evs_members(read.members) if evs is not None else None
    total = catalog.count_published_axes(
        valid_as_of=ctx.valid_as_of,
        decision_commit_id=ctx.decision_commit_id,
        axis_keys=axis_keys,
    )
    items = catalog.list_published_axes(
        valid_as_of=ctx.valid_as_of,
        decision_commit_id=ctx.decision_commit_id,
        limit=limit,
        offset=effective_offset,
        axis_keys=axis_keys,
    )
    # privacy invariant: a public/shared projection must leak no private field (VARCH-8a/4d).
    for item in items:
        assert_public_projection_clean(item)

    manifest = read.replay_manifest
    manifest_hash = (
        write_replay.compute_manifest_hash(manifest) if manifest is not None else None
    )
    if decoded_cursor is not None:
        if evs is None or manifest_hash is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cursor cannot be continued because the pinned materialized "
                    "snapshot is unavailable."
                ),
            )
        status = cursor_status(
            decoded_cursor,
            current_effective_version_set_hash=evs.evs_hash,
            current_replay_manifest_hash=manifest_hash,
        )
        if status != CURSOR_PINNED_OK:
            raise HTTPException(
                status_code=409,
                detail="Cursor is stale or superseded; restart the read.",
            )

    pins = ReadPins(
        resolved_decision_commit_id=ctx.decision_commit_id,
        valid_time_slice=ctx.valid_as_of.isoformat(),
        effective_version_set_hash=evs.evs_hash if evs is not None else None,
        replay_manifest_id=manifest.get("manifest_id") if manifest else None,
        replay_manifest_version=manifest.get("manifest_version") if manifest else None,
        replay_manifest_hash=manifest_hash,
        catalog_projection_version=CATALOG_PROJECTION_VERSION,
    )

    has_more = effective_offset + len(items) < total
    next_cursor: Optional[str] = None
    # an immutable cursor requires a materialized snapshot (it binds the EVS + manifest hashes);
    # without one we surface snapshot_required and fall back to offset paging.
    if has_more and evs is not None and manifest_hash is not None:
        cursor = BitemporalCursor(
            endpoint="/api/v1/semantic-dimensions",
            query_filters={"scope": scope},
            valid_time_selector=ctx.valid_as_of.isoformat(),
            decision_commit_id=ctx.decision_commit_id,
            effective_version_set_hash=evs.evs_hash,
            replay_manifest_hash=manifest_hash,
            sort_keys=("axis_key",),
            page_boundary={"offset": effective_offset + len(items)},
            authorization_scope_class=eval_scope,
            projection_version=CATALOG_PROJECTION_VERSION,
        )
        next_cursor = encode_cursor(
            cursor, secret=_cursor_secret(), key_id=settings.export_signing_key_id
        )

    return SemanticDimensionsResponse(
        items=items,
        total=total,
        limit=limit,
        offset=effective_offset,
        has_more=has_more,
        next_cursor=next_cursor,
        snapshot_required=read.snapshot_required,
        pins=pins,
    )
