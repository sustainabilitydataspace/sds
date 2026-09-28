#!/usr/bin/env python3
"""
Migration script to convert legacy Crosswalk data to standard-agnostic StandardMapping.
"""

from __future__ import annotations

from typing import List

from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.sql import func

from src.database.base import Base
from src.database.models import StandardMapping, SustainabilityStandard
from src.database.session import SessionLocal


class LegacyCrosswalk(Base):
    """Legacy E2 ESRS-GRI crosswalk table (read-only mapping)."""

    __tablename__ = "crosswalks"

    id = Column(Integer, primary_key=True)
    esrs_code = Column(String(50), nullable=False, index=True)
    gri_code = Column(String(150), index=True)
    gri_expanded = Column(String(200))
    esg_dimension = Column(String(20), index=True)
    dataset = Column(String(100))
    indicator = Column(Text)
    relationship_type = Column(String(20))
    source_row = Column(Integer)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())


def _seed_standards() -> List[SustainabilityStandard]:
    return [
        SustainabilityStandard(
            id="ESRS",
            name="European Sustainability Reporting Standards",
            organization="EFRAG",
        ),
        SustainabilityStandard(
            id="GRI",
            name="Global Reporting Initiative Standards",
            organization="GRI",
        ),
        SustainabilityStandard(
            id="ISSB",
            name="IFRS Sustainability Disclosure Standards",
            organization="ISSB/IFRS",
        ),
        SustainabilityStandard(
            id="SASB",
            name="Sustainability Accounting Standards",
            organization="SASB/IFRS",
        ),
        SustainabilityStandard(
            id="CDP",
            name="Carbon Disclosure Project",
            organization="CDP",
        ),
        SustainabilityStandard(
            id="TCFD",
            name="Task Force on Climate-related Financial Disclosures",
            organization="FSB",
        ),
        SustainabilityStandard(
            id="UN_SDG",
            name="UN Sustainable Development Goals",
            organization="United Nations",
        ),
    ]


def migrate_crosswalks() -> None:
    """Migrate existing Crosswalk records to StandardMapping."""
    db = SessionLocal()
    try:
        for std in _seed_standards():
            existing = db.query(SustainabilityStandard).filter_by(id=std.id).first()
            if not existing:
                db.add(std)

        crosswalks = (
            db.query(LegacyCrosswalk)
            .filter(LegacyCrosswalk.is_active.is_(True))
            .all()
        )

        for cw in crosswalks:
            mapping = StandardMapping(
                source_standard="ESRS",
                source_code=cw.esrs_code,
                source_label=cw.indicator,
                target_standard="GRI",
                target_code=cw.gri_code,
                target_label=cw.gri_expanded,
                esg_dimension=cw.esg_dimension,
                relationship_type=cw.relationship_type or "equivalent",
                confidence=1.0,
                dataset=cw.dataset,
                source_row=cw.source_row,
                is_active=cw.is_active,
                created_at=cw.created_at,
            )
            db.add(mapping)

        db.commit()
        print(f"Migrated {len(crosswalks)} crosswalks to standard_mappings")
    finally:
        db.close()


if __name__ == "__main__":
    migrate_crosswalks()
