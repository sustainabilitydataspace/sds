from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen

DEFAULT_DOCUMENTATION_URL = "https://sustainabilitydataspace.com/documentacion/"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_BYTES = 100 * 1024 * 1024
USER_AGENT = "SDS-E11-Evidence-Capture/1.0 (+https://sustainabilitydataspace.com/)"
DELIVERABLE_PDF_PATTERN = re.compile(r"/(?P<number>0[1-9]|1[0-3])\.pdf$")


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        for name, value in attrs:
            if name.casefold() == "href" and value:
                self.hrefs.append(value)


def extract_same_origin_pdf_urls(html: bytes, page_url: str) -> list[str]:
    parser = LinkParser()
    parser.feed(html.decode("utf-8", errors="replace"))
    page_origin = urlparse(page_url)
    pdf_urls: set[str] = set()
    for href in parser.hrefs:
        absolute_url = urldefrag(urljoin(page_url, href)).url
        parsed = urlparse(absolute_url)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc.casefold() != page_origin.netloc.casefold():
            continue
        if not parsed.path.casefold().endswith(".pdf"):
            continue
        pdf_urls.add(absolute_url)
    return sorted(pdf_urls)


def _request(url: str) -> Request:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"public evidence URL must use HTTP(S): {url}")
    return Request(
        url,
        headers={
            "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.8",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )


def _response_metadata(response: Any, url: str) -> dict[str, Any]:
    return {
        "url": url,
        "final_url": response.geturl(),
        "status": getattr(response, "status", 200),
        "content_type": response.headers.get_content_type(),
        "etag": response.headers.get("ETag"),
        "last_modified": response.headers.get("Last-Modified"),
    }


def fetch_bytes(
    url: str, timeout: float, max_bytes: int
) -> tuple[bytes, dict[str, Any]]:
    # _request rejects non-HTTP(S) schemes before constructing the request.
    with urlopen(_request(url), timeout=timeout) as response:  # nosec B310
        metadata = _response_metadata(response, url)
        body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"response exceeds {max_bytes} bytes: {url}")
    metadata.update(
        {
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
    )
    return body, metadata


def fetch_digest(url: str, timeout: float, max_bytes: int) -> dict[str, Any]:
    digest = hashlib.sha256()
    total_bytes = 0
    first_bytes = b""
    # _request rejects non-HTTP(S) schemes before constructing the request.
    with urlopen(_request(url), timeout=timeout) as response:  # nosec B310
        metadata = _response_metadata(response, url)
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            if len(first_bytes) < 8:
                first_bytes += chunk[: 8 - len(first_bytes)]
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                raise ValueError(f"response exceeds {max_bytes} bytes: {url}")
            digest.update(chunk)
    if not first_bytes.startswith(b"%PDF-"):
        raise ValueError(f"response does not have a PDF signature: {url}")
    metadata.update({"bytes": total_bytes, "sha256": digest.hexdigest()})
    return metadata


def capture(
    documentation_url: str,
    timeout: float,
    max_bytes: int,
) -> dict[str, Any]:
    page_body, page_record = fetch_bytes(documentation_url, timeout, max_bytes)
    pdf_urls = extract_same_origin_pdf_urls(page_body, page_record["final_url"])
    records = [fetch_digest(url, timeout, max_bytes) for url in pdf_urls]

    deliverables: dict[str, dict[str, Any]] = {}
    additional_resources: list[dict[str, Any]] = []
    for record in records:
        match = DELIVERABLE_PDF_PATTERN.search(urlparse(record["url"]).path)
        if match:
            deliverable_id = f"E{match.group('number')}"
            if deliverable_id in deliverables:
                raise ValueError(f"duplicate public PDF for {deliverable_id}")
            deliverables[deliverable_id] = record
        else:
            additional_resources.append(record)

    expected_deliverables = {f"E{index:02d}" for index in range(1, 14)}
    if set(deliverables) != expected_deliverables:
        missing = sorted(expected_deliverables - set(deliverables))
        extra = sorted(set(deliverables) - expected_deliverables)
        raise ValueError(
            f"public deliverable inventory mismatch; missing={missing}, extra={extra}"
        )

    return {
        "schema_version": 1,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "Read-only public GET capture of the E11 documentation page and every "
            "same-origin PDF linked from that page. Digests bind the observed bytes; "
            "they do not establish stakeholder acceptance or analytics impact."
        ),
        "capture_method": {
            "script": "scripts/capture_e11_public_evidence.py",
            "http_method": "GET",
            "user_agent": USER_AGENT,
            "timeout_seconds": timeout,
            "max_response_bytes": max_bytes,
            "replay_command": (
                "python scripts/capture_e11_public_evidence.py --output "
                "<new-evidence-path.json>"
            ),
        },
        "documentation_page": page_record,
        "discovered_same_origin_pdf_count": len(records),
        "deliverables": dict(sorted(deliverables.items())),
        "additional_pdf_resources": sorted(
            additional_resources, key=lambda item: item["url"]
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture replayable, digest-bound public E11 website evidence."
    )
    parser.add_argument("--url", default=DEFAULT_DOCUMENTATION_URL)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = capture(args.url, args.timeout, args.max_bytes)
    except Exception as exc:  # noqa: BLE001
        print(f"E11 evidence capture failed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "E11 evidence captured: "
        f"deliverables={len(payload['deliverables'])} "
        f"all_pdfs={payload['discovered_same_origin_pdf_count']} "
        f"output={args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
