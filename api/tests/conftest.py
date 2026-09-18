"""
Configuración global de pytest para el proyecto SustainabilityDataSpace.
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-pytest-minimum-32-chars!")
os.environ.setdefault("REQUIRE_DATABASE", "false")

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

DOCS_ONLY_MARKER = "docs_only"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        f"{DOCS_ONLY_MARKER}: test module does not require API unit-converter setup",
    )


# Mock data para unidades
MOCK_UNITS_DATA = {
    "version": "1.0.0",
    "categories": {
        "mass": {
            "base_unit": "kg",
            "units": {
                "kg": {
                    "name": "kilogram",
                    "symbol": "kg",
                    "conversion_factor": 1,
                    "aliases": ["kilogram"],
                },
                "g": {
                    "name": "gram",
                    "symbol": "g",
                    "conversion_factor": 0.001,
                    "aliases": ["gram"],
                },
                "t": {
                    "name": "tonne",
                    "symbol": "t",
                    "conversion_factor": 1000,
                    "aliases": ["ton", "metric_ton", "tonne"],
                },
            },
        },
        "energy": {
            "base_unit": "J",
            "units": {
                "J": {
                    "name": "joule",
                    "symbol": "J",
                    "conversion_factor": 1,
                    "aliases": ["joule"],
                },
                "kJ": {
                    "name": "kilojoule",
                    "symbol": "kJ",
                    "conversion_factor": 1000,
                    "aliases": ["kilojoule"],
                },
                "MJ": {
                    "name": "megajoule",
                    "symbol": "MJ",
                    "conversion_factor": 1000000,
                    "aliases": ["megajoule"],
                },
                "kWh": {
                    "name": "kilowatt hour",
                    "symbol": "kWh",
                    "conversion_factor": 3600000,
                    "aliases": ["kilowatt_hour"],
                },
                "MWh": {
                    "name": "megawatt hour",
                    "symbol": "MWh",
                    "conversion_factor": 3600000000,
                    "aliases": ["megawatt_hour"],
                },
            },
        },
        "volume": {
            "base_unit": "m³",
            "units": {
                "m³": {
                    "name": "cubic meter",
                    "symbol": "m³",
                    "conversion_factor": 1,
                    "aliases": ["cubic_meter", "m3"],
                },
                "L": {
                    "name": "liter",
                    "symbol": "L",
                    "conversion_factor": 0.001,
                    "aliases": ["liter", "litre"],
                },
                "mL": {
                    "name": "milliliter",
                    "symbol": "mL",
                    "conversion_factor": 0.000001,
                    "aliases": ["milliliter", "millilitre"],
                },
            },
        },
        "temperature": {
            "base_unit": "K",
            "special_conversions": True,
            "units": {
                "K": {
                    "name": "kelvin",
                    "symbol": "K",
                    "conversion_factor": 1,
                    "conversion_offset": 0,
                    "aliases": ["kelvin"],
                },
                "°C": {
                    "name": "celsius",
                    "symbol": "°C",
                    "conversion_factor": 1,
                    "conversion_offset": 273.15,
                    "aliases": ["celsius", "C"],
                },
                "°F": {
                    "name": "fahrenheit",
                    "symbol": "°F",
                    "conversion_factor": 0.5555555556,
                    "conversion_offset": 255.3722222,
                    "aliases": ["fahrenheit", "F"],
                },
            },
        },
        "emissions": {
            "base_unit": "kg CO2e",
            "units": {
                "kg CO2e": {
                    "name": "kilogram CO2 equivalent",
                    "symbol": "kg CO2e",
                    "conversion_factor": 1,
                    "aliases": ["kg_co2e", "kgCO2e"],
                },
                "t CO2e": {
                    "name": "tonne CO2 equivalent",
                    "symbol": "t CO2e",
                    "conversion_factor": 1000,
                    "aliases": ["t_co2e", "tCO2e"],
                },
            },
        },
    },
    "custom_conversion_rules": [],
}


@pytest.fixture
def mock_units_database():
    """Fixture que proporciona una base de datos de unidades mock."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(MOCK_UNITS_DATA, f)
        temp_path = f.name

    yield temp_path

    # Cleanup
    Path(temp_path).unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def mock_unit_converter_database(request, mock_units_database):
    """Auto-fixture que mockea la base de datos de unidades para todos los tests."""
    if request.node.get_closest_marker(DOCS_ONLY_MARKER):
        yield
        return

    from decimal import Decimal

    from src.calculation.unit_converter import (
        ConversionRule,
        UnitCategory,
        UnitDefinition,
    )

    # Create mock unit definitions
    mock_unit_definitions = [
        UnitDefinition(
            "kg",
            "kilogram",
            UnitCategory.MASS,
            "kg",
            Decimal("1"),
            aliases=["kilogram"],
        ),
        UnitDefinition(
            "g", "gram", UnitCategory.MASS, "kg", Decimal("0.001"), aliases=["gram"]
        ),
        UnitDefinition(
            "t",
            "tonne",
            UnitCategory.MASS,
            "kg",
            Decimal("1000"),
            aliases=["ton", "metric_ton", "tonne"],
        ),
        UnitDefinition(
            "lb",
            "pound",
            UnitCategory.MASS,
            "kg",
            Decimal("0.453592"),
            aliases=["pound", "lbs"],
        ),
        UnitDefinition(
            "oz",
            "ounce",
            UnitCategory.MASS,
            "kg",
            Decimal("0.0283495"),
            aliases=["ounce"],
        ),
        UnitDefinition(
            "J", "joule", UnitCategory.ENERGY, "J", Decimal("1"), aliases=["joule"]
        ),
        UnitDefinition(
            "kJ",
            "kilojoule",
            UnitCategory.ENERGY,
            "J",
            Decimal("1000"),
            aliases=["kilojoule"],
        ),
        UnitDefinition(
            "MJ",
            "megajoule",
            UnitCategory.ENERGY,
            "J",
            Decimal("1000000"),
            aliases=["megajoule"],
        ),
        UnitDefinition(
            "GJ",
            "gigajoule",
            UnitCategory.ENERGY,
            "J",
            Decimal("1000000000"),
            aliases=["gigajoule"],
        ),
        UnitDefinition(
            "kWh",
            "kilowatt hour",
            UnitCategory.ENERGY,
            "J",
            Decimal("3600000"),
            aliases=["kilowatt_hour"],
        ),
        UnitDefinition(
            "MWh",
            "megawatt hour",
            UnitCategory.ENERGY,
            "J",
            Decimal("3600000000"),
            aliases=["megawatt_hour"],
        ),
        UnitDefinition(
            "m²",
            "square meter",
            UnitCategory.AREA,
            "m²",
            Decimal("1"),
            aliases=["square_meter", "m2"],
        ),
        UnitDefinition(
            "km²",
            "square kilometer",
            UnitCategory.AREA,
            "m²",
            Decimal("1000000"),
            aliases=["square_kilometer", "km2"],
        ),
        UnitDefinition(
            "ha",
            "hectare",
            UnitCategory.AREA,
            "m²",
            Decimal("10000"),
            aliases=["hectare"],
        ),
        UnitDefinition(
            "m³",
            "cubic meter",
            UnitCategory.VOLUME,
            "m³",
            Decimal("1"),
            aliases=["cubic_meter", "m3"],
        ),
        UnitDefinition(
            "L",
            "liter",
            UnitCategory.VOLUME,
            "m³",
            Decimal("0.001"),
            aliases=["liter", "litre"],
        ),
        UnitDefinition(
            "mL",
            "milliliter",
            UnitCategory.VOLUME,
            "m³",
            Decimal("0.000001"),
            aliases=["milliliter", "millilitre"],
        ),
        UnitDefinition(
            "K",
            "kelvin",
            UnitCategory.TEMPERATURE,
            "K",
            Decimal("1"),
            aliases=["kelvin"],
        ),
        UnitDefinition(
            "°C",
            "celsius",
            UnitCategory.TEMPERATURE,
            "K",
            Decimal("1"),
            Decimal("273.15"),
            aliases=["celsius", "C"],
        ),
        UnitDefinition(
            "°F",
            "fahrenheit",
            UnitCategory.TEMPERATURE,
            "K",
            Decimal("0.5555555556"),
            Decimal("255.3722222"),
            aliases=["fahrenheit", "F"],
        ),
        UnitDefinition(
            "kg CO2e",
            "kilogram CO2 equivalent",
            UnitCategory.EMISSIONS,
            "kg CO2e",
            Decimal("1"),
            aliases=["kg_co2e", "kgCO2e"],
        ),
        UnitDefinition(
            "t CO2e",
            "tonne CO2 equivalent",
            UnitCategory.EMISSIONS,
            "kg CO2e",
            Decimal("1000"),
            aliases=["t_co2e", "tCO2e"],
        ),
    ]

    # Create mock conversion rules
    mock_conversion_rules = []

    # Patch the UnitDatabase constructor to use our mock data
    with patch("src.calculation.unit_database.UnitDatabase") as mock_db_class:
        mock_db_instance = MagicMock()
        mock_db_instance.get_unit_definitions.return_value = mock_unit_definitions
        mock_db_instance.get_conversion_rules.return_value = mock_conversion_rules
        mock_db_class.return_value = mock_db_instance
        yield


