"""Tests for UnitDatabase (JSON-backed units registry)."""

from __future__ import annotations

import json

import pytest

from src.calculation.unit_converter import UnitCategory, UnitConversionError

# Important: import the real class at module import time so it is not affected by
# the autouse fixture patching `src.calculation.unit_database.UnitDatabase`.
from src.calculation.unit_database import UnitDatabase as RealUnitDatabase


def test_unit_database_default_path_loads():
    db = RealUnitDatabase()
    assert str(db.database_path).endswith("units_database.json")
    assert db.get_unit_definitions()


def test_unit_database_missing_file_creates_empty_database(tmp_path):
    db_path = tmp_path / "units_db.json"
    assert not db_path.exists()

    db = RealUnitDatabase(database_path=str(db_path))
    info = db.get_database_info()
    assert info["categories_count"] == 0
    assert info["custom_units_count"] == 0


def test_unit_database_load_invalid_json_raises(tmp_path):
    db_path = tmp_path / "broken.json"
    db_path.write_text("{ not valid json", encoding="utf-8")

    with pytest.raises(UnitConversionError):
        RealUnitDatabase(database_path=str(db_path))


def test_unit_database_save_database_failure_raises(tmp_path, monkeypatch):
    db_path = tmp_path / "db.json"
    db = RealUnitDatabase(database_path=str(db_path))

    import builtins

    monkeypatch.setattr(
        builtins, "open", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("boom"))
    )
    with pytest.raises(UnitConversionError):
        db.save_database()


def test_unit_database_get_unit_definitions_skips_unknown_category(tmp_path):
    db_path = tmp_path / "db.json"
    db_path.write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "categories": {
                    "mass": {
                        "base_unit": "kg",
                        "units": {
                            "kg": {
                                "symbol": "kg",
                                "name": "kilogram",
                                "conversion_factor": 1,
                                "aliases": ["kilogram"],
                            }
                        },
                    },
                    "unknown_category": {
                        "base_unit": "x",
                        "units": {
                            "x": {"symbol": "x", "name": "x", "conversion_factor": 1}
                        },
                    },
                },
                "custom_conversion_rules": [],
            }
        ),
        encoding="utf-8",
    )

    db = RealUnitDatabase(database_path=str(db_path))
    defs = db.get_unit_definitions()
    assert any(d.symbol == "kg" and d.category == UnitCategory.MASS for d in defs)
    assert all(d.category != "unknown_category" for d in defs)


def test_unit_database_get_unit_definitions_skips_bad_unit_data(tmp_path):
    db_path = tmp_path / "db.json"
    db_path.write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "categories": {
                    "mass": {
                        "base_unit": "kg",
                        "units": {
                            "kg": {
                                "symbol": "kg",
                                "name": "kilogram",
                                "conversion_factor": 1,
                                "aliases": ["kilogram"],
                            },
                            # Decimal("nope") triggers the warning path.
                            "bad": {
                                "symbol": "bad",
                                "name": "bad",
                                "conversion_factor": "nope",
                            },
                        },
                    }
                },
                "custom_conversion_rules": [],
            }
        ),
        encoding="utf-8",
    )

    db = RealUnitDatabase(database_path=str(db_path))
    defs = db.get_unit_definitions()
    assert any(d.symbol == "kg" for d in defs)
    assert all(d.symbol != "bad" for d in defs)


def test_unit_database_get_unit_definitions_skips_custom_units_unknown_category(
    tmp_path,
):
    db = RealUnitDatabase(database_path=str(tmp_path / "db.json"))
    db.custom_units = {
        "unknown_category": {
            "x": {
                "symbol": "x",
                "name": "x",
                "base_unit": "x",
                "conversion_factor": 1,
            }
        }
    }

    defs = db.get_unit_definitions()
    assert all(getattr(d.category, "value", None) != "unknown_category" for d in defs)


def test_unit_database_get_unit_definitions_skips_bad_custom_unit_data(tmp_path):
    db = RealUnitDatabase(database_path=str(tmp_path / "db.json"))
    db.custom_units = {
        UnitCategory.MASS.value: {
            # Missing conversion_factor triggers warning path.
            "bad": {"symbol": "bad", "name": "bad", "base_unit": "kg"},
        }
    }

    defs = db.get_unit_definitions()
    assert all(d.symbol != "bad" for d in defs)


def test_unit_database_get_conversion_rules_skips_invalid_entries(tmp_path):
    db = RealUnitDatabase(database_path=str(tmp_path / "db.json"))
    db.database_data["custom_conversion_rules"] = [
        {"to_unit": "g", "formula": "value * 1000"}
    ]
    db.custom_rules = [{"from_unit": "m"}]

    rules = db.get_conversion_rules()
    assert rules == []


def test_unit_database_add_remove_unit_custom_and_persistent(tmp_path):
    db_path = tmp_path / "db.json"
    db = RealUnitDatabase(database_path=str(db_path))

    assert db.add_unit(
        symbol="custom_kg",
        name="Custom kg",
        category=UnitCategory.MASS.value,
        base_unit="kg",
        conversion_factor=1.0,
        aliases=["ckg"],
        save_to_database=False,
    )
    assert any(d.symbol == "custom_kg" for d in db.get_unit_definitions())
    assert (
        db.remove_unit("custom_kg", UnitCategory.MASS.value, from_database=False)
        is True
    )

    assert db.add_unit(
        symbol="kg",
        name="kilogram",
        category=UnitCategory.MASS.value,
        base_unit="kg",
        conversion_factor=1.0,
        save_to_database=True,
    )
    assert db_path.exists()
    assert db.remove_unit("kg", UnitCategory.MASS.value, from_database=True) is True


