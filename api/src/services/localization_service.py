"""DB layer for SDS localization (LOC-1): read resolver + package importer/validator.

Composes the pure resolver in ``localization.py`` over ``localized_text`` rows, and validates +
imports translation packages idempotently per the converged design's package contract
(reject unknown subjects in strict mode, reject approved rows without evidence, reject duplicate
active approved rows, never mutate silently, supersede by closing the prior effective range).
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from sqlalchemy.orm import Session

from src.database.models import LocalizedText
from src.services.localization import (
    DEFAULT_LANGUAGE,
    LocalizationResult,
    TranslationRow,
    language_fallback_chain,
    normalize_language,
    resolve_display,
    text_hash,
)
from src.services.localization_worklist import WORKLIST_FIELDS, public_subject_uris

_STATUSES = {
    "draft",
    "machine_draft",
    "reviewed",
    "approved",
    "stale",
    "deprecated",
    "rejected",
}
_HEX64 = set("0123456789abcdef")


def _is_hex64(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in _HEX64 for ch in value.lower())
    )


class LocalizationError(ValueError):
    """Raised on a package-validation failure (strict mode)."""


class LocalizationRepository:
    """Read path: active approved localized_text rows -> resolved display fields."""

    def __init__(self, db: Session):
        self.db = db

    def localize(
        self,
        *,
        subject_kind: str,
        subject_uri: str,
        source_fields: Mapping[str, str],
        requested_language: Optional[str],
        source_language: str = DEFAULT_LANGUAGE,
        default_language: str = DEFAULT_LANGUAGE,
    ) -> LocalizationResult:
        """Resolve display text for one subject. Global (non-tenant) rows only in V1."""
        if not source_fields:
            return resolve_display(
                source_fields={},
                rows=[],
                requested_language=requested_language,
                source_language=source_language,
                default_language=default_language,
            )
        chain = language_fallback_chain(requested_language, default_language)
        rows = (
            self.db.query(LocalizedText)
            .filter(
                LocalizedText.subject_kind == subject_kind,
                LocalizedText.subject_uri == subject_uri,
                LocalizedText.field.in_(list(source_fields)),
                LocalizedText.language.in_(chain),
                LocalizedText.status == "approved",
                LocalizedText.effective_to.is_(None),
                LocalizedText.tenant_id.is_(None),
            )
            .all()
        )
        candidate = [
            TranslationRow(
                field=row.field,
                language=normalize_language(row.language),
                text=row.text,
                source_hash=(row.source_hash or "").lower(),
            )
            for row in rows
        ]
        return resolve_display(
            source_fields=source_fields,
            rows=candidate,
            requested_language=requested_language,
            source_language=source_language,
            default_language=default_language,
        )

    def coverage(
        self,
        *,
        subject_kind: str,
        language: str,
        subjects: Mapping[str, Mapping[str, str]],
        field: str = "label",
    ) -> Dict[str, Any]:
        """Readiness coverage for one (subject_kind, field, language).

        ``subjects`` maps subject_uri -> {field: current_source_text}. Classifies each subject's
        target-language ``field`` as approved_current / stale / draft_only / missing and reports a
        coverage percentage (approved_current / total).
        """
        lang = normalize_language(language)
        total = len(subjects)
        counts = {
            "approved_current": 0,
            "stale": 0,
            "draft_only": 0,
            "missing": 0,
        }
        if total:
            rows = (
                self.db.query(LocalizedText)
                .filter(
                    LocalizedText.subject_kind == subject_kind,
                    LocalizedText.subject_uri.in_(list(subjects)),
                    LocalizedText.field == field,
                    LocalizedText.language == lang,
                    LocalizedText.tenant_id.is_(None),
                )
                .all()
            )
            by_subject: Dict[str, List[LocalizedText]] = {}
            for row in rows:
                by_subject.setdefault(row.subject_uri, []).append(row)
            for subject_uri, source in subjects.items():
                src = source.get(field, "")
                subject_rows = by_subject.get(subject_uri, [])
                approved = [
                    r
                    for r in subject_rows
                    if r.status == "approved" and r.effective_to is None
                ]
                if any(
                    (r.source_hash or "").lower() == text_hash(src) for r in approved
                ):
                    counts["approved_current"] += 1
                elif approved:
                    counts["stale"] += 1
                elif any(
                    r.status in ("draft", "machine_draft", "reviewed")
                    for r in subject_rows
                ):
                    counts["draft_only"] += 1
                else:
                    counts["missing"] += 1
        coverage_pct = (
            round(100.0 * counts["approved_current"] / total, 2) if total else 0.0
        )
        return {
            "subject_kind": subject_kind,
            "field": field,
            "language": lang,
            "total_subjects": total,
            **counts,
            "coverage_percent": coverage_pct,
        }


@dataclass
class LocalizationImportReport:
    created: int = 0
    superseded: int = 0
    unchanged: int = 0
    skipped_unknown: List[str] = dc_field(default_factory=list)
    rejected: List[str] = dc_field(default_factory=list)
    committed: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "created": self.created,
            "superseded": self.superseded,
            "unchanged": self.unchanged,
            "skipped_unknown": list(self.skipped_unknown),
            "rejected": list(self.rejected),
            "committed": self.committed,
        }


def import_translations(
    db: Session,
    rows: Iterable[Mapping[str, Any]],
    *,
    subject_kind: str,
    strict: bool = True,
    created_by: str = "translation_import",
    known_subject_uris: Optional[Iterable[str]] = None,
    now: Optional[datetime] = None,
) -> LocalizationImportReport:
    """Validate + import translation rows for one ``subject_kind`` idempotently.

    strict: unknown subjects/kinds raise (block); partial mode reports them in skipped_unknown.
    Approved rows require reviewer_ref + source_ref, must have a verified translation_hash, and
    must be unique per (subject_uri, field, language) within the package AND vs the active DB row;
    an identical re-import is a no-op (idempotent), and a changed approved row supersedes the prior
    one by closing its effective range. Never auto-installs subjects or fabricates translations.
    """
    now = now or datetime.now(timezone.utc)
    if subject_kind not in WORKLIST_FIELDS:
        raise LocalizationError(f"unsupported subject_kind {subject_kind!r}")
    allowed_fields = set(WORKLIST_FIELDS[subject_kind])
    # Subject existence is mandatory (fail-closed): an explicit allowlist wins, otherwise the
    # public catalog served for this kind IS the allowlist. Translations never auto-install a
    # subject the API would not serve.
    if known_subject_uris is not None:
        known = set(known_subject_uris)
    else:
        known = public_subject_uris(db, subject_kind)
    report = LocalizationImportReport()
    seen_active_keys: set[tuple] = set()

    def _fail(msg: str) -> None:
        if strict:
            raise LocalizationError(msg)
        report.rejected.append(msg)

    for index, raw in enumerate(rows):
        ctx = f"row[{index}]"
        kind = str(raw.get("subject_kind") or "")
        if kind != subject_kind:
            if strict:
                raise LocalizationError(f"{ctx}: unsupported subject_kind {kind!r}")
            report.skipped_unknown.append(f"{ctx}: subject_kind={kind}")
            continue
        subject_uri = str(raw.get("subject_uri") or "")
        if not subject_uri:
            _fail(f"{ctx}: missing subject_uri")
            continue
        if subject_uri not in known:
            if strict:
                raise LocalizationError(f"{ctx}: unknown {subject_kind} {subject_uri}")
            report.skipped_unknown.append(f"{ctx}: subject_uri={subject_uri}")
            continue

        field = str(raw.get("field") or "")
        if field not in allowed_fields:
            _fail(f"{ctx}: unsupported field {field!r} for {subject_kind}")
            continue
        status = str(raw.get("status") or "")
        if status not in _STATUSES:
            _fail(f"{ctx}: unsupported status {status!r}")
            continue
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            _fail(f"{ctx}: empty text")
            continue
        language = normalize_language(raw.get("language"))
        source_language = normalize_language(
            raw.get("source_language") or DEFAULT_LANGUAGE
        )
        source_hash = str(raw.get("source_hash") or "").lower()
        if not _is_hex64(source_hash):
            _fail(f"{ctx}: source_hash must be 64-hex")
            continue
        translation_hash = str(raw.get("translation_hash") or text_hash(text)).lower()
        if translation_hash != text_hash(text):
            _fail(f"{ctx}: translation_hash does not match text")
            continue
        # Normalize optional evidence fields: an empty string (e.g. an unused CSV column on a
        # machine_draft row) becomes NULL so the DB CHECK constraints — which allow the enum
        # value or NULL, never '' — are satisfied.
        source_type = str(raw.get("source_type") or "").strip() or None
        source_ref = str(raw.get("source_ref") or "").strip() or None
        reviewer_ref = str(raw.get("reviewer_ref") or "").strip() or None
        if status == "approved" and not (source_ref and reviewer_ref):
            _fail(f"{ctx}: approved row requires source_ref + reviewer_ref")
            continue

        if status == "approved":
            key = (subject_uri, field, language)
            if key in seen_active_keys:
                _fail(f"{ctx}: duplicate active approved row for {key}")
                continue
            seen_active_keys.add(key)
            existing = (
                db.query(LocalizedText)
                .filter(
                    LocalizedText.subject_kind == subject_kind,
                    LocalizedText.subject_uri == subject_uri,
                    LocalizedText.field == field,
                    LocalizedText.language == language,
                    LocalizedText.status == "approved",
                    LocalizedText.effective_to.is_(None),
                    LocalizedText.tenant_id.is_(None),
                )
                .one_or_none()
            )
            if existing is not None:
                if (
                    (existing.source_hash or "").lower() == source_hash
                    and (existing.translation_hash or "").lower() == translation_hash
                    and existing.text == text
                ):
                    report.unchanged += 1
                    continue
                existing.effective_to = now
                existing.status = "deprecated"
                revision = (existing.revision or 1) + 1
                report.superseded += 1
            else:
                revision = 1
            db.add(
                LocalizedText(
                    subject_kind=subject_kind,
                    subject_uri=subject_uri,
                    field=field,
                    language=language,
                    display_language=str(raw.get("language") or language),
                    text=text,
                    status="approved",
                    source_language=source_language,
                    source_hash=source_hash,
                    translation_hash=translation_hash,
                    source_type=source_type,
                    source_ref=source_ref,
                    reviewer_ref=reviewer_ref,
                    revision=revision,
                    effective_from=now,
                    created_at=now,
                    created_by=created_by,
                )
            )
            report.created += 1
        else:
            # Preview-only rows (draft/machine_draft/reviewed): idempotent on identical content.
            duplicate = (
                db.query(LocalizedText)
                .filter(
                    LocalizedText.subject_kind == subject_kind,
                    LocalizedText.subject_uri == subject_uri,
                    LocalizedText.field == field,
                    LocalizedText.language == language,
                    LocalizedText.status == status,
                    LocalizedText.translation_hash == translation_hash,
                    LocalizedText.effective_to.is_(None),
                )
                .first()
            )
            if duplicate is not None:
                report.unchanged += 1
                continue
            db.add(
                LocalizedText(
                    subject_kind=subject_kind,
                    subject_uri=subject_uri,
                    field=field,
                    language=language,
                    display_language=str(raw.get("language") or language),
                    text=text,
                    status=status,
                    source_language=source_language,
                    source_hash=source_hash,
                    translation_hash=translation_hash,
                    source_type=source_type,
                    source_ref=source_ref,
                    reviewer_ref=reviewer_ref,
                    revision=1,
                    effective_from=now,
                    created_at=now,
                    created_by=created_by,
                )
            )
            report.created += 1

    db.flush()
    return report


def import_concept_translations(
    db: Session, rows: Iterable[Mapping[str, Any]], **kwargs: Any
) -> LocalizationImportReport:
    """Import ``concept`` translation rows (thin wrapper over import_translations)."""
    return import_translations(db, rows, subject_kind="concept", **kwargs)


def import_indicator_translations(
    db: Session, rows: Iterable[Mapping[str, Any]], **kwargs: Any
) -> LocalizationImportReport:
    """Import ``indicator`` translation rows (subject_uri = public indicator identifier)."""
    return import_translations(db, rows, subject_kind="indicator", **kwargs)