@pytest.fixture
def mock_database_session():
    """Mock para sesión de base de datos."""
    mock_session = MagicMock()
    mock_session.query.return_value.count.return_value = 10
    mock_session.query.return_value.all.return_value = []
    mock_session.add.return_value = None
    mock_session.commit.return_value = None
    mock_session.flush.return_value = None

    yield mock_session


@pytest.fixture(scope="session")
def admin_token():
    """Session-scoped ADMIN token (avoid slow /auth/login per test)."""
    from src.auth.jwt_handler import jwt_handler
    from src.auth.models import UserRole

    return jwt_handler.create_access_token(
        user_id="admin-001",
        username="admin",
        role=UserRole.ADMIN,
        company_id="acme",
    )


@pytest.fixture(scope="session")
def data_manager_token():
    """Session-scoped DATA_MANAGER token (avoid slow /auth/login per test)."""
    from src.auth.jwt_handler import jwt_handler
    from src.auth.models import UserRole

    return jwt_handler.create_access_token(
        user_id="dm-001",
        username="data_manager",
        role=UserRole.DATA_MANAGER,
        company_id="acme",
    )


@pytest.fixture(scope="session")
def analyst_token():
    """Session-scoped ANALYST token (avoid slow /auth/login per test)."""
    from src.auth.jwt_handler import jwt_handler
    from src.auth.models import UserRole

    return jwt_handler.create_access_token(
        user_id="analyst-001",
        username="analyst",
        role=UserRole.ANALYST,
        company_id="acme",
    )


