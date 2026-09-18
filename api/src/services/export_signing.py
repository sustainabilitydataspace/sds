"""Signing helpers for dataset export manifests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.config.settings import settings

EXPORT_SIGNATURE_ALGORITHM = "HMAC-SHA256"


@dataclass(frozen=True)
class ExportSignature:
    algorithm: str
    key_id: str
    signature: str
    signed_at: datetime
    signed_payload_hash: str


def _signing_secret() -> str:
    configured = settings.export_signing_secret
    if configured is not None and configured.get_secret_value():
        return configured.get_secret_value()
    return settings.jwt_secret_key.get_secret_value()


def _canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def sign_export_payload(payload: dict[str, Any]) -> ExportSignature:
    """Sign a canonical export payload and return detached signature metadata."""
    canonical = _canonical_payload(payload).encode("utf-8")
    secret = _signing_secret().encode("utf-8")
    signature_bytes = hmac.new(secret, canonical, hashlib.sha256).digest()
    signed_at = datetime.now(timezone.utc)
    return ExportSignature(
        algorithm=EXPORT_SIGNATURE_ALGORITHM,
        key_id=settings.export_signing_key_id,
        signature=base64.b64encode(signature_bytes).decode("ascii"),
        signed_at=signed_at,
        signed_payload_hash=hashlib.sha256(canonical).hexdigest(),
    )


def verify_export_signature(
    payload: dict[str, Any], signature: ExportSignature
) -> bool:
    """Verify a detached export signature against the canonical payload."""
    canonical = _canonical_payload(payload).encode("utf-8")
    expected_payload_hash = hashlib.sha256(canonical).hexdigest()
    if expected_payload_hash != signature.signed_payload_hash:
        return False
    expected = sign_export_payload(payload)
    return (
        signature.algorithm == expected.algorithm
        and signature.key_id == expected.key_id
        and hmac.compare_digest(signature.signature, expected.signature)
    )
