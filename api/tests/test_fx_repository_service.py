from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.orm.exc import MultipleResultsFound

from src.calculation.conversion.fx import (
    FXAmbiguousRateError,
    FXMissingRateError,
    FXPolicy,
)
from src.database.models import FXRateBatch, FXRateObservation, FXRatePeriod
from src.database.repositories.fx_repository import FXRepository
from src.services.fx_service import FXService


class OneOrNoneQuery:
    def __init__(self, result=None, exception=None):
        self.result = result
        self.exception = exception
        self.filters = []
        self.ordering = None

    def filter(self, *criteria):
        self.filters.extend(criteria)
        return self

    def order_by(self, *criteria):
        self.ordering = criteria
        return self

    def one_or_none(self):
        if self.exception is not None:
            raise self.exception
        return self.result

    def all(self):
        return self.result


class SessionWithQuery:
    def __init__(self, query):
        self.query_obj = query
        self.queried_models = []
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.refreshed = []

    def query(self, model):
        self.queried_models.append(model)
        return self.query_obj

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def refresh(self, row):
        self.refreshed.append(row)


class ModelQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self.filters = []
        self.ordering = None

    def filter(self, *criteria):
        self.filters.extend(criteria)
        return self

    def order_by(self, *criteria):
        self.ordering = criteria
        return self

    def all(self):
        return list(self.rows)

    def one_or_none(self):
        if len(self.rows) > 1:
            raise MultipleResultsFound()
        return self.rows[0] if self.rows else None


class SessionByModel:
    def __init__(self, rows_by_model=None):
        self.rows_by_model = rows_by_model or {}
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.refreshed = []
        self._next_batch_id = 1

    def query(self, model):
        return ModelQuery(self.rows_by_model.get(model, []))

    def add(self, row):
        self.added.append(row)
        self.rows_by_model.setdefault(type(row), []).append(row)

    def flush(self):
        for row in self.rows_by_model.get(FXRateBatch, []):
            if getattr(row, "id", None) is None:
                row.id = self._next_batch_id
                self._next_batch_id += 1

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def refresh(self, row):
        self.refreshed.append(row)

    def delete(self, row):
        for rows in self.rows_by_model.values():
            for index, candidate in enumerate(rows):
                if candidate is row:
                    rows.pop(index)
                    return
            for index, candidate in enumerate(rows):
                if candidate == row:
                    rows.pop(index)
                    return
                return


def test_daily_lookup_maps_sqlalchemy_ambiguity_to_domain_error():
    repo = FXRepository(
        SessionWithQuery(OneOrNoneQuery(exception=MultipleResultsFound()))
    )

    with pytest.raises(FXAmbiguousRateError):
        repo.find_daily_rate(
            provider="ECB",
            rate_type="reference",
            base_currency="USD",
            quote_currency="EUR",
            rate_date=date(2024, 3, 15),
        )


def test_period_lookup_maps_sqlalchemy_ambiguity_to_domain_error():
    repo = FXRepository(
        SessionWithQuery(OneOrNoneQuery(exception=MultipleResultsFound()))
    )

    with pytest.raises(FXAmbiguousRateError):
        repo.find_period_rate(
            provider="ECB",
            rate_type="reference",
            base_currency="USD",
            quote_currency="EUR",
            period_type="monthly_average",
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
        )


def test_active_policy_row_maps_to_domain_policy():
    db_row = SimpleNamespace(
        id="ecb-close",
        provider="ECB",
        rate_type="reference",
        selection_mode="closing_rate",
        business_day_rule="previous_available",
        triangulation_allowed=True,
        fallback_behavior="fail_closed",
        rounding_scale=4,
        rounding_mode="ROUND_HALF_EVEN",
        is_active=True,
    )
    repo = FXRepository(SessionWithQuery(OneOrNoneQuery(result=db_row)))

    policy = repo.get_policy("ecb-close")

    assert policy == FXPolicy(
        id="ecb-close",
        provider="ECB",
        rate_type="reference",
        selection_mode="closing_rate",
        business_day_rule="previous_available",
        triangulation_allowed=True,
        fallback_behavior="fail_closed",
        rounding_scale=4,
        rounding_mode="ROUND_HALF_EVEN",
    )


