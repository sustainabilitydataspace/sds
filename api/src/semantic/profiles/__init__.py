"""SDS semantic-atomization profile layer (VARCH-0).

Deterministic foundation that every downstream hash, trace, replay manifest,
disclosure pin, and license decision binds to:

- ``canonical_json`` — ``sds-canonical-json-v1`` deterministic serialization.
- ``computation`` — ``sds-computation-profile-v1`` deterministic decimal arithmetic.
- ``registry`` — content-addressable registry of implemented + ratified profiles.

Ratified-only profiles (frozen JSON Schemas under ``specs/``, no runtime yet):
private-commitment, replay-manifest, aggregate-disclosure,
consolidation-composition, factor-vintage-selection, license-rights-window,
public-temporal-read.
"""

from src.semantic.profiles import canonical_json, computation, registry

__all__ = ["canonical_json", "computation", "registry"]
