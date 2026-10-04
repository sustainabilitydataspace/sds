"""
FastAPI main application for SustainabilityDataSpace API.
"""

import asyncio
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

import structlog
from src.api.metrics import READINESS_STATE
from src.api.middleware import (
    IndicatorJobAdmissionMiddleware,
    RequestBodyLimitMiddleware,
    ValueJobAdmissionMiddleware,
    setup_error_handling,
    setup_logging_middleware,
    setup_security_headers_middleware,
)
from src.api.openapi_docs import enrich_openapi_schema
from src.api.openapi_localization import (
    DEFAULT_LANGUAGE,
    language_options,
    localize_openapi_schema,
    normalize_language,
)
from src.api.rate_limit import limiter
from src.api.routers import (
    admin_catalog,
    auth,
    calculations,
    fx,
    hierarchies,
    indicators,
    interoperability,
    mapping_assertions,
    mappings,
    ontology,
    semantic_dimensions,
    units,
    values,
)
from src.auth.dependencies import require_permission
from src.auth.models import Permission
from src.config.settings import settings
from src.logging_setup import configure_runtime_logging

logger = structlog.get_logger(__name__)


def _normalized_portal_mount_path() -> str:
    mount_path = (settings.portal_mount_path or "/portal").strip()
    if not mount_path.startswith("/"):
        mount_path = f"/{mount_path}"
    return mount_path.rstrip("/") or "/portal"


def _portal_static_directory() -> Path:
    configured = (settings.portal_mount_directory or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    container_candidate = Path("/app/portal")
    if container_candidate.exists():
        return container_candidate

    module_path = Path(__file__).resolve()
    return module_path.parent / "portal-missing"


_PORTAL_MOUNT_PATH = _normalized_portal_mount_path()
_PORTAL_DIR = _portal_static_directory()


def _portal_entry_url() -> str:
    return f"{_PORTAL_MOUNT_PATH}/home.html"


_PLACEHOLDER_SECRET_MARKERS = (
    "change_me",
    "changeme",
    "your-secret",
    "your_secret",
    "placeholder",
)


def _looks_like_placeholder_secret(value: str) -> bool:
    """Detect obvious copied-from-example placeholder secrets."""
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_SECRET_MARKERS)


def _validate_security_settings() -> None:
    """Validate security-sensitive settings for production-like deployments."""
    if not settings.require_database:
        if not settings.allow_in_memory_auth:
            raise RuntimeError(
                "REQUIRE_DATABASE=false requires explicit ALLOW_IN_MEMORY_AUTH=true; "
                "this local-only mode has no durable identity or session state"
            )
        configured_host = settings.host.strip().lower()
        try:
            is_loopback = (
                configured_host == "localhost"
                or ip_address(configured_host).is_loopback
            )
        except ValueError:
            is_loopback = False
        if not is_loopback:
            raise RuntimeError(
                "ALLOW_IN_MEMORY_AUTH is local-only and requires a loopback HOST"
            )
        return

    _jwt_val = settings.jwt_secret_key.get_secret_value()
    if not _jwt_val or _jwt_val == "your-secret-key-change-in-production":
        raise RuntimeError("JWT_SECRET_KEY must be set to a strong, non-default value")
    if len(_jwt_val) < 32:
        raise RuntimeError("JWT_SECRET_KEY must be at least 32 characters")
    if _looks_like_placeholder_secret(_jwt_val):
        raise RuntimeError(
            "JWT_SECRET_KEY still contains a placeholder value (e.g. 'change_me'); "
            "set a strong random secret before enabling REQUIRE_DATABASE"
        )

    if settings.seed_default_users:
        _admin_pw = (
            settings.bootstrap_admin_password.get_secret_value()
            if settings.bootstrap_admin_password is not None
            else ""
        )
        if _admin_pw and _looks_like_placeholder_secret(_admin_pw):
            raise RuntimeError(
                "BOOTSTRAP_ADMIN_PASSWORD still contains a placeholder value "
                "(e.g. 'change_me'); set a strong password when SEED_DEFAULT_USERS=true "
                "and REQUIRE_DATABASE=true"
            )
        if len(_admin_pw) < 16:
            raise RuntimeError(
                "BOOTSTRAP_ADMIN_PASSWORD must be at least 16 characters when "
                "SEED_DEFAULT_USERS=true"
            )
        if len(set(_admin_pw)) < 8:
            raise RuntimeError(
                "BOOTSTRAP_ADMIN_PASSWORD must have sufficient character diversity"
            )

    if "*" in (settings.allowed_origins or []):
        raise RuntimeError(
            "CORS allowed_origins cannot include '*' when REQUIRE_DATABASE=true"
        )


