from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

DIMENSION_KEYS = frozenset(
    {
        "mass",
        "length",
        "time",
        "temperature",
        "substance",
        "electric_current",
        "luminous_intensity",
        "currency",
        "co2e",
        "energy",
        "area",
        "volume",
        "count",
        "ratio",
    }
)


@dataclass(frozen=True)
class DimensionVector:
    """Immutable dimensional exponents for unit-expression compatibility.

    The ``currency`` key is only a dimensional compatibility marker for
    expressions such as EUR/year. It is not an FX conversion model and must not
    be used to infer rates between currencies.
    """

    exponents: Mapping[str, int]

    def __post_init__(self) -> None:
        cleaned: dict[str, int] = {}
        for key, exponent in dict(self.exponents or {}).items():
            if key not in DIMENSION_KEYS:
                raise ValueError(f"unknown dimension key: {key}")
            value = int(exponent)
            if value:
                cleaned[key] = value
        object.__setattr__(
            self, "exponents", MappingProxyType(dict(sorted(cleaned.items())))
        )

    @classmethod
    def dimensionless(cls) -> "DimensionVector":
        return cls({})

    @classmethod
    def of(cls, **exponents: int) -> "DimensionVector":
        return cls(exponents)

    def __mul__(self, other: "DimensionVector") -> "DimensionVector":
        merged = dict(self.exponents)
        for key, exponent in other.exponents.items():
            merged[key] = merged.get(key, 0) + exponent
        return DimensionVector(merged)

    def __truediv__(self, other: "DimensionVector") -> "DimensionVector":
        merged = dict(self.exponents)
        for key, exponent in other.exponents.items():
            merged[key] = merged.get(key, 0) - exponent
        return DimensionVector(merged)

    def __pow__(self, power: int) -> "DimensionVector":
        exponent = int(power)
        return DimensionVector(
            {key: value * exponent for key, value in self.exponents.items()}
        )

    def is_compatible_with(self, other: "DimensionVector") -> bool:
        return self.exponents == other.exponents

    def as_dict(self) -> dict[str, int]:
        return dict(self.exponents)


DIMENSIONLESS = DimensionVector.dimensionless()
