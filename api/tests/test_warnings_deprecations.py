"""Regression tests to prevent high-volume runtime warnings."""

import warnings

from src.auth.jwt_handler import jwt_handler
from src.auth.models import UserRole


def test_jwt_handler_does_not_emit_datetime_utcnow_deprecation_warning():
    """Token encode/decode should not trigger datetime.utcnow deprecation warnings."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        token = jwt_handler.create_access_token(
            user_id="u1",
            username="alice",
            role=UserRole.VIEWER,
            company_id="c1",
        )
        assert jwt_handler.verify_token(token) is not None

    offenders = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and (
            "utcnow" in str(w.message).lower()
            or "python 3.13" in str(w.message).lower()
        )
    ]
    assert offenders == []


def test_calculation_engine_does_not_emit_deprecated_ast_num_str_warnings():
    """Formula validation should not touch deprecated ast.Num/ast.Str nodes."""
    from src.calculation.engine import CalculationEngine

    engine = CalculationEngine()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        engine._validate_formula("a + b")

    offenders = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and ("ast.num" in str(w.message).lower() or "ast.str" in str(w.message).lower())
    ]
    assert offenders == []


def test_configuration_manager_does_not_emit_datetime_utcnow_deprecation_warning():
    """Hierarchy configuration operations should not use datetime.utcnow()."""
    from src.ontology.configuration_manager import (
        ConfigurationManager,
        HierarchyConfig,
        HierarchyLevel,
    )

    config_manager = ConfigurationManager(database_url="sqlite:///:memory:")
    config = HierarchyConfig(
        company_id="test_corp",
        hierarchy_type="organizational",
        name="Test Hierarchy",
        description="Test organizational hierarchy",
        levels=[
            HierarchyLevel(id="global", name="Global", parent=None),
            HierarchyLevel(id="europe", name="Europe", parent="global"),
        ],
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        config_manager.create_hierarchy_configuration(config)

    offenders = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and (
            "utcnow" in str(w.message).lower()
            or "datetime.utc" in str(w.message).lower()
        )
    ]
    assert offenders == []