def test_inactive_or_missing_policy_returns_none():
    repo = FXRepository(SessionWithQuery(OneOrNoneQuery(result=None)))

    assert repo.get_policy("inactive-policy") is None


def test_save_rate_batch_adds_batch_and_observations_without_commit_when_disabled():
    session = SessionWithQuery(OneOrNoneQuery())
    repo = FXRepository(session)

    batch = repo.save_rate_batch(
        [
            {
                "quote_currency": "EUR",
                "rate_date": date(2024, 3, 15),
                "rate_value": Decimal("0.920000"),
                "observed_at": None,
                "rate_metadata": {"source_row": 2},
            }
        ],
        provider="ECB",
        rate_type="reference",
        base_currency="USD",
        source_hash="hash-1",
        created_by="tester",
        source_name="ecb.csv",
        source_url="https://example.test/fx.csv",
        batch_metadata={"rows": 1},
        commit=False,
    )

    assert batch.provider == "ECB"
    assert batch.source_hash == "hash-1"
    assert len(session.added) == 2
    observation = session.added[1]
    assert observation.quote_currency == "EUR"
    assert observation.rate_date == date(2024, 3, 15)
    assert observation.rate_value == Decimal("0.920000")
    assert observation.provider == "ECB"
    assert observation.rate_type == "reference"
    assert observation.base_currency == "USD"
    assert observation.source_hash == "hash-1"
    assert session.committed is False


def test_save_rate_batch_result_upserts_existing_observation_without_duplicate():
    existing = SimpleNamespace(
        id=7,
        provider="ECB",
        rate_type="reference",
        base_currency="USD",
        quote_currency="EUR",
        rate_date=date(2024, 3, 15),
        rate_value=Decimal("0.910000"),
        source_hash="old-hash",
        observed_at=None,
        rate_metadata={"source_row": 1},
        batch_id=3,
    )
    session = SessionByModel({FXRateObservation: [existing]})

    result = FXRepository(session).save_rate_batch_result(
        [
            {
                "quote_currency": "EUR",
                "rate_date": date(2024, 3, 15),
                "rate_value": Decimal("0.920000"),
                "rate_metadata": {"source_row": 1, "revision": "ecb"},
            }
        ],
        provider="ECB",
        rate_type="reference",
        base_currency="USD",
        source_hash="new-hash",
        source_name="ECB history",
        source_url="https://example.test/ecb.zip",
        batch_metadata={"chunk_index": 1},
    )

    assert result.submitted_rows == 1
    assert result.created_rows == 0
    assert result.updated_rows == 1
    assert result.unchanged_rows == 0
    assert result.batch is not None
    assert len(session.rows_by_model[FXRateObservation]) == 1
    assert existing.rate_value == Decimal("0.920000")
    assert existing.source_hash == "new-hash"
    assert existing.rate_metadata == {"source_row": 1, "revision": "ecb"}
    assert existing.batch_id == result.batch.id
    assert session.committed is True


def test_save_rate_batch_result_is_noop_when_rows_are_unchanged():
    existing = SimpleNamespace(
        id=7,
        provider="ECB",
        rate_type="reference",
        base_currency="USD",
        quote_currency="EUR",
        rate_date=date(2024, 3, 15),
        rate_value=Decimal("0.920000"),
        source_hash="same-hash",
        observed_at=None,
        rate_metadata={"source_row": 1},
        batch_id=3,
    )
    session = SessionByModel({FXRateObservation: [existing]})

    result = FXRepository(session).save_rate_batch_result(
        [
            {
                "quote_currency": "EUR",
                "rate_date": date(2024, 3, 15),
                "rate_value": Decimal("0.920000"),
                "rate_metadata": {"source_row": 1},
            }
        ],
        provider="ECB",
        rate_type="reference",
        base_currency="USD",
        source_hash="same-hash",
    )

    assert result.submitted_rows == 1
    assert result.created_rows == 0
    assert result.updated_rows == 0
    assert result.unchanged_rows == 1
    assert result.batch is None
    assert session.added == []
    assert session.committed is False


