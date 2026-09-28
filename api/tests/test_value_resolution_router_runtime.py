from types import SimpleNamespace

import pytest

from src.api.routers import calculations, values
from src.auth.models import UserRole


@pytest.mark.asyncio
async def test_resolve_value_reuses_request_scoped_runtime_context(monkeypatch):
    request_data = object()
    calculation_request = object()
    response = object()
    calculation_response = object()
    db = object()
    unit_converter = object()
    conversion_engine = object()
    mapping_store = object()
    hierarchy_store = object()
    graph = object()
    value_store = object()
    company_scope = object()
    current_user = SimpleNamespace(
        role=UserRole.ANALYST,
        company_id="company-a",
    )
    http_request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(conversion_engine=object()),
        )
    )
    factory_calls = []
    mapping_store_calls = []
    scope_calls = []
    resolution_calls = []
    calculation_calls = []

    def fake_build_conversion_engine(db_session, converter):
        factory_calls.append((db_session, converter))
        return conversion_engine

    def fake_standard_mapping_store(*, db):
        mapping_store_calls.append(db)
        return mapping_store

    def fake_hierarchy_scope_company_id(user):
        scope_calls.append(user)
        return company_scope

    async def fake_run_calculation(request, **kwargs):
        calculation_calls.append((request, kwargs))
        return calculation_response

    async def fake_resolve_value_request(request, **kwargs):
        resolution_calls.append((request, kwargs))
        assert await kwargs["calculate"](calculation_request) is calculation_response
        return response

    monkeypatch.setattr(
        values,
        "build_conversion_engine",
        fake_build_conversion_engine,
        raising=False,
    )
    monkeypatch.setattr(
        values,
        "StandardMappingStore",
        fake_standard_mapping_store,
        raising=False,
    )
    monkeypatch.setattr(
        calculations,
        "_hierarchy_scope_company_id",
        fake_hierarchy_scope_company_id,
    )
    monkeypatch.setattr(calculations, "_run_calculation", fake_run_calculation)
    monkeypatch.setattr(values, "resolve_value_request", fake_resolve_value_request)

    result = await values.resolve_value(
        request_data=request_data,
        http_request=http_request,
        store=value_store,
        unit_converter=unit_converter,
        graph=graph,
        hierarchy_store=hierarchy_store,
        db=db,
        current_user=current_user,
    )

    assert result is response
    assert len(factory_calls) == 1
    assert factory_calls[0][0] is db
    assert factory_calls[0][1] is unit_converter
    assert len(mapping_store_calls) == 1
    assert mapping_store_calls[0] is db
    assert len(scope_calls) == 1
    assert scope_calls[0] is current_user
    assert len(resolution_calls) == 1
    resolved_request, resolution_context = resolution_calls[0]
    assert resolved_request is request_data
    assert resolution_context["value_store"] is value_store
    assert resolution_context["unit_converter"] is unit_converter
    assert resolution_context["mapping_store"] is mapping_store
    assert len(calculation_calls) == 1
    nested_request, calculation_context = calculation_calls[0]
    assert nested_request is calculation_request
    assert calculation_context["graph"] is graph
    assert calculation_context["store"] is value_store
    assert calculation_context["db"] is db
    assert calculation_context["unit_normalizer"] is unit_converter
    assert calculation_context["conversion_engine"] is conversion_engine
    assert calculation_context["hierarchy_store"] is hierarchy_store
    assert calculation_context["hierarchy_company_id"] is company_scope
    assert calculation_context["mapping_store"] is mapping_store


@pytest.mark.asyncio
async def test_resolve_value_offline_does_not_build_conversion_engine(monkeypatch):
    request_data = object()
    calculation_request = object()
    response = object()
    unit_converter = object()
    hierarchy_store = object()
    graph = object()
    value_store = object()
    company_scope = object()
    current_user = SimpleNamespace(
        role=UserRole.ADMIN,
        company_id="ignored-for-admin",
    )
    http_request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(conversion_engine=object()),
        )
    )
    scope_calls = []
    resolution_calls = []
    calculation_calls = []

    def fail_build_conversion_engine(*_args, **_kwargs):
        raise AssertionError("offline resolution must not build a conversion engine")

    def fake_hierarchy_scope_company_id(user):
        scope_calls.append(user)
        return company_scope

    async def fake_run_calculation(request, **kwargs):
        calculation_calls.append((request, kwargs))
        return object()

    async def fake_resolve_value_request(request, **kwargs):
        resolution_calls.append((request, kwargs))
        await kwargs["calculate"](calculation_request)
        return response

    monkeypatch.setattr(
        values,
        "build_conversion_engine",
        fail_build_conversion_engine,
        raising=False,
    )
    monkeypatch.setattr(
        calculations,
        "_hierarchy_scope_company_id",
        fake_hierarchy_scope_company_id,
    )
    monkeypatch.setattr(calculations, "_run_calculation", fake_run_calculation)
    monkeypatch.setattr(values, "resolve_value_request", fake_resolve_value_request)

    result = await values.resolve_value(
        request_data=request_data,
        http_request=http_request,
        store=value_store,
        unit_converter=unit_converter,
        graph=graph,
        hierarchy_store=hierarchy_store,
        db=None,
        current_user=current_user,
    )

    assert result is response
    assert len(scope_calls) == 1
    assert scope_calls[0] is current_user
    assert len(resolution_calls) == 1
    _, resolution_context = resolution_calls[0]
    assert resolution_context["mapping_store"] is None
    assert len(calculation_calls) == 1
    nested_request, calculation_context = calculation_calls[0]
    assert nested_request is calculation_request
    assert calculation_context["graph"] is graph
    assert calculation_context["store"] is value_store
    assert calculation_context["db"] is None
    assert calculation_context["unit_normalizer"] is unit_converter
    assert calculation_context["conversion_engine"] is None
    assert calculation_context["hierarchy_store"] is hierarchy_store
    assert calculation_context["hierarchy_company_id"] is company_scope
    assert calculation_context["mapping_store"] is None
