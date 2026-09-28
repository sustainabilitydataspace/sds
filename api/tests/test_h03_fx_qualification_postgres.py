"""H03 FX qualification on explicitly disposable PostgreSQL; run serially.

The synthetic history is approved only as a test fixture, not as ECB evidence.
Duplicate keys are rejected by PostgreSQL. Ambiguous lookup fault injection
exercises the defensive runtime path without removing those constraints.
"""

import os
from datetime import date
from decimal import Decimal

import pytest
from rdflib import Graph
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query, Session
from sqlalchemy.orm.exc import MultipleResultsFound

from alembic.script import ScriptDirectory
from src.api.models import ValueCreate
from src.calculation.contracts import (
    CalculationContract,
    CalculationContractInput,
    ContractExecutionError,
)
from src.calculation.conversion.fx import FXAmbiguousRateError, FXMissingRateError
from src.calculation.engine import (
    CalculationContext,
    CalculationEngine,
    TemporalGranularity,
)
from src.calculation.json_strategy import JSONStrategy
from src.calculation.unit_converter import UnitConverter
from src.calculation.value_provider import StoreObservationProvider
from src.config.settings import settings
from src.database.bootstrap_conversion_catalog import (
    DEFAULT_FX_POLICY_ID,
    bootstrap_conversion_catalog_if_empty,
)
from src.database.init_db import init_db_for_engine
from src.database.migrations import _alembic_config
from src.database.models import (
    Currency,
    CurrentValuePointer,
    ESGValue,
    FXPolicy,
    FXRateBatch,
    FXRateObservation,
    FXRatePeriod,
    ValueContext,
    ValueRevision,
)
from src.services.fx_service import FXService
from src.services.runtime_execution import build_conversion_engine
from src.services.value_ingest import ValueIngestError, ingest_value
from src.services.value_store import DatabaseValueStore

START, END = date(2024, 3, 1), date(2024, 3, 31)
TENANT = "h03-fixture"
RAW_CONCEPT = "urn:sds:reg:test:h03-revenue"


@pytest.fixture
def disposable_postgres():
    database_url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    if make_url(database_url).get_backend_name() != "postgresql":
        pytest.skip("H03 qualification requires disposable PostgreSQL")

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        init_db_for_engine(engine)
        with engine.connect() as connection:
            expected_head = ScriptDirectory.from_config(
                _alembic_config(connection)
            ).get_current_head()
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == expected_head
            )
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("url", "reset"),
    [
        (None, "true"),
        ("postgresql://localhost/h03", None),
        ("postgresql://localhost/h03", "false"),
        ("postgresql://localhost/h03", "TRUE"),
        ("sqlite://", "true"),
    ],
)
def test_disposable_gate_precedes_engine_creation(monkeypatch, url, reset):
    for name, value in (
        ("SDS_MIGRATION_TEST_DATABASE_URL", url),
        ("SDS_MIGRATION_TEST_ALLOW_RESET", reset),
    ):
        monkeypatch.delenv(name, raising=False)
        if value is not None:
            monkeypatch.setenv(name, value)

    def forbidden_engine(*_args, **_kwargs):
        pytest.fail("closed gate must not create an engine")

    monkeypatch.setitem(globals(), "create_engine", forbidden_engine)
    with pytest.raises(pytest.skip.Exception):
        next(disposable_postgres.__wrapped__())


def _value_counts(db):
    return tuple(
        db.query(model).count()
        for model in (ESGValue, ValueContext, ValueRevision, CurrentValuePointer)
    )


