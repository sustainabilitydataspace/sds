"""E6 Policy Service for ODRL-based policy management."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import structlog
from src.auth.models import Permission, UserRole

logger = structlog.get_logger(__name__)

# Path to ODRL policy registry (configurable via environment)
_default_registry_path = Path(__file__).parent / "policy_registry.json"
POLICY_REGISTRY_PATH = Path(
    os.environ.get("SDS_POLICY_REGISTRY_PATH", str(_default_registry_path))
)


class Policy:
    """Represents an ODRL policy."""

    def __init__(self, data: Dict[str, Any]):
        self.id = data.get("@id", "")
        self.type = data.get("@type", "")
        self.description = data.get("sds:description", "")
        self.permissions = data.get("odrl:permission", [])
        self.prohibitions = data.get("odrl:prohibition", [])
        self.obligations = data.get("odrl:obligation", [])

    def get_required_permission(self) -> Optional[str]:
        """Extract required permission from policy permissions."""
        for perm in self.permissions:
            if isinstance(perm, dict):
                required = perm.get("sds:requiredPermission")
                if required:
                    return required
        return None

    def get_allowed_roles(self) -> List[str]:
        """Extract allowed roles from policy assignee."""
        roles = []
        for perm in self.permissions:
            if isinstance(perm, dict):
                assignee = perm.get("odrl:assignee", {})
                if isinstance(assignee, dict):
                    source = assignee.get("odrl:source", "")
                    if source:
                        # Parse "sds:role:viewer,sds:role:analyst" format
                        for part in source.split(","):
                            if part.startswith("sds:role:"):
                                role = part.replace("sds:role:", "")
                                roles.append(role)
        return roles

    def get_target(self) -> Optional[str]:
        """Extract target resource from policy permissions."""
        for perm in self.permissions:
            if isinstance(perm, dict):
                target = perm.get("odrl:target")
                if target:
                    return target
        return None

    def get_actions(self) -> List[str]:
        """Extract allowed actions from policy permissions."""
        actions = []
        for perm in self.permissions:
            if isinstance(perm, dict):
                action = perm.get("odrl:action")
                if isinstance(action, list):
                    actions.extend(action)
                elif action:
                    actions.append(action)
        return actions


class ResourceMapping:
    """Maps resources to ODRL actions and required permissions."""

    def __init__(self, name: str, data: Dict[str, Any]):
        self.name = name
        self.read_action = data.get("readAction", "odrl:read")
        self.write_action = data.get("writeAction", "odrl:modify")
        self.delete_action = data.get("deleteAction", "odrl:delete")
        self.required_read_permission = data.get("requiredReadPermission")
        self.required_write_permission = data.get("requiredWritePermission")


class PolicyService:
    """Service for loading and evaluating ODRL policies."""

    def __init__(self, registry_path: Optional[Path] = None):
        self._registry_path = registry_path or POLICY_REGISTRY_PATH
        self._policies: Dict[str, Policy] = {}
        self._resource_mappings: Dict[str, ResourceMapping] = {}
        self._context: Dict[str, str] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy load policies from registry."""
        if self._loaded:
            return

        if self._registry_path.exists():
            try:
                with open(self._registry_path, "r") as f:
                    data = json.load(f)

                self._context = data.get("@context", {})

                # Load policies
                for policy_data in data.get("policies", []):
                    policy = Policy(policy_data)
                    if policy.id:
                        self._policies[policy.id] = policy

                # Load resource mappings
                for name, mapping_data in data.get("resourceMappings", {}).items():
                    self._resource_mappings[name] = ResourceMapping(name, mapping_data)

                logger.info(
                    "Loaded ODRL policies from registry",
                    policy_count=len(self._policies),
                    resource_count=len(self._resource_mappings),
                    path=str(self._registry_path),
                )
            except Exception as e:
                logger.error(
                    "Failed to load policy registry",
                    error=str(e),
                    path=str(self._registry_path),
                )
                self._loaded = True  # Mark as loaded to avoid retry loops
                raise RuntimeError(f"Failed to load policy registry: {e}") from e

        self._loaded = True

    def get_all_policies(self) -> List[Policy]:
        """Get all loaded policies."""
        self._ensure_loaded()
        return list(self._policies.values())

    def get_policy(self, policy_id: str) -> Optional[Policy]:
        """Get policy by ID."""
        self._ensure_loaded()
        return self._policies.get(policy_id)

    def get_policies_for_resource(self, resource: str) -> List[Policy]:
        """Get all policies applicable to a resource."""
        self._ensure_loaded()
        result = []
        resource_target = f"sds:resource:{resource}"
        for policy in self._policies.values():
            target = policy.get_target()
            if target == resource_target or target == f"{resource}:*":
                result.append(policy)
        return result

    def get_resource_mapping(self, resource: str) -> Optional[ResourceMapping]:
        """Get resource mapping by name."""
        self._ensure_loaded()
        return self._resource_mappings.get(resource)

    def get_required_permission_for_action(
        self, resource: str, action: str
    ) -> Optional[Permission]:
        """Get required permission for an action on a resource."""
        self._ensure_loaded()
        mapping = self._resource_mappings.get(resource)
        if not mapping:
            return None

        # Map ODRL actions to permissions
        if action in ("read", "odrl:read"):
            perm_str = mapping.required_read_permission
        elif action in ("write", "modify", "odrl:modify", "create"):
            perm_str = mapping.required_write_permission
        elif action in ("delete", "odrl:delete"):
            perm_str = mapping.required_write_permission
        else:
            return None

        # Convert string to Permission enum
        if perm_str:
            try:
                return Permission(perm_str)
            except ValueError:
                logger.warning("Unknown permission in policy", permission=perm_str)
                return None

        return None

    def evaluate_access(
        self,
        resource: str,
        action: str,
        user_role: UserRole,
        user_permissions: List[Permission],
    ) -> bool:
        """Evaluate if access should be granted based on policies."""
        self._ensure_loaded()

        # Get required permission for the action
        required_perm = self.get_required_permission_for_action(resource, action)
        if not required_perm:
            logger.warning(
                "No policy found for resource action",
                resource=resource,
                action=action,
            )
            # Default deny if no policy defined
            return False

        # Check if user has required permission
        has_permission = required_perm in user_permissions

        if has_permission:
            logger.debug(
                "Access granted by policy",
                resource=resource,
                action=action,
                role=user_role.value,
                permission=required_perm.value,
            )
        else:
            logger.debug(
                "Access denied by policy",
                resource=resource,
                action=action,
                role=user_role.value,
                required_permission=required_perm.value,
            )

        return has_permission

    def get_policy_summary(self) -> Dict[str, Any]:
        """Get summary of loaded policies for debugging/monitoring."""
        self._ensure_loaded()
        return {
            "policy_count": len(self._policies),
            "resource_count": len(self._resource_mappings),
            "policies": [
                {
                    "id": p.id,
                    "description": p.description,
                    "target": p.get_target(),
                    "actions": p.get_actions(),
                    "required_permission": p.get_required_permission(),
                }
                for p in self._policies.values()
            ],
            "resources": list(self._resource_mappings.keys()),
        }


# Singleton instance
_policy_service: Optional[PolicyService] = None


def get_policy_service() -> PolicyService:
    """Get or create the singleton PolicyService instance."""
    global _policy_service
    if _policy_service is None:
        _policy_service = PolicyService()
    return _policy_service
