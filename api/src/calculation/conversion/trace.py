from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ConversionStep:
    step_type: str
    original_value: str
    converted_value: str
    from_unit: str | None = None
    to_unit: str | None = None
    from_currency: str | None = None
    to_currency: str | None = None
    rule_id: str | None = None
    rate_observation_id: str | None = None
    rate_period_id: str | None = None
    policy_id: str | None = None
    source: str | None = None
    formula: str | None = None
    metadata: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}
