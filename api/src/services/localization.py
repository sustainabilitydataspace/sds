"""SDS localization resolver (LOC-1 foundation).

Implements the SDS API localization V1 resolution and staleness rules.

Canonical source fields stay authoritative in V1; this layer resolves APPROVED target-language
DISPLAY text from ``localized_text`` and reports localization metadata. Public runtime serves only
``approved`` rows whose ``source_hash`` still matches the current source text (SHA-256 over
NFC-normalized UTF-8) and whose effective interval is active; otherwise the row is stale and the
field falls back. Identifiers/codes/symbols/formulas are never translated — callers only pass
human-facing source fields here.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Dict, Iterable, List, Mapping, Optional

DEFAULT_LANGUAGE = "en"

#: Per-field resolution status (mirrors candidate-v2 readiness vocabulary).
STATUS_APPROVED_CURRENT = "approved_current"
STATUS_LANGUAGE_FALLBACK = "language_fallback"
STATUS_SOURCE_FALLBACK = "source_fallback"


def text_hash(value: str) -> str:
    """SHA-256 over NFC-normalized UTF-8 — the locked hash for source/translation text."""
    normalized = unicodedata.normalize("NFC", value or "")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_language(tag: Optional[str]) -> str:
    """Normalize a BCP-47 tag to a lowercase lookup key (e.g. ``es-ES`` -> ``es-es``)."""
    if not tag:
        return DEFAULT_LANGUAGE
    return tag.strip().lower().replace("_", "-") or DEFAULT_LANGUAGE


def _primary_subtag(tag: str) -> str:
    return normalize_language(tag).split("-", 1)[0]


def language_fallback_chain(
    requested: Optional[str], default_language: str = DEFAULT_LANGUAGE
) -> List[str]:
    """Ordered, de-duplicated lookup languages: requested, broader, then default.

    e.g. ``es-ES`` + default ``en`` -> ``["es-es", "es", "en"]``. The canonical
    source-language fallback is handled by the resolver, not this chain.
    """
    chain: List[str] = []
    for candidate in (
        normalize_language(requested),
        _primary_subtag(requested or default_language),
        normalize_language(default_language),
        _primary_subtag(default_language),
    ):
        if candidate and candidate not in chain:
            chain.append(candidate)
    return chain


@dataclass(frozen=True)
class TranslationRow:
    """A minimal, resolver-facing view of an active approved localized_text row."""

    field: str
    language: str  # normalized lookup language
    text: str
    source_hash: str


@dataclass
class LocalizationResult:
    display: Dict[str, str] = dc_field(default_factory=dict)
    requested_language: str = DEFAULT_LANGUAGE
    resolved_language: str = DEFAULT_LANGUAGE
    source_language: str = DEFAULT_LANGUAGE
    status: str = STATUS_SOURCE_FALLBACK
    fallback_chain: List[str] = dc_field(default_factory=list)
    missing_fields: List[str] = dc_field(default_factory=list)
    stale_fields: List[str] = dc_field(default_factory=list)

    def as_metadata(self) -> Dict[str, object]:
        return {
            "requested_language": self.requested_language,
            "resolved_language": self.resolved_language,
            "source_language": self.source_language,
            "status": self.status,
            "fallback_chain": list(self.fallback_chain),
            "missing_fields": sorted(self.missing_fields),
            "stale_fields": sorted(self.stale_fields),
        }


def resolve_display(
    *,
    source_fields: Mapping[str, str],
    rows: Iterable[TranslationRow],
    requested_language: Optional[str],
    source_language: str = DEFAULT_LANGUAGE,
    default_language: str = DEFAULT_LANGUAGE,
) -> LocalizationResult:
    """Resolve display text per field from candidate approved rows (PURE — no DB).

    ``rows`` must already be the active-approved candidate rows for this subject restricted
    to the fallback-chain languages (one per (field, language)). For each requested field:
    pick the first chain language whose approved row's source_hash matches the current source
    (else record ``stale`` and keep falling back); when none matches, use the canonical source
    text (``source_fallback``) and record ``missing``.
    """
    req = normalize_language(requested_language)
    chain = language_fallback_chain(requested_language, default_language)
    by_key: Dict[tuple, TranslationRow] = {
        (row.field, normalize_language(row.language)): row for row in rows
    }

    result = LocalizationResult(
        requested_language=req,
        source_language=normalize_language(source_language),
        fallback_chain=chain,
    )
    used_languages: set[str] = set()
    all_requested_current = True

    for fld, source_text in source_fields.items():
        src_hash = text_hash(source_text)
        chosen_lang: Optional[str] = None
        chosen_text: Optional[str] = None
        saw_stale_in_requested = False
        for lang in chain:
            row = by_key.get((fld, lang))
            if row is None:
                continue
            if row.source_hash != src_hash:
                # approved row exists but the source text changed -> stale, keep falling back
                if lang == req:
                    saw_stale_in_requested = True
                continue
            chosen_lang, chosen_text = lang, row.text
            break

        if chosen_text is not None:
            result.display[fld] = chosen_text
            used_languages.add(chosen_lang)
            if chosen_lang != req:
                all_requested_current = False
            if saw_stale_in_requested and chosen_lang != req:
                result.stale_fields.append(fld)
        else:
            result.display[fld] = source_text  # canonical source fallback
            used_languages.add(result.source_language)
            all_requested_current = False
            if saw_stale_in_requested:
                result.stale_fields.append(fld)
            else:
                result.missing_fields.append(fld)

    # resolved_language: the requested language when it fully served every field; else the
    # single other language used, else the source language.
    if all_requested_current and source_fields:
        result.resolved_language = req
        result.status = STATUS_APPROVED_CURRENT
    else:
        non_source = used_languages - {result.source_language}
        if (
            len(non_source) == 1
            and not result.missing_fields
            and not result.stale_fields
        ):
            result.resolved_language = next(iter(non_source))
            result.status = STATUS_LANGUAGE_FALLBACK
        else:
            result.resolved_language = result.source_language
            result.status = STATUS_SOURCE_FALLBACK
    return result
