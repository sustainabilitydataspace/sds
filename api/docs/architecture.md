# Architecture — SustainabilityDataSpace API

Visión general de la arquitectura, componentes y flujos de datos del API de Sustainability Data Space.

> Arquitectura soportada: runtime **DB-first** con PostgreSQL como plano canónico
> en modo estricto y proyección ontológica generada para consultas semánticas.

---

## Visión General

El API de Sustainability Data Space es un servicio **FastAPI** diseñado para:

1. **Almacenar datos ESG** (values) - Entradas de variables de sostenibilidad
2. **Transformar datos** (calculate) - Calcular indicadores desde variables base
3. **Exponer datos interoperables** - Compatibilidad con CSRD/ESRS y GRI sobre
   el modelo interno Sygris/SDS

---

## Diagrama de Arquitectura

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         API REST (FastAPI)                              │
│                        http://localhost:8090                            │
│                                                                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐   │
│  │   Values    │  │  Calculate  │  │   Ontology  │  │    Indicators│   │
│  │   /values   │  │  /calculate │  │  /concepts  │  │   /indicators│   │
│  └─────────────┘  └─────────────┘  └─────────────┘  └──────────────┘   │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐   │
│  │  Hierarchies│  │    Units    │  │  Crosswalks │  │   Auth       │   │
│  │/hierarchies │  │   /units    │  │ /crosswalks │  │   /auth      │   │
│  └─────────────┘  └─────────────┘  └─────────────┘  └──────────────┘   │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ Interoperability: readiness + value resolution                  │   │
│  │ /interoperability/readiness + /values/resolve                   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │
              ▼                     ▼
    ┌─────────────────┐  ┌────────────────────────────┐
    │   PostgreSQL    │  │   Ontology projection      │
    │ Port 5432 /     │  │ core_tbox + generated ABox │
    │ POSTGRES_PORT   │  │ local rdflib runtime       │
    │                 │  │                            │
    │ • Valores ESG   │  │ • Conceptos               │
    │ • Indicadores   │  │ • Equivalencias           │
    │ • Jerarquías    │  │ • Fórmulas / variables    │
    │ • Usuarios      │  │ • SPARQL local            │
    │ • Unidades      │  │                            │
    │ • Semántica     │  │                            │
    │                 │  │                            │
    │   CANÓNICO      │  │   DERIVADO DEL MODELO DB  │
    │   runtime       │  │   runtime                 │
    └─────────────────┘  └────────────────────────────┘