def test_save_rate_batch_result_rolls_back_on_commit_failure():
    class FailingCommitSession(SessionByModel):
        def commit(self):
            raise RuntimeError("commit failed")

    session = FailingCommitSession()

    with pytest.raises(RuntimeError, match="commit failed"):
        FXRepository(session).save_rate_batch_result(
            [
                {
                    "quote_currency": "EUR",
                    "rate_date": date(2024, 3, 15),
                    "rate_value": Decimal("0.920000"),
                }
            ],
            provider="ECB",
            rate_type="reference",
            base_currency="USD",
            source_hash="hash-1",
        )

    assert session.rolled_back is True


def test_materialize_monthly_periods_creates_average_and_is_idempotent():
    observations = [
        SimpleNamespace(
            id=1,
            provider="ECB",
            rate_type="reference",
            base_currency="USD",
            quote_currency="EUR",
            rate_date=date(2024, 3, 1),
            rate_value=Decimal("0.900000"),
            source_hash="hash-a",
        ),
        SimpleNamespace(
            id=2,
            provider="ECB",
            rate_type="reference",
            base_currency="USD",
            quote_currency="EUR",
            rate_date=date(2024, 3, 31),
            rate_value=Decimal("0.950000"),
            source_hash="hash-b",
        ),
    ]
    session = SessionByModel({FXRateObservation: observations, FXRatePeriod: []})
    repo = FXRepository(session)

    first = repo.materialize_monthly_periods(
        provider="ECB",
        rate_type="reference",
        base_currency="USD",
        quote_currency="EUR",
    )
    second = repo.materialize_monthly_periods(
        provider="ECB",
        rate_type="reference",
        base_currency="USD",
        quote_currency="EUR",
    )

    assert first.scanned_observations == 2
    assert first.created_periods == 1
    assert first.updated_periods == 0
    assert second.created_periods == 0
    assert second.updated_periods == 0
    assert second.unchanged_periods == 1
    period = session.rows_by_model[FXRatePeriod][0]
    assert period.period_type == "monthly_average"
    assert period.period_start == date(2024, 3, 1)
    assert period.period_end == date(2024, 3, 31)
    assert period.rate_value == Decimal("0.925000")
    assert period.source_observation_ids == [1, 2]
    assert len(period.source_hash) == 64


def test_materialize_monthly_periods_updates_existing_period_with_window_filters():
    observations = [
        SimpleNamespace(
            id=1,
            provider="ECB",
            rate_type="reference",
            base_currency="USD",
            quote_currency="EUR",
            rate_date=date(2024, 3, 1),
            rate_value=Decimal("0.900000"),
            source_hash="hash-a",
        ),
        SimpleNamespace(
            id=2,
            provider="ECB",
            rate_type="reference",
            base_currency="USD",
            quote_currency="EUR",
            rate_date=date(2024, 3, 31),
            rate_value=Decimal("0.950000"),
            source_hash="hash-b",
        ),
    ]
    existing_period = SimpleNamespace(
        provider="ECB",
        rate_type="reference",
        base_currency="USD",
        quote_currency="EUR",
        period_type="monthly_average",
        period_start=date(2024, 3, 1),
        period_end=date(2024, 3, 31),
        rate_value=Decimal("0.910000"),
        source_observation_ids=[],
        source_hash="old",
    )
    session = SessionByModel(
        {FXRateObservation: observations, FXRatePeriod: [existing_period]}
    )

    result = FXRepository(session).materialize_monthly_periods(
        provider="ECB",
        rate_type="reference",
        base_currency="USD",
        quote_currency="EUR",
        start=date(2024, 3, 1),
        end=date(2024, 3, 31),
    )

    assert result.created_periods == 0
    assert result.updated_periods == 1
    assert existing_period.rate_value == Decimal("0.925000")
    assert existing_period.source_observation_ids == [1, 2]


