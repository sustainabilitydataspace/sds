"""Deterministic dataset manifest helpers for export surfaces."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from typing import Any, Iterable, Optional

from src.services.export_signing import ExportSignature, sign_export_payload

DATASET_EXPORT_CONTRACT_VERSION = "1.0"


@dataclass(frozen=True)
class DatasetManifest:
    dataset: str
    record_count: int
    manifest_hash: str
    etag: str
    contract_version: str
    generated_at: datetime
    last_modified: Optional[datetime]
    signature_algorithm: str
    signature_key_id: str
    signature: str
    signed_at: datetime
    signed_payload_hash: str

    def last_modified_http(self) -> Optional[str]:
        if self.last_modified is None:
            return None
        value = self.last_modified
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return format_datetime(value, usegmt=True)

    def is_not_modified(
        self, *, if_none_match: Optional[str], if_modified_since: Optional[str]
    ) -> bool:
        if if_none_match:
            candidates = [
                candidate.strip()
                for candidate in if_none_match.split(",")
                if candidate.strip()
            ]
            if "*" in candidates:
                return True
            normalized_etag = self.etag
            weak_etag = f"W/{self.etag}"
            if any(
                candidate in {normalized_etag, weak_etag} for candidate in candidates
            ):
                return True
        if if_modified_since and self.last_modified is not None:
            try:
                candidate = parsedate_to_datetime(if_modified_since)
            except (TypeError, ValueError, IndexError):
                return False
            last_modified = self.last_modified
            if last_modified.tzinfo is None:
                last_modified = last_modified.replace(tzinfo=timezone.utc)
            if candidate.tzinfo is None:
                candidate = candidate.replace(tzinfo=timezone.utc)
            last_modified = last_modified.replace(microsecond=0)
            candidate = candidate.replace(microsecond=0)
            if last_modified <= candidate:
                return True
        return False

    def as_signed_payload(self) -> dict[str, Any]:
        last_modified = self.last_modified
        if last_modified is not None and last_modified.tzinfo is None:
            last_modified = last_modified.replace(tzinfo=timezone.utc)
        return {
            "dataset": self.dataset,
            "record_count": self.record_count,
            "manifest_hash": self.manifest_hash,
            "etag": self.etag,
            "contract_version": self.contract_version,
            "last_modified": (
                last_modified.isoformat() if last_modified is not None else None
            ),
        }

    def response_headers(
        self, *, filename: Optional[str] = None, include_filename: bool = True
    ) -> dict[str, str]:
        headers = {
            "ETag": self.etag,
            "X-SDS-Manifest-Hash": self.manifest_hash,
            "X-SDS-Contract-Version": self.contract_version,
            "X-SDS-Signature-Alg": self.signature_algorithm,
            "X-SDS-Signature-Key-Id": self.signature_key_id,
            "X-SDS-Signature": self.signature,
            "X-SDS-Signed-At": format_datetime(self.signed_at, usegmt=True),
            "X-SDS-Signed-Payload-Hash": self.signed_payload_hash,
        }
        last_modified_header = self.last_modified_http()
        if last_modified_header is not None:
            headers["Last-Modified"] = last_modified_header
        if include_filename and filename:
            headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return headers


def build_dataset_manifest(
    *, dataset: str, items: Iterable[dict[str, Any]], last_modified: Optional[datetime]
) -> DatasetManifest:
    rows = list(items)
    normalized = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    manifest_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    provisional = {
        "dataset": dataset,
        "record_count": len(rows),
        "manifest_hash": manifest_hash,
        "etag": f'"{manifest_hash}"',
        "contract_version": DATASET_EXPORT_CONTRACT_VERSION,
        "last_modified": (
            last_modified.replace(tzinfo=timezone.utc).isoformat()
            if last_modified is not None and last_modified.tzinfo is None
            else last_modified.isoformat() if last_modified is not None else None
        ),
    }
    signature: ExportSignature = sign_export_payload(provisional)
    return DatasetManifest(
        dataset=dataset,
        record_count=len(rows),
        manifest_hash=manifest_hash,
        etag=f'"{manifest_hash}"',
        contract_version=DATASET_EXPORT_CONTRACT_VERSION,
        generated_at=datetime.now(timezone.utc),
        last_modified=last_modified,
        signature_algorithm=signature.algorithm,
        signature_key_id=signature.key_id,
        signature=signature.signature,
        signed_at=signature.signed_at,
        signed_payload_hash=signature.signed_payload_hash,
    )