def _should_use_postgres_units() -> bool:
    """Resolve the authoritative unit backend for this app instance."""
    if settings.require_database:
        return True
    return settings.use_postgres_units is True


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    configure_runtime_logging(settings.log_level)
    logger.info("Starting SustainabilityDataSpace API")

    # Initialize components
    try:
        _validate_security_settings()
        use_postgres_units = _should_use_postgres_units()

        # VM/production: enforce a real DB-backed runtime when required.
        if settings.require_database or use_postgres_units:
            from src.database.init_db import init_db, ping_db
            from src.database.session import SessionLocal

            logger.info(
                "Database-backed unit mode enabled, validating connection and initializing schema",
                require_database=settings.require_database,
                use_postgres_units=use_postgres_units,
            )
            ping_db()
            init_db(externally_managed=settings.schema_migrations_externally_managed)
            logger.info("Database ready")

            if settings.require_database:
                # Controlled maintenance only: old instances/workers must be
                # stopped and a single writer enforced before starting this API.
                try:
                    from src.services.canonical_mapping_package_job_store import (
                        DatabaseCanonicalMappingPackageJobStore,
                    )
                    from src.services.value_import_job_store import (
                        DatabaseValueImportJobStore,
                    )

                    db_session = SessionLocal()
                    try:
                        reconciled_count = DatabaseValueImportJobStore(
                            db_session
                        ).reconcile_legacy_active_jobs()
                        mapping_reconciled_count = (
                            DatabaseCanonicalMappingPackageJobStore(
                                db_session
                            ).reconcile_legacy_active_jobs()
                        )
                    finally:
                        db_session.close()
                except Exception:
                    # Neither application logs nor the server's startup traceback
                    # may expose driver errors, SQL parameters or retained data.
                    raise RuntimeError(
                        "Startup job reconciliation could not complete; "
                        "startup refused"
                    ) from None
                logger.info(
                    "Legacy value job reconciliation completed",
                    reconciled_count=reconciled_count,
                )
                logger.info(
                    "Legacy canonical mapping job reconciliation completed",
                    reconciled_count=mapping_reconciled_count,
                )

            if settings.require_database and settings.seed_default_users:
                try:
                    from src.database.bootstrap import bootstrap_default_admin

                    db_session = SessionLocal()
                    try:
                        created = bootstrap_default_admin(db_session)
                    finally:
                        db_session.close()

                    if created:
                        logger.warning(
                            "Bootstrapped initial admin user",
                            username=settings.bootstrap_admin_username,
                        )
                except Exception as e:
                    logger.error("Failed to bootstrap default users", error=str(e))
                    raise

            try:
                db_session = SessionLocal()
                try:
                    if (
                        settings.require_database
                        and settings.seed_reference_data_on_startup
                    ):
                        from src.database.bootstrap_reference_data import (
                            bootstrap_reference_data_if_empty,
                        )

                        bootstrap_summary = bootstrap_reference_data_if_empty(
                            db_session
                        )
                        logger.info(
                            "Reference data bootstrap checked", **bootstrap_summary
                        )

                    semantic_summary = None
                    if settings.require_database:
                        from src.database.bootstrap_semantic_model import (
                            bootstrap_semantic_model_if_empty,
                        )
                        from src.services.semantic_concept_projector import (
                            SemanticConceptProjector,
                        )

                        semantic_summary = bootstrap_semantic_model_if_empty(db_session)
                        semantic_projection_summary = (
                            SemanticConceptProjector(db_session).project().as_dict()
                        )
                    else:
                        semantic_projection_summary = None

                    from src.database.bootstrap_units import bootstrap_units_if_empty

                    units_summary = bootstrap_units_if_empty(db_session)
                    from src.database.bootstrap_conversion_catalog import (
                        bootstrap_conversion_catalog_if_empty,
                    )

                    conversion_catalog_summary = bootstrap_conversion_catalog_if_empty(
                        db_session
                    )
                finally:
                    db_session.close()

                logger.info("Units bootstrap checked", **units_summary)
                logger.info(
                    "Conversion catalog bootstrap checked",
                    currencies_created=conversion_catalog_summary["currencies_created"],
                    policies_created=conversion_catalog_summary["policies_created"],
                    rate_observations_created=conversion_catalog_summary[
                        "rate_observations_created"
                    ],
                    rate_periods_created=conversion_catalog_summary[
                        "rate_periods_created"
                    ],
                )
                if semantic_summary is not None:
                    logger.info("Semantic model bootstrap checked", **semantic_summary)
                if semantic_projection_summary is not None:
                    logger.info(
                        "Semantic catalog projection checked",
                        **semantic_projection_summary,
                    )
            except Exception as e:
                logger.error("Failed to bootstrap database-backed units", error=str(e))
                raise

        # Initialize Unit Converter after database init/bootstrap so DB-backed mode is safe.
        from src.calculation.unit_converter import UnitConverter

        if use_postgres_units:
            from src.calculation.postgres_strategy import PostgresStrategy

            app.state.unit_converter = UnitConverter(
                storage_strategy=PostgresStrategy()
            )
        else:
            app.state.unit_converter = UnitConverter()

        storage_info = app.state.unit_converter.get_storage_info()
        if use_postgres_units and storage_info["units_count"] == 0:
            raise RuntimeError(
                "PostgreSQL unit storage selected but zero units were loaded. "
                "Bootstrap units before serving requests."
            )
        logger.info(
            "Unit converter initialized",
            backend=storage_info["backend"],
            units_count=storage_info["units_count"],
            read_only=storage_info["read_only"],
        )

        logger.info("API components initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize API components", error=str(e))
        raise

    yield

    # Shutdown
    logger.info("Shutting down SustainabilityDataSpace API")

    # Close unit converter database connections
    if hasattr(app.state, "unit_converter"):
        try:
            storage_strategy = app.state.unit_converter._storage_strategy
            if hasattr(storage_strategy, "close"):
                storage_strategy.close()
                logger.info("Unit converter connections closed")
        except Exception as e:
            logger.warning("Error closing unit converter connections", error=str(e))
        finally:
            delattr(app.state, "unit_converter")

    # Reset ephemeral in-memory stores between lifespans (tests/offline mode).
    for attr in ("value_store", "hierarchy_store", "user_store", "api_key_store"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


# Create FastAPI application
app = FastAPI(
    title="SustainabilityDataSpace API",
    description="""
    ## Unified ESG Ontology API

    The SustainabilityDataSpace API provides a comprehensive solution for managing Environmental, Social, and Governance (ESG) data through an integrated ontology that unifies current reporting taxonomies and operational SDS concepts:

    * **CSRD** - Corporate Sustainability Reporting Directive (European regulatory framework)
    * **GRI** - Global Reporting Initiative (voluntary global standards)
    * **GHG Protocol** - Greenhouse gas accounting metrics and calculation guidance
    * **Sygris** - Operational variables for sustainability data collection

    ### Key Features

    * **Automatic Unit Conversion** - Convert across a broad bundled unit catalog grouped in 16 categories
    * **Hierarchical Aggregation** - Organizational and temporal data aggregation
    * **Ontology Integration** - Semantic relationships between taxonomies
    * **Calculation Engine** - Automated ESG indicator calculations
    * **RBAC Security** - Role-based access control with JWT authentication

    ### Quick Start

    1. **Authenticate**: Use `/auth/login` to get access tokens
    2. **Configure Hierarchy**: Set up organizational structure via `/api/v1/hierarchies`
    3. **Insert Data**: Add ESG values using `/api/v1/values`
    4. **Calculate Indicators**: Compute metrics via `/api/v1/calculate`
    5. **Explore Ontology**: Query concepts and equivalences

    ### Support

    * **Interactive Docs**: [/docs](/docs) - Try API endpoints directly
    * **Alternative Docs**: [/redoc](/redoc) - Clean documentation interface
    * **OpenAPI Spec**: [/openapi.json](/openapi.json) - Machine-readable specification
    * **GitHub**: [sustainabilityds/api](https://github.com/sustainabilityds/api)
    * **Support**: api-support@sustainabilitydataspace.com
    """,
    version=settings.app_version,
    contact={
        "name": "SustainabilityDataSpace API Team",
        "url": "https://sustainabilitydataspace.com",
        "email": "api-support@sustainabilitydataspace.com",
    },
    servers=[
        {"url": "/", "description": "Current server (auto-detect)"},
        {
            "url": "http://localhost:8090",
            "description": "Development server (localhost)",
        },
        {
            "url": "https://api.sustainabilitydataspace.com",
            "description": "Production server",
        },
    ],
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Serve bundled UI assets (offline-safe docs).
_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# Serve versioned JSON-LD contexts (offline-safe; publishable URLs).
_CONTEXTS_DIR = _STATIC_DIR / "contexts"
app.mount("/contexts", StaticFiles(directory=str(_CONTEXTS_DIR)), name="contexts")
app.mount("/context", StaticFiles(directory=str(_CONTEXTS_DIR)), name="context")

if settings.portal_mount_enabled:
    if _PORTAL_DIR.exists():

        @app.get(_PORTAL_MOUNT_PATH, include_in_schema=False)
        @app.get(f"{_PORTAL_MOUNT_PATH}/", include_in_schema=False)
        async def portal_entry():
            """Convenience entrypoint for the same-origin prototype portal."""
            return RedirectResponse(url=_portal_entry_url(), status_code=307)

        app.mount(
            _PORTAL_MOUNT_PATH,
            StaticFiles(directory=str(_PORTAL_DIR), html=True),
            name="portal",
        )
    else:
        logger.warning(
            "Portal mount directory does not exist", directory=str(_PORTAL_DIR)
        )

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Register admission first so CORS/logging/security wrap its early response,
# while it still rejects DB job requests before routing or receiving bodies.
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_bytes=settings.request_max_body_bytes,
)
app.add_middleware(ValueJobAdmissionMiddleware)
app.add_middleware(IndicatorJobAdmissionMiddleware)

# Only add CORS middleware when origins are explicitly configured
if settings.allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=(
            settings.cors_allow_credentials
            if "*" not in settings.allowed_origins
            else False
        ),
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Setup logging and error handling middleware
setup_logging_middleware(app)
setup_security_headers_middleware(app)
setup_error_handling(app)

# Include routers
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(values.router, prefix="/api/v1", tags=["Values"])
app.include_router(calculations.router, prefix="/api/v1", tags=["Calculations"])
app.include_router(
    hierarchies.router, prefix="/api/v1/hierarchies", tags=["Hierarchies"]
)
app.include_router(ontology.router, prefix="/api/v1", tags=["Ontology"])
app.include_router(
    semantic_dimensions.router, prefix="/api/v1", tags=["Semantic Dimensions"]
)
app.include_router(units.router, prefix="/api/v1", tags=["Units"])
app.include_router(
    admin_catalog.router, prefix="/api/v1/admin", tags=["Catalog administration"]
)
app.include_router(fx.router, prefix="/api/v1", tags=["FX"])
app.include_router(
    interoperability.router,
    prefix="/api/v1/interoperability",
    tags=["Interoperability"],
)

# E5 Integration routers (E1/E2/E6 -> E5)
app.include_router(indicators.router, prefix="/api/v1/indicators", tags=["Indicators"])
app.include_router(mappings.router, prefix="/api/v1/mappings", tags=["Mappings"])
app.include_router(
    mapping_assertions.router,
    prefix="/api/v1/internal/canonical-mapping-packages",
    tags=["Internal Mapping Assertions"],
)


def _with_swagger_mobile_css(response: HTMLResponse) -> HTMLResponse:
    """Keep stock Swagger UI within the mobile viewport."""
    html = response.body.decode("utf-8")
    asset_version = "sds-swagger-mobile-v1"
    if "/static/swagger-ui/sds-mobile.css" not in html:
        mobile_asset = (
            f'<link rel="stylesheet" href="/static/swagger-ui/sds-mobile.css'
            f'?v={asset_version}">'
        )
        html = html.replace("</head>", f"{mobile_asset}\n</head>")
    return HTMLResponse(content=html, status_code=response.status_code)


def _with_swagger_language_selector(
    response: HTMLResponse, selected: str
) -> HTMLResponse:
    """Inject a language selector that reloads the docs with ?lang=<code>.

    Switching reloads ``/docs?lang=xx`` (full page), which points Swagger UI at the
    localized ``/openapi.localized.json?lang=xx`` spec — no client-side spec
    rewriting, so it stays offline-safe and contract-faithful.
    """
    html = response.body.decode("utf-8")
    options = "".join(
        '<option value="{code}"{sel}>{label}</option>'.format(
            code=opt["code"],
            label=opt["label"],
            sel=" selected" if opt["code"] == selected else "",
        )
        for opt in language_options()
    )
    selector_label = (
        "Idioma de la documentación API" if selected == "es" else "API docs language"
    )
    selector = (
        '<div id="sds-lang-selector" '
        'style="position:fixed;top:8px;right:12px;z-index:10000;'
        "font-family:sans-serif;font-size:13px;background:#fff;padding:4px 6px;"
        'border:1px solid #ccc;border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,.15)">'
        '<label for="sds-lang" style="margin-right:4px">\U0001f310</label>'
        f'<select id="sds-lang" aria-label="{selector_label}" '
        "onchange=\"window.location.search='?lang='+this.value\">"
        f"{options}</select></div>"
    )
    html = html.replace("<body>", f"<body>\n{selector}", 1)
    return HTMLResponse(content=html, status_code=response.status_code)


@app.get("/docs", include_in_schema=False)
async def docs(lang: str = DEFAULT_LANGUAGE):
    """Swagger UI (offline-safe; no external CDNs) with a language selector."""
    selected = normalize_language(lang)
    openapi_url = (
        app.openapi_url
        if selected == DEFAULT_LANGUAGE
        else f"/openapi.localized.json?lang={selected}"
    )
    response = get_swagger_ui_html(
        openapi_url=openapi_url,
        title=f"{app.title} - API Docs",
        oauth2_redirect_url="/docs/oauth2-redirect",
        swagger_js_url="/static/swagger-ui/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger-ui/swagger-ui.css",
        swagger_favicon_url="/static/swagger-ui/favicon-32x32.png",
        swagger_ui_parameters={"tryItOutEnabled": True},
    )
    return _with_swagger_language_selector(_with_swagger_mobile_css(response), selected)


@app.get("/openapi.localized.json", include_in_schema=False)
async def localized_openapi(lang: str = DEFAULT_LANGUAGE):
    """Localized OpenAPI spec: human-facing text translated, machine contract intact.

    The canonical ``/openapi.json`` is unchanged; this variant localizes
    human-facing docs text while paths, operationIds, schemas, params, and enums
    remain identical, so generated clients stay compatible across languages.
    """
    return JSONResponse(localize_openapi_schema(app.openapi(), lang))


@app.get("/docs/oauth2-redirect", include_in_schema=False)
async def swagger_ui_redirect():
    """Swagger UI OAuth2 redirect endpoint."""
    return get_swagger_ui_oauth2_redirect_html()


@app.get("/redoc", include_in_schema=False)
async def redoc():
    """ReDoc UI (offline-safe; no external CDNs)."""
    return get_redoc_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - ReDoc",
        redoc_js_url="/static/redoc/redoc.standalone.js",
        redoc_favicon_url="/static/swagger-ui/favicon-32x32.png",
        with_google_fonts=False,
    )