@pytest.fixture(scope="session")
def viewer_token():
    """Session-scoped VIEWER token (avoid slow /auth/login per test)."""
    from src.auth.jwt_handler import jwt_handler
    from src.auth.models import UserRole

    return jwt_handler.create_access_token(
        user_id="viewer-001",
        username="viewer",
        role=UserRole.VIEWER,
        company_id="acme",
    )


@pytest.fixture
def client(mock_unit_converter_database):
    """Synchronous HTTP client bound to the FastAPI app (no TestClient/threads).

    Starlette/FastAPI TestClient relies on AnyIO blocking portals, which can
    hang in some Python/AnyIO combinations. This wrapper executes requests via
    httpx.AsyncClient + ASGITransport on a dedicated event loop, avoiding portals.
    """
    import asyncio

    import httpx

    from src.api.main import app
    from src.api.rate_limit import limiter

    class SyncASGIClient:
        def __init__(
            self, loop: asyncio.AbstractEventLoop, async_client: httpx.AsyncClient
        ):
            self._loop = loop
            self._client = async_client
            self.base_url = "http://testserver"

        def request(self, method: str, url: str, **kwargs):
            response = self._loop.run_until_complete(
                self._client.request(method, url, **kwargs)
            )
            self._loop.run_until_complete(response.aread())
            return response

        def get(self, url: str, **kwargs):
            return self.request("GET", url, **kwargs)

        def post(self, url: str, **kwargs):
            return self.request("POST", url, **kwargs)

        def put(self, url: str, **kwargs):
            return self.request("PUT", url, **kwargs)

        def delete(self, url: str, **kwargs):
            return self.request("DELETE", url, **kwargs)

    policy = asyncio.get_event_loop_policy()
    previous_loop = getattr(getattr(policy, "_local", None), "_loop", None)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Clear cross-test rate-limit state so auth/login tests don't bleed into one another.
    limiter._storage.reset()

    # httpx.ASGITransport (0.25.x) does not manage ASGI lifespan events.
    # Run FastAPI's lifespan manually so app.state is initialized (unit_converter, etc.).
    lifespan_cm = app.router.lifespan_context(app)
    loop.run_until_complete(lifespan_cm.__aenter__())

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async_client = httpx.AsyncClient(transport=transport, base_url="http://testserver")

    client_instance = SyncASGIClient(loop=loop, async_client=async_client)
    try:
        yield client_instance
    finally:
        try:
            limiter._storage.reset()
        except Exception:
            pass

        try:
            loop.run_until_complete(async_client.aclose())
        except Exception:
            pass

        try:
            loop.run_until_complete(lifespan_cm.__aexit__(None, None, None))
        except Exception:
            pass

        try:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except Exception:
            pass

        try:
            loop.close()
        finally:
            if previous_loop is not None and not previous_loop.is_closed():
                asyncio.set_event_loop(previous_loop)
            else:
                asyncio.set_event_loop(None)
