"""VARCH-8e contract tests — backward-compatible temporal selectors on /api/v1/concepts.

The existing /concepts list+detail gain optional valid_as_of/decision_as_of/as_of_commit_id
selectors and reproducibility pins, WITHOUT changing default behavior. Runs via the app client
(requires slowapi + email-validator). The offline app has no DB session, so a temporal read
returns 503 (canonical data required) — which itself proves the selector activates the temporal
path rather than being silently ignored; the default (no selector) path is unchanged with
pins=None.
"""

from __future__ import annotations

import importlib.util

import pytest

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


def test_concepts_default_unchanged_pins_none(client, admin_token) -> None:
    # no selector -> backward-compatible behavior; pins is absent/None.
    resp = client.get("/api/v1/concepts?limit=5", headers=_auth(admin_token))
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body and "total" in body  # unchanged shape
    assert body.get("pins") is None


def test_concepts_selector_activates_temporal_path(client, admin_token) -> None:
    # a temporal selector requires canonical DB data; the offline app returns 503 -> proves the
    # selector is wired (not ignored), not a 404/500.
    resp = client.get("/api/v1/concepts?decision_as_of=1", headers=_auth(admin_token))
    assert resp.status_code == 503


def test_concepts_detail_selector_activates_temporal_path(client, admin_token) -> None:
    resp = client.get(
        "/api/v1/concepts/some:concept?valid_as_of=2026-01-01T00:00:00%2B00:00",
        headers=_auth(admin_token),
    )
    # 503 (temporal path needs DB) — not 404/500; the selector is wired into detail too.
    assert resp.status_code == 503


def test_openapi_concepts_exposes_selectors(client) -> None:
    spec = client.get("/openapi.json").json()
    for path in ("/api/v1/concepts", "/api/v1/concepts/{concept_uri}"):
        params = {p["name"] for p in spec["paths"][path]["get"].get("parameters", [])}
        assert {"valid_as_of", "decision_as_of", "as_of_commit_id"} <= params, path


def test_paginated_and_concept_models_declare_pins(client) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "pins" in schemas["PaginatedResponse"]["properties"]
    assert "pins" in schemas["ConceptInfo"]["properties"]


def test_temporal_pins_helper_fails_closed_without_session() -> None:
    # M1: a direct call with a selector but a non-Session db (e.g. an unresolved Depends marker)
    # must fail closed (503), never reach the repository / 500.
    from fastapi import HTTPException

    from src.api.routers.ontology import _concepts_temporal_pins

    with pytest.raises(HTTPException) as exc:
        _concepts_temporal_pins(
            object(),  # not a Session, not None
            valid_as_of=None,
            decision_as_of=1,
            as_of_commit_id=None,
            current_user=None,  # never reached — the db guard precedes scope/user use
        )
    assert exc.value.status_code == 503


def test_temporal_pins_helper_returns_none_without_selector() -> None:
    from src.api.routers.ontology import _concepts_temporal_pins

    assert (
        _concepts_temporal_pins(
            object(),
            valid_as_of=None,
            decision_as_of=None,
            as_of_commit_id=None,
            current_user=None,
        )
        is None
    )


def test_temporal_pins_bind_explicit_decision_and_utc_anchor(monkeypatch) -> None:
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from sqlalchemy.orm import Session

    from src.api.routers import ontology
    from src.auth.models import User, UserRole

    now = datetime.now(timezone.utc)
    user = User(
        id="test-admin",
        username="test-admin",
        email="admin@example.com",
        role=UserRole.ADMIN,
        created_at=now,
        updated_at=now,
    )
    observed = []
    db = Session()
    fake_repo = object()
    monkeypatch.setattr(
        ontology,
        "SemanticResolverRepository",
        lambda session: (observed.append(session) or fake_repo),
    )

    def resolve(repo, **kwargs):
        assert repo is fake_repo
        assert kwargs["evs_key"] == ontology.CONCEPTS_EVS_KEY
        assert kwargs["scope_context"] == "shared"
        assert kwargs["reporting_period"] == datetime(2026, 1, 2, tzinfo=timezone.utc)
        assert kwargs["request_decision_commit_id"] == 7
        return SimpleNamespace(
            context=SimpleNamespace(
                decision_commit_id=7, valid_as_of=kwargs["reporting_period"]
            ),
            effective_version_set=SimpleNamespace(evs_hash="evs-hash"),
            replay_manifest={"manifest_id": "manifest-7", "manifest_version": "v1"},
        )

    monkeypatch.setattr(ontology, "resolve_read_context", resolve)
    monkeypatch.setattr(
        ontology.write_replay, "compute_manifest_hash", lambda _: "hash-7"
    )
    try:
        pins = ontology._concepts_temporal_pins(
            db,
            valid_as_of=datetime(2026, 1, 2),
            decision_as_of=7,
            as_of_commit_id=99,
            current_user=user,
        )
    finally:
        db.close()
    assert observed == [db]
    assert pins == {
        "resolved_decision_commit_id": 7,
        "valid_time_slice": "2026-01-02T00:00:00+00:00",
        "effective_version_set_hash": "evs-hash",
        "replay_manifest_id": "manifest-7",
        "replay_manifest_version": "v1",
        "replay_manifest_hash": "hash-7",
        "catalog_projection_version": ontology.CONCEPTS_PROJECTION_VERSION,
    }


def test_temporal_pins_refuse_missing_latest_decision(monkeypatch) -> None:
    from datetime import datetime, timezone

    from fastapi import HTTPException
    from sqlalchemy.orm import Session

    from src.api.routers import ontology
    from src.auth.models import User, UserRole

    now = datetime.now(timezone.utc)
    user = User(
        id="test-analyst",
        username="test-analyst",
        email="analyst@example.com",
        role=UserRole.ANALYST,
        created_at=now,
        updated_at=now,
    )

    class EmptyRepo:
        def latest_committed_commit_id(self):
            return None

    monkeypatch.setattr(ontology, "SemanticResolverRepository", lambda _: EmptyRepo())
    db = Session()
    try:
        with pytest.raises(HTTPException) as exc:
            ontology._concepts_temporal_pins(
                db,
                valid_as_of=now,
                decision_as_of=None,
                as_of_commit_id=None,
                current_user=user,
            )
        assert exc.value.status_code == 503
        assert "No committed decision" in exc.value.detail
    finally:
        db.close()