def test_disposable_postgres_fx_ingestion_and_contract(
    disposable_postgres, monkeypatch, mock_units_database
):
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", False)
    # Explicit local unit storage avoids probing the application's DB URL.
    converter = UnitConverter(storage_strategy=JSONStrategy(mock_units_database))
    contract = CalculationContract(
        contract_id="h03-fixture-contract",
        concept="urn:sds:reg:test:h03-total",
        contract_version="test-only",
        contract_hash="h03-synthetic-fixture",
        runtime_status="executable",
        formula="usd_revenue + gbp_revenue",
        result_unit="1",
        result_currency="EUR",
        inputs=tuple(
            CalculationContractInput(
                f"{currency.lower()}_revenue",
                f"{RAW_CONCEPT}:{currency}",
                unit="1",
                expected_currency="EUR",
                fx_policy_id=DEFAULT_FX_POLICY_ID,
            )
            for currency in ("USD", "GBP")
        ),
    )
    context = CalculationContext(
        entity_id="h03-plant",
        period_start=START,
        period_end=END,
        temporal_granularity=TemporalGranularity.MONTHLY,
        organizational_level=1,
    )

    with Session(disposable_postgres) as db:
        summary = bootstrap_conversion_catalog_if_empty(db)
        assert summary["currencies_total"] > 0
        assert (
            db.query(Currency).filter(Currency.code.in_(["USD", "GBP", "EUR"])).count()
            == 3
        )
        assert db.get(FXPolicy, DEFAULT_FX_POLICY_ID).fallback_behavior == "fail_closed"
        assert all(
            db.query(model).count() == 0
            for model in (FXRateBatch, FXRateObservation, FXRatePeriod)
        )
        store = DatabaseValueStore(db, tenant_id=TENANT)
        conversion = build_conversion_engine(db, converter)
        repository = conversion.fx_converter.rate_repository
        assert repository.db is db

        def ingest(value_id, currency="USD", *, converted=True, concept=None):
            return ingest_value(
                value_id=value_id,
                value_data=ValueCreate(
                    concept=concept or f"{RAW_CONCEPT}:{currency}",
                    entity=context.entity_id,
                    period=END,
                    value=100,
                    unit="1",
                    currency=currency,
                    expected_currency="EUR" if converted else None,
                    fx_policy_id=DEFAULT_FX_POLICY_ID if converted else None,
                    period_start=START,
                    period_end=END,
                ),
                converter=converter,
                conversion_engine=conversion,
                store=store,
                hierarchy_store=None,
                graph=Graph(),
                created_by="h03-fixture",
                company_id=TENANT,
                strict=False,
            )

        def calculate():
            return CalculationEngine(precision=2).calculate_contract(
                contract,
                context,
                StoreObservationProvider(store),
                conversion_engine=conversion,
            )

        # Catalogue bootstrap deliberately does not make FX executable.
        with pytest.raises(ValueIngestError, match="FX rate not found") as missing:
            ingest("h03-empty")
        assert isinstance(missing.value.__cause__, FXMissingRateError)
        db.commit()  # A rollback must not hide any erroneous value write.
        assert _value_counts(db) == (0, 0, 0, 0)

        # Two observations per pair, intentionally not a complete market month.
        for currency, rates in (("USD", ("0.8", "1.0")), ("GBP", ("1.1", "1.3"))):
            FXService(repository).import_rates_csv(
                [
                    {"quote_currency": "EUR", "rate_date": day, "rate_value": rate}
                    for day, rate in zip((START, date(2024, 3, 4)), rates)
                ],
                provider="ECB",
                rate_type="reference",
                base_currency=currency,
                source_hash="0" * 64,
                source_name="H03 synthetic fixture; not market evidence",
                created_by="h03-fixture",
            )
            materialized = repository.materialize_monthly_periods(
                provider="ECB",
                rate_type="reference",
                base_currency=currency,
                quote_currency="EUR",
                start=START,
                end=END,
            )
            assert materialized.created_periods == 1
        periods = {row.base_currency: row for row in db.query(FXRatePeriod).all()}
        assert {key: row.rate_value for key, row in periods.items()} == {
            "USD": Decimal("0.9"),
            "GBP": Decimal("1.2"),
        }
        observations = db.query(FXRateObservation).all()
        assert len(observations) == 4
        for currency, period in periods.items():
            assert set(period.source_observation_ids) == {
                row.id for row in observations if row.base_currency == currency
            }

        result = ingest("h03-converted", concept="urn:sds:reg:test:h03-converted")
        assert result.value == Decimal("90")
        assert result.original_value is not None
        assert Decimal(result.original_value) == Decimal("100")
        assert result.currency == "EUR" and result.original_currency == "USD"
        assert result.currency_conversion_applied
        assert len(result.conversion_trace) == 1
        trace = result.conversion_trace[0]
        assert trace["rate_period_id"] == str(periods["USD"].id)
        assert trace["policy_id"] == DEFAULT_FX_POLICY_ID
        assert (
            trace["metadata"]["source_observation_ids"]
            == periods["USD"].source_observation_ids
        )
        # A new session verifies committed JSONB provenance in both value lanes.
        with Session(disposable_postgres) as reader:
            stored = DatabaseValueStore(reader, tenant_id=TENANT).get(result.id)
            assert stored.value == result.value
            assert stored.conversion_trace == result.conversion_trace
            assert (
                reader.get(ESGValue, result.id).conversion_trace
                == result.conversion_trace
            )
            revision = (
                reader.query(ValueRevision).filter_by(source_record_id=result.id).one()
            )
            assert revision.conversion_trace == result.conversion_trace

        ingest("h03-usd", converted=False)
        ingest("h03-gbp", currency="GBP", converted=False)
        before = _value_counts(db)
        assert before == (3, 3, 3, 3)
        calculated = calculate()
        assert calculated.value == Decimal("210.00")
        fx_steps = calculated.trace.conversions_applied
        assert len(fx_steps) == 2
        assert {step["rate_period_id"] for step in fx_steps} == {
            str(row.id) for row in periods.values()
        }
        assert all(step["policy_id"] == DEFAULT_FX_POLICY_ID for step in fx_steps)
        assert (
            _value_counts(db) == before
        )  # Calculation returns; it does not save a value.

        # A missing period fails both operations despite daily observations.
        usd_period = periods["USD"]
        db.delete(usd_period)
        db.flush()
        with pytest.raises(ValueIngestError, match="FX rate not found"):
            ingest("h03-missing-period")
        with pytest.raises(ContractExecutionError, match="FX rate not found"):
            calculate()
        assert _value_counts(db) == before
        db.rollback()  # Restore only the deliberately removed period.

        # Migrated uniqueness prevents real ambiguous period rows.
        duplicate = {
            column.name: getattr(usd_period, column.name)
            for column in FXRatePeriod.__table__.columns
            if column.name not in {"id", "created_at"}
        }
        with pytest.raises(IntegrityError) as rejected:
            with db.begin_nested():
                db.add(FXRatePeriod(**duplicate))
                db.flush()
        assert rejected.value.orig.diag.constraint_name == "uq_fx_rate_period_key"

        # Inject the ORM ambiguity at the lookup boundary, keeping the real
        # repository error translation, engine, ingestion and stores in use.
        original_lookup = Query.one_or_none

        def ambiguous_lookup(query):
            if query.column_descriptions[0].get("entity") is FXRatePeriod:
                raise MultipleResultsFound("H03 injected ambiguous period lookup")
            return original_lookup(query)

        with monkeypatch.context() as fault:
            fault.setattr(Query, "one_or_none", ambiguous_lookup)
            with pytest.raises(
                ValueIngestError, match="ambiguous FX period rate"
            ) as ambiguous:
                ingest("h03-ambiguous")
            assert isinstance(ambiguous.value.__cause__, FXAmbiguousRateError)
            with pytest.raises(
                ContractExecutionError, match="ambiguous FX period rate"
            ):
                calculate()
        db.commit()
        with Session(disposable_postgres) as reader:
            assert _value_counts(reader) == before
            assert (
                reader.query(ESGValue.id)
                .filter(
                    ESGValue.id.in_(
                        ["h03-empty", "h03-missing-period", "h03-ambiguous"]
                    )
                )
                .count()
                == 0
            )
