"""AST-based safe expression evaluator for unit conversion formulas.

Replaces unsafe uses of Python's built-in eval() with a strict whitelist
approach that only allows numeric constants, basic arithmetic operators,
unary operators, and named variables.

Security: F-001 critical fix — prevents Remote Code Execution (RCE) via
crafted formula strings.
"""

import ast
import operator
from typing import Optional

# Whitelisted binary operators
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}

# Whitelisted unary operators
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def safe_eval(expr: str, variables: Optional[dict[str, float]] = None) -> float:
    """Safely evaluate a math expression string.

    Only supports: numeric literals, +, -, *, /, //, **, %,
    unary +/-, parentheses, and named variables from the
    provided dictionary.

    Args:
        expr: Mathematical expression string (e.g. "value * 1000").
        variables: Optional mapping of variable names to float values.

    Returns:
        The float result of the expression.

    Raises:
        ValueError: If the expression contains unsafe constructs or
            references undefined variables.
        SyntaxError: If the expression is not valid Python syntax.
    """
    if not expr or not expr.strip():
        raise ValueError("Expression must not be empty")

    variables = variables or {}

    tree = ast.parse(expr.strip(), mode="eval")
    return float(_walk(tree.body, variables))


def _walk(node: ast.AST, variables: dict[str, float]) -> float:
    """Recursively evaluate an AST node against the whitelist."""

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            raise ValueError(f"Unsafe expression node: Constant of type bool")
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(
            f"Unsafe expression node: Constant of type {type(node.value).__name__}"
        )

    if isinstance(node, ast.BinOp):
        op_func = _BIN_OPS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"Unsafe expression node: {type(node.op).__name__}")
        left = _walk(node.left, variables)
        right = _walk(node.right, variables)
        try:
            return op_func(left, right)
        except (ZeroDivisionError, OverflowError) as exc:
            raise ValueError(f"Arithmetic error in expression: {exc}") from exc

    if isinstance(node, ast.UnaryOp):
        op_func = _UNARY_OPS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"Unsafe expression node: {type(node.op).__name__}")
        return op_func(_walk(node.operand, variables))

    if isinstance(node, ast.Name):
        if node.id in variables:
            return float(variables[node.id])
        raise ValueError(f"Unsafe expression node: undefined variable '{node.id}'")

    raise ValueError(f"Unsafe expression node: {type(node).__name__}")
