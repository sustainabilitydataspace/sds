from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = REPO_ROOT.parent


def _load_script(module_name: str, relative_path: str):
    path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_units_catalog_contains_source_traced_ucum_and_imperial_atoms():
    payload = json.loads(
        (REPO_ROOT / "src" / "data" / "units_database.json").read_text(encoding="utf-8")
    )
    categories = payload["categories"]
    units = {
        symbol: unit
        for category in categories.values()
        for symbol, unit in category["units"].items()
    }

    assert payload["version"] == "2.1.0"
    assert payload["sources"]["ucum"]["url"] == "https://ucum.org/ucum"
    assert len(categories) >= 16
    assert len(units) >= 150

    assert units["kg"]["metadata"]["ucum_code"] == "kg"
    assert units["kg"]["metadata"]["source_system"] == "SI/BIPM"
    assert units["kg"]["metadata"]["dimension_vector"] == {"mass": 1}
    assert units["lb"]["metadata"]["source_system"] == "NIST Handbook 44 Appendix C"
    assert units["mi"]["metadata"]["system"] == "us_customary"
    assert units["MWh"]["metadata"]["scale"] == "mega"
    assert units["CurrencyMillion"]["metadata"]["dimension_vector"] == {"currency": 1}


def test_iso4217_builder_parses_current_and_historic_six_xml(tmp_path):
    builder = _load_script(
        "build_iso4217_currency_seed", "scripts/build_iso4217_currency_seed.py"
    )
    list_one = tmp_path / "list-one.xml"
    list_three = tmp_path / "list-three.xml"
    list_one.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<ISO_4217 Pblshd="2026-01-01">
  <CcyTbl>
    <CcyNtry><CtryNm>UNITED KINGDOM</CtryNm><CcyNm>Pound Sterling</CcyNm><Ccy>GBP</Ccy><CcyNbr>826</CcyNbr><CcyMnrUnts>2</CcyMnrUnts></CcyNtry>
    <CcyNtry><CtryNm>TEST ENTITY</CtryNm><CcyNm>Gold</CcyNm><Ccy>XAU</Ccy><CcyNbr>959</CcyNbr><CcyMnrUnts>N.A.</CcyMnrUnts></CcyNtry>
  </CcyTbl>
</ISO_4217>""",
        encoding="utf-8",
    )
    list_three.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<ISO_4217 Pblshd="2026-01-01">
  <HstrcCcyTbl>
    <HstrcCcyNtry><CtryNm>BULGARIA</CtryNm><CcyNm>Bulgarian Lev</CcyNm><Ccy>BGN</Ccy><CcyNbr>975</CcyNbr><CcyMnrUnts>2</CcyMnrUnts><WthdrwlDt>2026-01-01</WthdrwlDt></HstrcCcyNtry>
  </HstrcCcyTbl>
</ISO_4217>""",
        encoding="utf-8",
    )

    rows = builder.build_currency_seed(list_one, list_three)
    by_code = {row["code"]: row for row in rows}

    assert by_code["GBP"]["is_active"] is True
    assert by_code["GBP"]["minor_units"] == 2
    assert by_code["GBP"]["currency_metadata"]["entities"] == ["UNITED KINGDOM"]
    assert by_code["XAU"]["minor_units"] == 0
    assert by_code["XAU"]["currency_metadata"]["minor_units_raw"] == "N.A."
    assert by_code["BGN"]["is_active"] is False
    assert by_code["BGN"]["valid_to"] == "2026-01-01"


def test_ecb_importer_converts_eur_reference_history_to_sds_rows():
    importer = _load_script("import_ecb_fx_rates", "scripts/import_ecb_fx_rates.py")
    csv_text = "Date,USD,GBP\n2024-03-15,1.1000,0.8500\n2024-03-16,N/A,0.8600\n"

    rows = importer.parse_ecb_hist_csv(csv_text)

    usd = next(row for row in rows if row["base_currency"] == "USD")
    gbp_rows = [row for row in rows if row["base_currency"] == "GBP"]

    assert usd["quote_currency"] == "EUR"
    assert usd["rate_date"].isoformat() == "2024-03-15"
    assert usd["rate_value"] == Decimal("0.909091")
    assert usd["provider"] == "ECB"
    assert usd["rate_type"] == "reference"
    assert len(gbp_rows) == 2


def test_ecb_zip_loader_rejects_ambiguous_and_oversized_archives(monkeypatch):
    importer = _load_script(
        "import_ecb_fx_rates_secure", "scripts/import_ecb_fx_rates.py"
    )
    ambiguous = io.BytesIO()
    with zipfile.ZipFile(ambiguous, "w") as archive:
        archive.writestr("one.csv", "Date,USD\n2024-01-01,1.1\n")
        archive.writestr("two.csv", "Date,USD\n2024-01-01,1.1\n")

    with pytest.raises(ValueError, match="exactly one CSV"):
        importer.load_ecb_hist_csv_from_zip(ambiguous.getvalue())

    oversized = io.BytesIO()
    with zipfile.ZipFile(oversized, "w") as archive:
        archive.writestr("history.csv", "Date,USD\n" + ("x" * 32))
    monkeypatch.setattr(importer, "MAX_ZIP_MEMBER_BYTES", 16, raising=False)
    with pytest.raises(ValueError, match="expanded byte budget"):
        importer.load_ecb_hist_csv_from_zip(oversized.getvalue())


def test_ecb_history_loader_accepts_sds_shaped_csv():
    loader = _load_script("load_ecb_fx_history", "scripts/load_ecb_fx_history.py")
    csv_text = (
        "rate_date,base_currency,quote_currency,rate_value,provider,rate_type\n"
        "2024-03-15,USD,EUR,0.909091,ECB,reference\n"
    )

    rows = loader.parse_sds_fx_csv(csv_text)

    assert rows == [
        {
            "rate_date": "2024-03-15",
            "base_currency": "USD",
            "quote_currency": "EUR",
            "rate_value": Decimal("0.909091"),
            "provider": "ECB",
            "rate_type": "reference",
        }
    ]


def test_ecb_history_loader_filters_rows_to_requested_window_and_currency():
    loader = _load_script("load_ecb_fx_history", "scripts/load_ecb_fx_history.py")
    rows = [
        {
            "rate_date": "2019-12-31",
            "base_currency": "USD",
            "quote_currency": "EUR",
            "rate_value": Decimal("0.900000"),
            "provider": "ECB",
            "rate_type": "reference",
        },
        {
            "rate_date": "2020-01-02",
            "base_currency": "USD",
            "quote_currency": "EUR",
            "rate_value": Decimal("0.910000"),
            "provider": "ECB",
            "rate_type": "reference",
        },
        {
            "rate_date": "2020-01-02",
            "base_currency": "GBP",
            "quote_currency": "EUR",
            "rate_value": Decimal("1.180000"),
            "provider": "ECB",
            "rate_type": "reference",
        },
    ]

    filtered = loader.filter_fx_rows(
        rows,
        start_date=loader.DEFAULT_START_DATE,
        end_date=loader.DEFAULT_START_DATE.replace(day=2),
        base_currencies={"USD"},
    )

    assert len(filtered) == 1
    assert filtered[0]["rate_date"].isoformat() == "2020-01-02"
    assert filtered[0]["base_currency"] == "USD"
