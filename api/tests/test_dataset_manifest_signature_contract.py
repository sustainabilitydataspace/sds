"""Tests for signed dataset manifests."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from src.services.dataset_manifest import build_dataset_manifest
from src.services.export_signing import ExportSignature, verify_export_signature


def test_export_manifest_import_does_not_initialize_application_settings():
    api_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import src.services.export_signing; "
            "assert 'src.config.settings' not in sys.modules; "
            "assert 'src.database.session' not in sys.modules",
        ],
        cwd=api_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_services_package_preserves_lazy_public_service_export():
    import src.services as services
    from src.services.unit_service import UnitService

    assert services.UnitService is UnitService


def test_build_dataset_manifest_includes_detached_signature_fields():
    manifest = build_dataset_manifest(
        dataset="values",
        items=[{"id": "1", "concept": "csrd:E3_5"}],
        last_modified=datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc),
    )

    assert manifest.signature_algorithm == "HMAC-SHA256"
    assert manifest.signature_key_id
    assert manifest.signature
    assert manifest.signed_payload_hash
    assert verify_export_signature(
        manifest.as_signed_payload(),
        ExportSignature(
            algorithm=manifest.signature_algorithm,
            key_id=manifest.signature_key_id,
            signature=manifest.signature,
            signed_at=manifest.signed_at,
            signed_payload_hash=manifest.signed_payload_hash,
        ),
    )


def test_verify_export_signature_rejects_tampered_payload():
    manifest = build_dataset_manifest(
        dataset="indicators",
        items=[{"id": "urn:sds:test:1", "identifier": "urn:sds:test:1"}],
        last_modified=None,
    )
    tampered = manifest.as_signed_payload()
    tampered["record_count"] = 999

    assert not verify_export_signature(
        tampered,
        ExportSignature(
            algorithm=manifest.signature_algorithm,
            key_id=manifest.signature_key_id,
            signature=manifest.signature,
            signed_at=manifest.signed_at,
            signed_payload_hash=manifest.signed_payload_hash,
        ),
    )


def test_verify_export_signature_rejects_tampered_signed_at():
    manifest = build_dataset_manifest(
        dataset="indicators",
        items=[{"id": "urn:sds:test:1", "identifier": "urn:sds:test:1"}],
        last_modified=None,
    )

    assert not verify_export_signature(
        manifest.as_signed_payload(),
        ExportSignature(
            algorithm=manifest.signature_algorithm,
            key_id=manifest.signature_key_id,
            signature=manifest.signature,
            signed_at=manifest.signed_at + timedelta(days=1),
            signed_payload_hash=manifest.signed_payload_hash,
        ),
    )


def test_signature_can_be_verified_from_public_response_headers() -> None:
    manifest = build_dataset_manifest(
        dataset="values",
        items=[{"id": "1"}],
        last_modified=None,
    )
    headers = manifest.response_headers()

    assert manifest.signed_at.microsecond == 0
    reconstructed = ExportSignature(
        algorithm=headers["X-SDS-Signature-Alg"],
        key_id=headers["X-SDS-Signature-Key-Id"],
        signature=headers["X-SDS-Signature"],
        signed_at=parsedate_to_datetime(headers["X-SDS-Signed-At"]),
        signed_payload_hash=headers["X-SDS-Signed-Payload-Hash"],
    )
    assert verify_export_signature(manifest.as_signed_payload(), reconstructed)


def test_dataset_manifest_cache_headers_and_naive_dates():
    last_modified = datetime(2026, 4, 20, 10, 0)
    manifest = build_dataset_manifest(
        dataset="values",
        items=[{"id": "1"}],
        last_modified=last_modified,
    )

    assert manifest.last_modified_http() == "Mon, 20 Apr 2026 10:00:00 GMT"
    assert manifest.is_not_modified(if_none_match="*", if_modified_since=None)
    assert manifest.is_not_modified(
        if_none_match=f"W/{manifest.etag}", if_modified_since=None
    )
    future_date = (last_modified + timedelta(minutes=1)).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )
    assert not manifest.is_not_modified(
        if_none_match='"different-export"', if_modified_since=future_date
    )
    subsecond_manifest = build_dataset_manifest(
        dataset="values",
        items=[{"id": "changed"}],
        last_modified=last_modified + timedelta(microseconds=1),
    )
    assert not subsecond_manifest.is_not_modified(
        if_none_match=None, if_modified_since=manifest.last_modified_http()
    )
    assert not manifest.is_not_modified(
        if_none_match=None,
        if_modified_since=(last_modified + timedelta(minutes=1)).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        ),
    )
    assert not manifest.is_not_modified(
        if_none_match=None,
        if_modified_since=(last_modified + timedelta(minutes=1)).strftime(
            "%a, %d %b %Y %H:%M:%S"
        ),
    )
    assert not manifest.is_not_modified(
        if_none_match=None,
        if_modified_since="not a valid http date",
    )
    assert manifest.as_signed_payload()["last_modified"] == (
        "2026-04-20T10:00:00+00:00"
    )

    headers = manifest.response_headers(filename="values.json")
    assert headers["Last-Modified"] == "Mon, 20 Apr 2026 10:00:00 GMT"
    assert headers["Content-Disposition"] == 'attachment; filename="values.json"'
    assert "Content-Disposition" not in manifest.response_headers(
        filename="values.json", include_filename=False
    )


def test_removed_latest_row_never_validates_an_older_export_http_date():
    previous = build_dataset_manifest(
        dataset="values",
        items=[{"id": "old"}, {"id": "removed"}],
        last_modified=datetime(2026, 4, 20, tzinfo=timezone.utc),
    )
    current = build_dataset_manifest(
        dataset="values",
        items=[{"id": "old"}],
        last_modified=datetime(2026, 4, 19, tzinfo=timezone.utc),
    )
    assert current.etag != previous.etag
    assert not current.is_not_modified(
        if_none_match=None, if_modified_since=previous.last_modified_http()
    )
