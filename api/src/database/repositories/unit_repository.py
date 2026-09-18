"""Repository for unit-related database operations."""

from typing import Any, Dict, List, Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session

from ..models import ConversionRule, Unit, UnitCategory


class UnitRepository:
    """Repository for unit operations."""

    def __init__(self, db: Session):
        self.db = db

    # Unit Categories
    def get_unit_categories(self) -> List[UnitCategory]:
        """Get all unit categories."""
        return self.db.query(UnitCategory).all()

    def get_unit_category_by_name(self, name: str) -> Optional[UnitCategory]:
        """Get unit category by name."""
        return self.db.query(UnitCategory).filter(UnitCategory.name == name).first()

    def create_unit_category(
        self,
        name: str,
        base_unit: str,
        description: str = None,
        special_conversions: bool = False,
    ) -> UnitCategory:
        """Create a new unit category."""
        category = UnitCategory(
            name=name,
            base_unit=base_unit,
            description=description,
            special_conversions=special_conversions,
        )
        self.db.add(category)
        self.db.commit()
        self.db.refresh(category)
        return category

    # Units
    def get_units(
        self, category_name: str = None, active_only: bool = True
    ) -> List[Unit]:
        """Get units, optionally filtered by category."""
        query = self.db.query(Unit)

        if active_only:
            query = query.filter(Unit.is_active == True)

        if category_name:
            query = query.join(UnitCategory).filter(UnitCategory.name == category_name)

        return query.all()

    def get_unit_by_symbol(self, symbol: str) -> Optional[Unit]:
        """Get unit by symbol."""
        return (
            self.db.query(Unit)
            .filter(and_(Unit.symbol == symbol, Unit.is_active == True))
            .first()
        )

    def create_unit(
        self,
        category_id: int,
        symbol: str,
        name: str,
        conversion_factor: float,
        conversion_offset: float = 0,
        aliases: List[str] = None,
        metadata: Dict[str, Any] = None,
    ) -> Unit:
        """Create a new unit."""
        unit = Unit(
            category_id=category_id,
            symbol=symbol,
            name=name,
            conversion_factor=conversion_factor,
            conversion_offset=conversion_offset,
            aliases=aliases or [],
            unit_metadata=metadata or {},
        )
        self.db.add(unit)
        self.db.commit()
        self.db.refresh(unit)
        return unit

    def update_unit(self, unit_id: int, **kwargs) -> Optional[Unit]:
        """Update a unit."""
        unit = self.db.query(Unit).filter(Unit.id == unit_id).first()
        if unit:
            for key, value in kwargs.items():
                if hasattr(unit, key):
                    setattr(unit, key, value)
            self.db.commit()
            self.db.refresh(unit)
        return unit

    def delete_unit(self, unit_id: int) -> bool:
        """Soft delete a unit."""
        unit = self.db.query(Unit).filter(Unit.id == unit_id).first()
        if unit:
            unit.is_active = False
            self.db.commit()
            return True
        return False

    # Conversion Rules
    def get_conversion_rules(self, active_only: bool = True) -> List[ConversionRule]:
        """Get all conversion rules."""
        query = self.db.query(ConversionRule)
        if active_only:
            query = query.filter(ConversionRule.is_active == True)
        return query.all()

    def get_conversion_rule(
        self, from_unit: str, to_unit: str
    ) -> Optional[ConversionRule]:
        """Get conversion rule between two units."""
        return (
            self.db.query(ConversionRule)
            .filter(
                and_(
                    ConversionRule.from_unit == from_unit,
                    ConversionRule.to_unit == to_unit,
                    ConversionRule.is_active == True,
                )
            )
            .first()
        )

    def create_conversion_rule(
        self,
        from_unit: str,
        to_unit: str,
        formula: str,
        reverse_formula: str = None,
        description: str = None,
        conditions: Dict[str, Any] = None,
        metadata: Dict[str, Any] = None,
    ) -> ConversionRule:
        """Create a new conversion rule."""
        rule = ConversionRule(
            from_unit=from_unit,
            to_unit=to_unit,
            formula=formula,
            reverse_formula=reverse_formula,
            description=description,
            conditions=conditions or {},
            rule_metadata=metadata or {},
        )
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)
        return rule
