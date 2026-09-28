from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.models import UnitConversionRequest
from src.api.routers import units
from src.calculation.unit_converter import UnitConversionError


@pytest.mark.asyncio
async def test_get_unit_converter_requires_app_state():
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    with pytest.raises(HTTPException) as exc:
        await units.get_unit_converter(request)

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_unit_conversion_router_success_and_error_edges():
    request = UnitConversionRequest(value=Decimal("2"), from_unit="kg", to_unit="g")
    converter = SimpleNamespace(
        convert=lambda **_kwargs: SimpleNamespace(
            original_value=Decimal("2"),
            original_unit="kg",
            converted_value=Decimal("2000"),
            converted_unit="g",
            conversion_factor=Decimal("1000"),
            formula_used="value * 1000",
        )
    )
    response = await units.convert_units(
        request=request,
        unit_converter=converter,
        current_user=object(),
    )
    assert response.converted_value == Decimal("2000")

    failing_converter = SimpleNamespace(
        convert=lambda **_kwargs: (_ for _ in ()).throw(UnitConversionError("bad unit"))
    )
    with pytest.raises(HTTPException) as exc:
        await units.convert_units(
            request=request,
            unit_converter=failing_converter,
            current_user=object(),
        )
    assert exc.value.status_code == 400

    exploding_converter = SimpleNamespace(
        convert=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with pytest.raises(HTTPException) as exc:
        await units.convert_units(
            request=request,
            unit_converter=exploding_converter,
            current_user=object(),
        )
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_list_and_validate_units_router_error_edges():
    converter = SimpleNamespace(
        get_supported_units=lambda category=None: [
            {
                "symbol": "kg",
                "name": "kilogram",
                "category": "mass",
                "base_unit": "kg",
                "aliases": ["kilogram"],
            }
        ],
        normalize_unit_symbol=lambda unit: unit,
        are_units_compatible=lambda *_args: True,
        get_conversion_path=lambda *_args: ["kg", "g"],
    )
    listed = await units.list_units(
        category="mass",
        unit_converter=converter,
        current_user=object(),
    )
    assert listed[0].symbol == "kg"

    with pytest.raises(HTTPException) as exc:
        await units.list_units(
            category="not-a-category",
            unit_converter=converter,
            current_user=object(),
        )
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await units.list_units(
            category=None,
            unit_converter=SimpleNamespace(
                get_supported_units=lambda category=None: (_ for _ in ()).throw(
                    RuntimeError("list boom")
                )
            ),
            current_user=object(),
        )
    assert exc.value.status_code == 500

    validation = await units.validate_units(
        from_unit="kg",
        to_unit="g",
        unit_converter=converter,
        current_user=object(),
    )
    assert validation["conversion_path"] == ["kg", "g"]

    with pytest.raises(HTTPException) as exc:
        await units.validate_units(
            from_unit="kg",
            to_unit="g",
            unit_converter=SimpleNamespace(
                are_units_compatible=lambda *_args: (_ for _ in ()).throw(
                    RuntimeError("validate boom")
                )
            ),
            current_user=object(),
        )
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_unit_conversion_router_allows_affine_temperature_factor_none():
    request = UnitConversionRequest(value=Decimal("0"), from_unit="°C", to_unit="K")
    converter = SimpleNamespace(
        convert=lambda **_kwargs: SimpleNamespace(
            original_value=Decimal("0"),
            original_unit="°C",
            converted_value=Decimal("273.15"),
            converted_unit="K",
            conversion_factor=None,
            formula_used="temperature conversion via Kelvin",
        )
    )

    response = await units.convert_units(
        request=request,
        unit_converter=converter,
        current_user=object(),
    )

    assert response.conversion_factor is None


@pytest.mark.asyncio
async def test_unit_conversion_router_passes_the_requested_effective_date():
    request = UnitConversionRequest(
        value=Decimal("2"),
        from_unit="legacy_a",
        to_unit="legacy_b",
        as_of=date(2022, 6, 1),
    )
    received: dict = {}

    def convert(**kwargs):
        received.update(kwargs)
        return SimpleNamespace(
            original_value=Decimal("2"),
            original_unit="legacy_a",
            converted_value=Decimal("4"),
            converted_unit="legacy_b",
            conversion_factor=Decimal("2"),
            formula_used="value * 2",
        )

    await units.convert_units(
        request=request,
        unit_converter=SimpleNamespace(convert=convert),
        current_user=object(),
    )

    assert received["as_of"] == date(2022, 6, 1)


@pytest.mark.asyncio
async def test_validate_units_rejects_unknown_units_with_400():
    converter = SimpleNamespace(
        normalize_unit_symbol=lambda *_args: (_ for _ in ()).throw(
            UnitConversionError("Unknown unit: nope")
        )
    )

    with pytest.raises(HTTPException) as exc:
        await units.validate_units(
            from_unit="kg",
            to_unit="nope",
            unit_converter=converter,
            current_user=object(),
        )

    assert exc.value.status_code == 400
    assert "Unknown unit" in exc.value.detail


@pytest.mark.asyncio
async def test_validate_units_does_not_return_compatible_with_empty_path():
    converter = SimpleNamespace(
        normalize_unit_symbol=lambda unit: unit,
        are_units_compatible=lambda *_args: True,
        get_conversion_path=lambda *_args: [],
    )

    validation = await units.validate_units(
        from_unit="kg",
        to_unit="m",
        unit_converter=converter,
        current_user=object(),
    )

    assert validation["compatible"] is False
    assert validation["conversion_path"] == []
