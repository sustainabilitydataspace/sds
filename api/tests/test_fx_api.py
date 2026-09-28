"""API tests for FX catalog, conversion preview, and rate import permissions."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from src.api.main import app
from src.database.session import get_db


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self):
        self.currencies = [
            SimpleNamespace(
                code="EUR",
                numeric_code="978",
                name="Euro",
                minor_units=2,
                valid_from=date(1999, 1, 1),
                valid_to=None,
                is_active=True,
            ),
            SimpleNamespace(
                code="GBP",
                numeric_code="826",
                name="Pound Sterling",
                minor_units=2,
                valid_from=None,
                valid_to=None,
                is_active=True,
            ),
        ]
        self.policies = [
            SimpleNamespace(
                id="ecb-reference-monthly-average",
                name="ECB reference monthly average",
                provider="ECB",
                rate_type="reference",
                selection_mode="monthly_average",
                business_day_rule="previous_available",
                triangulation_allowed=False,
                fallback_behavior="fail_closed",
                rounding_scale=6,
                rounding_mode="ROUND_HALF_UP",
                is_active=True,
            )
        ]

    def query(self, model):
        name = getattr(model, "__name__", "")
        if name == "Currency":
            return _FakeQuery(self.currencies)
        if name == "FXPolicy":
            return _FakeQuery(self.policies)
        return _FakeQuery([])


class _FakeFXService:
    missing_rate = False
    imported_by: str | None = None
    imported_source_hash: str | None = None

    def __init__(self, repository):
        self.repository = repository

    def convert(
        self,
        value,
        *,
        from_currency,
        to_currency,
        value_date,
        period_start,
        period_end,
        policy_id,
    ):
        if self.missing_rate:
            from src.calculation.conversion.fx import FXMissingRateError

            raise FXMissingRateError("FX rate not found for GBP/EUR on 2024-03")
        return SimpleNamespace(
            value=Decimal(str(value)) * Decimal("1.170000"),
            currency=to_currency,
            trace=[
                {
                    "step_type": "fx",
                    "from_currency": from_currency,
                    "to_currency": to_currency,
                    "rate_observation_id": "fx-1",
                    "policy_id": policy_id,
                }
            ],
        )

    def coverage(self, **kwargs):
        return [
            SimpleNamespace(
                id=1,
                base_currency=kwargs["base_currency"],
                quote_currency=kwargs["quote_currency"],
                rate_date=date(2024, 3, 15),
                rate_value=Decimal("1.170000"),
                provider=kwargs["provider"],
                rate_type=kwargs["rate_type"],
                source_hash="hash-1",
            )
        ]

    def import_rates_csv(self, rows, *, created_by=None, **kwargs):
        self.__class__.imported_by = created_by
        self.__class__.imported_source_hash = kwargs["source_hash"]
        return SimpleNamespace(id=42)

    def import_rates_csv_result(self, rows, *, created_by=None, **kwargs):
        batch = self.import_rates_csv(rows, created_by=created_by, **kwargs)
        return SimpleNamespace(
            batch=batch,
            submitted_rows=len(rows),
            created_rows=len(rows),
            updated_rows=0,
            unchanged_rows=0,
        )


def _override_db():
    yield _FakeSession()


def test_fx_currencies_lists_active_currencies(client, viewer_token):
    app.dependency_overrides[get_db] = _override_db
    try:
        response = client.get("/api/v1/fx/currencies", headers=_auth(viewer_token))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [item["code"] for item in payload] == ["EUR", "GBP"]
    assert payload[0]["name"] == "Euro"


def test_fx_policies_lists_active_policies(client, viewer_token):
    app.dependency_overrides[get_db] = _override_db
    try:
        response = client.get("/api/v1/fx/policies", headers=_auth(viewer_token))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload == [
        {
            "id": "ecb-reference-monthly-average",
            "name": "ECB reference monthly average",
            "provider": "ECB",
            "rate_type": "reference",
            "selection_mode": "monthly_average",
            "business_day_rule": "previous_available",
            "triangulation_allowed": False,
            "fallback_behavior": "fail_closed",
            "rounding_scale": 6,
            "rounding_mode": "ROUND_HALF_UP",
        }
    ]


def test_fx_rate_coverage_returns_matching_observations(
    client, viewer_token, monkeypatch
):
    from src.api.routers import fx as fx_router

    monkeypatch.setattr(fx_router, "FXRepository", lambda db: object())
    monkeypatch.setattr(fx_router, "FXService", _FakeFXService)
    app.dependency_overrides[get_db] = _override_db
    try:
        response = client.get(
            "/api/v1/fx/rates/coverage",
            headers=_auth(viewer_token),
            params={
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GBP",
                "quote_currency": "EUR",
                "start": "2024-03-01",
                "end": "2024-03-31",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload == [
        {
            "id": 1,
            "base_currency": "GBP",
            "quote_currency": "EUR",
            "rate_date": "2024-03-15",
            "rate_value": 1.17,
            "provider": "ECB",
            "rate_type": "reference",
            "source_hash": "hash-1",
        }
    ]


def test_fx_rate_coverage_maps_dict_rows_and_generic_failures(
    client, viewer_token, monkeypatch
):
    from src.api.routers import fx as fx_router

    class DictCoverageService(_FakeFXService):
        def coverage(self, **kwargs):
            return [
                {
                    "id": 2,
                    "base_currency": kwargs["base_currency"],
                    "quote_currency": kwargs["quote_currency"],
                    "rate_date": date(2024, 3, 16),
                    "rate_value": Decimal("1.180000"),
                    "provider": kwargs["provider"],
                    "rate_type": kwargs["rate_type"],
                    "source_hash": "hash-2",
                }
            ]

    class ExplodingCoverageService(_FakeFXService):
        def coverage(self, **kwargs):
            raise RuntimeError("backend offline")

    monkeypatch.setattr(fx_router, "FXRepository", lambda db: object())
    app.dependency_overrides[get_db] = _override_db
    try:
        monkeypatch.setattr(fx_router, "FXService", DictCoverageService)
        ok_response = client.get(
            "/api/v1/fx/rates/coverage",
            headers=_auth(viewer_token),
            params={
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GBP",
                "quote_currency": "EUR",
                "start": "2024-03-01",
                "end": "2024-03-31",
            },
        )
        monkeypatch.setattr(fx_router, "FXService", ExplodingCoverageService)
        error_response = client.get(
            "/api/v1/fx/rates/coverage",
            headers=_auth(viewer_token),
            params={
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GBP",
                "quote_currency": "EUR",
                "start": "2024-03-01",
                "end": "2024-03-31",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert ok_response.status_code == 200, ok_response.text
    assert ok_response.json()[0]["id"] == 2
    assert error_response.status_code == 500
    assert "FX coverage lookup failed" in error_response.text


def test_fx_rate_coverage_rejects_invalid_currency_codes(
    client, viewer_token, monkeypatch
):
    from src.api.routers import fx as fx_router

    monkeypatch.setattr(fx_router, "FXRepository", lambda db: object())
    monkeypatch.setattr(fx_router, "FXService", _FakeFXService)
    app.dependency_overrides[get_db] = _override_db
    try:
        response = client.get(
            "/api/v1/fx/rates/coverage",
            headers=_auth(viewer_token),
            params={
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GB1",
                "quote_currency": "EUR",
                "start": "2024-03-01",
                "end": "2024-03-31",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "currency code" in response.text


def test_fx_convert_returns_converted_value_and_trace(
    client, viewer_token, monkeypatch
):
    from src.api.routers import fx as fx_router

    monkeypatch.setattr(fx_router, "FXRepository", lambda db: object())
    monkeypatch.setattr(fx_router, "FXService", _FakeFXService)
    app.dependency_overrides[get_db] = _override_db
    try:
        response = client.post(
            "/api/v1/fx/convert",
            headers=_auth(viewer_token),
            json={
                "value": 100,
                "from_currency": "GBP",
                "to_currency": "EUR",
                "value_date": "2024-03-15",
                "period_start": "2024-03-01",
                "period_end": "2024-03-31",
                "fx_policy_id": "ecb-reference-monthly-average",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["original_value"] == 100.0
    assert payload["converted_value"] == 117.0
    assert payload["from_currency"] == "GBP"
    assert payload["to_currency"] == "EUR"
    assert payload["trace"][0]["rate_observation_id"] == "fx-1"


def test_fx_convert_missing_rate_returns_clear_400(client, viewer_token, monkeypatch):
    from src.api.routers import fx as fx_router

    class MissingRateService(_FakeFXService):
        missing_rate = True

    monkeypatch.setattr(fx_router, "FXRepository", lambda db: object())
    monkeypatch.setattr(fx_router, "FXService", MissingRateService)
    app.dependency_overrides[get_db] = _override_db
    try:
        response = client.post(
            "/api/v1/fx/convert",
            headers=_auth(viewer_token),
            json={
                "value": 100,
                "from_currency": "GBP",
                "to_currency": "EUR",
                "value_date": "2024-03-15",
                "period_start": "2024-03-01",
                "period_end": "2024-03-31",
                "fx_policy_id": "ecb-reference-monthly-average",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "FX rate not found" in response.text


def test_fx_convert_ambiguous_rate_returns_clear_400(client, viewer_token, monkeypatch):
    from src.api.routers import fx as fx_router

    class AmbiguousRateService(_FakeFXService):
        def convert(self, *args, **kwargs):
            from src.calculation.conversion.fx import FXAmbiguousRateError

            raise FXAmbiguousRateError("ambiguous FX daily rate")

    monkeypatch.setattr(fx_router, "FXRepository", lambda db: object())
    monkeypatch.setattr(fx_router, "FXService", AmbiguousRateService)
    app.dependency_overrides[get_db] = _override_db
    try:
        response = client.post(
            "/api/v1/fx/convert",
            headers=_auth(viewer_token),
            json={
                "value": 100,
                "from_currency": "GBP",
                "to_currency": "EUR",
                "value_date": "2024-03-15",
                "period_start": "2024-03-01",
                "period_end": "2024-03-31",
                "fx_policy_id": "ecb-reference-monthly-average",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "ambiguous FX daily rate" in response.text


def test_fx_convert_unexpected_error_returns_500(client, viewer_token, monkeypatch):
    from src.api.routers import fx as fx_router

    class ExplodingConvertService(_FakeFXService):
        def convert(self, *args, **kwargs):
            raise RuntimeError("unexpected converter failure")

    monkeypatch.setattr(fx_router, "FXRepository", lambda db: object())
    monkeypatch.setattr(fx_router, "FXService", ExplodingConvertService)
    app.dependency_overrides[get_db] = _override_db
    try:
        response = client.post(
            "/api/v1/fx/convert",
            headers=_auth(viewer_token),
            json={
                "value": 100,
                "from_currency": "GBP",
                "to_currency": "EUR",
                "value_date": "2024-03-15",
                "period_start": "2024-03-01",
                "period_end": "2024-03-31",
                "fx_policy_id": "ecb-reference-monthly-average",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert "Internal server error" in response.text


def test_fx_rate_import_forbidden_for_viewer_and_allowed_for_data_manager(
    client, viewer_token, data_manager_token, admin_token, monkeypatch
):
    from src.api.routers import fx as fx_router

    _FakeFXService.imported_by = None
    monkeypatch.setattr(fx_router, "FXRepository", lambda db: object())
    monkeypatch.setattr(fx_router, "FXService", _FakeFXService)
    app.dependency_overrides[get_db] = _override_db
    payload = {
        "provider": "ECB",
        "rate_type": "reference",
        "base_currency": "GBP",
        "source_hash": "a" * 64,
        "rows": [
            {
                "quote_currency": "EUR",
                "rate_date": "2024-03-15",
                "rate_value": "1.170000",
            }
        ],
    }
    try:
        viewer_response = client.post(
            "/api/v1/fx/rates/import",
            headers=_auth(viewer_token),
            json=payload,
        )
        data_manager_response = client.post(
            "/api/v1/fx/rates/import",
            headers=_auth(data_manager_token),
            json=payload,
        )
        admin_response = client.post(
            "/api/v1/fx/rates/import",
            headers=_auth(admin_token),
            json=payload,
        )
    finally:
        app.dependency_overrides.clear()

    assert viewer_response.status_code == 403
    assert data_manager_response.status_code == 201, data_manager_response.text
    assert admin_response.status_code == 201, admin_response.text
    assert data_manager_response.json()["batch_id"] == 42
    assert data_manager_response.json()["imported_rows"] == 1
    assert data_manager_response.json()["created_rows"] == 1
    assert data_manager_response.json()["updated_rows"] == 0
    assert data_manager_response.json()["unchanged_rows"] == 0
    assert _FakeFXService.imported_by == "admin"


def test_fx_rate_import_forbidden_for_non_manager_roles(
    client,
    analyst_token,
    viewer_token,
    monkeypatch,
):
    from src.api.routers import fx as fx_router

    monkeypatch.setattr(fx_router, "FXRepository", lambda db: object())
    monkeypatch.setattr(fx_router, "FXService", _FakeFXService)
    app.dependency_overrides[get_db] = _override_db
    payload = {
        "provider": "ECB",
        "rate_type": "reference",
        "base_currency": "GBP",
        "source_hash": "a" * 64,
        "rows": [
            {
                "quote_currency": "EUR",
                "rate_date": "2024-03-15",
                "rate_value": "1.170000",
            }
        ],
    }
    try:
        responses = [
            client.post(
                "/api/v1/fx/rates/import",
                headers=_auth(token),
                json=payload,
            )
            for token in (analyst_token, viewer_token)
        ]
    finally:
        app.dependency_overrides.clear()

    assert [response.status_code for response in responses] == [403, 403]


def test_fx_rate_import_rejects_invalid_currency_values_and_non_positive_rates(
    client, data_manager_token, monkeypatch
):
    from src.api.routers import fx as fx_router

    monkeypatch.setattr(fx_router, "FXRepository", lambda db: object())
    monkeypatch.setattr(fx_router, "FXService", _FakeFXService)
    app.dependency_overrides[get_db] = _override_db
    try:
        blank_base_response = client.post(
            "/api/v1/fx/rates/import",
            headers=_auth(data_manager_token),
            json={
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "   ",
                "source_hash": "a" * 64,
                "rows": [
                    {
                        "quote_currency": "EUR",
                        "rate_date": "2024-03-15",
                        "rate_value": "1.170000",
                    }
                ],
            },
        )
        numeric_quote_response = client.post(
            "/api/v1/fx/rates/import",
            headers=_auth(data_manager_token),
            json={
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GBP",
                "source_hash": "a" * 64,
                "rows": [
                    {
                        "quote_currency": "EU1",
                        "rate_date": "2024-03-15",
                        "rate_value": "1.170000",
                    }
                ],
            },
        )
        zero_rate_response = client.post(
            "/api/v1/fx/rates/import",
            headers=_auth(data_manager_token),
            json={
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GBP",
                "source_hash": "a" * 64,
                "rows": [
                    {
                        "quote_currency": "EUR",
                        "rate_date": "2024-03-15",
                        "rate_value": "0",
                    }
                ],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert blank_base_response.status_code == 422
    assert numeric_quote_response.status_code == 422
    assert zero_rate_response.status_code == 422


def test_fx_rate_import_maps_service_errors(client, data_manager_token, monkeypatch):
    from src.api.routers import fx as fx_router

    class ValueErrorImportService(_FakeFXService):
        def import_rates_csv_result(self, rows, **kwargs):
            raise ValueError("duplicate rate row")

    class ExplodingImportService(_FakeFXService):
        def import_rates_csv_result(self, rows, **kwargs):
            raise RuntimeError("write failed")

    payload = {
        "provider": "ECB",
        "rate_type": "reference",
        "base_currency": "GBP",
        "source_hash": "a" * 64,
        "rows": [
            {
                "quote_currency": "EUR",
                "rate_date": "2024-03-15",
                "rate_value": "1.170000",
            }
        ],
    }
    monkeypatch.setattr(fx_router, "FXRepository", lambda db: object())
    app.dependency_overrides[get_db] = _override_db
    try:
        monkeypatch.setattr(fx_router, "FXService", ValueErrorImportService)
        value_error_response = client.post(
            "/api/v1/fx/rates/import",
            headers=_auth(data_manager_token),
            json=payload,
        )
        monkeypatch.setattr(fx_router, "FXService", ExplodingImportService)
        generic_error_response = client.post(
            "/api/v1/fx/rates/import",
            headers=_auth(data_manager_token),
            json=payload,
        )
    finally:
        app.dependency_overrides.clear()

    assert value_error_response.status_code == 400
    assert "duplicate rate row" in value_error_response.text
    assert generic_error_response.status_code == 500
    assert "FX rate import failed" in generic_error_response.text


def test_fx_rate_import_returns_accounting_and_200_for_noop(
    client, data_manager_token, monkeypatch
):
    from src.api.routers import fx as fx_router

    class NoopImportService(_FakeFXService):
        def import_rates_csv_result(self, rows, **kwargs):
            return SimpleNamespace(
                batch=None,
                submitted_rows=len(rows),
                created_rows=0,
                updated_rows=0,
                unchanged_rows=len(rows),
            )

    payload = {
        "provider": "ECB",
        "rate_type": "reference",
        "base_currency": "GBP",
        "source_hash": "a" * 64,
        "rows": [
            {
                "quote_currency": "EUR",
                "rate_date": "2024-03-15",
                "rate_value": "1.170000",
            }
        ],
    }
    monkeypatch.setattr(fx_router, "FXRepository", lambda db: object())
    monkeypatch.setattr(fx_router, "FXService", NoopImportService)
    app.dependency_overrides[get_db] = _override_db
    try:
        response = client.post(
            "/api/v1/fx/rates/import",
            headers=_auth(data_manager_token),
            json=payload,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json()["batch_id"] is None
    assert response.json()["imported_rows"] == 1
    assert response.json()["created_rows"] == 0
    assert response.json()["updated_rows"] == 0
    assert response.json()["unchanged_rows"] == 1


def test_fx_rate_import_requires_sha256_source_hash(
    client, data_manager_token, monkeypatch
):
    from src.api.routers import fx as fx_router

    monkeypatch.setattr(fx_router, "FXRepository", lambda db: object())
    monkeypatch.setattr(fx_router, "FXService", _FakeFXService)
    app.dependency_overrides[get_db] = _override_db
    try:
        short_hash_response = client.post(
            "/api/v1/fx/rates/import",
            headers=_auth(data_manager_token),
            json={
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GBP",
                "source_hash": "hash-1",
                "rows": [
                    {
                        "quote_currency": "EUR",
                        "rate_date": "2024-03-15",
                        "rate_value": "1.170000",
                    }
                ],
            },
        )
        non_hex_hash_response = client.post(
            "/api/v1/fx/rates/import",
            headers=_auth(data_manager_token),
            json={
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GBP",
                "source_hash": "z" * 64,
                "rows": [
                    {
                        "quote_currency": "EUR",
                        "rate_date": "2024-03-15",
                        "rate_value": "1.170000",
                    }
                ],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert short_hash_response.status_code == 422
    assert non_hex_hash_response.status_code == 422


def test_fx_rate_import_normalizes_source_hash_to_lowercase(
    client, data_manager_token, monkeypatch
):
    from src.api.routers import fx as fx_router

    _FakeFXService.imported_source_hash = None
    monkeypatch.setattr(fx_router, "FXRepository", lambda db: object())
    monkeypatch.setattr(fx_router, "FXService", _FakeFXService)
    app.dependency_overrides[get_db] = _override_db
    try:
        response = client.post(
            "/api/v1/fx/rates/import",
            headers=_auth(data_manager_token),
            json={
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GBP",
                "source_hash": "A" * 64,
                "rows": [
                    {
                        "quote_currency": "EUR",
                        "rate_date": "2024-03-15",
                        "rate_value": "1.170000",
                    }
                ],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201, response.text
    assert _FakeFXService.imported_source_hash == "a" * 64


def test_fx_rate_import_rejects_oversized_sync_batches(
    client, data_manager_token, monkeypatch
):
    from src.api.routers import fx as fx_router

    monkeypatch.setattr(fx_router, "FXRepository", lambda db: object())
    monkeypatch.setattr(fx_router, "FXService", _FakeFXService)
    app.dependency_overrides[get_db] = _override_db
    try:
        response = client.post(
            "/api/v1/fx/rates/import",
            headers=_auth(data_manager_token),
            json={
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GBP",
                "source_hash": "a" * 64,
                "rows": [
                    {
                        "quote_currency": "EUR",
                        "rate_date": "2024-03-15",
                        "rate_value": "1.170000",
                    }
                    for _ in range(10_001)
                ],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_fx_rate_import_rejects_empty_rows(client, data_manager_token, monkeypatch):
    from src.api.routers import fx as fx_router

    monkeypatch.setattr(fx_router, "FXRepository", lambda db: object())
    monkeypatch.setattr(fx_router, "FXService", _FakeFXService)
    app.dependency_overrides[get_db] = _override_db
    try:
        response = client.post(
            "/api/v1/fx/rates/import",
            headers=_auth(data_manager_token),
            json={
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GBP",
                "source_hash": "a" * 64,
                "rows": [],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