def test_unit_database_add_unit_invalid_category_returns_false(tmp_path):
    db = RealUnitDatabase(database_path=str(tmp_path / "db.json"))
    assert (
        db.add_unit(
            symbol="x",
            name="x",
            category="not-a-category",
            base_unit="x",
            conversion_factor=1.0,
        )
        is False
    )


def test_unit_database_add_unit_creates_categories_when_missing(tmp_path):
    db_path = tmp_path / "db.json"
    db = RealUnitDatabase(database_path=str(db_path))
    db.database_data = {}

    assert db.add_unit(
        symbol="kg",
        name="kilogram",
        category=UnitCategory.MASS.value,
        base_unit="kg",
        conversion_factor=1.0,
        save_to_database=True,
    )
    assert "categories" in db.database_data
    assert "mass" in db.database_data["categories"]
    assert "kg" in db.database_data["categories"]["mass"]["units"]


def test_unit_database_add_conversion_rule_save_to_database_and_error(tmp_path):
    db_path = tmp_path / "db.json"
    db = RealUnitDatabase(database_path=str(db_path))
    db.database_data = {}

    assert db.add_conversion_rule(
        from_unit="kg",
        to_unit="g",
        formula="value * 1000",
        save_to_database=True,
    )
    assert "custom_conversion_rules" in db.database_data
    assert any(
        r.from_unit == "kg" and r.to_unit == "g" for r in db.get_conversion_rules()
    )

    db.save_database = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    assert (
        db.add_conversion_rule(
            from_unit="m",
            to_unit="cm",
            formula="value * 100",
            save_to_database=True,
        )
        is False
    )


def test_unit_database_remove_unit_returns_false_when_missing_and_on_error(tmp_path):
    db = RealUnitDatabase(database_path=str(tmp_path / "db.json"))
    assert (
        db.remove_unit("missing", UnitCategory.MASS.value, from_database=False) is False
    )

    db.database_data = None  # force exception path
    assert db.remove_unit("any", UnitCategory.MASS.value, from_database=True) is False


def test_unit_database_export_includes_custom_units_and_handles_error(
    tmp_path, monkeypatch
):
    db = RealUnitDatabase(database_path=str(tmp_path / "db.json"))
    assert db.add_unit(
        symbol="custom_kg",
        name="Custom kg",
        category=UnitCategory.MASS.value,
        base_unit="kg",
        conversion_factor=1.0,
        save_to_database=False,
    )

    export_path = tmp_path / "export.json"
    assert db.export_database(str(export_path)) is True
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert (
        exported["categories"][UnitCategory.MASS.value]["units"]["custom_kg"]["symbol"]
        == "custom_kg"
    )

    import builtins

    monkeypatch.setattr(
        builtins, "open", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("boom"))
    )
    assert db.export_database(str(tmp_path / "fail.json")) is False


def test_unit_database_import_merge_existing_category_and_replace_and_error(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "db.json"
    db = RealUnitDatabase(database_path=str(db_path))
    db.database_data = {
        "version": "1.0.0",
        "categories": {
            "mass": {
                "base_unit": "kg",
                "units": {
                    "kg": {"symbol": "kg", "name": "kilogram", "conversion_factor": 1}
                },
            }
        },
        "custom_conversion_rules": [],
    }

    import_path = tmp_path / "import.json"
    import_path.write_text(
        json.dumps(
            {
                "categories": {
                    "mass": {
                        "base_unit": "kg",
                        "units": {
                            "g": {
                                "symbol": "g",
                                "name": "gram",
                                "conversion_factor": 0.001,
                            }
                        },
                    }
                },
                "custom_conversion_rules": [],
            }
        ),
        encoding="utf-8",
    )

    assert db.import_database(str(import_path), merge=True) is True
    assert "g" in db.database_data["categories"]["mass"]["units"]

    replace_path = tmp_path / "replace.json"
    replace_path.write_text(
        json.dumps({"categories": {"energy": {"base_unit": "J", "units": {}}}}),
        encoding="utf-8",
    )
    assert db.import_database(str(replace_path), merge=False) is True
    assert "energy" in db.database_data["categories"]

    import builtins

    monkeypatch.setattr(
        builtins, "open", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("boom"))
    )
    assert db.import_database(str(tmp_path / "missing.json"), merge=True) is False


def test_unit_database_add_conversion_rule_and_import_export(tmp_path):
    db_path = tmp_path / "db.json"
    db = RealUnitDatabase(database_path=str(db_path))

    assert db.add_conversion_rule(
        from_unit="kg",
        to_unit="g",
        formula="value * 1000",
        reverse_formula="value / 1000",
        save_to_database=False,
    )
    rules = db.get_conversion_rules()
    assert any(r.from_unit == "kg" and r.to_unit == "g" for r in rules)

    export_path = tmp_path / "export.json"
    assert db.export_database(str(export_path)) is True
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert "custom_conversion_rules" in exported

    # Import merge: add a category and ensure it merges
    import_path = tmp_path / "import.json"
    import_path.write_text(
        json.dumps(
            {
                "categories": {
                    "energy": {
                        "base_unit": "J",
                        "units": {
                            "J": {
                                "symbol": "J",
                                "name": "joule",
                                "conversion_factor": 1,
                                "aliases": ["joule"],
                            }
                        },
                    }
                },
                "custom_conversion_rules": [
                    {"from_unit": "m", "to_unit": "cm", "formula": "value * 100"}
                ],
            }
        ),
        encoding="utf-8",
    )

    assert db.import_database(str(import_path), merge=True) is True
    info = db.get_database_info()
    assert "energy" in info["categories"]
