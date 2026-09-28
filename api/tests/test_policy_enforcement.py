"""Unit tests for E6 policy enforcement."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.auth.models import Permission, User, UserRole
from src.policies.policy_enforcer import (
    PolicyEnforcer,
    get_policy_enforcer,
    require_crosswalk_read,
    require_crosswalk_write,
    require_indicator_read,
    require_indicator_write,
    require_mapping_read,
    require_mapping_write,
    require_policy,
)
from src.policies.policy_service import (
    Policy,
    PolicyService,
    ResourceMapping,
    get_policy_service,
)

# =============================================================================
# Policy Service Tests
# =============================================================================


class TestPolicy:
    """Tests for Policy class."""

    def test_policy_from_dict(self):
        """Test Policy initialization from dictionary."""
        data = {
            "@id": "sds:policy:test",
            "@type": "odrl:Set",
            "sds:description": "Test policy",
            "odrl:permission": [
                {
                    "odrl:action": "odrl:read",
                    "odrl:target": "sds:resource:indicators",
                    "odrl:assignee": {
                        "@type": "odrl:PartyCollection",
                        "odrl:source": "sds:role:viewer,sds:role:admin",
                    },
                    "sds:requiredPermission": "read_indicators",
                }
            ],
        }
        policy = Policy(data)

        assert policy.id == "sds:policy:test"
        assert policy.type == "odrl:Set"
        assert policy.description == "Test policy"

    def test_get_required_permission(self):
        """Test extracting required permission from policy."""
        data = {
            "@id": "sds:policy:test",
            "odrl:permission": [
                {"sds:requiredPermission": "read_indicators"},
            ],
        }
        policy = Policy(data)

        assert policy.get_required_permission() == "read_indicators"

    def test_get_allowed_roles(self):
        """Test extracting allowed roles from policy assignee."""
        data = {
            "@id": "sds:policy:test",
            "odrl:permission": [
                {
                    "odrl:assignee": {
                        "odrl:source": "sds:role:viewer,sds:role:analyst,sds:role:admin",
                    },
                }
            ],
        }
        policy = Policy(data)
        roles = policy.get_allowed_roles()

        assert "viewer" in roles
        assert "analyst" in roles
        assert "admin" in roles
        assert len(roles) == 3

    def test_get_target(self):
        """Test extracting target resource from policy."""
        data = {
            "@id": "sds:policy:test",
            "odrl:permission": [
                {"odrl:target": "sds:resource:mappings"},
            ],
        }
        policy = Policy(data)

        assert policy.get_target() == "sds:resource:mappings"

    def test_get_actions(self):
        """Test extracting actions from policy."""
        data = {
            "@id": "sds:policy:test",
            "odrl:permission": [
                {"odrl:action": ["odrl:read", "odrl:modify"]},
            ],
        }
        policy = Policy(data)
        actions = policy.get_actions()

        assert "odrl:read" in actions
        assert "odrl:modify" in actions


class TestResourceMapping:
    """Tests for ResourceMapping class."""

    def test_resource_mapping_from_dict(self):
        """Test ResourceMapping initialization."""
        data = {
            "readAction": "odrl:read",
            "writeAction": "odrl:modify",
            "deleteAction": "odrl:delete",
            "requiredReadPermission": "read_indicators",
            "requiredWritePermission": "manage_indicators",
        }
        mapping = ResourceMapping("indicators", data)

        assert mapping.name == "indicators"
        assert mapping.read_action == "odrl:read"
        assert mapping.write_action == "odrl:modify"
        assert mapping.delete_action == "odrl:delete"
        assert mapping.required_read_permission == "read_indicators"
        assert mapping.required_write_permission == "manage_indicators"


class TestPolicyService:
    """Tests for PolicyService."""

    def test_lazy_loading(self):
        """Test that policies are lazily loaded."""
        service = PolicyService(registry_path=Path("/nonexistent/path.json"))
        assert service._loaded is False

        service._ensure_loaded()
        assert service._loaded is True

    def test_load_from_json_file(self):
        """Test loading policies from JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "policies.json"
            test_data = {
                "@context": {},
                "policies": [
                    {
                        "@id": "sds:policy:test",
                        "sds:description": "Test policy",
                        "odrl:permission": [],
                    }
                ],
                "resourceMappings": {
                    "indicators": {
                        "readAction": "odrl:read",
                        "requiredReadPermission": "read_indicators",
                    }
                },
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            service = PolicyService(registry_path=json_path)

            assert len(service.get_all_policies()) == 1
            assert service.get_resource_mapping("indicators") is not None

    def test_get_policy(self):
        """Test getting policy by ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "policies.json"
            test_data = {
                "policies": [
                    {"@id": "sds:policy:test-1", "sds:description": "Policy 1"},
                    {"@id": "sds:policy:test-2", "sds:description": "Policy 2"},
                ],
                "resourceMappings": {},
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            service = PolicyService(registry_path=json_path)
            policy = service.get_policy("sds:policy:test-1")

            assert policy is not None
            assert policy.description == "Policy 1"

            # Test not found
            assert service.get_policy("sds:policy:nonexistent") is None

    def test_get_required_permission_for_action(self):
        """Test getting required permission for an action."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "policies.json"
            test_data = {
                "policies": [],
                "resourceMappings": {
                    "indicators": {
                        "readAction": "odrl:read",
                        "writeAction": "odrl:modify",
                        "requiredReadPermission": "read_indicators",
                        "requiredWritePermission": "manage_indicators",
                    }
                },
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            service = PolicyService(registry_path=json_path)

            # Read action
            perm = service.get_required_permission_for_action("indicators", "read")
            assert perm == Permission.READ_INDICATORS

            # Write action
            perm = service.get_required_permission_for_action("indicators", "write")
            assert perm == Permission.MANAGE_INDICATORS

            # Unknown action
            perm = service.get_required_permission_for_action("indicators", "unknown")
            assert perm is None

            # Unknown resource
            perm = service.get_required_permission_for_action("unknown", "read")
            assert perm is None

    def test_evaluate_access_granted(self):
        """Test access evaluation when permission is granted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "policies.json"
            test_data = {
                "policies": [],
                "resourceMappings": {
                    "indicators": {
                        "requiredReadPermission": "read_indicators",
                    }
                },
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            service = PolicyService(registry_path=json_path)

            result = service.evaluate_access(
                resource="indicators",
                action="read",
                user_role=UserRole.VIEWER,
                user_permissions=[Permission.READ_INDICATORS],
            )

            assert result is True

    def test_evaluate_access_denied(self):
        """Test access evaluation when permission is denied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "policies.json"
            test_data = {
                "policies": [],
                "resourceMappings": {
                    "indicators": {
                        "requiredReadPermission": "read_indicators",
                    }
                },
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            service = PolicyService(registry_path=json_path)

            result = service.evaluate_access(
                resource="indicators",
                action="read",
                user_role=UserRole.VIEWER,
                user_permissions=[],  # No permissions
            )

            assert result is False

    def test_evaluate_access_no_policy(self):
        """Test access evaluation when no policy exists for resource."""
        service = PolicyService(registry_path=Path("/nonexistent.json"))

        result = service.evaluate_access(
            resource="unknown",
            action="read",
            user_role=UserRole.ADMIN,
            user_permissions=[Permission.MANAGE_SYSTEM],
        )

        # Default deny when no policy
        assert result is False

    def test_get_policy_summary(self):
        """Test getting policy summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "policies.json"
            test_data = {
                "policies": [
                    {"@id": "sds:policy:test", "sds:description": "Test"},
                ],
                "resourceMappings": {
                    "indicators": {},
                    "mappings": {},
                },
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            service = PolicyService(registry_path=json_path)
            summary = service.get_policy_summary()

            assert summary["policy_count"] == 1
            assert summary["resource_count"] == 2
            assert "indicators" in summary["resources"]
            assert "mappings" in summary["resources"]

    def test_policy_service_remaining_registry_and_permission_edges(self):
        """Cover malformed registries, wildcard targets, delete actions, and bad permissions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_path = Path(tmpdir) / "bad.json"
            bad_path.write_text("{not json")
            service = PolicyService(registry_path=bad_path)
            with pytest.raises(RuntimeError, match="Failed to load policy registry"):
                service.get_all_policies()

            json_path = Path(tmpdir) / "policies.json"
            test_data = {
                "policies": [
                    {
                        "@id": "sds:policy:wildcard",
                        "sds:description": "Wildcard policy",
                        "odrl:permission": [
                            {
                                "odrl:target": "mappings:*",
                                "odrl:action": "odrl:delete",
                                "sds:requiredPermission": "not_a_real_permission",
                            },
                            "ignored-permission-shape",
                        ],
                    }
                ],
                "resourceMappings": {
                    "mappings": {
                        "requiredReadPermission": "read_mappings",
                        "requiredWritePermission": "manage_mappings",
                    },
                    "broken": {
                        "requiredReadPermission": "not_a_real_permission",
                    },
                },
            }
            json_path.write_text(json.dumps(test_data))
            service = PolicyService(registry_path=json_path)

            assert [p.id for p in service.get_policies_for_resource("mappings")] == [
                "sds:policy:wildcard"
            ]
            assert (
                service.get_required_permission_for_action("mappings", "delete")
                == Permission.MANAGE_MAPPINGS
            )
            assert service.get_required_permission_for_action("broken", "read") is None
            policy = service.get_policy("sds:policy:wildcard")
            assert policy is not None
            assert policy.get_allowed_roles() == []
            assert policy.get_actions() == ["odrl:delete"]


# =============================================================================
# Policy Enforcer Tests
# =============================================================================


class TestPolicyEnforcer:
    """Tests for PolicyEnforcer."""

    def _create_user(
        self, role: UserRole = UserRole.VIEWER, permissions: list = None
    ) -> User:
        """Create a test user."""
        return User(
            id="user-123",
            username="testuser",
            email="test@example.com",
            full_name="Test User",
            company_id="company-1",
            role=role,
            is_active=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            permissions=permissions or [],
        )

    def test_check_access_granted(self):
        """Test check_access returns True when permitted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "policies.json"
            test_data = {
                "policies": [],
                "resourceMappings": {
                    "indicators": {"requiredReadPermission": "read_indicators"},
                },
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            service = PolicyService(registry_path=json_path)
            enforcer = PolicyEnforcer(service)

            user = self._create_user(permissions=[Permission.READ_INDICATORS])
            result = enforcer.check_access("indicators", "read", user)

            assert result is True

    def test_check_access_denied(self):
        """Test check_access returns False when denied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "policies.json"
            test_data = {
                "policies": [],
                "resourceMappings": {
                    "indicators": {"requiredReadPermission": "read_indicators"},
                },
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            service = PolicyService(registry_path=json_path)
            enforcer = PolicyEnforcer(service)

            user = self._create_user(permissions=[])  # No permissions
            result = enforcer.check_access("indicators", "read", user)

            assert result is False

    def test_enforce_raises_on_denied(self):
        """Test enforce raises HTTPException when denied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "policies.json"
            test_data = {
                "policies": [],
                "resourceMappings": {
                    "indicators": {"requiredReadPermission": "read_indicators"},
                },
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            service = PolicyService(registry_path=json_path)
            enforcer = PolicyEnforcer(service)

            user = self._create_user(permissions=[])

            with pytest.raises(HTTPException) as exc_info:
                enforcer.enforce("indicators", "read", user)

            assert exc_info.value.status_code == 403
            assert "Access denied" in exc_info.value.detail

    def test_enforce_passes_on_granted(self):
        """Test enforce does not raise when permitted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "policies.json"
            test_data = {
                "policies": [],
                "resourceMappings": {
                    "mappings": {"requiredReadPermission": "read_mappings"},
                },
            }
            with open(json_path, "w") as f:
                json.dump(test_data, f)

            service = PolicyService(registry_path=json_path)
            enforcer = PolicyEnforcer(service)

            user = self._create_user(permissions=[Permission.READ_MAPPINGS])

            # Should not raise
            enforcer.enforce("mappings", "read", user)

    @pytest.mark.asyncio
    async def test_policy_dependency_returns_user_after_enforcement(self):
        user = self._create_user(permissions=[Permission.READ_INDICATORS])
        enforcer = MagicMock(spec=PolicyEnforcer)

        dependency = require_policy("indicators", "read")
        result = await dependency(user=user, enforcer=enforcer)

        assert result is user
        enforcer.enforce.assert_called_once_with("indicators", "read", user)

    def test_policy_enforcer_dependency_factory_and_convenience_wrappers(self):
        service = MagicMock(spec=PolicyService)

        assert get_policy_enforcer(service).policy_service is service
        assert require_indicator_read() is not None
        assert require_indicator_write() is not None
        assert require_crosswalk_read() is not None
        assert require_crosswalk_write() is not None
        assert require_mapping_read() is not None
        assert require_mapping_write() is not None


# =============================================================================
# Permission Model Integration Tests
# =============================================================================


class TestPermissionIntegration:
    """Tests for permission model integration."""

    def test_indicator_permissions_exist(self):
        """Test that indicator permissions are defined."""
        assert Permission.READ_INDICATORS
        assert Permission.MANAGE_INDICATORS

    def test_mapping_permissions_exist(self):
        """Test that mapping permissions are defined."""
        assert Permission.READ_MAPPINGS
        assert Permission.MANAGE_MAPPINGS

    def test_viewer_has_read_permissions(self):
        """Test that VIEWER role has read permissions for indicators/mappings."""
        from src.auth.models import ROLE_PERMISSIONS

        viewer_perms = ROLE_PERMISSIONS[UserRole.VIEWER]

        assert Permission.READ_INDICATORS in viewer_perms
        assert Permission.READ_MAPPINGS in viewer_perms
        assert Permission.MANAGE_INDICATORS not in viewer_perms
        assert Permission.MANAGE_MAPPINGS not in viewer_perms

    def test_analyst_has_read_permissions(self):
        """Test that ANALYST role has read permissions."""
        from src.auth.models import ROLE_PERMISSIONS

        analyst_perms = ROLE_PERMISSIONS[UserRole.ANALYST]

        assert Permission.READ_INDICATORS in analyst_perms
        assert Permission.READ_MAPPINGS in analyst_perms

    def test_data_manager_has_manage_permissions(self):
        """Test that DATA_MANAGER role has manage permissions."""
        from src.auth.models import ROLE_PERMISSIONS

        dm_perms = ROLE_PERMISSIONS[UserRole.DATA_MANAGER]

        assert Permission.READ_INDICATORS in dm_perms
        assert Permission.MANAGE_INDICATORS in dm_perms
        assert Permission.READ_MAPPINGS in dm_perms
        assert Permission.MANAGE_MAPPINGS in dm_perms

    def test_admin_has_all_permissions(self):
        """Test that ADMIN role has all indicator/mapping permissions."""
        from src.auth.models import ROLE_PERMISSIONS

        admin_perms = ROLE_PERMISSIONS[UserRole.ADMIN]

        assert Permission.READ_INDICATORS in admin_perms
        assert Permission.MANAGE_INDICATORS in admin_perms
        assert Permission.READ_MAPPINGS in admin_perms
        assert Permission.MANAGE_MAPPINGS in admin_perms


# =============================================================================
# Policy Registry Tests
# =============================================================================


class TestPolicyRegistry:
    """Tests for the actual policy registry file."""

    def test_actual_policy_registry_loads(self):
        """Test that the actual policy_registry.json loads correctly."""
        service = get_policy_service()

        # Should load without errors
        policies = service.get_all_policies()
        assert len(policies) > 0

    def test_actual_indicator_resource_mapping(self):
        """Test that indicator resource mapping is defined."""
        service = get_policy_service()
        mapping = service.get_resource_mapping("indicators")

        assert mapping is not None
        assert mapping.required_read_permission == "read_indicators"
        assert mapping.required_write_permission == "manage_indicators"

    def test_actual_mapping_resource_mapping(self):
        """Test that mapping resource mapping is defined."""
        service = get_policy_service()
        mapping = service.get_resource_mapping("mappings")

        assert mapping is not None
        assert mapping.required_read_permission == "read_mappings"
        assert mapping.required_write_permission == "manage_mappings"

    def test_policy_summary_structure(self):
        """Test policy summary has expected structure."""
        service = get_policy_service()
        summary = service.get_policy_summary()

        assert "policy_count" in summary
        assert "resource_count" in summary
        assert "policies" in summary
        assert "resources" in summary
        assert summary["policy_count"] >= 4  # At least 4 policies defined
        assert summary["resource_count"] >= 2  # indicators and mappings

    def test_api_local_registry_is_not_canonical_e6_registry(self):
        """Guard: the API-local RBAC registry must not be mistaken for the
        canonical E6 governance registry. It lacks denyByDefault, purposeVocabulary,
        regionVocabulary, and uid-style policies, and instead carries
        resourceMappings for endpoint RBAC."""
        import json
        from pathlib import Path

        api_registry_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "policies"
            / "policy_registry.json"
        )
        api_registry = json.loads(api_registry_path.read_text(encoding="utf-8"))

        # API-local registry must NOT claim canonical E6 fields
        assert (
            "denyByDefault" not in api_registry
        ), "API-local registry should not declare denyByDefault; that belongs to the canonical E6 registry"
        assert (
            "defaultDenyPolicy" not in api_registry
        ), "API-local registry should not declare defaultDenyPolicy; that belongs to the canonical E6 registry"
        assert (
            "purposeVocabulary" not in api_registry
        ), "API-local registry should not declare purposeVocabulary; that belongs to the canonical E6 registry"
        assert (
            "regionVocabulary" not in api_registry
        ), "API-local registry should not declare regionVocabulary; that belongs to the canonical E6 registry"

        # API-local registry MUST carry resourceMappings for endpoint RBAC
        assert (
            "resourceMappings" in api_registry
        ), "API-local registry must define resourceMappings for HTTP endpoint RBAC"
        assert api_registry[
            "resourceMappings"
        ], "API-local registry resourceMappings must not be empty"

        # Policies in API-local registry use @id (not uid) because they are ODRL-style
        policies = api_registry.get("policies", [])
        assert all("@id" in p for p in policies), "API-local policies must use @id"
        assert not any(
            "uid" in p for p in policies
        ), "API-local policies must not use uid (canonical E6 style)"
