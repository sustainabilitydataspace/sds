from __future__ import annotations

class UnitConversionError(ValueError):
    pass


_CANONICAL = {
    # Energy
    "kwh": "kWh",
    "kilowatt_hour": "kWh",
    "kilowatt-hour": "kWh",
    "mwh": "MWh",
    "megawatt_hour": "MWh",
    "megawatt-hour": "MWh",
    # Volume
    "l": "L",
    "liter": "L",
    "liters": "L",
    "litre": "L",
    "m3": "m³",
    "m^3": "m³",
    "m³": "m³",
    "cubic_meter": "m³",
    "cubic_metre": "m³",
}


def normalize_unit(unit: str) -> str:
    u = (unit or "").strip()
    if not u:
        raise UnitConversionError("Empty unit")

    key = u.lower().replace(" ", "_")
    if key in _CANONICAL:
        return _CANONICAL[key]

    # Preserve already-canonical symbols like "kWh", "MWh", "L", "m³"
    if u in {"kWh", "MWh", "L", "m³"}:
        return u

    raise UnitConversionError(f"Unknown unit: {unit!r}")


def convert_unit(value: float, from_unit: str, to_unit: str) -> float:
    src = normalize_unit(from_unit)
    dst = normalize_unit(to_unit)

    if src == dst:
        return float(value)

    # Energy
    if src == "kWh" and dst == "MWh":
        return float(value) / 1000.0
    if src == "MWh" and dst == "kWh":
        return float(value) * 1000.0

    # Volume
    if src == "L" and dst == "m³":
        return float(value) / 1000.0
    if src == "m³" and dst == "L":
        return float(value) * 1000.0

    raise UnitConversionError(f"Unsupported conversion: {src} -> {dst}")
