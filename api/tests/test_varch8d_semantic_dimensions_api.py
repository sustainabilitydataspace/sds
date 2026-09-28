"""VARCH-8d contract tests — /api/v1/semantic-dimensions public temporal-read endpoint.

Exercises the HTTP surface via the app client: auth gating, the response_pins envelope, optional
bitemporal selectors (default latest published), centralized scope authorization, and the
snapshot_required / offset fallback. Uses the offline app (no DB) — the endpoint returns 503 when
no DB session is available, which is itself the documented contract for canonical reads; the
DB-backed path is covered by the resolver-read + catalog repo unit/smoke tests.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.semantic.read_cursor import BitemporalCursor

# The FastAPI app requires slowapi + email-validator (EmailStr in auth.models). This env
# deliberately omits them (the offline suite runs VARCH tests by path), so the app — and any
# TestClient test — cannot import here. Skip the HTTP-layer tests gracefully rather than erroring
# on collection; the router code is compile-checked and codex-reviewed, and the DB-facing catalog
# repo is covered by test_varch8d_catalog_repo.py against the disposable PG.
if (
    importlib.util.find_spec("slowapi") is None
    or importlib.util.find_spec("email_validator") is None
):
    pytest.skip(
        "API app deps (slowapi / email-validator) not installed in this env",
        allow_module_level=True,
    )


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_requires_authentication(client) -> None:
    resp = client.get("/api/v1/semantic-dimensions")
    assert resp.status_code in (401, 403)


def test_endpoint_is_registered(client, admin_token) -> None:
    # The route exists and enforces auth + DB availability (503 without a DB session in the
    # offline app), never 404 (which would mean the router was not mounted).
    resp = client.get("/api/v1/semantic-dimensions", headers=_auth(admin_token))
    assert resp.status_code != 404
    assert resp.status_code in (200, 503)


def test_openapi_lists_endpoint_and_selectors(client) -> None:
    spec = client.get("/openapi.json").json()
    assert "/api/v1/semantic-dimensions" in spec["paths"]
    params = {
        p["name"]
        for p in spec["paths"]["/api/v1/semantic-dimensions"]["get"].get(
            "parameters", []
        )
    }
    # the ratified selectors must be exposed as query params.
    assert {
        "valid_as_of",
        "decision_as_of",
        "as_of_commit_id",
        "scope",
        "cursor",
    } <= params


def test_response_model_declares_pins(client) -> None:
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    assert "SemanticDimensionsResponse" in schemas
    pin_schema = schemas["ReadPins"]["properties"]
    for pin in (
        "resolved_decision_commit_id",
        "valid_time_slice",
        "effective_version_set_hash",
        "replay_manifest_id",
        "replay_manifest_version",
        "replay_manifest_hash",
        "catalog_projection_version",
    ):
        assert pin in pin_schema, f"missing response pin {pin}"


@pytest.mark.parametrize(
    "selector",
    ["valid_as_of=2026-01-01T00:00:00%2B00:00", "decision_as_of=1", "scope=public"],
)
def test_selectors_accepted_without_404(client, admin_token, selector) -> None:
    resp = client.get(
        f"/api/v1/semantic-dimensions?{selector}", headers=_auth(admin_token)
    )
    assert resp.status_code != 404
    assert resp.status_code in (200, 400, 503)


_HASH_A = "a" * 64
_HASH_B = "b" * 64
_READ_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _admin_user() -> SimpleNamespace:
    return SimpleNamespace(role=SimpleNamespace(value="admin"), company_id=None)


def _cursor(*, offset: int, evs_hash: str = _HASH_A) -> BitemporalCursor:
    return BitemporalCursor(
        endpoint="/api/v1/semantic-dimensions",
        query_filters={"scope": "shared"},
        valid_time_selector=_READ_TIME.isoformat(),
        decision_commit_id=7,
        effective_version_set_hash=evs_hash,
        replay_manifest_hash=_HASH_B,
        sort_keys=("axis_key",),
        page_boundary={"offset": offset},
        authorization_scope_class="shared",
        projection_version="sds-semantic-dimensions-v1",
    )


def _read_result(*, members: tuple[str, ...] = ("contract:waste_treatment_type",)):
    return SimpleNamespace(
        context=SimpleNamespace(valid_as_of=_READ_TIME, decision_commit_id=7),
        effective_version_set=SimpleNamespace(evs_hash=_HASH_A),
        members=tuple(
            SimpleNamespace(subject_kind="calculation_dimension", subject_ref=ref)
            for ref in members
        ),
        replay_manifest={"manifest_id": "manifest-1", "manifest_version": "v1"},
        snapshot_required=False,
    )


def _install_route_fakes(monkeypatch, *, rows: list[dict], read=None) -> dict:
    from src.api.routers import semantic_dimensions as route

    calls: dict[str, list[dict]] = {"count": [], "list": [], "read": []}
    read = read or _read_result()

    class FakeResolverRepository:
        def __init__(self, db):
            self.db = db

        def latest_committed_commit_id(self):
            return 7

    class FakeCatalogRepository:
        def __init__(self, db):
            self.db = db

        def count_published_axes(self, **kwargs):
            calls["count"].append(kwargs)
            axis_keys = kwargs.get("axis_keys")
            if axis_keys is None:
                return len(rows)
            return len([row for row in rows if row["axis_key"] in axis_keys])

        def list_published_axes(self, **kwargs):
            calls["list"].append(kwargs)
            axis_keys = kwargs.get("axis_keys")
            selected = rows
            if axis_keys is not None:
                selected = [row for row in rows if row["axis_key"] in axis_keys]
            offset = kwargs["offset"]
            limit = kwargs["limit"]
            return selected[offset : offset + limit]

    def fake_resolve_read_context(*args, **kwargs):
        calls["read"].append(kwargs)
        return read

    monkeypatch.setattr(route, "SemanticResolverRepository", FakeResolverRepository)
    monkeypatch.setattr(route, "SemanticCatalogRepository", FakeCatalogRepository)
    monkeypatch.setattr(route, "resolve_read_context", fake_resolve_read_context)
    monkeypatch.setattr(route.write_replay, "compute_manifest_hash", lambda _: _HASH_B)
    return calls


def test_cursor_continuation_uses_signed_page_boundary(monkeypatch) -> None:
    from src.api.routers import semantic_dimensions as route

    calls = _install_route_fakes(
        monkeypatch,
        rows=[
            {"axis_key": "waste_hazard_status", "label": "Hazard", "description": None},
            {
                "axis_key": "waste_treatment_type",
                "label": "Treatment",
                "description": None,
            },
        ],
        read=_read_result(
            members=("contract:waste_hazard_status", "contract:waste_treatment_type")
        ),
    )
    monkeypatch.setattr(
        route,
        "decode_cursor",
        lambda _token, *, secret: _cursor(offset=1),
        raising=False,
    )

    response = route.list_semantic_dimensions(
        valid_as_of=None,
        decision_as_of=None,
        as_of_commit_id=None,
        cursor="opaque",
        scope="shared",
        limit=1,
        offset=0,
        db=object(),
        current_user=_admin_user(),
    )

    assert response.offset == 1
    assert [item["axis_key"] for item in response.items] == ["waste_treatment_type"]
    assert calls["list"][-1]["offset"] == 1
    assert calls["read"][-1]["request_decision_commit_id"] == 7


def test_tenant_semantic_reads_bind_distinct_scope_contexts(monkeypatch) -> None:
    from src.api.routers import semantic_dimensions as route

    calls = _install_route_fakes(monkeypatch, rows=[])
    for tenant in ("company-a", "company-b"):
        route.list_semantic_dimensions(
            valid_as_of=None,
            decision_as_of=7,
            as_of_commit_id=None,
            cursor=None,
            scope="tenant",
            limit=100,
            offset=0,
            db=object(),
            current_user=SimpleNamespace(
                role=SimpleNamespace(value="viewer"), company_id=tenant
            ),
        )

    assert [call["scope_context"] for call in calls["read"]] == [
        "tenant:company-a",
        "tenant:company-b",
    ]


def test_tenant_cursor_cannot_be_replayed_by_another_tenant(monkeypatch) -> None:
    from src.api.routers import semantic_dimensions as route

    _install_route_fakes(
        monkeypatch,
        rows=[
            {"axis_key": "waste_hazard_status", "label": "Hazard", "description": None},
            {
                "axis_key": "waste_treatment_type",
                "label": "Treatment",
                "description": None,
            },
        ],
        read=_read_result(
            members=("contract:waste_hazard_status", "contract:waste_treatment_type")
        ),
    )
    first = route.list_semantic_dimensions(
        valid_as_of=None,
        decision_as_of=7,
        as_of_commit_id=None,
        cursor=None,
        scope="tenant",
        limit=1,
        offset=0,
        db=object(),
        current_user=SimpleNamespace(
            role=SimpleNamespace(value="viewer"), company_id="company-a"
        ),
    )
    assert first.next_cursor is not None

    with pytest.raises(HTTPException) as exc:
        route.list_semantic_dimensions(
            valid_as_of=None,
            decision_as_of=None,
            as_of_commit_id=None,
            cursor=first.next_cursor,
            scope="tenant",
            limit=1,
            offset=0,
            db=object(),
            current_user=SimpleNamespace(
                role=SimpleNamespace(value="viewer"), company_id="company-b"
            ),
        )

    assert exc.value.status_code == 400


def test_stale_cursor_returns_conflict(monkeypatch) -> None:
    from src.api.routers import semantic_dimensions as route

    _install_route_fakes(
        monkeypatch,
        rows=[
            {
                "axis_key": "waste_treatment_type",
                "label": "Treatment",
                "description": None,
            }
        ],
    )
    monkeypatch.setattr(
        route,
        "decode_cursor",
        lambda _token, *, secret: _cursor(offset=0, evs_hash="c" * 64),
        raising=False,
    )

    with pytest.raises(HTTPException) as exc:
        route.list_semantic_dimensions(
            valid_as_of=None,
            decision_as_of=None,
            as_of_commit_id=None,
            cursor="opaque",
            scope="shared",
            limit=1,
            offset=0,
            db=object(),
            current_user=_admin_user(),
        )

    assert exc.value.status_code == 409


def test_evs_members_bound_semantic_dimension_rows(monkeypatch) -> None:
    from src.api.routers import semantic_dimensions as route

    calls = _install_route_fakes(
        monkeypatch,
        rows=[
            {"axis_key": "outside_axis", "label": "Outside", "description": None},
            {
                "axis_key": "waste_treatment_type",
                "label": "Treatment",
                "description": None,
            },
        ],
    )

    response = route.list_semantic_dimensions(
        valid_as_of=_READ_TIME,
        decision_as_of=7,
        as_of_commit_id=None,
        scope="shared",
        cursor=None,
        limit=100,
        offset=0,
        db=object(),
        current_user=_admin_user(),
    )

    assert response.total == 1
    assert [item["axis_key"] for item in response.items] == ["waste_treatment_type"]
    assert calls["count"][-1]["axis_keys"] == ("waste_treatment_type",)


def test_blank_cursor_is_treated_as_first_page(monkeypatch) -> None:
    from src.api.routers import semantic_dimensions as route

    _install_route_fakes(
        monkeypatch,
        rows=[
            {
                "axis_key": "waste_treatment_type",
                "label": "Treatment",
                "description": None,
            },
        ],
    )

    response = route.list_semantic_dimensions(
        valid_as_of=_READ_TIME,
        decision_as_of=7,
        as_of_commit_id=None,
        scope="shared",
        cursor="",
        limit=100,
        offset=0,
        db=object(),
        current_user=_admin_user(),
    )

    assert [item["axis_key"] for item in response.items] == ["waste_treatment_type"]
