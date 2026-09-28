"""Signing helpers for dataset export manifests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

EXPORT_SIGNATURE_ALGORITHM = "HMAC-SHA256"


@dataclass(frozen=True)
class ExportSignature:
    algorithm: str
    key_id: str
    signature: str
    signed_at: datetime
    signed_payload_hash: str


def _signing_secret() -> str:
    from src.config.settings import settings

    return settings.export_signing_secret.get_secret_value()


def _canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def sign_export_payload(payload: dict[str, Any]) -> ExportSignature:
    """Sign a canonical export payload and return detached signature metadata."""
    from src.config.settings import settings

    canonical = _canonical_payload(payload).encode("utf-8")
    payload_hash = hashlib.sha256(canonical).hexdigest()
    # HTTP-date has one-second precision; sign exactly the timestamp exposed to
    # independent response verifiers.
    signed_at = datetime.now(timezone.utc).replace(microsecond=0)
    envelope = _canonical_payload(
        {
            "algorithm": EXPORT_SIGNATURE_ALGORITHM,
            "key_id": settings.export_signing_key_id,
            "payload_hash": payload_hash,
            "signed_at": signed_at.isoformat(),
        }
    ).encode("utf-8")
    secret = _signing_secret().encode("utf-8")
    signature_bytes = hmac.new(secret, envelope, hashlib.sha256).digest()
    return ExportSignature(
        algorithm=EXPORT_SIGNATURE_ALGORITHM,
        key_id=settings.export_signing_key_id,
        signature=base64.b64encode(signature_bytes).decode("ascii"),
        signed_at=signed_at,
        signed_payload_hash=payload_hash,
    )


def verify_export_signature(
    payload: dict[str, Any], signature: ExportSignature
) -> bool:
    """Verify a detached export signature against the canonical payload."""
    from src.config.settings import settings

    canonical = _canonical_payload(payload).encode("utf-8")
    expected_payload_hash = hashlib.sha256(canonical).hexdigest()
    if expected_payload_hash != signature.signed_payload_hash:
        return False
    if signature.algorithm != EXPORT_SIGNATURE_ALGORITHM:
        return False
    if signature.key_id != settings.export_signing_key_id:
        return False
    envelope = _canonical_payload(
        {
            "algorithm": signature.algorithm,
            "key_id": signature.key_id,
            "payload_hash": signature.signed_payload_hash,
            "signed_at": signature.signed_at.isoformat(),
        }
    ).encode("utf-8")
    expected = hmac.new(
        _signing_secret().encode("utf-8"), envelope, hashlib.sha256
    ).digest()
    try:
        supplied = base64.b64decode(signature.signature, validate=True)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(supplied, expected)
