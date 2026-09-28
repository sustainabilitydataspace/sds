from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "api" / "scripts" / "backfill_concept_indicator_links.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "backfill_concept_indicator_links_script", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


linkage = _load_module()


def _concept(uri: str, taxonomy: str, label: str):
    return SimpleNamespace(uri=uri, taxonomy=taxonomy, label=label)


def _indicator(
    identifier: str,
    *,
    code_esrs: str | None = None,
    code_gri: str | None = None,
    title: str = "",
):
    return SimpleNamespace(
        id=identifier, code_esrs=code_esrs, code_gri=code_gri, title=title
    )


def test_build_candidates_links_csrd_family_prefix_and_exact_title():
    concept = _concept(
        "https://data.efrag.org/esrs#E3_5", "CSRD", "Total water consumption"
    )
    indicators = [
        _indicator(
            "urn:sds:reg:esrs:e3_4_01",
            code_esrs="E3_4_01",
            title="Total water consumption",
        ),
        _indicator(
            "urn:sds:reg:esrs:e3_4_02",
            code_esrs="E3_4_02",
            title="Total water consumption in areas at water risk",
        ),
    ]

    candidates = linkage.build_candidates(concept, indicators)

    assert [candidate.indicator_id for candidate in candidates] == [
        "urn:sds:reg:esrs:e3_4_01"
    ]
    assert candidates[0].link_type == "exact_unique_title_same_taxonomy"


def test_build_candidates_links_gri_family_prefix_to_multiple_children():
    concept = _concept(
        "https://data.globalreporting.org/gri#303_3", "GRI", "Water withdrawal"
    )
    indicators = [
        _indicator(
            "urn:sds:reg:gri:gri_303_3_a",
            code_gri="GRI 303-3.a",
            title="Total water withdrawal from all areas",
        ),
        _indicator(
            "urn:sds:reg:gri:gri_303_3_b",
            code_gri="GRI 303-3.b",
            title="Total water withdrawal from all areas with water stress",
        ),
        _indicator(
            "urn:sds:reg:gri:gri_305_1_a",
            code_gri="GRI 305-1.a",
            title="Gross direct scope 1 GHG emissions",
        ),
    ]

    candidates = linkage.build_candidates(concept, indicators)

    assert {candidate.indicator_id for candidate in candidates} == {
        "urn:sds:reg:gri:gri_303_3_a",
        "urn:sds:reg:gri:gri_303_3_b",
    }
    assert {candidate.link_type for candidate in candidates} == {
        "family_prefix_same_taxonomy"
    }