def test_materialize_monthly_periods_rolls_back_on_commit_failure():
    class FailingCommitSession(SessionByModel):
        def commit(self):
            raise RuntimeError("period commit failed")

    session = FailingCommitSession(
        {
            FXRateObservation: [
                SimpleNamespace(
                    id=1,
                    provider="ECB",
                    rate_type="reference",
                    base_currency="USD",
                    quote_currency="EUR",
                    rate_date=date(2024, 4, 1),
                    rate_value=Decimal("0.900000"),
                    source_hash="hash-a",
                )
            ],
            FXRatePeriod: [],
        }
    )

    with pytest.raises(RuntimeError, match="period commit failed"):
        FXRepository(session).materialize_monthly_periods(
            provider="ECB",
            rate_type="reference",
            base_currency="USD",
            quote_currency="EUR",
        )

    assert session.rolled_back is True


def test_delete_seed_observations_not_in_keys_prunes_named_seed_rows_only():
    seed_name = "ecb-reference-history.csv"
    keep_seed = SimpleNamespace(
        provider="ECB",
        rate_type="reference",
        base_currency="GBP",
        quote_currency="EUR",
        rate_date=date(2024, 3, 15),
        rate_metadata={"seed": seed_name},
    )
    delete_seed = SimpleNamespace(
        provider="ECB",
        rate_type="reference",
        base_currency="GBP",
        quote_currency="EUR",
        rate_date=date(2024, 3, 31),
        rate_metadata={"seed": seed_name},
    )
    keep_operational = SimpleNamespace(
        provider="ECB",
        rate_type="reference",
        base_currency="USD",
        quote_currency="EUR",
        rate_date=date(2024, 3, 31),
        rate_metadata={"source": "ECB"},
    )
    session = SessionByModel(
        {FXRateObservation: [keep_seed, delete_seed, keep_operational]}
    )

    result = FXRepository(session).delete_seed_observations_not_in_keys(
        provider="ECB",
        rate_type="reference",
        allowed_keys={
            ("ECB", "reference", "GBP", "EUR", date(2024, 3, 15)),
            ("ECB", "reference", "USD", "EUR", date(2024, 3, 31)),
        },
        seed_name=seed_name,
    )

    assert result.scanned_rows == 2
    assert result.deleted_rows == 1
    assert session.rows_by_model[FXRateObservation] == [keep_seed, keep_operational]
    assert session.committed is True


def test_delete_seed_observations_rolls_back_on_delete_failure():
    class FailingDeleteSession(SessionByModel):
        def delete(self, row):
            raise RuntimeError("delete failed")

    delete_seed = SimpleNamespace(
        provider="ECB",
        rate_type="reference",
        base_currency="GBP",
        quote_currency="EUR",
        rate_date=date(2024, 3, 31),
        rate_metadata={"seed": "ecb-reference-history.csv"},
    )
    session = FailingDeleteSession({FXRateObservation: [delete_seed]})

    with pytest.raises(RuntimeError, match="delete failed"):
        FXRepository(session).delete_seed_observations_not_in_keys(
            provider="ECB",
            rate_type="reference",
            allowed_keys=set(),
            seed_name="ecb-reference-history.csv",
        )

    assert session.rolled_back is True


def test_save_rate_batch_commits_and_refreshes_by_default():
    session = SessionWithQuery(OneOrNoneQuery())
    repo = FXRepository(session)

    batch = repo.save_rate_batch(
        [
            {
                "quote_currency": "EUR",
                "rate_date": date(2024, 3, 15),
                "rate_value": Decimal("0.920000"),
            }
        ],
        provider="ECB",
        rate_type="reference",
        base_currency="USD",
        source_hash="hash-1",
    )

    assert session.committed is True
    assert session.refreshed == [batch]


def test_repository_coverage_filters_and_orders_daily_observations():
    rows = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    query = OneOrNoneQuery(result=rows)
    repo = FXRepository(SessionWithQuery(query))

    result = repo.list_rate_coverage(
        provider="ECB",
        rate_type="reference",
        base_currency="USD",
        quote_currency="EUR",
        start=date(2024, 3, 1),
        end=date(2024, 3, 31),
    )

    assert result == rows
    assert query.ordering is not None


def test_repository_private_batch_lookup_returns_empty_for_empty_rows():
    assert (
        FXRepository(SessionByModel())._existing_observations_for_batch(
            [],
            provider="ECB",
            rate_type="reference",
            base_currency="USD",
        )
        == {}
    )


