from __future__ import annotations

from sqlalchemy.orm import Session

from src.calculation.conversion.fx import FXConverter
from src.calculation.conversion.orchestrator import ConversionEngine
from src.calculation.unit_converter import UnitConverter
from src.database.repositories.fx_repository import FXRepository


def build_conversion_engine(
    db_session: Session, unit_converter: UnitConverter
) -> ConversionEngine:
    fx_repository = FXRepository(db_session)
    return ConversionEngine(
        physical_converter=unit_converter.get_physical_converter(),
        fx_converter=FXConverter(fx_repository),
        fx_policy_resolver=fx_repository.get_policy,
    )
