"""Build the bundled SDS units catalog with source-traced metadata.

The catalog is intentionally seed-backed instead of scraped at runtime. UCUM,
SI/BIPM, and NIST are treated as authoritative source systems for identifiers
and conventional factors; SDS adds sustainability-specific scales such as
currency magnitudes and CO2e aliases.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "api" / "src" / "data" / "units_database.json"
DEFAULT_OUTPUT = DEFAULT_INPUT

SOURCES: dict[str, dict[str, str]] = {
    "ucum": {
        "name": "Unified Code for Units of Measure",
        "url": "https://ucum.org/ucum",
        "version": "UCUM canonical unit atoms",
    },
    "bipm_si": {
        "name": "BIPM SI Brochure",
        "url": "https://www.bipm.org/en/publications/si-brochure",
        "version": "SI Brochure",
    },
    "nist_hb44": {
        "name": "NIST Handbook 44 Appendix C",
        "url": "https://www.nist.gov/pml/owm/nist-handbook-44-current-edition",
        "version": "current edition",
    },
    "sds": {
        "name": "SDS canonical sustainability scales and semantic units",
        "url": "https://sustainabilitydataspace.com/",
        "version": "2.1.0",
    },
}

CATEGORY_DIMENSIONS: dict[str, dict[str, int]] = {
    "mass": {"mass": 1},
    "energy": {"energy": 1},
    "volume": {"volume": 1},
    "area": {"area": 1},
    "length": {"length": 1},
    "time": {"time": 1},
    "temperature": {"temperature": 1},
    "emissions": {"co2e": 1},
    "currency": {"currency": 1},
    "percentage": {"ratio": 1},
    "ratio": {"ratio": 1},
    "count": {"count": 1},
    "semantic": {},
    "power": {"energy": 1, "time": -1},
    "pressure": {"mass": 1, "length": -1, "time": -2},
    "speed": {"length": 1, "time": -1},
    "force": {"mass": 1, "length": 1, "time": -2},
    "density": {"mass": 1, "volume": -1},
    "flow": {"volume": 1, "time": -1},
}

SYSTEM_BY_CATEGORY: dict[str, str] = {
    "mass": "si",
    "energy": "si",
    "volume": "si",
    "area": "si",
    "length": "si",
    "time": "si",
    "temperature": "si",
    "emissions": "sustainability",
    "currency": "monetary_scale",
    "percentage": "dimensionless",
    "ratio": "dimensionless",
    "count": "sustainability_count",
    "semantic": "reported_semantic_unit_policy",
    "power": "si",
    "pressure": "si",
    "speed": "si",
    "force": "si",
    "density": "si",
    "flow": "si",
}

NIST_SYMBOLS = {
    "lb",
    "oz",
    "st",
    "short ton",
    "long ton",
    "grain",
    "slug",
    "ft",
    "in",
    "yd",
    "mi",
    "nmi",
    "mil",
    "fathom",
    "chain",
    "rod",
    "furlong",
    "acre",
    "ft²",
    "in²",
    "yd²",
    "mi²",
    "gal",
    "ft³",
    "in³",
    "yd³",
    "qt",
    "pt",
    "fl oz",
    "imp gal",
    "bbl",
    "cup",
    "tbsp",
    "tsp",
    "bushel",
    "acre-ft",
    "psi",
    "inHg",
    "mph",
    "knot",
    "ft/s",
    "ft/min",
    "lbf",
    "lb/ft³",
    "lb/gal",
    "gal/min",
    "ft³/min",
    "ft³/s",
}

US_CUSTOMARY_SYMBOLS = {
    "lb",
    "oz",
    "short ton",
    "grain",
    "slug",
    "ft",
    "in",
    "yd",
    "mi",
    "mil",
    "chain",
    "rod",
    "furlong",
    "acre",
    "ft²",
    "in²",
    "yd²",
    "mi²",
    "gal",
    "ft³",
    "in³",
    "yd³",
    "qt",
    "pt",
    "fl oz",
    "bbl",
    "cup",
    "tbsp",
    "tsp",
    "bushel",
    "acre-ft",
    "psi",
    "inHg",
    "mph",
    "ft/s",
    "ft/min",
    "lbf",
    "lb/ft³",
    "lb/gal",
    "gal/min",
    "ft³/min",
    "ft³/s",
}

SCALE_BY_SYMBOL: dict[str, str] = {
    "µg": "micro",
    "mg": "milli",
    "g": "base_gram",
    "kg": "base_si",
    "t": "mega_gram",
    "kt": "kilo_tonne",
    "Mt": "mega_tonne",
    "Gt": "giga_tonne",
    "mL": "milli",
    "cL": "centi",
    "dL": "deci",
    "cm³": "centi_cubed",
    "mm²": "milli_squared",
    "cm²": "centi_squared",
    "km²": "kilo_squared",
    "mm": "milli",
    "cm": "centi",
    "dm": "deci",
    "km": "kilo",
    "nm": "nano",
    "µm": "micro",
    "ms": "milli",
    "µs": "micro",
    "ns": "nano",
    "kJ": "kilo",
    "MJ": "mega",
    "GJ": "giga",
    "TJ": "tera",
    "PJ": "peta",
    "Wh": "watt_hour",
    "kWh": "kilo",
    "MWh": "mega",
    "GWh": "giga",
    "TWh": "tera",
    "kW": "kilo",
    "MW": "mega",
    "GW": "giga",
    "kPa": "kilo",
    "MPa": "mega",
    "mbar": "milli",
    "kN": "kilo",
    "gCO2e": "base_gram",
    "kg CO2e": "kilo",
    "t CO2e": "tonne",
    "kt CO2e": "kilo_tonne",
    "Mt CO2e": "mega_tonne",
    "CurrencyThousand": "thousand",
    "CurrencyMillion": "million",
    "CurrencyBillion": "billion",
}

UCUM_CODE_OVERRIDES: dict[str, str] = {
    "µg": "ug",
    "m³": "m3",
    "ft³": "[ft_i]3",
    "cm³": "cm3",
    "in³": "[in_i]3",
    "yd³": "[yd_i]3",
    "m²": "m2",
    "km²": "km2",
    "ft²": "[ft_i]2",
    "in²": "[in_i]2",
    "yd²": "[yd_i]2",
    "mi²": "[mi_i]2",
    "ft": "[ft_i]",
    "in": "[in_i]",
    "yd": "[yd_i]",
    "mi": "[mi_i]",
    "short ton": "[ston_av]",
    "long ton": "[lton_av]",
    "fl oz": "[foz_us]",
    "imp gal": "[gal_br]",
    "bbl": "[bbl_us]",
    "acre-ft": "[acr_us].[ft_i]",
    "°C": "Cel",
    "°F": "[degF]",
    "°R": "[degR]",
    "BTU": "[Btu_IT]",
    "MMBtu": "10^6.[Btu_IT]",
    "therm": "[thm_us]",
    "toe": "10^7.cal",
    "hp": "[HP]",
    "BTU/h": "[Btu_IT]/h",
    "psi": "[psi]",
    "atm": "atm",
    "torr": "Torr",
    "inHg": "[in_i'Hg]",
    "knot": "[kn_i]",
    "lbf": "[lbf_av]",
    "kgf": "kgf",
    "gal/min": "[gal_us]/min",
    "ft³/min": "[ft_i]3/min",
    "ft³/s": "[ft_i]3/s",
    "Currency": "{currency}",
    "CurrencyThousand": "10^3.{currency}",
    "CurrencyMillion": "10^6.{currency}",
    "CurrencyBillion": "10^9.{currency}",
}

COUNT_UNIT_ALIASES = [
    "Count",
    "actions",
    "business partners",
    "cases",
    "complaints",
    "employees",
    "employees (head count)",
    "employees (head count or FTE)",
    "employees and workers",
    "fatalities",
    "fines",
    "governance body members",
    "head count",
    "incidents",
    "injuries",
    "instances",
    "legal actions",
    "non-employees (head count or FTE)",
    "operations",
    "recalls",
    "species",
    "suppliers",
    "workers",
]

SEMANTIC_VALUE_ALIASES = [
    "Boolean",
    "Date",
    "Text",
    "boolean",
    "date",
    "text",
]

REPORTED_SEMANTIC_UNIT_ALIASES = [
    "(organization-specific denominator)",
    "mass unit",
    "organization-specific metric",
    "reported area unit",
    "reported distance unit",
    "reported product quantity unit",
    "reported target unit",
    "varies by denominator",
    "weight or volume",
]

ADDITIONAL_UNITS: dict[str, dict[str, dict[str, Any]]] = {
    "mass": {
        "pg": {"name": "picogram", "conversion_factor": 1e-15, "aliases": ["picogram"]},
        "ng": {"name": "nanogram", "conversion_factor": 1e-12, "aliases": ["nanogram"]},
        "kt": {"name": "kilotonne", "conversion_factor": 1_000_000, "aliases": ["kilotonne"]},
        "Mt": {"name": "megatonne", "conversion_factor": 1_000_000_000, "aliases": ["megatonne"]},
        "Gt": {"name": "gigatonne", "conversion_factor": 1_000_000_000_000, "aliases": ["gigatonne"]},
        "grain": {"name": "grain", "conversion_factor": 0.00006479891, "aliases": ["gr"]},
        "slug": {"name": "slug", "conversion_factor": 14.59390294, "aliases": ["slug"]},
        "carat": {"name": "carat", "conversion_factor": 0.0002, "aliases": ["ct"]},
    },
    "energy": {
        "eV": {"name": "electronvolt", "conversion_factor": 1.602176634e-19, "aliases": ["electronvolt"]},
        "keV": {"name": "kiloelectronvolt", "conversion_factor": 1.602176634e-16, "aliases": ["kiloelectronvolt"]},
        "MeV": {"name": "megaelectronvolt", "conversion_factor": 1.602176634e-13, "aliases": ["megaelectronvolt"]},
        "TWh": {"name": "terawatt_hour", "conversion_factor": 3.6e15, "aliases": ["terawatt_hour"]},
        "quad": {"name": "quadrillion_btu", "conversion_factor": 1.05505585262e18, "aliases": ["quadrillion_btu"]},
    },
    "volume": {
        "µL": {"name": "microliter", "conversion_factor": 1e-9, "aliases": ["uL", "microliter"]},
        "Nm3": {
            "name": "normal_cubic_meter",
            "conversion_factor": 1,
            "aliases": ["Nm³", "normal cubic meter", "normal cubic metre"],
            "metadata": {
                "conversion_policy": "standard-condition-volume",
            },
        },
        "dam³": {"name": "cubic_decameter", "conversion_factor": 1000, "aliases": ["cubic_decameter"]},
        "hm³": {"name": "cubic_hectometer", "conversion_factor": 1_000_000, "aliases": ["cubic_hectometer"]},
        "km³": {"name": "cubic_kilometer", "conversion_factor": 1_000_000_000, "aliases": ["cubic_kilometer"]},
        "cup": {"name": "us_cup", "conversion_factor": 0.0002365882365, "aliases": ["us_cup"]},
        "tbsp": {"name": "us_tablespoon", "conversion_factor": 0.00001478676478, "aliases": ["tablespoon"]},
        "tsp": {"name": "us_teaspoon", "conversion_factor": 0.000004928921594, "aliases": ["teaspoon"]},
        "bushel": {"name": "us_bushel", "conversion_factor": 0.03523907017, "aliases": ["bu"]},
        "acre-ft": {"name": "acre_foot", "conversion_factor": 1233.48183754752, "aliases": ["acre_foot"]},
    },
    "area": {
        "a": {"name": "are", "conversion_factor": 100, "aliases": ["are"]},
        "daa": {"name": "decare", "conversion_factor": 1000, "aliases": ["decare"]},
        "rood": {"name": "rood", "conversion_factor": 1011.7141056, "aliases": ["rood"]},
    },
    "length": {
        "pm": {"name": "picometer", "conversion_factor": 1e-12, "aliases": ["picometer"]},
        "Å": {"name": "angstrom", "conversion_factor": 1e-10, "aliases": ["angstrom"]},
        "Mm": {"name": "megameter", "conversion_factor": 1_000_000, "aliases": ["megameter"]},
        "Gm": {"name": "gigameter", "conversion_factor": 1_000_000_000, "aliases": ["gigameter"]},
        "mil": {"name": "thou", "conversion_factor": 0.0000254, "aliases": ["thou"]},
        "fathom": {"name": "fathom", "conversion_factor": 1.8288, "aliases": ["fathom"]},
        "chain": {"name": "chain", "conversion_factor": 20.1168, "aliases": ["ch"]},
        "rod": {"name": "rod", "conversion_factor": 5.0292, "aliases": ["perch", "pole"]},
        "furlong": {"name": "furlong", "conversion_factor": 201.168, "aliases": ["fur"]},
    },
    "time": {
        "ns": {"name": "nanosecond", "conversion_factor": 1e-9, "aliases": ["nanosecond"]},
        "quarter": {"name": "calendar_quarter", "conversion_factor": 7_884_000, "aliases": ["calendar_quarter"]},
        "semester": {"name": "calendar_semester", "conversion_factor": 15_768_000, "aliases": ["calendar_semester"]},
    },
    "emissions": {
        "µg CO2e": {"name": "microgram_co2e", "conversion_factor": 1e-9, "aliases": ["ug CO2e", "microgram_co2e"]},
        "mg CO2e": {"name": "milligram_co2e", "conversion_factor": 1e-6, "aliases": ["milligram_co2e"]},
        "Gt CO2e": {"name": "gigatonne_co2e", "conversion_factor": 1_000_000_000_000, "aliases": ["gigatonne_co2e"]},
    },
    "power": {
        "mW": {"name": "milliwatt", "conversion_factor": 0.001, "aliases": ["milliwatt"]},
        "TW": {"name": "terawatt", "conversion_factor": 1_000_000_000_000, "aliases": ["terawatt"]},
        "VA": {"name": "volt_ampere", "conversion_factor": 1, "aliases": ["volt_ampere"]},
        "kVA": {"name": "kilovolt_ampere", "conversion_factor": 1000, "aliases": ["kilovolt_ampere"]},
        "MVA": {"name": "megavolt_ampere", "conversion_factor": 1_000_000, "aliases": ["megavolt_ampere"]},
        "ton_refrigeration": {"name": "ton_of_refrigeration", "conversion_factor": 3516.8528420667, "aliases": ["TR"]},
    },
    "pressure": {
        "GPa": {"name": "gigapascal", "conversion_factor": 1_000_000_000, "aliases": ["gigapascal"]},
        "mmHg": {"name": "millimeter_mercury", "conversion_factor": 133.322387415, "aliases": ["millimeter_mercury"]},
        "inHg": {"name": "inch_mercury", "conversion_factor": 3386.38815789, "aliases": ["inch_mercury"]},
        "cmH2O": {"name": "centimeter_water", "conversion_factor": 98.0665, "aliases": ["centimeter_water"]},
    },
    "speed": {
        "m/min": {"name": "meter_per_minute", "conversion_factor": 1 / 60, "aliases": ["meter_per_minute"]},
        "ft/min": {"name": "foot_per_minute", "conversion_factor": 0.00508, "aliases": ["fpm"]},
    },
    "force": {
        "dyn": {"name": "dyne", "conversion_factor": 0.00001, "aliases": ["dyne"]},
        "kip": {"name": "kilopound_force", "conversion_factor": 4448.2216152605, "aliases": ["kip_force"]},
    },
    "density": {
        "g/L": {"name": "gram_per_liter", "conversion_factor": 1, "aliases": ["gram_per_liter"]},
        "mg/L": {"name": "milligram_per_liter", "conversion_factor": 0.001, "aliases": ["milligram_per_liter"]},
        "lb/gal": {"name": "pound_per_us_gallon", "conversion_factor": 119.826427316, "aliases": ["pound_per_gallon"]},
    },
    "flow": {
        "mL/min": {"name": "milliliter_per_minute", "conversion_factor": 1.6666666666666667e-8, "aliases": ["milliliter_per_minute"]},
        "ft³/s": {"name": "cubic_foot_per_second", "conversion_factor": 0.028316846592, "aliases": ["cfs"]},
        "MGD": {"name": "million_us_gallons_per_day", "conversion_factor": 0.0438126364, "aliases": ["million_gallons_per_day"]},
    },
    "currency": {
        "Currency": {"name": "currency_unit", "conversion_factor": 1, "aliases": ["currency_unit", "monetary_unit"]},
        "CurrencyThousand": {"name": "thousand_currency_units", "conversion_factor": 1000, "aliases": ["kCurrency"]},
        "CurrencyMillion": {"name": "million_currency_units", "conversion_factor": 1_000_000, "aliases": ["mCurrency"]},
        "CurrencyBillion": {"name": "billion_currency_units", "conversion_factor": 1_000_000_000, "aliases": ["bnCurrency"]},
    },
    "ratio": {
        "cases_per_million_hours_worked": {
            "name": "cases_per_million_hours_worked",
            "conversion_factor": 1,
            "aliases": ["cases per million hours worked"],
            "metadata": {
                "dimension_vector": {"count": 1, "time": -1},
                "conversion_policy": "rate_display_unit",
            },
        },
        "hours_per_employee": {
            "name": "hours_per_employee",
            "conversion_factor": 1,
            "aliases": ["hours per employee"],
            "metadata": {
                "dimension_vector": {"time": 1, "count": -1},
                "conversion_policy": "rate_display_unit",
            },
        },
        "count_and_percent": {
            "name": "count_and_percent",
            "conversion_factor": 1,
            "aliases": ["count_and_percent", "number and percent"],
            "metadata": {
                "non_convertible": True,
                "conversion_policy": "compound_display_unit",
            },
        },
        "count_and_rate": {
            "name": "count_and_rate",
            "conversion_factor": 1,
            "aliases": ["count_and_rate", "number and rate"],
            "metadata": {
                "non_convertible": True,
                "conversion_policy": "compound_display_unit",
            },
        },
        "rate": {
            "name": "rate",
            "conversion_factor": 1,
            "aliases": ["rate"],
            "metadata": {"conversion_policy": "rate_display_unit"},
        },
        "m3_per_million_currency_revenue": {
            "name": "cubic_meter_per_million_currency_revenue",
            "conversion_factor": 1,
            "aliases": ["m3 per million EUR net revenue"],
            "metadata": {
                "dimension_vector": {"volume": 1, "currency": -1},
                "conversion_policy": "currency_denominator_requires_reporting_context",
            },
        },
        "energy_per_currency": {
            "name": "energy_per_currency",
            "conversion_factor": 1,
            "aliases": ["MWh per currency"],
            "metadata": {
                "dimension_vector": {"energy": 1, "currency": -1},
                "conversion_policy": "currency_denominator_requires_reporting_context",
            },
        },
        "currency_per_emissions": {
            "name": "currency_per_emissions",
            "conversion_factor": 1,
            "aliases": ["currency_per_tCO2eq"],
            "metadata": {
                "dimension_vector": {"currency": 1, "co2e": -1},
                "conversion_policy": "emissions_denominator_requires_reporting_context",
            },
        },
        "emissions_per_activity_or_output": {
            "name": "emissions_per_activity_or_output",
            "conversion_factor": 1,
            "aliases": [
                "tCO2e/(organization-specific denominator)",
                "tCO2eq_per_activity_or_output_unit",
            ],
            "metadata": {
                "dimension_vector": {"co2e": 1},
                "non_convertible": True,
                "conversion_policy": "organization_specific_denominator",
            },
        },
        "g CO2e/MJ": {
            "name": "gram_co2e_per_megajoule",
            "conversion_factor": 1,
            "aliases": ["g CO2e per MJ", "gCO2e/MJ"],
            "metadata": {
                "dimension_vector": {"co2e": 1, "energy": -1},
                "non_convertible": True,
                "conversion_policy": "curated_emissions_intensity_conversion_only",
            },
        },
        "kg CO2e/GJ": {
            "name": "kilogram_co2e_per_gigajoule",
            "conversion_factor": 1,
            "aliases": ["kg CO2e per GJ", "kgCO2e/GJ"],
            "metadata": {
                "dimension_vector": {"co2e": 1, "energy": -1},
                "non_convertible": True,
                "conversion_policy": "curated_emissions_intensity_conversion_only",
            },
        },
        "kg CO2e/kWh": {
            "name": "kilogram_co2e_per_kilowatt_hour",
            "conversion_factor": 1,
            "aliases": ["kg CO2e per kWh", "kgCO2e/kWh"],
            "metadata": {
                "dimension_vector": {"co2e": 1, "energy": -1},
                "non_convertible": True,
                "conversion_policy": "curated_emissions_intensity_conversion_only",
            },
        },
        "t CO2e/MWh": {
            "name": "tonne_co2e_per_megawatt_hour",
            "conversion_factor": 1,
            "aliases": ["t CO2e per MWh", "tCO2e/MWh"],
            "metadata": {
                "dimension_vector": {"co2e": 1, "energy": -1},
                "non_convertible": True,
                "conversion_policy": "curated_emissions_intensity_conversion_only",
            },
        },
        "kg CO2e/kg": {
            "name": "kilogram_co2e_per_kilogram",
            "conversion_factor": 1,
            "aliases": ["kg CO2e per kg", "kg CO2e per kilogram", "kgCO2e/kg"],
            "metadata": {
                "dimension_vector": {"co2e": 1, "mass": -1},
                "non_convertible": True,
                "conversion_policy": "curated_emissions_factor_unit",
            },
        },
        "kg CO2e/m2/year": {
            "name": "kilogram_co2e_per_square_meter_year",
            "conversion_factor": 1,
            "aliases": [
                "kg CO2e/m²/year",
                "kg CO2e per m2 per year",
                "kg CO2e per square meter per year",
                "kg CO2e per square metre per year",
                "kgCO2e/m2/year",
            ],
            "metadata": {
                "dimension_vector": {"co2e": 1, "area": -1, "time": -1},
                "non_convertible": True,
                "conversion_policy": "curated_emissions_factor_unit",
            },
        },
        "kg CO2e/asset type/year": {
            "name": "kilogram_co2e_per_asset_type_year",
            "conversion_factor": 1,
            "aliases": [
                "kg CO2e per asset type per year",
                "kgCO2e/asset type/year",
            ],
            "metadata": {
                "dimension_vector": {"co2e": 1, "time": -1},
                "non_convertible": True,
                "conversion_policy": "reported_semantic_denominator",
            },
        },
        "kg CO2e/building or asset type/year": {
            "name": "kilogram_co2e_per_building_or_asset_type_year",
            "conversion_factor": 1,
            "aliases": [
                "kg CO2e per building or asset type per year",
                "kgCO2e/building or asset type/year",
            ],
            "metadata": {
                "dimension_vector": {"co2e": 1, "time": -1},
                "non_convertible": True,
                "conversion_policy": "reported_semantic_denominator",
            },
        },
        "kg CO2e/reported product quantity unit": {
            "name": "kilogram_co2e_per_reported_product_quantity_unit",
            "conversion_factor": 1,
            "aliases": [
                "kg CO2e per reported product quantity unit",
                "kgCO2e/reported product quantity unit",
            ],
            "metadata": {
                "dimension_vector": {"co2e": 1},
                "non_convertible": True,
                "conversion_policy": "reported_semantic_denominator",
            },
        },
        "kg CO2e/reported energy quantity unit": {
            "name": "kilogram_co2e_per_reported_energy_quantity_unit",
            "conversion_factor": 1,
            "aliases": [
                "kg CO2e per reported energy quantity unit",
                "kgCO2e/reported energy quantity unit",
            ],
            "metadata": {
                "dimension_vector": {"co2e": 1},
                "non_convertible": True,
                "conversion_policy": "reported_semantic_denominator",
            },
        },
        "energy_per_organization_specific_denominator": {
            "name": "energy_per_organization_specific_denominator",
            "conversion_factor": 1,
            "aliases": [
                "Energy/(organization-specific denominator)",
                "MWh/(organization-specific denominator)",
                "energy per organization-specific denominator",
            ],
            "metadata": {
                "dimension_vector": {"energy": 1},
                "non_convertible": True,
                "conversion_policy": "organization_specific_denominator",
            },
        },
        "MWh/t": {
            "name": "megawatt_hour_per_tonne",
            "conversion_factor": 3_600_000,
            "aliases": ["MWh/tonne", "MWh per tonne", "MWh per metric tonne"],
            "metadata": {
                "dimension_vector": {"energy": 1, "mass": -1},
                "conversion_policy": "compound_display_unit",
            },
        },
        "MWh/kg": {
            "name": "megawatt_hour_per_kilogram",
            "conversion_factor": 3_600_000_000,
            "aliases": ["MWh per kg", "MWh per kilogram"],
            "metadata": {
                "dimension_vector": {"energy": 1, "mass": -1},
                "conversion_policy": "compound_display_unit",
            },
        },
        "MWh/L": {
            "name": "megawatt_hour_per_litre",
            "conversion_factor": 3_600_000_000_000,
            "aliases": ["MWh/litre", "MWh/liter", "MWh per litre", "MWh per liter"],
            "metadata": {
                "dimension_vector": {"energy": 1, "volume": -1},
                "conversion_policy": "compound_display_unit",
            },
        },
        "MWh/Nm3": {
            "name": "megawatt_hour_per_normal_cubic_meter",
            "conversion_factor": 3_600_000_000,
            "aliases": [
                "MWh/Nm³",
                "MWh per Nm3",
                "MWh per normal cubic meter",
                "MWh per normal cubic metre",
            ],
            "metadata": {
                "dimension_vector": {"energy": 1, "volume": -1},
                "conversion_policy": "standard-condition-volume-compound-display-unit",
            },
        },
        "MWh/reported_semantic_unit": {
            "name": "megawatt_hour_per_reported_semantic_unit",
            "conversion_factor": 1,
            "aliases": [
                "MWh/reported quantity unit",
                "MWh per reported quantity unit",
                "MWh per reported semantic unit",
            ],
            "metadata": {
                "dimension_vector": {"energy": 1},
                "non_convertible": True,
                "conversion_policy": "reported_semantic_denominator",
            },
        },
    },
    "count": {
        "count": {
            "name": "count",
            "conversion_factor": 1,
            "aliases": COUNT_UNIT_ALIASES,
            "metadata": {
                "dimension_vector": {"count": 1},
                "conversion_policy": "same_population_count_only",
            },
        },
    },
    "semantic": {
        "semantic_value": {
            "name": "semantic_value",
            "conversion_factor": 1,
            "aliases": SEMANTIC_VALUE_ALIASES,
            "metadata": {
                "non_convertible": True,
                "conversion_policy": "reported_semantic_value",
            },
        },
        "reported_semantic_unit": {
            "name": "reported_semantic_unit",
            "conversion_factor": 1,
            "aliases": REPORTED_SEMANTIC_UNIT_ALIASES,
            "metadata": {
                "non_convertible": True,
                "conversion_policy": "reported_semantic_unit",
            },
        },
    },
}

CATEGORY_DESCRIPTIONS = {
    "currency": "Dimensionless monetary amount scale atoms. FX rates are handled by the FX engine, not by unit factors.",
    "count": "Sustainability count labels normalized for catalog lookup; population semantics stay in dimensions and formulas.",
    "semantic": "Non-convertible reporting placeholders and data-value markers used for catalog lookup and UI policy.",
    "ratio": "Dimensionless ratios, rates, intensities, and compound display units.",
}


def build_units_catalog(input_path: Path = DEFAULT_INPUT) -> dict[str, Any]:
    catalog = json.loads(input_path.read_text(encoding="utf-8"))
    categories = catalog.setdefault("categories", {})

    for category, units in ADDITIONAL_UNITS.items():
        category_payload = categories.setdefault(
            category,
            {
                "base_unit": _base_unit_for_category(category),
                "units": {},
                "description": CATEGORY_DESCRIPTIONS.get(category, f"{category} units."),
                "special_conversions": False,
            },
        )
        category_payload["base_unit"] = category_payload.get("base_unit") or _base_unit_for_category(category)
        category_payload.setdefault("units", {})
        category_payload.setdefault("description", CATEGORY_DESCRIPTIONS.get(category, category_payload.get("description", "")))
        category_payload.setdefault("special_conversions", False)
        for symbol, unit in units.items():
            merged = {**unit, "symbol": symbol}
            existing = category_payload["units"].get(symbol)
            if isinstance(existing, dict):
                merged = _merge_unit(existing, merged)
            category_payload["units"][symbol] = merged

    for category, category_payload in categories.items():
        category_payload.setdefault("description", CATEGORY_DESCRIPTIONS.get(category, category_payload.get("description", "")))
        category_payload.setdefault("special_conversions", False)
        category_payload.setdefault("units", {})
        for symbol, unit in category_payload["units"].items():
            unit.setdefault("symbol", symbol)
            unit.setdefault("aliases", [])
            unit["aliases"] = _dedupe([str(alias) for alias in unit.get("aliases", [])])
            metadata = dict(unit.get("metadata") or {})
            metadata = _metadata_for(category, symbol, metadata)
            unit["metadata"] = metadata

    for rule in catalog.get("custom_conversion_rules", []):
        if not isinstance(rule, dict):
            continue
        metadata = dict(rule.get("metadata") or {})
        metadata.setdefault("source_system", "SDS conversion rule")
        metadata.setdefault("source_version", SOURCES["sds"]["version"])
        metadata.setdefault("source_url", SOURCES["sds"]["url"])
        rule["metadata"] = metadata

    catalog["version"] = "2.1.0"
    catalog["last_updated"] = "2026-05-22"
    catalog["description"] = (
        "Source-traced unit catalog for SDS calculations, covering SI/UCUM "
        "atoms, common imperial and US customary units, sustainability CO2e "
        "scales, currency amount scales, and non-convertible reporting-unit "
        "semantics."
    )
    catalog["sources"] = SOURCES
    return catalog


def write_units_catalog(catalog: Mapping[str, Any], output_path: Path = DEFAULT_OUTPUT) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _merge_unit(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = {**existing, **incoming}
    aliases = list(existing.get("aliases", [])) + list(incoming.get("aliases", []))
    merged["aliases"] = _dedupe([str(alias) for alias in aliases])
    metadata = dict(existing.get("metadata") or {})
    metadata.update(incoming.get("metadata") or {})
    if metadata:
        merged["metadata"] = metadata
    return merged


def _metadata_for(category: str, symbol: str, existing: dict[str, Any]) -> dict[str, Any]:
    source_key = (
        "sds"
        if category in {"currency", "emissions", "percentage", "ratio", "count", "semantic"}
        else "bipm_si"
    )
    source_system = "SDS canonical sustainability unit" if source_key == "sds" else "SI/BIPM"
    system = SYSTEM_BY_CATEGORY.get(category, category)
    if symbol in NIST_SYMBOLS:
        source_key = "nist_hb44"
        source_system = "NIST Handbook 44 Appendix C"
        system = "us_customary" if symbol in US_CUSTOMARY_SYMBOLS else "imperial"
    if category == "currency":
        source_system = "SDS currency scale"
        system = "monetary_scale"
    elif category == "count":
        source_system = "SDS sustainability count label policy"
        system = "sustainability_count"
    elif category == "semantic":
        source_system = "SDS non-convertible reporting unit policy"
        system = "reported_semantic_unit_policy"

    source = SOURCES[source_key]
    metadata = {
        "ucum_code": UCUM_CODE_OVERRIDES.get(symbol, symbol),
        "dimension_vector": CATEGORY_DIMENSIONS.get(category, {}),
        "source_system": source_system,
        "source_version": source["version"],
        "source_url": source["url"],
        "system": system,
        "category": category,
    }
    if symbol in SCALE_BY_SYMBOL:
        metadata["scale"] = SCALE_BY_SYMBOL[symbol]
    metadata.update(existing)
    return metadata


def _base_unit_for_category(category: str) -> str:
    base_units = {
        "currency": "Currency",
        "count": "count",
        "semantic": "reported_semantic_unit",
    }
    return base_units.get(category, "")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value and value not in seen:
            output.append(value)
            seen.add(value)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    write_units_catalog(build_units_catalog(args.input), args.output)


if __name__ == "__main__":
    main()