@app.get("/", response_model=Dict[str, Any])
async def root():
    """Root endpoint with API information."""
    return {
        "name": "SustainabilityDataSpace API",
        "version": settings.app_version,
        "description": "API para gestión de ontología ESG unificada",
        "docs": "/docs",
        "redoc": "/redoc",
        "portal": (
            _portal_entry_url()
            if settings.portal_mount_enabled and _PORTAL_DIR.exists()
            else None
        ),
        "status": "running",
    }


@app.get(
    "/ready",
    response_model=Dict[str, Any],
    dependencies=[Depends(require_permission(Permission.VIEW_SYSTEM_HEALTH))],
)
async def readiness_check():
    """Readiness check endpoint."""
    status_code, payload = await _bounded_readiness_status()
    return JSONResponse(status_code=status_code, content=payload)


def _readiness_status() -> tuple[int, Dict[str, Any]]:
    """Evaluate the complete dependency and semantic readiness contract."""
    status_code, payload = _dependency_health()
    semantic_status = None
    if status_code == 200 and settings.require_database:
        semantic_status = _semantic_projection_readiness()
        payload["semantic_projection"] = semantic_status
        if semantic_status["status"] != "healthy":
            status_code = 503
    payload["components"] = {
        name: data["status"] for name, data in payload["dependencies"].items()
    }
    payload["dependency_status"] = payload["status"]
    payload["status"] = "ready" if status_code == 200 else "unhealthy"
    return status_code, payload


