"""PROBLEMA 1 (ONTO-CONTRACT) contract tests — calculation semantics on concept detail.

Disposable-PG tests proving that ``GET /api/v1/ontology/concepts/{uri}`` (via the service +
repository read path) surfaces the Atomizer calculation-contract semantics (formula_kind,
dimensions, aggregation, calculation_contract_id) and a non-null formula/related_variables for a
concept whose indicator has an active **publicly-registered** contract — while concepts without a
public contract, and concepts whose active contract is non-public (audit_only/container/...),
serialize exactly as before (all new fields null). Gated on the disposable PG env vars.

Root cause fixed: the read path now joins ``Concept.indicator_id`` ->
``CanonicalCalculationContract`` (active, exposure=public_register) which is deterministic via the
unique partial index ``ix_canonical_calc_contracts_active_indicator``.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from rdflib import Graph
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.database import models as m
from src.database.repositories.concept_repository import ConceptRepository
from src.services.concept_service import ConceptService


def _disposable_session() -> Session:
    database_url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    from sqlalchemy import text

    from src.database.init_db import init_db_for_engine

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    init_db_for_engine(engine)
    return Session(bind=engine)


def _seed_indicator(db: Session, indicator_id: str) -> None:
    db.add(
        m.Indicator(
            id=indicator_id,
            identifier=f"urn:sds:reg:{indicator_id}",
            title=f"Indicator {indicator_id}",
            dimension="ENVIRONMENTAL",
        )
    )


def _seed_concept(db: Session, *, uri: str, indicator_id: str | None) -> m.Concept:
    concept = m.Concept(
        uri=uri,
        label="Hazardous waste to disposal",
        taxonomy="CSRD",
        concept_type="Indicator",
        indicator_id=indicator_id,
        projection_source="indicator_catalog",
    )
    db.add(concept)
    return concept


def _seed_contract(
    db: Session,
    *,
    indicator_id: str,
    exposure: str,
    contract_hash: str,
    with_children: bool = True,
) -> m.CanonicalCalculationContract:
    package = m.AtomizerPackageImport(package_hash=f"hash-{contract_hash[:8]}")
    db.add(package)
    db.flush()
    contract = m.CanonicalCalculationContract(
        package_import_id=package.id,
        contract_version="2026.1",
        model_id="ESRS-E5",
        node_id=f"node-{contract_hash[:8]}",
        indicator_id=indicator_id,
        indicator_identifier=f"urn:sds:reg:{indicator_id}",
        label="E5-5_09 hazardous waste to disposal",
        exposure=exposure,
        runtime_status="semantic_only",
        formula_kind="ratio",
        semantic_expression="hazardous_to_disposal / total_waste",
        aggregation_policy={"temporal": "SUM", "perimeter": "SUM"},
        contract_hash=contract_hash,
        is_active=True,
    )
    db.add(contract)
    db.flush()
    if with_children:
        # deliberately out of dimension_id order to prove deterministic sorting
        db.add(
            m.CanonicalCalculationDimension(
                contract_id=contract.id,
                package_import_id=package.id,
                dimension_id="waste_treatment_type",
                mode="enum",
                member_values=["disposal", "recovery"],
                required=True,
            )
        )
        db.add(
            m.CanonicalCalculationDimension(
                contract_id=contract.id,
                package_import_id=package.id,
                dimension_id="waste_hazard_status",
                mode="enum",
                member_values=["hazardous", "non_hazardous"],
                required=True,
            )
        )
        db.add(
            m.CanonicalCalculationComponent(
                contract_id=contract.id,
                package_import_id=package.id,
                component_order=0,
                component_id="c0",
                variable_uri="hazardous_to_disposal",
            )
        )
        db.add(
            m.CanonicalCalculationComponent(
                contract_id=contract.id,
                package_import_id=package.id,
                component_order=1,
                component_id="c1",
                variable_uri="total_waste",
            )
        )
    db.flush()
    return contract


def test_public_concept_exposes_public_contract_semantics():
    db = _disposable_session()
    try:
        _seed_indicator(db, "ind-pub")
        _seed_concept(db, uri="csrd:E5-5_09", indicator_id="ind-pub")
        _seed_contract(
            db,
            indicator_id="ind-pub",
            exposure="public_register",
            contract_hash="a" * 64,
        )
        db.commit()

        detail = ConceptService(db=db).get_concept_by_uri(
            "csrd:E5-5_09", public_catalog=True
        )
        assert detail is not None
        assert detail["formula_kind"] == "ratio"
        assert detail["calculation_contract_id"] == "a" * 64
        assert detail["calculation_contract_version"] == "2026.1"
        assert detail["aggregation"] == {"temporal": "SUM", "perimeter": "SUM"}
        # dimensions deterministically sorted by dimension_id
        dim_ids = [d["dimension_id"] for d in detail["dimensions"]]
        assert dim_ids == ["waste_hazard_status", "waste_treatment_type"]
        assert all(d["required"] is True for d in detail["dimensions"])
        # formula + related_variables fall back to the contract when not projected
        assert detail["formula"] == "hazardous_to_disposal / total_waste"
        assert detail["related_variables"] == ["hazardous_to_disposal", "total_waste"]
    finally:
        db.close()


def test_concept_without_contract_is_backward_compatible():
    db = _disposable_session()
    try:
        _seed_indicator(db, "ind-none")
        _seed_concept(db, uri="csrd:NO_CONTRACT", indicator_id="ind-none")
        db.commit()

        detail = ConceptService(db=db).get_concept_by_uri(
            "csrd:NO_CONTRACT", public_catalog=True
        )
        assert detail is not None
        assert detail.get("formula_kind") is None
        assert detail.get("dimensions") is None
        assert detail.get("aggregation") is None
        assert detail.get("calculation_contract_id") is None
        assert detail.get("formula") is None
        assert detail["related_variables"] == []
    finally:
        db.close()


def test_public_path_does_not_leak_non_public_contract():
    """codex P1 M1: an active audit_only (non-public) contract must NOT surface publicly."""
    db = _disposable_session()
    try:
        _seed_indicator(db, "ind-audit")
        _seed_concept(db, uri="csrd:AUDIT_ONLY", indicator_id="ind-audit")
        _seed_contract(
            db, indicator_id="ind-audit", exposure="audit_only", contract_hash="b" * 64
        )
        db.commit()

        service = ConceptService(db=db)
        public = service.get_concept_by_uri("csrd:AUDIT_ONLY", public_catalog=True)
        assert public.get("formula_kind") is None
        assert public.get("dimensions") is None
        assert public.get("calculation_contract_id") is None
        assert public.get("formula") is None

        # the internal (non-public) path may still surface it
        internal = service.get_concept_by_uri("csrd:AUDIT_ONLY", public_catalog=False)
        assert internal.get("calculation_contract_id") == "b" * 64
        assert internal.get("formula_kind") == "ratio"
    finally:
        db.close()


def test_repo_active_contract_selection_is_gated_and_deterministic():
    db = _disposable_session()
    try:
        _seed_indicator(db, "ind-pub")
        _seed_contract(
            db,
            indicator_id="ind-pub",
            exposure="public_register",
            contract_hash="c" * 64,
        )
        db.commit()
        repo = ConceptRepository(db)

        assert repo.get_active_calculation_contract(None) is None
        assert repo.get_active_calculation_contract("missing") is None
        pub = repo.get_active_calculation_contract("ind-pub", public_only=True)
        assert pub is not None and pub.contract_hash == "c" * 64

        # audit_only indicator: gated out for public_only, returned otherwise
        _seed_indicator(db, "ind-audit")
        _seed_contract(
            db, indicator_id="ind-audit", exposure="audit_only", contract_hash="d" * 64
        )
        db.commit()
        assert (
            repo.get_active_calculation_contract("ind-audit", public_only=True) is None
        )
        assert (
            repo.get_active_calculation_contract("ind-audit", public_only=False)
            is not None
        )
    finally:
        db.close()


# --- HTTP/router-boundary coverage (codex P1 acceptance M2) -------------------
# Adding the five optional contract fields to ConceptInfo is an ADDITIVE,
# non-breaking API change: no-contract responses gain the keys as null (the same
# pattern VARCH-8e used for `pins`, since the routes do not set
# response_model_exclude_none). These tests lock that accepted contract at the
# serialized-model boundary.


@pytest.mark.asyncio
async def test_concept_detail_http_shape_is_additive_without_contract():
    import src.api.routers.ontology as ontology

    svc = MagicMock()
    svc.has_semantic_data.return_value = True
    svc.get_concept_by_uri.return_value = {
        "uri": "csrd:NO_CONTRACT",
        "label": "No contract",
        "taxonomy": "CSRD",
        "concept_type": "Indicator",
        "related_variables": [],
    }

    concept = await ontology.get_concept_details(
        "csrd:NO_CONTRACT",
        graph=Graph(),
        concept_service=svc,
        current_user=MagicMock(),
    )
    dumped = concept.model_dump()
    for field in (
        "formula_kind",
        "dimensions",
        "aggregation",
        "calculation_contract_id",
        "calculation_contract_version",
    ):
        assert field in dumped, f"{field} must be present (additive contract)"
        assert dumped[field] is None, f"{field} must serialize null without a contract"


@pytest.mark.asyncio
async def test_concept_detail_http_exposes_contract_fields_when_present():
    import src.api.routers.ontology as ontology

    svc = MagicMock()
    svc.has_semantic_data.return_value = True
    svc.get_concept_by_uri.return_value = {
        "uri": "csrd:E5-5_09",
        "label": "Hazardous waste to disposal",
        "taxonomy": "CSRD",
        "concept_type": "Indicator",
        "related_variables": ["hazardous_to_disposal", "total_waste"],
        "formula": "hazardous_to_disposal / total_waste",
        "formula_kind": "ratio",
        "dimensions": [
            {"dimension_id": "waste_hazard_status", "mode": "enum", "required": True},
            {"dimension_id": "waste_treatment_type", "mode": "enum", "required": True},
        ],
        "aggregation": {"temporal": "SUM", "perimeter": "SUM"},
        "calculation_contract_id": "a" * 64,
        "calculation_contract_version": "2026.1",
    }

    concept = await ontology.get_concept_details(
        "csrd:E5-5_09",
        graph=Graph(),
        concept_service=svc,
        current_user=MagicMock(),
    )
    assert concept.formula_kind == "ratio"
    assert concept.calculation_contract_id == "a" * 64
    assert concept.calculation_contract_version == "2026.1"
    assert concept.aggregation == {"temporal": "SUM", "perimeter": "SUM"}
    assert [d["dimension_id"] for d in concept.dimensions] == [
        "waste_hazard_status",
        "waste_treatment_type",
    ]
