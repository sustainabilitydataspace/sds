"""Tests for the strict operational values CSV contract."""

from __future__ import annotations

from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest

from src.services.value_csv_import import (
    ValueCsvContractError,
    _normalize_value_type,
    _parse_boolean,
    _parse_value,
    load_values_from_csv,
    load_values_from_handle,
)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_load_values_from_csv_parses_operational_rows(tmp_path):
    csv_path = _write(
        tmp_path / "values.csv",
        "concept,entity,period,value,unit\n"
        "syg:Water_Cooling,test_madrid_plant,2024-01-15,1.5,m³\n",
    )

    rows = load_values_from_csv(csv_path)

    assert len(rows) == 1
    assert rows[0].concept == "syg:Water_Cooling"
    assert rows[0].entity == "test_madrid_plant"
    assert rows[0].value_type == "numeric"


def test_load_values_from_csv_rejects_semantic_headers(tmp_path):
    csv_path = _write(
        tmp_path / "semantic_like.csv",
        "identifier,title,dimension,unitName\n" "urn:sds:reg:test:1,Example,E,m³\n",
    )

    with pytest.raises(ValueCsvContractError, match="missing required columns"):
        load_values_from_csv(csv_path)


def test_load_values_from_csv_rejects_unexpected_columns(tmp_path):
    csv_path = _write(
        tmp_path / "unexpected.csv",
        "concept,entity,period,value,unit,code_esrs\n"
        "syg:Water_Cooling,test_madrid_plant,2024-01-15,1.5,m³,E3-5\n",
    )

    with pytest.raises(ValueCsvContractError, match="unsupported columns"):
        load_values_from_csv(csv_path)


def test_load_values_from_csv_uses_default_entity(tmp_path):
    csv_path = _write(
        tmp_path / "values.csv",
        "concept,entity,period,value,unit\n" "syg:Water_Cooling,,2024-01-15,1.5,m³\n",
    )

    rows = load_values_from_csv(csv_path, default_entity="test_madrid_plant")

    assert rows[0].entity == "test_madrid_plant"


def test_load_values_from_csv_merges_default_metadata_with_metadata_json(tmp_path):
    csv_path = _write(
        tmp_path / "values.csv",
        "concept,entity,period,value,unit,metadata_json\n"
        'syg:Water_Cooling,test_madrid_plant,2024-01-15,1.5,m³,"{""quality"": ""validated"", ""source"": ""row""}"\n',
    )

    rows = load_values_from_csv(
        csv_path,
        default_metadata={"source": "csv_import", "batch": "b1"},
    )

    assert rows[0].metadata == {
        "source": "row",
        "batch": "b1",
        "quality": "validated",
    }
    assert rows[0].external_key is None


def test_metadata_json_rejects_duplicate_keys(tmp_path):
    csv_path = _write(
        tmp_path / "values.csv",
        "concept,entity,period,value,unit,metadata_json\n"
        'syg:Water_Cooling,test_madrid_plant,2024,1.5,m³,"{'
        '""source"":""first"",""source"":""second""}"\n',
    )

    with pytest.raises(ValueCsvContractError, match="duplicate JSON key"):
        load_values_from_csv(csv_path)


def test_values_csv_rejects_rows_with_extra_cells(tmp_path):
    csv_path = _write(
        tmp_path / "values.csv",
        "concept,entity,period,value,unit\n"
        "syg:Water_Cooling,test_madrid_plant,2024,1.5,m³,unexpected\n",
    )

    with pytest.raises(ValueCsvContractError, match="extra cells"):
        load_values_from_csv(csv_path)


def test_load_values_from_csv_accepts_external_key_column(tmp_path):
    csv_path = _write(
        tmp_path / "values.csv",
        "concept,entity,period,external_key,value,unit\n"
        "syg:Water_Cooling,test_madrid_plant,2024-01-15,erp:obs-1,1.5,m³\n",
    )

    rows = load_values_from_csv(csv_path)

    assert rows[0].external_key == "erp:obs-1"


