"""Bootstrap bundled reference data into an empty database."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import text
from sqlalchemy.orm import Session

import structlog
from src.database.models import SustainabilityStandard
from src.database.repositories.indicator_repository import IndicatorRepository
from src.database.repositories.standard_mapping_repository import (
    StandardMappingRepository,
)

logger = structlog.get_logger(__name__)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
INDICATORS_JSON_PATH = DATA_DIR / "indicators.json"
MAPPINGS_JSON_PATH = DATA_DIR / "mappings.json"
REFERENCE_DATA_LOCK_KEY = 640_104_213

SUPPORTED_STANDARDS = [
    {
        "id": "ESRS",
        "name": "European Sustainability Reporting Standards",
        "organization": "EFRAG",
    },
    {
        "id": "GRI",
        "name": "Global Reporting Initiative Standards",
        "organization": "GRI",
    },
    {
        "id": "ISSB",
        "name": "IFRS Sustainability Disclosure Standards",
        "organization": "ISSB/IFRS",
    },
    {
        "id": "SASB",
        "name": "Sustainability Accounting Standards",
        "organization": "SASB/IFRS",
    },
    {
        "id": "CDP",
        "name": "Carbon Disclosure Project",
        "organization": "CDP",
    },
    {
        "id": "TCFD",
        "name": "Task Force on Climate-related Financial Disclosures",
        "organization": "FSB",
    },
    {
        "id": "UN_SDG",
        "name": "UN Sustainable Development Goals",
        "organization": "United Nations",
    },
]


def _load_records(path: Path, key: str) -> List[Dict[str, Any]]:
    if not path.exists():
        logger.warning("Reference data file missing", path=str(path), key=key)
        return []

    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    records = payload.get(key, [])
    if not isinstance(records, list):
        logger.warning(
            "Reference data payload has invalid shape", path=str(path), key=key
        )
        return []
    return [record for record in records if isinstance(record, dict)]


def _seed_supported_standards(db: Session) -> int:
    created = 0
    for standard in SUPPORTED_STANDARDS:
        existing = db.query(SustainabilityStandard).filter_by(id=standard["id"]).first()
        if existing:
            continue
        db.add(SustainabilityStandard(**standard))
        created += 1
    return created


def _with_postgres_lock(db: Session):
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return None

    db.execute(text("SELECT pg_advisory_lock(:key)"), {"key": REFERENCE_DATA_LOCK_KEY})
    return REFERENCE_DATA_LOCK_KEY


def _release_postgres_lock(db: Session, lock_key: int | None) -> None:
    if lock_key is None:
        return
    db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})


def bootstrap_reference_data_if_empty(
    db: Session,
    *,
    indicators_path: Path = INDICATORS_JSON_PATH,
    mappings_path: Path = MAPPINGS_JSON_PATH,
) -> Dict[str, int]:
    """Seed operator-supplied indicators and mappings when the database is empty.

    Public SDS deployments may keep third-party standard content outside the
    source tree. Missing packs leave the respective catalog empty so the
    operator can import an authorized package through the normal SDS flow.
    """

    lock_key = _with_postgres_lock(db)
    try:
        indicator_repo = IndicatorRepository(db)
        mapping_repo = StandardMappingRepository(db)

        indicators_before = indicator_repo.count()
        mappings_before = mapping_repo.count()
        standards_created = 0
        indicators_seeded = 0
        mappings_seeded = 0

        if indicators_before == 0:
            indicator_records = _load_records(indicators_path, "indicators")
            if indicator_records:
                indicators_seeded = indicator_repo.bulk_upsert(indicator_records)
                logger.info(
                    "Seeded bundled indicators into database",
                    count=indicators_seeded,
                    path=str(indicators_path),
                )

        if mappings_before == 0:
            mapping_records = _load_records(mappings_path, "mappings")
            if mapping_records:
                standards_created = _seed_supported_standards(db)
                mappings_seeded = mapping_repo.bulk_upsert(mapping_records)
                logger.info(
                    "Seeded bundled mappings into database",
                    count=mappings_seeded,
                    standards_created=standards_created,
                    path=str(mappings_path),
                )

        summary = {
            "indicators_before": indicators_before,
            "mappings_before": mappings_before,
            "indicators_seeded": indicators_seeded,
            "mappings_seeded": mappings_seeded,
            "standards_created": standards_created,
            "indicators_total": indicator_repo.count(),
            "mappings_total": mapping_repo.count(),
        }
        return summary
    finally:
        _release_postgres_lock(db, lock_key)
