from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def load_policy_registry(registry_path: Path) -> Dict[str, Any]:
    """Load a policy registry JSON file.

    Returns an empty dict when the file doesn't exist. This keeps callers
    offline-first and allows graceful fallbacks.
    """
    if not registry_path.exists():
        return {}
    return json.loads(registry_path.read_text(encoding="utf-8"))


def policy_id_for_purpose(registry: Dict[str, Any], purpose: str) -> Optional[str]:
    """Return the default policy id for a purpose, or the registry default deny policy."""
    vocab = registry.get("purposeVocabulary", {})
    if isinstance(vocab, dict) and purpose in vocab and isinstance(vocab[purpose], dict):
        pid = vocab[purpose].get("defaultPolicyId")
        if isinstance(pid, str) and pid:
            return pid

    pid = registry.get("defaultDenyPolicy")
    return pid if isinstance(pid, str) and pid else None


def resolve_policy_id(registry: Dict[str, Any], purpose: str, *, default: str = "policy-deny-all") -> str:
    """Resolve a policy id deterministically with a deny-by-default fallback."""
    pid = policy_id_for_purpose(registry, purpose)
    return pid or default