def test_load_values_from_csv_accepts_conversion_metadata(tmp_path):
    csv_path = _write(
        tmp_path / "values.csv",
        "concept,entity,period,value,unit,currency,value_date,period_start,period_end,expected_unit,expected_currency,fx_policy_id\n"
        "syg:Revenue,test_company,2024-12-31,100,CurrencyMillion,gbp,2024-12-31,2024-01-01,2024-12-31,,eur,ecb-reference-monthly-average\n",
    )

    rows = load_values_from_csv(csv_path)

    assert rows[0].currency == "GBP"
    assert rows[0].expected_currency == "EUR"
    assert rows[0].expected_unit is None
    assert rows[0].fx_policy_id == "ecb-reference-monthly-average"
    assert str(rows[0].value_date) == "2024-12-31"
    assert str(rows[0].period_start) == "2024-01-01"
    assert str(rows[0].period_end) == "2024-12-31"


def test_load_values_from_csv_accepts_physical_expected_unit(tmp_path):
    csv_path = _write(
        tmp_path / "values.csv",
        "concept,entity,period,value,unit,expected_unit\n"
        "syg:Energy,test_company,2024-12-31,1000,kWh,MWh\n",
    )

    rows = load_values_from_csv(csv_path)

    assert rows[0].currency is None
    assert rows[0].expected_currency is None
    assert rows[0].expected_unit == "MWh"


def test_load_values_from_handle_rejects_second_data_row_when_max_rows_is_one():
    csv_text = (
        "concept,entity,period,value,unit\n"
        "syg:Water,e,2024-01-01,1,m3\n"
        "syg:Secret_Row_Content,e,2024-01-02,2,m3\n"
    )

    with pytest.raises(ValueCsvContractError) as exc_info:
        load_values_from_handle(StringIO(csv_text), max_rows=1)

    message = str(exc_info.value)
    assert "configured row limit of 1" in message
    assert "Secret_Row_Content" not in message


def test_load_values_from_handle_without_max_rows_keeps_existing_unlimited_behavior():
    csv_text = (
        "concept,entity,period,value,unit\n"
        "syg:Water,e,2024-01-01,1,m3\n"
        "syg:Energy,e,2024-01-02,2,m3\n"
    )

    rows = load_values_from_handle(StringIO(csv_text))

    assert [row.concept for row in rows] == ["syg:Water", "syg:Energy"]


@pytest.mark.parametrize(
    ("headers", "row", "match"),
    [
        (
            "currency,expected_currency,fx_policy_id,value_date",
            "gb1,eur,ecb-reference-monthly-average,2024-12-31",
            "currency must be a 3-letter currency code",
        ),
        (
            "currency,expected_currency,value_date",
            "gbp,eur,2024-12-31",
            "expected_currency requires fx_policy_id",
        ),
        (
            "currency,fx_policy_id,value_date",
            "gbp,ecb-reference-monthly-average,2024-12-31",
            "fx_policy_id requires expected_currency",
        ),
        (
            "expected_currency,fx_policy_id,value_date",
            "eur,ecb-reference-monthly-average,2024-12-31",
            "expected_currency requires row-level currency",
        ),
        (
            "currency,expected_currency,fx_policy_id",
            "gbp,eur,ecb-reference-monthly-average",
            "FX values require value_date or period_start plus period_end",
        ),
        (
            "currency,expected_currency,fx_policy_id,period_start",
            "gbp,eur,ecb-reference-monthly-average,2024-01-01",
            "period_start and period_end must be supplied together",
        ),
        (
            "period_start",
            "2024-01-01",
            "period_start and period_end must be supplied together",
        ),
        (
            "period_end",
            "2024-12-31",
            "period_start and period_end must be supplied together",
        ),
    ],
)
def test_load_values_from_csv_rejects_unsafe_conversion_metadata(
    tmp_path, headers, row, match
):
    csv_path = _write(
        tmp_path / "values.csv",
        "concept,entity,period,value,unit," + headers + "\n"
        "syg:Revenue,test_company,2024-12-31,100,gbp," + row + "\n",
    )

    with pytest.raises(ValueCsvContractError, match=match):
        load_values_from_csv(csv_path)


def test_load_values_from_csv_accepts_atomizer_value_types(tmp_path):
    csv_path = _write(
        tmp_path / "values.csv",
        "concept,entity,period,value,unit,value_type\n"
        "syg:Policy_Status,test_madrid_plant,2024-12-31,true,Boolean,boolean\n"
        "syg:Transition_Narrative,test_madrid_plant,2024-12-31,Plan approved by board,Text,narrative\n"
        "syg:Mixed_Disclosure,test_madrid_plant,2024-12-31,See table 2 and 2024 baseline,Text,semi-narrative\n",
    )

    rows = load_values_from_csv(csv_path)

    assert rows[0].value is True
    assert rows[0].value_type == "boolean"
    assert rows[1].value == "Plan approved by board"
    assert rows[1].value_type == "narrative"
    assert rows[2].value == "See table 2 and 2024 baseline"
    assert rows[2].value_type == "semi-narrative"