```

---

## Componentes Principales

### 1. API Layer (`src/api/`)

**Responsabilidad:** Exponer endpoints REST con validación, autenticación y autorización.

| Router | Endpoint Base | Función |
|--------|--------------|---------|
| Auth | `/auth/*` | Login, JWT, API keys, gestión de usuarios |
| Values | `/api/v1/values` | CRUD de valores ESG |
| Calculate | `/api/v1/calculate` | Motor de cálculo de indicadores |
| Indicators | `/api/v1/indicators` | Consulta, exportación e importación controlada de indicadores |
| Concepts | `/api/v1/concepts` | Ontología de conceptos ESG |
| Ontology | `/api/v1/ontology/*` | SPARQL, equivalencias, taxonomías |
| Hierarchies | `/api/v1/hierarchies` | Jerarquías organizativas/geográficas |
| Units | `/api/v1/units` | Conversión de unidades |
| Mappings | `/api/v1/mappings` | Mapeos estándar-agnósticos |
| Interoperability | `/api/v1/interoperability` | Readiness runtime para rutas manuales de interoperabilidad |

### 2. Authentication & Authorization (`src/auth/`)

**Responsabilidad:** Seguridad, autenticación y control de acceso.

```
┌─────────────────────────────────────────────────────────┐
│                    Auth System                          │
├─────────────────────────────────────────────────────────┤
│  JWT Handler     │  Genera/valida tokens JWT            │
│  RBAC            │  Control de acceso basado en roles   │
│  API Keys        │  Autenticación por clave de API      │
│  Policy Enforcer │  Políticas ODRL (E6)                 │
└─────────────────────────────────────────────────────────┘
```

**Roles disponibles:**
- `VIEWER`: Solo lectura
- `ANALYST`: Lectura + cálculos + SPARQL
- `DATA_MANAGER`: Escritura (valores/jerarquías)
- `ADMIN`: Administración completa

### 3. Calculation Engine (`src/calculation/`)

**Responsabilidad:** Calcular indicadores desde variables base.

```
┌─────────────────────────────────────────────────────────┐
│               Calculation Engine                        │
├─────────────────────────────────────────────────────────┤
│  safe_eval       │  Evaluación segura de fórmulas       │
│  engine          │  Motor de cálculo principal          │
│  aggregators     │  Agregaciones (SUM, AVG, MAX, etc.)  │
│  unit_converter  │  Conversión de unidades (facade)     │
│  conversion      │  Physical + FX conversion engine     │
└─────────────────────────────────────────────────────────┘
```

#### Conversion Engine

SDS separates physical unit conversion from monetary FX conversion. Physical
conversion is dimension-aware and deterministic. FX conversion is historical,
provider-backed, and policy-selected by date or reporting period. Runtime
calculation and value ingest call the shared `ConversionEngine`; they do not
select conversion rules directly.

La composición se crea por operación con la sesión SQLAlchemy que ya pertenece
a la petición o al proceso llamador. `runtime_execution.build_conversion_engine`
reutiliza el conversor físico del `UnitConverter` y conecta `FXRepository`,
`FXConverter` y la política FX sin retener una sesión global en el lifespan.
Las rutas de cálculo, resolución e ingesta síncrona reciben esa instancia; si
no hay sesión DB, no se crea un motor FX implícito.

**Flujo de cálculo:**
1. Recibir petición con concepto, entidad, período
2. Resolver dependencias (variables necesarias)
3. Buscar valores de variables en el período
4. Aplicar fórmula de cálculo
5. Retornar resultado con traza opcional

### 4. Data Layer (`src/database/`)

**Responsabilidad:** Persistencia y acceso a datos.

**Modelos principales:**
- `ESGValue`: Valores ESG con historial
- `Indicator`: Indicadores del registro E1
- `IndicatorImportJob`: Validaciones e importaciones asíncronas del catálogo de indicadores
- `HierarchyConfiguration`: Configuración de jerarquías
- `UserAccount`: Usuarios y autenticación
- `APIKeyRecord`: Claves de API
- `Unit/UnitCategory`: Sistema de unidades

**Patrón Repository:**
```python
IndicatorRepository     → Operaciones CRUD de indicadores
UserRepository          → Gestión de usuarios
HierarchyRepository     → Jerarquías organizativas
```

### 5. Ontology Services (`src/ontology/`)

**Responsabilidad:** Gestión de ontologías ESG.

```
┌─────────────────────────────────────────────────────────┐
│              Ontology Services                          │
├─────────────────────────────────────────────────────────┤
│  local_graph     │  Carga del split generado core/projection │
│  semantic_backfill │ Backfill semántico desde ontología congelada │
│  projection_generator │ Generación de proyección ontológica     │
└─────────────────────────────────────────────────────────┘
```

### 6. Services Layer (`src/services/`)

**Responsabilidad:** Lógica de negocio y coordinación.

| Servicio | Función |
|----------|---------|
| `indicator_store` | Almacenamiento de indicadores (DB canónica; fallback JSON solo en modo offline/no estricto) |
| `indicator_import` | Validación, normalización, planificación y aplicación transaccional de paquetes CSV de indicadores |
| `value_store` | Almacenamiento de valores ESG |
| `value_resolution` | Resolución runtime de valores por almacenamiento directo, cálculo, mapping equivalente y conversión soportada |
| `runtime_readiness` | Comprobación no destructiva de catálogos, mappings, contratos y conversiones necesarios para rutas manuales |
| `unit_service` | Conversión y gestión de unidades (DB-backed cuando `REQUIRE_DATABASE=true`) |
| `hierarchy_service` | Gestión de jerarquías |
| `concept_service` / `ontology_service` | Lectura de conceptos/equivalencias/taxonomías desde el modelo semántico canónico |
| `admin_catalog` | Importación de contratos de cálculo y reparación, corrección de factores e informe de impacto del catálogo de unidades (rutas `/api/v1/admin`) |
| `error_diagnostics` | Registro saneado y acotado de errores `500` por `request_id`, legible por administradores |
| `demo_package` | Verificación e instalación transaccional del paquete de demostración A2.3 |

---

## Flujo de Datos

### Flujo 1: Ingesta de Valores ESG

```
Cliente
   │ POST /api/v1/values
   │ {concept, entity, period, value, unit}
   ▼
Auth Middleware ──► JWT validation + RBAC
   │
   ▼
Values Router ──► Validación de input (Pydantic)
   │
   ▼
Value Store ──► Normalización de unidad
   │
   ▼
PostgreSQL ──► INSERT INTO esg_values
   │
   ▼
Response 201 Created
```

### Flujo 2: Cálculo de Indicador

```
Cliente
   │ POST /api/v1/calculate
   │ {concept, entity, period, granularity}
   ▼
Auth Middleware
   │
   ▼
Calculate Router
   │
   ├──► Ontology Service: Resolver dependencias
   │         └──► ¿Qué variables necesita este indicador?
   │
   ├──► Value Store: Buscar valores
   │         └──► SELECT * FROM esg_values WHERE concept IN (...)
   │
   ├──► Calculation Engine: Aplicar fórmula
   │         └──► safe_eval(formula, variables)
   │
   └──► Response con resultado + traza
```

En modo `REQUIRE_DATABASE=true`, el router de cálculo usa contratos de cálculo
canónicos cuando existen. Si no hay contrato canónico directo, el resolver puede
construir un contrato ejecutable desde el modelo semántico DB (`concepts`,
`concept_formulas`, `concept_variables`) siempre que la fórmula y las variables
estén cargadas. La ejecución recibe el normalizador de unidades del runtime para
evitar divergencias entre aliases como `m3` y `m³`.

### Flujo 2b: Resolución runtime de valores interoperables

`POST /api/v1/values/resolve` coordina las superficies existentes sin crear
datos nuevos. El endpoint usa `value_store`,
`RuntimeCalculationContractResolver`, `StandardMappingStore` y el catálogo de
unidades del runtime activo.

```
Cliente
   │ POST /api/v1/values/resolve
   │ {target_concept, source_concept?, entity, period, target_unit?}
   ▼
Auth + RBAC
   │ requiere read_values + execute_calculations + convert_units + read_mappings
   ▼
Value Resolution Service
   │
   ├──► 1. valor directo para target_concept/entity/period
   ├──► 2. cálculo ejecutable para target_concept
   ├──► 3. mapping exacto/equivalente desde source_concept o candidato
   ├──► 4. puente certificado para inputs no equivalentes autorizados
   └──► 5. conversión de unidad dentro de la misma dimensión física
   │
   ├──► resolved: devuelve valor, unidad, método y traza
   └──► fail-closed: not_found, not_enough_evidence,
        not_transformable, ambiguous_mapping o unsupported_conversion
```

Invariantes:

- Un mapping solo permite transferencia numérica si la relación publicada es
  exacta/equivalente. Relaciones parciales, broader/narrower, component o
  support-context quedan visibles como evidencia, pero no generan valor.
- La excepción controlada son los cálculos con puente certificado: un contrato
  puede autorizar componentes ESRS más estrechos, por ejemplo para devolver
  GRI 302-1.e, y entonces la respuesta expone `execution_authority` y
  `bridge_id`.
- Las conversiones son dimensionales. `kWh -> MWh` es una conversión física de
  energía; `GJ -> t CO2e` no lo es y debe fallar salvo que exista un contrato de
  cálculo con factor de emisión y trazabilidad.
- El resolver es genérico para cualquier indicador cargado con evidencia
  operativa suficiente; no inventa valores para indicadores que solo existen
  nominalmente en el catálogo.
- La ingesta estricta de valores valida `entity` contra jerarquías activas de
  la compañía antes de persistir. Un `Unknown entity` es una negativa cerrada
  correcta: primero debe existir el perímetro en `/api/v1/hierarchies`.

### Flujo 2c: Readiness de interoperabilidad

`GET /api/v1/interoperability/readiness` es una comprobación no destructiva para
operadores y pruebas manuales. Verifica que el runtime actual tiene:

- catálogos semánticos requeridos,
- conversión `L -> m3`,
- conversión `kWh -> MWh`,
- rechazo correcto de `GJ -> t CO2e` como conversión directa,
- mapping equivalente `csrd:E3-4_05 -> gri:303-5.c`,
- contrato ejecutable para `urn:sds:disclosure:csrd:e3-5`.

La respuesta `status=ready` confirma que esos checks concretos de
interoperabilidad pueden probarse en Swagger o por cliente HTTP. No certifica
automáticamente todos los indicadores del catálogo; cada indicador depende de
sus valores, contratos, mappings y unidades cargados.

### Flujo 3: Consulta de Indicadores

```
Cliente
   │ GET /api/v1/indicators?dimension=E&esrs=E1
   ▼
Auth Middleware
   │
   ▼
Indicators Router
   │
   ├──► Indicator Repository
   │         ├──► PostgreSQL canónica en modo DB-first
   │         └──► Fallback JSON solo en modo offline/no estricto
   │
   └──► Response con lista paginada
```

### Flujo 4: Importación de Indicadores por API

La validación síncrona de indicadores requiere `manage_indicators`. La admisión asíncrona en modo DB está bloqueada pendiente de H15; para importar se conserva el CLI soportado.

```
Cliente admin
   │ POST /api/v1/indicators/import-csv-validations
   │ multipart/form-data: file=<register.csv>
   ▼
Auth + RBAC ──► permiso manage_indicators
   │
   ▼
Upload Guard
   ├──► máximo configurable: INDICATOR_IMPORT_MAX_BYTES (default 10 MiB)
   └──► máximo configurable: INDICATOR_IMPORT_MAX_ROWS (default 10.000)
   │
   ▼
Indicator Import Service
   ├──► valida cabeceras del contrato CSV
   ├──► normaliza URNs legacy a identificadores canónicos
   ├──► compara contra PostgreSQL
   └──► devuelve plan: creates / updates / unchanged / rejected rows
```

La validación no escribe indicadores ni snapshots. Puede persistir metadatos del job y el payload subido; una validación correcta no habilita admisión asíncrona en DB. Los errores de fila se exponen de forma paginada para que el frontend pueda guiar la corrección.

```
Cliente admin
   │ POST /api/v1/indicators/import-csv-jobs
   │ validation_id=<id validado> o file=<register.csv>
   ▼
Guard ASGI (REQUIRE_DATABASE=true)
   └──► 503 application/json antes de recibir el cuerpo o resolver dependencias
```

El rechazo cubre CSV directo y `validation_id`. No adquiere locks, consulta
payloads, terminaliza jobs por timeout ni programa trabajo. CORS, logging y
cabeceras de seguridad envuelven el guard y se conservan en la respuesta.

H15 sigue abierto: el modelo persistido no tiene lease durable ni token de
fencing; el runner no reclama trabajo de forma durable ni recupera ejecución
tras reinicio. El lock de admisión y el timeout existentes no garantizan
exclusión de workers ni terminalización segura. Este slice no modifica
modelo, runner, store ni jobs existentes.

Endpoints públicos del flujo:

| Endpoint | Función |
|----------|---------|
| `POST /api/v1/indicators/import-csv-validations` | Subir y validar CSV sin modificar catálogo |
| `POST /api/v1/indicators/import-csv-jobs` | DB devuelve `503` antes de admisión (CSV o validation_id); H15 abierto |
| `GET /api/v1/indicators/import-jobs/{job_id}` | Consultar estado, resumen y resultado |
| `GET /api/v1/indicators/import-jobs/{job_id}/errors` | Consultar errores de fila paginados |

El CLI `scripts/import_indicators.py` sigue soportado para operaciones server-side y puede conservar `--resume` por lotes. El CLI y la validación API comparten parsing, normalización y validación. La admisión asíncrona API en DB permanece bloqueada; el comportamiento memoria/demo no cambia.

---

## Estrategia Offline-First

El sistema está diseñado para funcionar **sin infraestructura externa**:

| Componente | Modo Online | Modo Offline |
|------------|-------------|--------------|
| Base de datos | PostgreSQL | JSON en memoria/disco |
| Vector search | No aplica | El runtime productivo ya no usa vector DB separada |
| SPARQL | DB-backed pendiente; el modo estricto rechaza fallback local | rdflib local |
| Unidades | PostgreSQL (autoridad runtime si `REQUIRE_DATABASE=true`) | JSON (`units_database.json`) |

**Configuración para modo offline:**
```env
REQUIRE_DATABASE=false
USE_POSTGRES_UNITS=false
```

En modo DB-backed (`REQUIRE_DATABASE=true`), el arranque crea esquema y rellena, si hace falta, indicadores bundled, mappings bundled y el catálogo de unidades PostgreSQL antes de servir tráfico. En este modo estricto, el runtime semántico solo usa la proyección generada. `/api/v1/sparql` devuelve `503` si no hay semántica canónica DB-backed disponible y `501` mientras la ejecución SPARQL DB-backed siga pendiente; no usa el grafo local como fallback.

---

## Seguridad

### Capas de Seguridad

```
┌─────────────────────────────────────────┐
│  Layer 1: HTTPS/TLS (reverse proxy)     │
├─────────────────────────────────────────┤
│  Layer 2: JWT/API Key Authentication    │
├─────────────────────────────────────────┤
│  Layer 3: RBAC Authorization            │
├─────────────────────────────────────────┤
│  Layer 4: Policy Enforcement (ODRL)     │
├─────────────────────────────────────────┤
│  Layer 5: Input Validation (Pydantic)   │
├─────────────────────────────────────────┤
│  Layer 6: SQL Injection Prevention      │
│           (SQLAlchemy ORM)              │
└─────────────────────────────────────────┘
```

### Políticas ODRL (E6)

El sistema implementa 8 políticas deny-by-default:
- `reporting`: Datos para informes regulatorios
- `audit`: Datos para auditorías
- `research`: Uso en investigación
- `supervisory`: Acceso de supervisores
- `interop-testing`: Testing de interoperabilidad
- `cross-border`: Transferencia transfronteriza

---

## Escalabilidad

### Horizontal ( Stateless )

El API es stateless y puede escalarse horizontalmente:

```
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│  API Instance│◄────►│  API Instance│◄────►│  API Instance│
│     #1      │      │     #2      │      │     #3      │
└──────┬──────┘      └──────┬──────┘      └──────┬──────┘
       │                    │                    │
       └────────────────────┼────────────────────┘
                            ▼
                    ┌───────────────┐
                    │  PostgreSQL   │
                    │   (Primary)   │
                    └───────────────┘
```

### Caché

- **Resultados de cálculo:** TTL configurable
- **Ontología:** Carga en memoria al iniciar
- **Unidades:** Catálogo cargado desde PostgreSQL o JSON al iniciar, según backend resuelto

---

## Monitoreo

### Endpoints de Observabilidad

| Endpoint | Descripción |
|----------|-------------|
| `GET /healthz` | Health check del plano canónico |
| `GET /metrics` | Métricas Prometheus |

### Métricas Clave

- `http_requests_total` - Contador de requests HTTP
- `http_request_duration_seconds` - Latencia de requests
- `calculation_errors_total` - Errores en cálculos
- `db_connection_pool_size` - Tamaño del pool de conexiones

---

## Referencias

- **[Getting Started](getting-started.md)** - Puesta en marcha
- **[Configuration](configuration.md)** - Variables de entorno
- **[Deployment](deployment.md)** - Despliegue en producción
- **[API Reference](api.md)** - Documentación de endpoints
