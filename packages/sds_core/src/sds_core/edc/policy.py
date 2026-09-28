from __future__ import annotations

from typing import Any, Dict, List


class PolicyValidationError(Exception):
    """Raised when policy validation fails."""


class AllowAllDetectedError(PolicyValidationError):
    """Raised when an allow-all policy pattern is detected."""


def validate_policy_not_allow_all(uid: str, odrl: Dict[str, Any]) -> None:
    """Validate that a policy does not represent an allow-all pattern.

    Allow-all patterns include:
    - Permission with no constraints
    - Permission with empty constraints array
    - No prohibitions and permissive permissions
    """
    permissions = odrl.get("permission", [])
    prohibitions = odrl.get("prohibition", [])

    # Deny-all policies (no permissions, has prohibitions) are valid
    if not permissions and prohibitions:
        return

    # Check each permission for allow-all patterns
    for perm in permissions:
        constraints = perm.get("constraint", [])

        # Empty or missing constraints = allow-all
        if not constraints:
            raise AllowAllDetectedError(
                f"Policy '{uid}' has permission with no constraints (allow-all pattern). "
                "All permissions must have at least one constraint."
            )

        # Check for effectively empty constraints
        for c in constraints:
            left = c.get("leftOperand", "")
            right = c.get("rightOperand", "")
            if not left or (right == "" and right is not False and right != 0):
                raise AllowAllDetectedError(
                    f"Policy '{uid}' has constraint with empty operand (effective allow-all). "
                    f"Constraint: {c}"
                )


def transform_odrl_to_edc(uid: str, odrl: Dict[str, Any]) -> Dict[str, Any]:
    """Transform ODRL policy to EDC policy format."""

    def transform_constraint(c: Dict[str, Any]) -> Dict[str, Any]:
        left = c.get("leftOperand", "")
        op = c.get("operator", "eq")
        right = c.get("rightOperand", "")

        op_map = {
            "eq": "EQ",
            "neq": "NEQ",
            "lt": "LT",
            "lteq": "LTEQ",
            "gt": "GT",
            "gteq": "GTEQ",
            "isAnyOf": "IN",
            "isNoneOf": "NOT_IN",
        }
        edc_op = op_map.get(op, op.upper())

        return {
            "leftExpression": left,
            "operator": edc_op,
            "rightExpression": right,
        }

    def transform_duty(d: Dict[str, Any]) -> Dict[str, Any]:
        action = d.get("action", "")
        if isinstance(action, dict):
            action_value = action.get("rdf:value", {}).get("@id", str(action))
        else:
            action_value = action

        duty: Dict[str, Any] = {"action": {"type": str(action_value).upper()}}

        constraints = d.get("constraint", [])
        if constraints:
            duty["constraints"] = [transform_constraint(c) for c in constraints]

        return duty

    def transform_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
        action = rule.get("action", "use")
        action_type = action.upper() if isinstance(action, str) else "USE"

        edc_rule: Dict[str, Any] = {
            "action": {"type": action_type},
            "constraints": [],
            "duties": [],
        }

        for c in rule.get("constraint", []):
            edc_rule["constraints"].append(transform_constraint(c))

        for d in rule.get("duty", []):
            edc_rule["duties"].append(transform_duty(d))

        return edc_rule

    permissions = [transform_rule(p) for p in odrl.get("permission", [])]
    prohibitions = [transform_rule(p) for p in odrl.get("prohibition", [])]
    obligations = [transform_duty(o) for o in odrl.get("obligation", [])]

    return {
        "uid": uid,
        "policy": {
            "permissions": permissions,
            "prohibitions": prohibitions,
            "obligations": obligations,
        },
    }


def validate_no_allow_all_in_output(policies: List[Dict[str, Any]]) -> None:
    """Final validation that no allow-all policies exist in output."""
    for p in policies:
        uid = p.get("uid", "")
        policy = p.get("policy", {})

        permissions = policy.get("permissions", [])
        prohibitions = policy.get("prohibitions", [])

        if not prohibitions:
            for perm in permissions:
                constraints = perm.get("constraints", [])
                if not constraints:
                    raise AllowAllDetectedError(
                        f"Output policy '{uid}' has allow-all pattern "
                        "(permission with no constraints and no prohibitions)"
                    )