class PolicyRepository:
    def __init__(self, policy=None):
        self.policy = policy
        self.convert_calls = []
        self.saved_batches = []
        self.coverage_calls = []

    def get_policy(self, policy_id):
        return self.policy if policy_id == "known-policy" else None

    def save_rate_batch(self, rows, **kwargs):
        self.saved_batches.append((rows, kwargs))
        return SimpleNamespace(id=123, rows=rows)

    def save_rate_batch_result(self, rows, **kwargs):
        batch = self.save_rate_batch(rows, **kwargs)
        return SimpleNamespace(
            batch=batch,
            submitted_rows=len(rows),
            created_rows=len(rows),
            updated_rows=0,
            unchanged_rows=0,
        )

    def list_rate_coverage(self, **kwargs):
        self.coverage_calls.append(kwargs)
        return ["coverage-row"]


def test_service_conversion_missing_policy_fails_closed():
    service = FXService(PolicyRepository(policy=None))

    with pytest.raises(FXMissingRateError):
        service.convert(
            Decimal("100"),
            from_currency="USD",
            to_currency="EUR",
            value_date="2024-03-15",
            period_start="2024-03-01",
            period_end="2024-03-31",
            policy_id="missing-policy",
        )


def test_service_conversion_delegates_to_domain_converter_with_exact_policy(
    monkeypatch,
):
    policy = FXPolicy(
        id="known-policy",
        provider="ECB",
        rate_type="reference",
        selection_mode="transaction_date",
    )
    repo = PolicyRepository(policy=policy)
    captured = {}

    class CapturingConverter:
        def __init__(self, rate_repository):
            captured["repository"] = rate_repository

        def convert(self, value, **kwargs):
            captured["value"] = value
            captured["kwargs"] = kwargs
            return "converted"

    monkeypatch.setattr("src.services.fx_service.FXConverter", CapturingConverter)

    result = FXService(repo).convert(
        "100.50",
        from_currency="USD",
        to_currency="EUR",
        value_date="2024-03-15",
        period_start="2024-03-01",
        period_end="2024-03-31",
        policy_id="known-policy",
    )

    assert result == "converted"
    assert captured["repository"] is repo
    assert captured["value"] == Decimal("100.50")
    assert captured["kwargs"] == {
        "from_currency": "USD",
        "to_currency": "EUR",
        "value_date": date(2024, 3, 15),
        "period_start": date(2024, 3, 1),
        "period_end": date(2024, 3, 31),
        "policy": policy,
    }


def test_import_rates_csv_parses_required_fields_and_saves_batch():
    repo = PolicyRepository()
    service = FXService(repo)

    batch = service.import_rates_csv(
        [
            {
                "quote_currency": "eur",
                "rate_date": "2024-03-15",
                "rate_value": "0.920000",
                "rate_metadata": {"source_row": 1},
            }
        ],
        provider="ECB",
        rate_type="reference",
        base_currency="usd",
        source_hash="hash-1",
        created_by="tester",
    )

    assert batch.id == 123
    rows, kwargs = repo.saved_batches[0]
    assert rows == [
        {
            "quote_currency": "EUR",
            "rate_date": date(2024, 3, 15),
            "rate_value": Decimal("0.920000"),
            "rate_metadata": {"source_row": 1},
        }
    ]
    assert kwargs == {
        "provider": "ECB",
        "rate_type": "reference",
        "base_currency": "USD",
        "source_hash": "hash-1",
        "created_by": "tester",
    }


def test_import_rates_csv_result_preserves_optional_source_metadata():
    repo = PolicyRepository()
    observed = date(2024, 3, 16)

    result = FXService(repo).import_rates_csv_result(
        [
            {
                "quote_currency": "eur",
                "rate_date": date(2024, 3, 15),
                "rate_value": "0.920000",
                "observed_at": observed,
                "rate_metadata": {"source_row": 4},
            }
        ],
        provider="ECB",
        rate_type="reference",
        base_currency="usd",
        source_hash="hash-1",
        source_name="ecb.csv",
        source_url="https://example.test/ecb.csv",
        batch_metadata={"chunk": 2},
    )

    assert result.batch.id == 123
    rows, kwargs = repo.saved_batches[0]
    assert rows == [
        {
            "quote_currency": "EUR",
            "rate_date": date(2024, 3, 15),
            "rate_value": Decimal("0.920000"),
            "observed_at": observed,
            "rate_metadata": {"source_row": 4},
        }
    ]
    assert kwargs["base_currency"] == "USD"
    assert kwargs["source_name"] == "ecb.csv"
    assert kwargs["source_url"] == "https://example.test/ecb.csv"
    assert kwargs["batch_metadata"] == {"chunk": 2}