def _semantic_projection_readiness() -> Dict[str, Any]:
    """Return readiness status for active-indicator semantic projection."""
    from src.database.session import SessionLocal
    from src.services.semantic_concept_projector import SemanticConceptProjector

    db_session = SessionLocal()
    try:
        coverage = SemanticConceptProjector(db_session).coverage_summary()
    except Exception:
        return {
            "status": "unhealthy",
            "error_code": "semantic_projection_unavailable",
        }
    finally:
        db_session.close()

    status = "healthy" if coverage.get("ready") else "unhealthy"
    return {"status": status, **coverage}


_health_probe_slots = threading.BoundedSemaphore(2)


async def _bounded_readiness_status() -> tuple[int, Dict[str, Any]]:
    if not _health_probe_slots.acquire(blocking=False):
        return 503, {"status": "unhealthy"}

    def run_probe():
        try:
            return _readiness_status()
        finally:
            _health_probe_slots.release()

    return await asyncio.to_thread(run_probe)


def _dependency_health() -> tuple[int, Dict[str, Any]]:
    dependencies: Dict[str, Dict[str, str]] = {}
    required_unhealthy = False

    # Database — required when REQUIRE_DATABASE=true OR when the unit backend is
    # PostgreSQL-backed (USE_POSTGRES_UNITS), since the runtime then depends on it.
    if settings.require_database or _should_use_postgres_units():
        try:
            from src.database.init_db import ping_db

            ping_db()
            dependencies["database"] = {"status": "healthy"}
        except Exception:
            dependencies["database"] = {"status": "unhealthy"}
            required_unhealthy = True
    else:
        dependencies["database"] = {"status": "disabled"}

    if required_unhealthy:
        overall = "unhealthy"
        status_code = 503
    else:
        overall = "healthy"
        status_code = 200

    payload = {
        "status": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dependencies": dependencies,
    }
    return status_code, payload


