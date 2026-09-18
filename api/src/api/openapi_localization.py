"""OpenAPI localization overlay for the SDS API docs surface.

Implements the OpenAPI-docs layer of the SDS API localization V1 design:
``/openapi.json`` stays canonical (English); a localized variant
(``/openapi.localized.json?lang=es``) may translate **human-facing description and
summary text only**, never the machine contract. The overlay therefore touches
``info.title`` / ``info.description``, tag descriptions, and per-operation
``summary`` / ``description`` — and NEVER ``operationId``, path keys, parameter
names, schema names, ``$ref`` targets, or enum values, so the spec stays
contract-compatible across languages.

Translation is keyed by the canonical English string (robust to operationId
churn). Spanish docs also replace generated operation guidance descriptions
with deterministic Spanish text, so Swagger does not show English guidance
fallbacks. This module is self-contained (no DB); the DB-backed translation
foundation for catalog content (concepts, etc.) is a separate slice.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Tuple

DEFAULT_LANGUAGE = "en"
#: Languages the docs surface can render. Extend by adding a `_SUMMARY_<lang>` /
#: `_INFO_<lang>` map below and registering it in `_TRANSLATIONS`.
SUPPORTED_LANGUAGES: Tuple[str, ...] = ("en", "es")
LANGUAGE_LABELS: Dict[str, str] = {"en": "English", "es": "Español"}


def normalize_language(lang: str | None) -> str:
    """Map a raw `lang` value to a supported language code, else the default.

    Accepts BCP-47-ish input (e.g. ``es-ES``) by taking the primary subtag.
    Unknown / unsupported languages fall back to ``DEFAULT_LANGUAGE`` so the
    endpoint never errors on an unexpected value.
    """
    if not lang:
        return DEFAULT_LANGUAGE
    primary = lang.strip().lower().replace("_", "-").split("-", 1)[0]
    return primary if primary in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def language_options() -> List[Dict[str, str]]:
    """Return ordered {code,label} options for the Swagger language selector."""
    return [
        {"code": code, "label": LANGUAGE_LABELS.get(code, code)}
        for code in SUPPORTED_LANGUAGES
    ]


_INFO_ES_TITLE = "API de SustainabilityDataSpace"

_INFO_ES_DESCRIPTION = """
    ## API de Ontología ESG Unificada

    La API de SustainabilityDataSpace ofrece una solución integral para gestionar datos Ambientales, Sociales y de Gobernanza (ESG) mediante una ontología integrada que unifica las taxonomías de reporte actuales y los conceptos operativos de SDS:

    * **CSRD** - Directiva de Información Corporativa en materia de Sostenibilidad (marco regulatorio europeo)
    * **GRI** - Global Reporting Initiative (estándares globales voluntarios)
    * **GHG Protocol** - Métricas de contabilidad de gases de efecto invernadero y guía de cálculo
    * **Sygris** - Variables operativas para la recopilación de datos de sostenibilidad

    ### Funcionalidades clave

    * **Conversión automática de unidades** - Convierte sobre un amplio catálogo de unidades agrupado en 16 categorías
    * **Agregación jerárquica** - Agregación de datos organizativa y temporal
    * **Integración de ontología** - Relaciones semánticas entre taxonomías
    * **Motor de cálculo** - Cálculo automatizado de indicadores ESG
    * **Seguridad RBAC** - Control de acceso basado en roles con autenticación JWT

    ### Inicio rápido

    1. **Autentíquese**: use `/auth/login` para obtener tokens de acceso
    2. **Configure la jerarquía**: defina la estructura organizativa vía `/api/v1/hierarchies`
    3. **Inserte datos**: añada valores ESG con `/api/v1/values`
    4. **Calcule indicadores**: calcule métricas vía `/api/v1/calculate`
    5. **Explore la ontología**: consulte conceptos y equivalencias

    ### Soporte

    * **Documentación interactiva**: [/docs](/docs) - Pruebe los endpoints directamente
    * **Documentación alternativa**: [/redoc](/redoc) - Interfaz de documentación limpia
    * **Especificación OpenAPI**: [/openapi.json](/openapi.json) - Especificación legible por máquina
    * **GitHub**: [sustainabilityds/api](https://github.com/sustainabilityds/api)
    * **Soporte**: api-support@sustainabilitydataspace.com
    """

#: Canonical-English operation summary -> Spanish. Missing keys fall back to English.
_SUMMARY_ES: Dict[str, str] = {
    "Activate hierarchy configuration": "Activar configuración de jerarquía",
    "Batch calculate indicators": "Calcular indicadores por lotes",
    "Browse published semantic dimensions (bitemporal public temporal read)": (
        "Explorar dimensiones semánticas publicadas (lectura temporal pública bitemporal)"
    ),
    "Calculate indicator": "Calcular indicador",
    "Change password": "Cambiar contraseña",
    "Check FX rate coverage": "Comprobar cobertura de tipos de cambio",
    "Check SDS interoperability runtime readiness": (
        "Comprobar disponibilidad del runtime de interoperabilidad SDS"
    ),
    "Convert between units": "Convertir entre unidades",
    "Create API key": "Crear clave de API",
    "Create a new value": "Crear un nuevo valor",
    "Create hierarchy configuration": "Crear configuración de jerarquía",
    "Create user": "Crear usuario",
    "Delete hierarchy configuration": "Eliminar configuración de jerarquía",
    "Delete value by ID": "Eliminar valor por ID",
    "Execute SPARQL query": "Ejecutar consulta SPARQL",
    "Export Indicators": "Exportar indicadores",
    "Export Mappings": "Exportar mapeos",
    "Export values": "Exportar valores",
    "Get Indicator": "Obtener indicador",
    "Get Indicators By Esrs": "Obtener indicadores por ESRS",
    "Get Indicators By Gri": "Obtener indicadores por GRI",
    "Get Mapping Package Import Job": "Obtener trabajo de importación de paquete de mapeo",
    "Get Mapping Package Validation Job": (
        "Obtener trabajo de validación de paquete de mapeo"
    ),
    "Get Mappings Between": "Obtener mapeos entre",
    "Get Mappings From": "Obtener mapeos desde",
    "Get Mappings To": "Obtener mapeos hacia",
    "Get calculation dependencies": "Obtener dependencias de cálculo",
    "Get concept details": "Obtener detalles del concepto",
    "Get concept equivalences": "Obtener equivalencias del concepto",
    "Get current user": "Obtener usuario actual",
    "Get hierarchy configuration": "Obtener configuración de jerarquía",
    "Get indicator import job status": (
        "Obtener estado del trabajo de importación de indicadores"
    ),
    "Get indicator import row errors": (
        "Obtener errores de fila de importación de indicadores"
    ),
    "Get token information": "Obtener información del token",
    "Get value by ID": "Obtener valor por ID",
    "Get value import job status": "Obtener estado del trabajo de importación de valores",
    "Get values with filters": "Obtener valores con filtros",
    "Healthz": "Estado de salud",
    "Import controlled FX rates rows": "Importar filas controladas de tipos de cambio",
    "Import controlled FX rate rows": "Importar filas controladas de tipos de cambio",
    "Import values as one batch": "Importar valores en un solo lote",
    "Import values from CSV": "Importar valores desde CSV",
    "Indicator Changes": "Cambios de indicadores",
    "Indicator Diff": "Diferencias de indicadores",
    "Indicator History": "Historial de indicadores",
    "Indicator Manifest": "Manifiesto de indicadores",
    "Inspect Mapping Assertion Package": "Inspeccionar paquete de aserciones de mapeo",
    "List API keys": "Listar claves de API",
    "List Indicators": "Listar indicadores",
    "List Mappings": "Listar mapeos",
    "List Supported Standards": "Listar estándares soportados",
    "List active FX policies": "Listar políticas de cambio activas",
    "List active currencies": "Listar monedas activas",
    "List available taxonomies": "Listar taxonomías disponibles",
    "List concepts": "Listar conceptos",
    "List hierarchy configurations": "Listar configuraciones de jerarquía",
    "List supported units": "Listar unidades soportadas",
    "List value revisions": "Listar revisiones de valor",
    "Mapping Changes": "Cambios de mapeos",
    "Mapping Diff": "Diferencias de mapeos",
    "Mapping History": "Historial de mapeos",
    "Mapping Manifest": "Manifiesto de mapeos",
    "Preview FX conversion": "Previsualizar conversión de divisas",
    "Preview Mapping Package Materialization": (
        "Previsualizar materialización de paquete de mapeo"
    ),
    "Read the persisted values changed feed": (
        "Leer el feed de cambios de valores persistidos"
    ),
    "Read value context lineage": "Leer el linaje de contexto del valor",
    "Read value revision change events": (
        "Leer eventos de cambio de revisión de valor"
    ),
    "Read value revision lineage": "Leer el linaje de revisión de valor",
    "Readiness Check": "Comprobación de disponibilidad",
    "Refresh access token": "Renovar token de acceso",
    "Resolve one value through SDS runtime evidence": (
        "Resolver un valor mediante la evidencia del runtime SDS"
    ),
    "Revoke API key": "Revocar clave de API",
    "Root": "Raíz",
    "Search Indicators": "Buscar indicadores",
    "Search Mappings": "Buscar mapeos",
    "Search concepts (translation-aware)": "Buscar conceptos con traducciones",
    "Concept localization readiness": "Disponibilidad de localización de conceptos",
    "Submit Mapping Package Import Job": (
        "Enviar trabajo de importación de paquete de mapeo"
    ),
    "Submit an async CSV values import job": (
        "Enviar un trabajo asíncrono de importación de valores CSV"
    ),
    "Submit an async indicator register import job": (
        "Enviar un trabajo asíncrono de importación de registro de indicadores"
    ),
    "Submit an async values import job": (
        "Enviar un trabajo asíncrono de importación de valores"
    ),
    "Update current user": "Actualizar usuario actual",
    "Update hierarchy configuration": "Actualizar configuración de jerarquía",
    "User login": "Inicio de sesión",
    "User logout": "Cierre de sesión",
    "Validate Mapping Package Against Installed Standards": (
        "Validar paquete de mapeo contra estándares instalados"
    ),
    "Validate an indicator register CSV": "Validar un CSV de registro de indicadores",
    "Validate unit compatibility": "Validar compatibilidad de unidades",
    "Value export manifest": "Manifiesto de exportación de valores",
}

_EXAMPLE_SUMMARY_ES: Dict[str, str] = {
    "nordhaven_2024_energy_value": "Valor energético Nordhaven 2024",
    "nordhaven_2024_energy_history": "Histórico energético Nordhaven 2024",
    "energy_input": "Entrada energética para conversión de unidades",
    "nordhaven_operational_perimeter": "Perímetro operativo Nordhaven",
    "esrs_e1_5_fossil_energy_sum": "Calcular suma de energía fósil ESRS E1-5",
    "resolve_csrd_to_gri_water": "Resolver agua CSRD hacia agua GRI",
    "resolve_gri_302_1e_from_certified_bridge": (
        "Resolver GRI 302-1.e con puente certificado"
    ),
    "refuse_energy_to_emissions": "Rechazar energía a emisiones sin factor",
    "resolve_energy_kwh_to_mwh": "Resolver energía con conversión de unidades",
    "kilowatt_hours_to_megawatt_hours": "Kilovatios hora a megavatios hora",
    "liters_to_cubic_meters": "Litros a metros cúbicos",
    "usd_to_eur_monthly_average_preview": "Vista previa USD a EUR con media mensual",
    "single_rate": "Un tipo de cambio controlado",
}

_EXAMPLE_DESCRIPTION_ES: Dict[str, str] = {
    "nordhaven_2024_energy_value": (
        "Cuerpo JSON para Swagger con la observación energética anual cargada "
        "del grupo Nordhaven 2024."
    ),
    "nordhaven_2024_energy_history": (
        "Cuerpo JSON con filas energéticas del grupo Nordhaven procedentes del "
        "conjunto operativo cargado."
    ),
    "energy_input": ("Cuerpo JSON para probar la ruta de resolución de kWh a MWh."),
    "nordhaven_operational_perimeter": (
        "Cuerpo JSON para crear el grupo Nordhaven y una planta usados por los "
        "ejemplos de valores."
    ),
    "esrs_e1_5_fossil_energy_sum": (
        "Cuerpo JSON Nordhaven 2024 para una fórmula trazable con varias "
        "entradas: `e1_5_10 + e1_5_11 + e1_5_12 + e1_5_13 + e1_5_14`."
    ),
    "resolve_csrd_to_gri_water": (
        "Cuerpo JSON para resolver un valor mediante un mapeo exacto o " "equivalente."
    ),
    "resolve_gri_302_1e_from_certified_bridge": (
        "Cuerpo JSON para calcular el valor GRI 302-1.e solicitado a partir de "
        "entradas ESRS autorizadas. Una respuesta correcta debe mostrar "
        "`execution_authority` = `certified_bridge` y un `bridge_id` con el "
        "contrato de cálculo aprobado."
    ),
    "refuse_energy_to_emissions": (
        "Cuerpo JSON que muestra que convertir kWh a t CO2e necesita un "
        "contrato de factor de emisiones."
    ),
    "resolve_energy_kwh_to_mwh": (
        "Cuerpo JSON para conversión energética dentro de la misma dimensión, "
        "sin factor de emisiones."
    ),
    "kilowatt_hours_to_megawatt_hours": (
        "Cuerpo JSON para probar una conversión de energía."
    ),
    "liters_to_cubic_meters": (
        "Cuerpo JSON para previsualizar una conversión de unidades."
    ),
    "usd_to_eur_monthly_average_preview": (
        "Cuerpo JSON para previsualizar una conversión FX con la orientación "
        "del histórico BCE cargado: moneda extranjera hacia EUR."
    ),
    "single_rate": "Cuerpo JSON para una fila controlada de tipo de cambio.",
}

#: Per-language registry. Each entry: info overrides + summary map.
_TRANSLATIONS: Dict[str, Dict[str, Any]] = {
    "es": {
        "info_title": _INFO_ES_TITLE,
        "info_description": _INFO_ES_DESCRIPTION,
        "summaries": _SUMMARY_ES,
        "example_summaries": _EXAMPLE_SUMMARY_ES,
        "example_descriptions": _EXAMPLE_DESCRIPTION_ES,
    }
}


def localize_openapi_schema(schema: Dict[str, Any], lang: str) -> Dict[str, Any]:
    """Return a deep copy of ``schema`` with human-facing text localized to ``lang``.

    ``info`` text, operation summaries, and generated operation guidance
    descriptions are localized. The machine contract — paths, operationIds,
    parameter/schema names, ``$ref`` targets, enum values — is never altered.
    Unknown languages and English return a canonical copy unchanged.
    """
    normalized = normalize_language(lang)
    localized = copy.deepcopy(schema)
    if normalized == DEFAULT_LANGUAGE:
        return localized
    table = _TRANSLATIONS.get(normalized)
    if not table:
        return localized

    info = localized.get("info")
    if isinstance(info, dict):
        if table.get("info_title"):
            info["title"] = table["info_title"]
        if table.get("info_description"):
            info["description"] = table["info_description"]

    summaries = table.get("summaries", {})
    for path, path_item in localized.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if not isinstance(operation, dict):
                continue
            summary = operation.get("summary")
            if summary and summary in summaries:
                operation["summary"] = summaries[summary]
            if normalized == "es":
                operation["description"] = _spanish_operation_description(
                    path=path,
                    method=str(method).upper(),
                    operation=operation,
                )
                _localize_common_response_descriptions(operation)
                _localize_request_examples(operation, table)
    return localized


def _spanish_operation_description(
    *, path: str, method: str, operation: Dict[str, Any]
) -> str:
    """Build Spanish Swagger guidance without changing the API contract."""
    summary = str(operation.get("summary") or f"{method} {path}").rstrip(".")
    sections: List[str] = []
    if path.startswith("/api/v1/internal/"):
        sections.append(
            "Uso interno/administrativo: este endpoint forma parte de flujos "
            "controlados de administración de SDS y no del recorrido normal de "
            "consumo público."
        )

    sections.append(f"### Para qué sirve\nFunción principal: {summary}.")
    sections.append(f"### Cómo usarlo\n{_spanish_how_to_use(method, operation)}")
    example = _spanish_example(method, operation)
    if example:
        sections.append(f"### Ejemplo\n{example}")
    sections.append(f"**Autenticación:** {_spanish_auth_note(path, operation)}")
    return "\n\n".join(sections)


def _spanish_how_to_use(method: str, operation: Dict[str, Any]) -> str:
    parameters = operation.get("parameters") or []
    path_parameters = [
        parameter.get("name")
        for parameter in parameters
        if isinstance(parameter, dict) and parameter.get("in") == "path"
    ]
    query_parameters = [
        parameter.get("name")
        for parameter in parameters
        if isinstance(parameter, dict) and parameter.get("in") == "query"
    ]
    header_parameters = [
        parameter.get("name")
        for parameter in parameters
        if isinstance(parameter, dict) and parameter.get("in") == "header"
    ]
    has_body = bool(operation.get("requestBody"))

    actions: List[str] = []
    if path_parameters:
        actions.append(
            "rellene los parámetros de ruta "
            + ", ".join(f"`{name}`" for name in path_parameters if name)
        )
    if query_parameters:
        actions.append(
            "use los filtros de consulta necesarios y deje en blanco los opcionales "
            "que no apliquen"
        )
    if header_parameters:
        actions.append("complete las cabeceras indicadas cuando el flujo lo requiera")
    if has_body:
        actions.append(
            "revise el esquema del cuerpo y, si hay ejemplos, empiece desde el "
            "ejemplo que corresponda al caso"
        )

    if not actions:
        actions.append("abra la operación y ejecútela sin campos adicionales")

    verb_note = {
        "GET": "lectura",
        "POST": "creación, cálculo o envío",
        "PUT": "actualización completa",
        "PATCH": "actualización parcial",
        "DELETE": "eliminación controlada",
    }.get(method, "ejecución")
    return (
        f"Es una operación de {verb_note}. En Swagger, {', '.join(actions)}, "
        "compruebe la respuesta y conserve los identificadores devueltos si "
        "necesita encadenar otro endpoint."
    )


def _spanish_example(method: str, operation: Dict[str, Any]) -> str | None:
    request_body = operation.get("requestBody") or {}
    content = request_body.get("content") if isinstance(request_body, dict) else None
    has_named_examples = False
    if isinstance(content, dict):
        for media in content.values():
            if isinstance(media, dict) and media.get("examples"):
                has_named_examples = True
                break

    parameters = operation.get("parameters") or []
    has_parameter_examples = any(
        isinstance(parameter, dict)
        and ("example" in parameter or "examples" in parameter)
        for parameter in parameters
    )

    if has_named_examples:
        return (
            "Seleccione uno de los ejemplos del cuerpo de petición, ajuste solo "
            "los campos necesarios y ejecute la llamada."
        )
    if has_parameter_examples:
        return (
            "Los campos muestran valores de ejemplo cuando existe un valor "
            "estable; si un identificador está vacío, cópielo desde una respuesta "
            "previa de listado, creación o trabajo asíncrono."
        )
    if method == "DELETE":
        return (
            "Antes de ejecutar, verifique que el identificador corresponde al "
            "recurso que desea eliminar."
        )
    return None


def _spanish_auth_note(path: str, operation: Dict[str, Any]) -> str:
    if path in {"/auth/login", "/healthz", "/ready", "/"}:
        return "No requiere token bearer para la llamada básica."
    if path == "/auth/refresh":
        return "Use el `refresh_token` obtenido en el inicio de sesión."
    if operation.get("security"):
        return (
            "Use el botón de autorización de Swagger con un token bearer obtenido "
            "en `/auth/login` antes de ejecutar la operación."
        )
    return "No requiere token bearer normalmente."


def _localize_common_response_descriptions(operation: Dict[str, Any]) -> None:
    translations = {
        "Successful Response": "Respuesta correcta",
        "Validation Error": "Error de validación",
        "OK": "Correcto",
    }
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return
    for response in responses.values():
        if not isinstance(response, dict):
            continue
        description = response.get("description")
        if description in translations:
            response["description"] = translations[description]


def _localize_request_examples(
    operation: Dict[str, Any], table: Dict[str, Any]
) -> None:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return
    content = request_body.get("content")
    if not isinstance(content, dict):
        return

    summaries = table.get("example_summaries", {})
    descriptions = table.get("example_descriptions", {})
    for media in content.values():
        if not isinstance(media, dict):
            continue
        examples = media.get("examples")
        if not isinstance(examples, dict):
            continue
        for example_key, example in examples.items():
            if not isinstance(example, dict):
                continue
            if example_key in summaries:
                example["summary"] = summaries[example_key]
            if example_key in descriptions:
                example["description"] = descriptions[example_key]