@pytest.mark.parametrize(
    "rows",
    [
        [
            {
                "quote_currency": "EUR",
                "rate_date": "2024-03-15",
                "rate_value": "0.920000",
            },
            {
                "quote_currency": "eur",
                "rate_date": date(2024, 3, 15),
                "rate_value": "0.920000",
            },
        ],
        [
            {
                "quote_currency": "EUR",
                "rate_date": "2024-03-15",
                "rate_value": "0.920000",
            },
            {
                "quote_currency": "EUR",
                "rate_date": "2024-03-15",
                "rate_value": "0.930000",
            },
        ],
    ],
)
def test_import_rates_csv_rejects_duplicate_observation_keys_before_save(rows):
    repo = PolicyRepository()

    with pytest.raises(ValueError, match="duplicate FX rate row"):
        FXService(repo).import_rates_csv(
            rows,
            provider="ECB",
            rate_type="reference",
            base_currency="gbp",
            source_hash="hash-1",
        )

    assert repo.saved_batches == []


@pytest.mark.parametrize("missing_field", ["quote_currency", "rate_date", "rate_value"])
def test_import_rates_csv_rejects_missing_required_fields(missing_field):
    row = {
        "quote_currency": "EUR",
        "rate_date": "2024-03-15",
        "rate_value": "0.920000",
    }
    row.pop(missing_field)

    with pytest.raises(ValueError, match=missing_field):
        FXService(PolicyRepository()).import_rates_csv(
            [row],
            provider="ECB",
            rate_type="reference",
            base_currency="USD",
            source_hash="hash-1",
        )


@pytest.mark.parametrize("rate_value", ["0", "-1"])
def test_import_rates_csv_rejects_non_positive_rates(rate_value):
    with pytest.raises(ValueError, match="rate_value"):
        FXService(PolicyRepository()).import_rates_csv(
            [
                {
                    "quote_currency": "EUR",
                    "rate_date": "2024-03-15",
                    "rate_value": rate_value,
                }
            ],
            provider="ECB",
            rate_type="reference",
            base_currency="USD",
            source_hash="hash-1",
        )


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (
            {
                "quote_currency": "EUR",
                "rate_date": "",
                "rate_value": "0.920000",
            },
            "missing required field: rate_date",
        ),
        (
            {
                "quote_currency": "EUR",
                "rate_date": "not-a-date",
                "rate_value": "0.920000",
            },
            "rate_date must be an ISO date",
        ),
        (
            {
                "quote_currency": "EUR",
                "rate_date": "2024-03-15",
                "rate_value": "not-decimal",
            },
            "rate_value must be a decimal",
        ),
    ],
)
def test_import_rates_csv_rejects_invalid_dates_and_decimals(row, message):
    with pytest.raises(ValueError, match=message):
        FXService(PolicyRepository()).import_rates_csv(
            [row],
            provider="ECB",
            rate_type="reference",
            base_currency=None,
            source_hash="hash-1",
        )


def test_coverage_delegates_with_parsed_dates():
    repo = PolicyRepository()

    result = FXService(repo).coverage(
        provider="ECB",
        rate_type="reference",
        base_currency="usd",
        quote_currency="eur",
        start="2024-03-01",
        end="2024-03-31",
    )

    assert result == ["coverage-row"]
    assert repo.coverage_calls == [
        {
            "provider": "ECB",
            "rate_type": "reference",
            "base_currency": "USD",
            "quote_currency": "EUR",
            "start": date(2024, 3, 1),
            "end": date(2024, 3, 31),
        }
    ]
