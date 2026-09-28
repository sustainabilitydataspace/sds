from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
E11_DOC_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E11-website"
    / "final"
    / "e11-website-publication-report.md"
)
E11_URL_PATH = (
    REPO_ROOT / "deliverables" / "E11-website" / "evidence" / "official-url.md"
)
E11_LIVE_INVENTORY_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E11-website"
    / "evidence"
    / "e11-live-publication-inventory-2026-09-24.json"
)
CAPTURE_SCRIPT_PATH = REPO_ROOT / "scripts" / "capture_e11_public_evidence.py"


def _load_script(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_e11_report_exists_and_bounds_publication_claims():
    assert E11_DOC_PATH.exists(), f"Missing E11 publication report: {E11_DOC_PATH}"

    text = E11_DOC_PATH.read_text(encoding="utf-8")
    normalized = " ".join(text.split())
    assert text.startswith("# E11 - Website Publication Report")
    assert "**Date:** 2026-09-18" in text
    assert "**Version:** V2026-09-18" in text
    assert "**Status:** `published-local`" in text
    assert "https://sustainabilitydataspace.com/" in text
    for section in (
        "Official URL",
        "Current closure",
        "Current public-surface review",
        "Evidence boundary",
    ):
        assert f"## {section}" in text
    assert (
        "E11/R21 is closed by the current project closure decision of 2026-09-18"
        in normalized
    )
    assert "visible 404 resource-menu and demo links" in normalized
    assert "Those observations are not presented as corrected" in normalized
    assert "does not claim analytics, future website traffic, or new" in normalized


def test_e11_official_url_evidence_is_recorded():
    assert E11_URL_PATH.exists(), f"Missing E11 official URL evidence: {E11_URL_PATH}"
    text = E11_URL_PATH.read_text(encoding="utf-8")
    normalized_text = " ".join(text.split())
    assert "https://sustainabilitydataspace.com/" in text
    assert "Register status: `official-url-recorded`." in text
    for date in (
        "2026-05-30",
        "2026-05-31",
        "2026-06-01",
        "2026-06-08",
        "2026-06-23",
        "2026-07-22",
    ):
        assert date in text
    assert "HTTP 404" in text
    assert "HTTP 200" in text
    assert "does not by itself close content acceptance" in normalized_text.casefold()
    assert (
        "does not claim that the local `web/sds_website/` test artifact is official evidence"
        in normalized_text
    )


def test_e11_live_inventory_is_replayable_and_digest_bound():
    payload = json.loads(E11_LIVE_INVENTORY_PATH.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["captured_at_utc"].startswith("2026-09-24T")
    assert "do not establish stakeholder acceptance" in payload["scope"]
    assert (
        payload["capture_method"]["script"] == "scripts/capture_e11_public_evidence.py"
    )
    assert payload["capture_method"]["http_method"] == "GET"
    assert payload["documentation_page"]["url"] == (
        "https://sustainabilitydataspace.com/documentacion/"
    )
    assert payload["documentation_page"]["status"] == 200
    assert re.fullmatch(r"[0-9a-f]{64}", payload["documentation_page"]["sha256"])

    deliverables = payload["deliverables"]
    assert set(deliverables) == {f"E{index:02d}" for index in range(1, 14)}
    for index in range(1, 14):
        record = deliverables[f"E{index:02d}"]
        assert record["url"].endswith(f"/{index:02d}.pdf")
        assert record["status"] == 200
        assert record["bytes"] > 0
        assert "pdf" in record["content_type"].casefold()
        assert re.fullmatch(r"[0-9a-f]{64}", record["sha256"])


def test_e11_capture_script_discovers_only_same_origin_pdf_links():
    capture = _load_script("capture_e11_public_evidence", CAPTURE_SCRIPT_PATH)
    html = b"""
    <a href="/wp-content/uploads/2026/07/01.pdf">one</a>
    <a href="https://sustainabilitydataspace.com/wp-content/uploads/2026/07/01.pdf#page=2">duplicate</a>
    <a href="https://example.com/external.pdf">external</a>
    <a href="/not-a-pdf">html</a>
    """

    assert capture.extract_same_origin_pdf_urls(
        html, "https://sustainabilitydataspace.com/documentacion/"
    ) == ["https://sustainabilitydataspace.com/wp-content/uploads/2026/07/01.pdf"]
