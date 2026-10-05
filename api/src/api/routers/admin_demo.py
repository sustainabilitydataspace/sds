"""Admin install and status of the bundled demo A2.3 package.

Bearer ADMIN with ``manage_system`` only. The package is fixed in the deployed
code: requests choose no files, paths, tenants or content.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from rdflib import Graph
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

import structlog
from src.api.rate_limit import limiter
from src.api.safe_validation_route import SecretSafeValidationRoute
from src.auth.authorization import require_bearer_authentication
from src.auth.dependencies import get_current_active_user, require_permission
from src.auth.models import Permission, User, UserRole
from src.database.session import get_db_optional
from src.ontology.local_graph import get_ontology_graph
from src.services import demo_package

logger = structlog.get_logger(__name__)

router = APIRouter(
    route_class=SecretSafeValidationRoute,
    dependencies=[Depends(require_permission(Permission.MANAGE_SYSTEM))],
)

DEMO_CONCEPT = "urn:sds:reg:esrs:e1_5_02"
DEMO_INPUTS = sorted(
    f"urn:sds:reg:esrs:e1_5_{n}" for n in ("10", "11", "12", "13", "14")
)
DEMO_EXPECTED = Decimal("832.964")


def _admin_actor(current_user: User, request: Request) -> demo_package.Actor:
    require_bearer_authentication(current_user)
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo administration requires an admin bearer token",
        )
    return demo_package.Actor(
        user_id=current_user.id,
        auth_method=current_user.auth_method or "bearer",
        request_id=getattr(request.state, "request_id", None),
    )


def _require_db(db: Optional[Session]) -> Session:
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo administration requires the database",
        )
    return db


def _raise(exc: demo_package.DemoPackageError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message)


def _check(fn) -> Dict[str, Any]:
    try:
        return fn()
    except Exception as exc:  # each check is independent and reported
        return {"ok": False, "error_type": type(exc).__name__}


def run_demo_checks(db: Session, *, graph: Graph, converter) -> Dict[str, Any]:
    """Read-only V1-V5 checks through the services the public routes use.

    Synchronous: routes call it through ``run_in_threadpool`` (V3 drives the
    async calculation runner with ``asyncio.run`` in that worker thread).
    """
    from src.api.models import CalculationRequest
    from src.api.routers.calculations import (
        _dependencies_from_contract,
        _run_calculation,
    )
    from src.calculation.contracts import RuntimeCalculationContractResolver
    from src.database.models import Indicator
    from src.services.hierarchy_store import DatabaseHierarchyStore
    from src.services.runtime_execution import build_conversion_engine
    from src.services.standard_mapping_store import StandardMappingStore
    from src.services.value_store import DatabaseValueStore

    def v1():
        row = (
            db.query(Indicator)
            .filter(Indicator.identifier == DEMO_CONCEPT, Indicator.is_active.is_(True))
            .first()
        )
        return {"ok": bool(row and row.title and row.code_esrs and row.dimension)}

    def v2():
        values, total = DatabaseValueStore(db, tenant_id=demo_package.TENANT).list(
            concept=None,
            entity="nh_group",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            unit=None,
            changed_since=None,
            limit=50,
            offset=0,
        )
        return {"ok": total == 9, "count": total}

    def v3():
        import asyncio

        request = CalculationRequest(
            concept=DEMO_CONCEPT,
            entity="nh_group",
            period="2024",
            granularity="annual",
            include_trace=True,
        )
        response = asyncio.run(
            _run_calculation(
                request,
                graph=graph,
                store=DatabaseValueStore(db, tenant_id=demo_package.TENANT),
                db=db,
                contract_resolver=RuntimeCalculationContractResolver(db=db),
                unit_normalizer=converter,
                conversion_engine=build_conversion_engine(db, converter),
                hierarchy_store=DatabaseHierarchyStore(db),
                hierarchy_company_id=demo_package.TENANT,
                mapping_store=StandardMappingStore(db=db),
            )
        )
        value = Decimal(str(response.value)).quantize(Decimal("0.001"))
        return {
            "ok": value == DEMO_EXPECTED,
            "value": str(value),
            "unit": response.unit,
            "formula": response.formula_used or "",
        }

    def v4():
        rows = StandardMappingStore(db=db).search(
            source_standard="ESRS",
            target_standard="GRI",
            target_code="GRI 305",
            limit=10,
        )
        return {"ok": len(rows) >= 1, "count": len(rows)}

    def v5():
        contract = RuntimeCalculationContractResolver(db=db).resolve(DEMO_CONCEPT)
        dependencies = _dependencies_from_contract(DEMO_CONCEPT, contract)
        inputs = sorted(dependencies["required_variables"])
        return {"ok": inputs == DEMO_INPUTS, "inputs": inputs}

    checks = {
        "V1_indicator_with_codes": _check(v1),
        "V2_values": _check(v2),
        "V3_calculation": _check(v3),
        "V4_mappings": _check(v4),
        "V5_dependencies": _check(v5),
    }
    db.rollback()
    return checks


def _status(db: Session, *, graph: Graph, converter) -> Dict[str, Any]:
    try:
        package = demo_package.load_package()
    except demo_package.DemoPackageError as exc:
        return {
            "package_id": demo_package.PACKAGE_ID,
            "integrity": "failed",
            "detail": exc.message,
        }
    return {
        "package_id": package.package_id,
        "package_digest": package.digest,
        "integrity": "ok",
        "tenant": demo_package.TENANT,
        "components": demo_package.component_status(db, package),
        "checks": run_demo_checks(db, graph=graph, converter=converter),
        "history": demo_package.ledger_history(db),
    }


def _install(
    db: Session, actor: demo_package.Actor, *, graph: Graph, converter
) -> Dict[str, Any]:
    package = demo_package.load_package()
    result = demo_package.install(
        db, package, actor=actor, converter=converter, graph=graph
    )
    checks = run_demo_checks(db, graph=graph, converter=converter)
    ok = all(item.get("ok") for item in checks.values())
    state = demo_package.record_verification(
        db, result["ledger_id"], ok=ok, checks=checks
    )
    return dict(result, state=state, checks=checks)


@router.get(
    "/demo-packages/a23",
    summary="Get the demo A2.3 package status",
    description=(
        "Integrity of the bundled demo package, read-only state of each component, "
        "the V1-V5 functional checks and the last install attempts."
    ),
)
@limiter.limit("20/minute")
async def get_demo_package_status(
    request: Request,
    graph: Graph = Depends(get_ontology_graph),
    current_user: User = Depends(get_current_active_user),
    db: Optional[Session] = Depends(get_db_optional),
) -> Dict[str, Any]:
    _admin_actor(current_user, request)
    db = _require_db(db)
    converter = getattr(request.app.state, "unit_converter", None)
    return await run_in_threadpool(_status, db, graph=graph, converter=converter)


@router.post(
    "/demo-packages/a23/install",
    summary="Install the demo A2.3 package",
    description=(
        "Install the bundled demo package in one transaction (confirm=true). Shared "
        "data is reused, demo-owned data must match exactly (409 otherwise); then "
        "runs the V1-V5 checks. Takes no other parameters."
    ),
)
@limiter.limit("2/minute")
async def install_demo_package(
    request: Request,
    confirm: bool = Query(False),
    graph: Graph = Depends(get_ontology_graph),
    current_user: User = Depends(get_current_active_user),
    db: Optional[Session] = Depends(get_db_optional),
) -> Dict[str, Any]:
    actor = _admin_actor(current_user, request)
    if set(request.query_params) - {"confirm"} or await request.body():
        raise HTTPException(
            status_code=422, detail="This operation takes no parameters"
        )
    if not confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to install")
    db = _require_db(db)
    converter = getattr(request.app.state, "unit_converter", None)
    try:
        result = await run_in_threadpool(
            _install, db, actor, graph=graph, converter=converter
        )
    except demo_package.DemoPackageError as exc:
        logger.warning(
            "admin_demo_install_refused", step=exc.step, actor_user_id=actor.user_id
        )
        _raise(exc)
    logger.info(
        "admin_demo_installed",
        actor_user_id=actor.user_id,
        state=result["state"],
        ledger_id=result.get("ledger_id"),
    )
    return result
