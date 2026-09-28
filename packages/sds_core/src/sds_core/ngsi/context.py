from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from sds_core.semantics.uris import PUBLISH_CONTEXT_URL_V1_0


NGSI_CORE_CONTEXT_URL = "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld"

# Local dev context file (kept as a string so it can be used directly in JSON-LD).
DEFAULT_DEV_SDS_CONTEXT_FILE = "semantics/context/ngsi_ld_context.jsonld"

# Published, resolvable context URL for cross-connector consumption.
DEFAULT_PUBLISH_SDS_CONTEXT_URL = PUBLISH_CONTEXT_URL_V1_0

# Versioned context document used for embed mode (inline @context object).
DEFAULT_EMBED_SDS_CONTEXT_FILE = Path("semantics/context/sds/v1.0.jsonld")

ODRL_CONTEXT: Dict[str, object] = {
    "odrl": "http://www.w3.org/ns/odrl/2/",
    "hasPolicy": {"@id": "odrl:hasPolicy", "@type": "@id"},
    "conformsTo": "http://purl.org/dc/terms/conformsTo",
    "purpose": "http://www.w3.org/ns/odrl/2/purpose",
    "spatialCoverage": "http://purl.org/dc/terms/spatial",
}


def load_embedded_context(context_file: Path) -> Dict[str, Any]:
    doc: Any = json.loads(context_file.read_text(encoding="utf-8"))
    ctx = doc.get("@context") if isinstance(doc, dict) else None
    if not isinstance(ctx, dict):
        raise ValueError(f"Invalid JSON-LD context file (expected object with '@context'): {context_file}")
    return ctx


def build_context(
    context_mode: str,
    publish_context_url: str = DEFAULT_PUBLISH_SDS_CONTEXT_URL,
    embed_context_file: Path = DEFAULT_EMBED_SDS_CONTEXT_FILE,
) -> List[object]:
    contexts: List[object] = [NGSI_CORE_CONTEXT_URL, ODRL_CONTEXT]

    if context_mode == "publish":
        contexts.append(publish_context_url)
        return contexts

    if context_mode == "embed":
        contexts.append(load_embedded_context(embed_context_file))
        return contexts

    contexts.append(DEFAULT_DEV_SDS_CONTEXT_FILE)
    return contexts