def test_load_values_from_csv_infers_text_and_boolean_values(tmp_path):
    csv_path = _write(
        tmp_path / "values.csv",
        "concept,entity,period,value,unit\n"
        "syg:Policy_Status,test_madrid_plant,2024-12-31,false,Boolean\n"
        "syg:Transition_Narrative,test_madrid_plant,2024-12-31,Awaiting assurance,Text\n",
    )

    rows = load_values_from_csv(csv_path)

    assert rows[0].value is False
    assert rows[0].value_type == "boolean"
    assert rows[1].value == "Awaiting assurance"
    assert rows[1].value_type == "narrative"


def test_load_values_from_csv_rejects_non_numeric_explicit_number(tmp_path):
    csv_path = _write(
        tmp_path / "values.csv",
        "concept,entity,period,value,unit,value_type\n"
        "syg:Water_Cooling,test_madrid_plant,2024-01-15,not-a-number,m³,number\n",
    )

    with pytest.raises(ValueCsvContractError, match="requires a numeric value"):
        load_values_from_csv(csv_path)


def test_values_csv_accepts_all_supported_sds_atomizer_value_types():
    supported_value_types = {"numeric", "boolean", "narrative", "semi-narrative"}
    sample_value = {
        "numeric": "1.5",
        "boolean": "true",
        "narrative": "Narrative disclosure",
        "semi-narrative": "See table 2 and 2024 baseline",
    }
    csv_text = "concept,entity,period,value,unit,value_type\n" + "".join(
        f"syg:Sample_{value_type},test_madrid_plant,2024-12-31,{sample_value[value_type]},Text,{value_type}\n"
        for value_type in sorted(supported_value_types)
    )

    rows = load_values_from_handle(StringIO(csv_text))

    assert {row.value_type for row in rows} == supported_value_types


@pytest.mark.parametrize("metadata_json", ['"{bad}"', '"[]"', '"\\"x\\""'])
def test_load_values_from_csv_rejects_invalid_metadata_json(tmp_path, metadata_json):
    csv_path = _write(
        tmp_path / "values.csv",
        "concept,entity,period,value,unit,metadata_json\n"
        f"syg:Water_Cooling,test_madrid_plant,2024-01-15,1.5,m³,{metadata_json}\n",
    )

    with pytest.raises(ValueCsvContractError):
        load_values_from_csv(csv_path)


def test_values_csv_file_header_and_empty_row_edges(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_values_from_csv(tmp_path / "missing.csv")

    with pytest.raises(ValueCsvContractError, match="no header"):
        load_values_from_handle(StringIO(""))

    with pytest.raises(ValueCsvContractError, match="duplicate columns"):
        load_values_from_handle(
            StringIO(
                "concept,entity,period,value,unit,unit\n"
                "syg:Water,e,2024-01-01,1,m3,m3\n"
            )
        )

    with pytest.raises(ValueCsvContractError, match="empty"):
        load_values_from_handle(StringIO("concept,entity,period,value,unit\n"))

    with pytest.raises(ValueCsvContractError, match="Invalid values CSV row"):
        load_values_from_handle(
            StringIO("concept,entity,period,value,unit\nsyg:Water,e,2024,1,m3,extra\n")
        )

    with pytest.raises(ValueCsvContractError, match="value must not be empty"):
        load_values_from_handle(
            StringIO("concept,entity,period,value,unit\nsyg:Water,e,2024,,m3\n")
        )


def test_values_csv_scalar_parser_and_alias_edges():
    assert _normalize_value_type("decimal") == "numeric"
    assert _normalize_value_type("bool") == "boolean"
    assert _normalize_value_type("string") == "narrative"
    assert _normalize_value_type("seminarrative") == "semi-narrative"
    assert _parse_value("1", "numeric") == (Decimal("1"), "numeric")
    assert _parse_value("yes", "auto") == (True, "boolean")
    assert _parse_value("no", "auto") == (False, "boolean")
    assert _parse_boolean("1") is True
    assert _parse_boolean("0") is False
    with pytest.raises(ValueCsvContractError, match="Boolean values"):
        _parse_boolean("maybe")
    with pytest.raises(ValueCsvContractError, match="value_type must be one of"):
        _parse_value("value", "unsupported")
