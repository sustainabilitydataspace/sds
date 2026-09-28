"""Regression tests for conversion catalog bootstrap helpers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.database import bootstrap_conversion_catalog as conversion_bootstrap
from src.database.models import (
    Currency,
    FXPolicy,
    FXRateBatch,
    FXRateObservation,
    FXRatePeriod,
)


class _FakeQuery:
    def __init__(self, session: "_FakeSession", model):
        self._session = session
        self._model = model
        self._criteria = []

    def count(self) -> int:
        return len(self._session._rows[self._model])

    def filter(self, *criteria):
        self._criteria.extend(criteria)
        return self

    def first(self):
        for row in self._session._rows[self._model]:
            if all(_matches(row, criterion) for criterion in self._criteria):
                return row
        return None

    def one_or_none(self):
        rows = self.all()
        if len(rows) > 1:
            raise AssertionError("fake query returned multiple rows")
        return rows[0] if rows else None

    def all(self):
        return [
            row
            for row in self._session._rows[self._model]
            if all(_matches(row, criterion) for criterion in self._criteria)
        ]


def _matches(row, criterion) -> bool:
    left = getattr(criterion, "left", None)
    right = getattr(criterion, "right", None)
    key = getattr(left, "key", getattr(left, "name", None))
    expected = getattr(right, "value", right)
    if key is None:
        return True
    return getattr(row, key) == expected


class _FakeSession:
    def __init__(self):
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
        self._rows = {
            Currency: [],
            FXPolicy: [],
            FXRateBatch: [],
            FXRateObservation: [],
            FXRatePeriod: [],
        }
        self._pending = []
        self._next_ids = {
            FXRateBatch: 1,
            FXRateObservation: 1,
            FXRatePeriod: 1,
        }
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        return _FakeQuery(self, model)

    def add(self, row):
        self._pending.append(row)

    def flush(self):
        self._persist_pending()

    def commit(self):
        self._persist_pending()
        self.commits += 1

    def rollback(self):
        self._pending.clear()
        self.rollbacks += 1

    def _persist_pending(self):
        while self._pending:
            row = self._pending.pop(0)
            row_type = type(row)
            if row_type in self._next_ids and getattr(row, "id", None) in (None, 0):
                row.id = self._next_ids[row_type]
                self._next_ids[row_type] += 1
            if row not in self._rows[row_type]:
                self._rows[row_type].append(row)


def test_bootstrap_conversion_catalog_is_idempotent():
    from src.database.bootstrap_conversion_catalog import (
        bootstrap_conversion_catalog_if_empty,
    )

    session = _FakeSession()

    first = bootstrap_conversion_catalog_if_empty(session)
    second = bootstrap_conversion_catalog_if_empty(session)

    assert first["currencies_created"] == len(first["currency_codes"])
    assert first["currencies_created"] >= 300
    assert first["policies_created"] == 1
    assert first["rate_batches_created"] == 0
    assert first["rate_observations_created"] == 0
    assert first["rate_periods_created"] == 0
    assert {"EUR", "GBP", "USD", "XAU"}.issubset(first["currency_codes"])

    assert second["currencies_created"] == 0
    assert second["policies_created"] == 0
    assert second["rate_batches_created"] == 0
    assert second["rate_observations_created"] == 0
    assert second["rate_observations_updated"] == 0
    assert second["rate_periods_created"] == 0
    assert second["rate_periods_updated"] == 0

    assert (
        sorted(row.code for row in session._rows[Currency]) == first["currency_codes"]
    )
    assert sorted(row.id for row in session._rows[FXPolicy]) == [
        "ecb-reference-monthly-average",
    ]
    assert len(session._rows[FXRateObservation]) == 0
    assert len(session._rows[FXRatePeriod]) == 0


def test_bootstrap_conversion_catalog_seeds_pinned_currency_and_policy_fields():
    from src.database.bootstrap_conversion_catalog import (
        DEFAULT_FX_POLICY_ID,
        bootstrap_conversion_catalog_if_empty,
    )

    session = _FakeSession()

    bootstrap_conversion_catalog_if_empty(session)

    currencies = {row.code: row for row in session._rows[Currency]}
    assert currencies["EUR"].numeric_code == "978"
    assert currencies["EUR"].minor_units == 2
    assert currencies["EUR"].valid_from.isoformat() == "1999-01-01"
    assert currencies["GBP"].numeric_code == "826"
    assert currencies["USD"].numeric_code == "840"
    assert currencies["XAU"].minor_units == 0
    assert currencies["XAU"].currency_metadata["minor_units_raw"] == "N.A."
    assert currencies["ADP"].is_active is False

    policy = next(
        row for row in session._rows[FXPolicy] if row.id == DEFAULT_FX_POLICY_ID
    )
    assert policy.provider == "ECB"
    assert policy.rate_type == "reference"
    assert policy.selection_mode == "monthly_average"
    assert policy.fallback_behavior == "fail_closed"
    assert policy.triangulation_allowed is False


def test_bootstrap_conversion_catalog_preserves_operational_fx():
    from src.database.bootstrap_conversion_catalog import (
        bootstrap_conversion_catalog_if_empty,
    )

    session = _FakeSession()
    session._rows[FXRateObservation].append(
        FXRateObservation(
            id=10,
            batch_id=7,
            base_currency="GBP",
            quote_currency="EUR",
            rate_date=date(2024, 3, 31),
            rate_value=Decimal("1.180000"),
            provider="ECB",
            rate_type="reference",
            source_hash="operational",
            rate_metadata={"source": "ECB history"},
        )
    )

    summary = bootstrap_conversion_catalog_if_empty(session)

    assert summary["rate_batches_created"] == 0
    assert summary["rate_observations_created"] == 0
    assert summary["rate_periods_created"] == 0
    assert len(session._rows[FXRateObservation]) == 1


def test_bootstrap_conversion_catalog_missing_seed_file_returns_current_counts(
    tmp_path,
):
    from src.database.bootstrap_conversion_catalog import (
        bootstrap_conversion_catalog_if_empty,
    )

    session = _FakeSession()
    missing_currencies = tmp_path / "missing-currencies.json"

    summary = bootstrap_conversion_catalog_if_empty(
        session,
        currencies_seed_path=missing_currencies,
    )

    assert summary["currencies_before"] == 0
    assert summary["policies_before"] == 0
    assert summary["rate_observations_before"] == 0
    assert summary["currencies_created"] == 0
    assert summary["policies_created"] == 0
    assert summary["rate_observations_created"] == 0
    assert summary["rate_periods_created"] == 0
    assert session.commits == 0
    assert all(not rows for rows in session._rows.values())


def test_bootstrap_conversion_catalog_helper_edges_and_postgres_lock(tmp_path):
    class _PostgresSession:
        bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def __init__(self):
            self.executed = []

        def execute(self, statement, params):
            self.executed.append((str(statement), params))

    session = _PostgresSession()
    lock_key = conversion_bootstrap._with_postgres_lock(session)
    conversion_bootstrap._release_postgres_lock(session, lock_key)

    assert lock_key == conversion_bootstrap.CONVERSION_BOOTSTRAP_LOCK_KEY
    assert len(session.executed) == 2

    target = SimpleNamespace(value="old")
    assert conversion_bootstrap._update_if_changed(target, value="new") == 1
    assert target.value == "new"
    parsed_date = conversion_bootstrap._parse_optional_date(date(2024, 1, 2))
    assert parsed_date == date(2024, 1, 2)
    assert conversion_bootstrap._parse_optional_decimal("1.25") == Decimal("1.25")

    currencies = tmp_path / "currencies.json"
    currencies.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a list"):
        conversion_bootstrap._load_currency_seed(currencies)

    currencies.write_text('["bad"]', encoding="utf-8")
    with pytest.raises(ValueError, match="rows must be objects"):
        conversion_bootstrap._load_currency_seed(currencies)

    currencies.write_text('[{"code": "EURO"}]', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid currency code"):
        conversion_bootstrap._load_currency_seed(currencies)


def test_bootstrap_conversion_catalog_rolls_back_on_commit_failure():
    class _CommitFailingSession(_FakeSession):
        def commit(self):
            raise RuntimeError("commit failed")

    session = _CommitFailingSession()
    with pytest.raises(RuntimeError, match="commit failed"):
        conversion_bootstrap.bootstrap_conversion_catalog_if_empty(session)

    assert session.rollbacks == 1