@app.get("/healthz", response_model=Dict[str, Any])
@limiter.exempt
async def healthz(request: Request):
    """Health endpoint with dependency status (offline-safe when deps disabled)."""
    if not _health_probe_slots.acquire(blocking=False):
        return JSONResponse(status_code=503, content={"status": "unhealthy"})

    def run_probe():
        try:
            return _dependency_health()
        finally:
            _health_probe_slots.release()

    status_code, payload = await asyncio.to_thread(run_probe)
    payload["request_id"] = getattr(request.state, "request_id", None)
    return JSONResponse(status_code=status_code, content=payload)


@app.get(
    "/metrics",
    include_in_schema=False,
    dependencies=[Depends(require_permission(Permission.VIEW_SYSTEM_HEALTH))],
)
async def metrics():
    """Prometheus metrics endpoint."""
    readiness_status, _ = await _bounded_readiness_status()
    READINESS_STATE.set(1 if readiness_status == 200 else 0)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def custom_openapi() -> Dict[str, Any]:
    """Generate OpenAPI once, then add user-facing endpoint guidance."""
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        summary=app.summary,
        description=app.description,
        routes=app.routes,
        webhooks=app.webhooks.routes,
        tags=app.openapi_tags,
        servers=app.servers,
        terms_of_service=app.terms_of_service,
        contact=app.contact,
        license_info=app.license_info,
        separate_input_output_schemas=app.separate_input_output_schemas,
    )
    app.openapi_schema = enrich_openapi_schema(openapi_schema)
    return app.openapi_schema


app.openapi = custom_openapi


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        log_level=settings.log_level.casefold(),
        proxy_headers=False,
    )
