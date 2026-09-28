"""Tests for safe expression evaluator (F-001 security fix)."""

import pytest

from src.calculation.safe_eval import safe_eval


class TestArithmetic:
    """Test basic arithmetic operations."""

    def test_addition(self):
        assert safe_eval("2 + 3") == 5.0

    def test_subtraction(self):
        assert safe_eval("10 - 4") == 6.0

    def test_multiplication(self):
        assert safe_eval("3 * 7") == 21.0

    def test_division(self):
        assert safe_eval("15 / 3") == 5.0

    def test_floor_division(self):
        assert safe_eval("7 // 2") == 3.0

    def test_power(self):
        assert safe_eval("2 ** 3") == 8.0

    def test_modulo(self):
        assert safe_eval("10 % 3") == 1.0


class TestUnary:
    """Test unary operators."""

    def test_negative(self):
        assert safe_eval("-5") == -5.0

    def test_positive(self):
        assert safe_eval("+5") == 5.0

    def test_double_negative(self):
        assert safe_eval("--5") == 5.0


class TestComplex:
    """Test complex expressions."""

    def test_mixed_ops(self):
        assert safe_eval("(10 - 2) * 3 / 4") == 6.0

    def test_nested_parens(self):
        assert safe_eval("((2 + 3) * (4 - 1))") == 15.0

    def test_floats(self):
        assert abs(safe_eval("3.14 * 2") - 6.28) < 1e-9

    def test_precedence(self):
        assert safe_eval("2 + 3 * 4") == 14.0

    def test_fahrenheit_to_celsius(self):
        # (212 - 32) * 5 / 9 = 100
        assert safe_eval("(212 - 32) * 5 / 9") == 100.0


class TestVariables:
    """Test variable substitution."""

    def test_simple_variable(self):
        assert safe_eval("value * 2", {"value": 5.0}) == 10.0

    def test_multiple_variables(self):
        result = safe_eval("x + y", {"x": 3.0, "y": 7.0})
        assert result == 10.0

    def test_variable_in_formula(self):
        result = safe_eval("value / 3.6", {"value": 36.0})
        assert result == 10.0

    def test_variable_with_parens(self):
        result = safe_eval("(value - 32) * 5 / 9", {"value": 212.0})
        assert result == 100.0

    def test_unknown_variable_raises(self):
        with pytest.raises(ValueError, match="Unsafe expression node"):
            safe_eval("unknown * 2", {"value": 5.0})


class TestRejectsUnsafe:
    """Test that unsafe expressions are rejected."""

    def test_rejects_import(self):
        with pytest.raises(ValueError):
            safe_eval("__import__('os')")

    def test_rejects_attribute_access(self):
        with pytest.raises(ValueError):
            safe_eval("(1).__class__")

    def test_rejects_function_calls(self):
        with pytest.raises(ValueError):
            safe_eval("print(1)")

    def test_rejects_string_literals(self):
        with pytest.raises(ValueError):
            safe_eval("'hello'")

    def test_rejects_lists(self):
        with pytest.raises(ValueError):
            safe_eval("[1, 2, 3]")

    def test_rejects_dicts(self):
        with pytest.raises(ValueError):
            safe_eval("{'a': 1}")

    def test_rejects_lambda(self):
        with pytest.raises(ValueError):
            safe_eval("lambda: 1")

    def test_rejects_comprehension(self):
        with pytest.raises(ValueError):
            safe_eval("[x for x in range(10)]")

    def test_rejects_subscript(self):
        with pytest.raises(ValueError):
            safe_eval("a[0]", {"a": [1, 2, 3]})

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError):
            safe_eval("")

    def test_rejects_builtins_bypass(self):
        with pytest.raises(ValueError):
            safe_eval("__builtins__")

    def test_rejects_exec(self):
        with pytest.raises(ValueError):
            safe_eval("exec('1')")

    def test_rejects_semicolon_injection(self):
        with pytest.raises((ValueError, SyntaxError)):
            safe_eval("1; import os")

    def test_division_by_zero_raises_value_error(self):
        with pytest.raises(ValueError, match="Arithmetic error"):
            safe_eval("1 / 0")

    def test_modulo_by_zero_raises_value_error(self):
        with pytest.raises(ValueError, match="Arithmetic error"):
            safe_eval("5 % 0")

    def test_bool_literal_rejected(self):
        with pytest.raises(ValueError, match="bool"):
            safe_eval("True + 1")

    def test_rejects_unsupported_binary_operator(self):
        with pytest.raises(ValueError, match="MatMult"):
            safe_eval("1 @ 2")

    def test_rejects_unsupported_unary_operator(self):
        with pytest.raises(ValueError, match="Invert"):
            safe_eval("~1")
