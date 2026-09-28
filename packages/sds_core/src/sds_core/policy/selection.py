from __future__ import annotations

from typing import Any, Dict, List, Optional

DEFAULT_DENY_POLICY_ID = "policy-deny-all"


def select_policy_for_dataset(
    registry: Any,
    *,
    purpose: str,
    region: str,
    explicit_policy_id: Optional[str] = None,
    default_deny_policy_id: str = DEFAULT_DENY_POLICY_ID,
) -> tuple[str, List[str]]:
    """Select governance policies for a dataset.

    Returns (primary_policy_id, additive_policy_ids).

    Design goals:
    - Deny-by-default fallback (safe when registry is missing or partial).
    - Backwards compatible with earlier “purpose → defaultPolicyId” resolution.
    - Compatible with both raw registry dicts and richer registry objects (duck typing).
    """
    registry_dict = _as_registry_dict(registry)
    default_deny = _default_deny_policy_id(
        registry_dict, fallback=default_deny_policy_id
    )

    policies_by_uid = _policies_by_uid(registry)
    has_policy_definitions = bool(policies_by_uid)

    if explicit_policy_id:
        if has_policy_definitions and explicit_policy_id not in policies_by_uid:
            return default_deny, []
        # Validate explicit policy against region compatibility unless it is
        # the default deny policy or has no region restriction.
        if (
            has_policy_definitions
            and explicit_policy_id != default_deny
            and not _region_allowed_for_policy(
                registry_dict, policies_by_uid, explicit_policy_id, region
            )
        ):
            return default_deny, []
        return explicit_policy_id, _collect_additive_policies(policies_by_uid, region)

    purpose_vocab = _purpose_vocabulary(registry_dict)
    primary = _policy_id_for_purpose(purpose_vocab, purpose)

    if not primary:
        primary = default_deny
    elif has_policy_definitions and primary not in policies_by_uid:
        primary = default_deny

    if has_policy_definitions and not _region_allowed_for_policy(
        registry_dict, policies_by_uid, primary, region
    ):
        return default_deny, []

    return primary, _collect_additive_policies(policies_by_uid, region)


def _as_registry_dict(registry: Any) -> Dict[str, Any]:
    if isinstance(registry, dict):
        return registry

    out: Dict[str, Any] = {}
    for src_key, dst_key in (
        ("default_deny_policy", "defaultDenyPolicy"),
        ("purpose_vocabulary", "purposeVocabulary"),
        ("region_vocabulary", "regionVocabulary"),
    ):
        if hasattr(registry, src_key):
            out[dst_key] = getattr(registry, src_key)
    return out


def _default_deny_policy_id(registry: Dict[str, Any], *, fallback: str) -> str:
    pid = registry.get("defaultDenyPolicy")
    return pid if isinstance(pid, str) and pid else fallback


def _purpose_vocabulary(registry: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    vocab = registry.get("purposeVocabulary")
    return vocab if isinstance(vocab, dict) else {}


def _policy_id_for_purpose(
    purpose_vocab: Dict[str, Dict[str, str]], purpose: str
) -> Optional[str]:
    entry = purpose_vocab.get(purpose)
    if not isinstance(entry, dict):
        return None
    pid = entry.get("defaultPolicyId")
    return pid if isinstance(pid, str) and pid else None


def _policies_by_uid(registry: Any) -> Dict[str, Any]:
    if isinstance(registry, dict):
        policies = registry.get("policies", [])
        if isinstance(policies, dict):
            return policies
        if not isinstance(policies, list):
            return {}
        out: Dict[str, Any] = {}
        for p in policies:
            if isinstance(p, dict) and isinstance(p.get("uid"), str) and p.get("uid"):
                out[p["uid"]] = p
        return out

    if hasattr(registry, "policies"):
        policies = getattr(registry, "policies")
        return policies if isinstance(policies, dict) else {}

    return {}


def _region_allowed_for_policy(
    registry_dict: Dict[str, Any],
    policies_by_uid: Dict[str, Any],
    policy_id: str,
    region: str,
) -> bool:
    policy = policies_by_uid.get(policy_id)
    if not policy:
        return True

    regions: List[str] = []
    if isinstance(policy, dict):
        raw_regions = policy.get("regions", [])
        if isinstance(raw_regions, list):
            regions = [r for r in raw_regions if isinstance(r, str) and r]
    else:
        raw_regions = getattr(policy, "regions", [])
        if isinstance(raw_regions, list):
            regions = [r for r in raw_regions if isinstance(r, str) and r]

    if not regions or not region:
        return True

    if region in regions:
        return True

    region_vocab = registry_dict.get("regionVocabulary", {})
    if isinstance(region_vocab, dict):
        region_entry = region_vocab.get(region, {})
        if isinstance(region_entry, dict):
            parent = region_entry.get("parentRegion", "")
            if isinstance(parent, str) and parent and parent in regions:
                return True

    return False


def _collect_additive_policies(
    policies_by_uid: Dict[str, Any], region: str
) -> List[str]:
    additive: List[str] = []

    # Keep the current E6-000 hardening behavior as the first additive policy:
    # EU geofence applied for EU + ES.
    uid = "policy-geofence-eu"
    policy = policies_by_uid.get(uid)
    if not policy:
        return additive

    composition_mode = ""
    if isinstance(policy, dict):
        composition_mode = str(policy.get("compositionMode", "") or "")
    else:
        composition_mode = str(getattr(policy, "composition_mode", "") or "")

    if composition_mode == "additive" and region in ["EU", "ES"]:
        additive.append(uid)

    return additive
