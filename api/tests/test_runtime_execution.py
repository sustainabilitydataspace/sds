import inspect
from datetime import date
from decimal import Decimal

from src.calculation.conversion.fx import FXPolicy
from src.calculation.conversion.orchestrator import ConversionRequest
from src.services.runtime_execution import build_conversion_engine


class FakeSession:
    def __init__(self):
        self.calls = []

    def close(self):
        self.calls.append("close")

    def commit(self):
        self.calls.append("commit")

    def rollback(self):
        self.calls.append("rollback")

    def begin(self):
        self.calls.append("begin")
        raise AssertionError("factory must not start a transaction")

    def __enter__(self):
        self.calls.append("__enter__")
        raise AssertionError("factory must not own the session context")

    def __exit__(self, exc_type, exc, traceback):
        self.calls.append("__exit__")
        raise AssertionError("factory must not own the session context")


class FakeUnitConverter:
    def __init__(self, physical_converter):
        self.physical_converter = physical_converter
        self.calls = []

    def get_physical_converter(self):
        self.calls.append("get_physical_converter")
        return self.physical_converter


class FakePhysicalConverter:
    pass


class FakeFXRepository:
    instances = []

    def __init__(self, db):
        self.db = db
        self.policy_calls = []
        self.daily_rate_calls = []
        FakeFXRepository.instances.append(self)

    def get_policy(self, policy_id):
        self.policy_calls.append(policy_id)
        return FXPolicy(
            id=policy_id,
            provider="fixture",
            rate_type="spot",
            selection_mode="transaction_date",
        )

    def find_daily_rate(self, **kwargs):
        self.daily_rate_calls.append(kwargs)
        return {
            "id": "rate-1",
            "provider": kwargs["provider"],
            "rate_type": kwargs["rate_type"],
            "base_currency": kwargs["base_currency"],
            "quote_currency": kwargs["quote_currency"],
            "rate_date": kwargs["rate_date"],
            "rate_value": Decimal("0.900000"),
        }

    def find_period_rate(self, **kwargs):
        raise AssertionError("transaction-date policy must not use period lookup")


def test_build_conversion_engine_uses_caller_session_and_existing_unit_converter(
    monkeypatch,
):
    from src.services import runtime_execution

    FakeFXRepository.instances = []
    monkeypatch.setattr(runtime_execution, "FXRepository", FakeFXRepository)

    db_session = FakeSession()
    physical_converter = FakePhysicalConverter()
    unit_converter = FakeUnitConverter(physical_converter)

    engine = build_conversion_engine(db_session, unit_converter)

    assert unit_converter.calls == ["get_physical_converter"]
    assert engine.physical_converter is physical_converter
    assert len(FakeFXRepository.instances) == 1
    repository = FakeFXRepository.instances[0]
    assert repository.db is db_session
    assert engine.fx_converter is not None
    assert engine.fx_converter.rate_repository is repository
    assert engine.fx_policy_resolver == repository.get_policy

    result = engine.normalize(
        ConversionRequest(
            value=Decimal("100"),
            unit="kg",
            expected_unit="kg",
            currency="USD",
            expected_currency="EUR",
            value_date=date(2024, 3, 15),
            fx_policy_id="fixture-daily",
        )
    )

    assert result.value == Decimal("90.000000")
    assert result.currency == "EUR"
    assert repository.policy_calls == ["fixture-daily"]
    assert repository.daily_rate_calls == [
        {
            "provider": "fixture",
            "rate_type": "spot",
            "base_currency": "USD",
            "quote_currency": "EUR",
            "rate_date": date(2024, 3, 15),
        }
    ]
    assert db_session.calls == []


def test_runtime_execution_factory_has_no_global_runtime_dependencies():
    from src.services import runtime_execution

    source = inspect.getsource(runtime_execution)

    assert "PostgresStrategy" not in source
    assert "SessionLocal" not in source
    assert "settings" not in source
    assert "app.state" not in source
