# API Reference — SustainabilityDataSpace API

> **Última actualización:** 2026-06-29

Referencia operativa de la API SDS.
La **fuente de verdad completa de schemas** sigue siendo `http://localhost:8090/docs` y `http://localhost:8090/openapi.json`.

---

## Base URLs

| Entorno | URL |
|---------|-----|
| Local | `http://localhost:8090` |
| Docs interactivas | `http://localhost:8090/docs` |
| ReDoc | `http://localhost:8090/redoc` |

---

## Uso de Swagger UI

`/docs` usa Swagger UI stock y offline-safe.

El modo **Try it out** queda activo por defecto para que la sección
**Parameters** sea editable al abrir una operación. Los campos de parámetros y
los cuerpos JSON de alto valor incluyen ejemplos reutilizables: puedes ejecutar
el ejemplo tal cual, borrar valores opcionales o cambiarlos manualmente. Los
endpoints genéricos no repiten texto de ejemplo artificial; la referencia
operativa está en los propios campos de parámetros, los ejemplos de body y el
schema OpenAPI.

`/openapi.json` sigue siendo la fuente de verdad para generadores y clientes.
SDS enriquece ese schema con notas breves de uso, ejemplos de request body para
flujos clave y valores `example` para parámetros comunes como `concept`,
`entity`, `period_start`, `period_end`, `limit`, `standard` y `code`. Los
ejemplos operativos visibles usan datos Nordhaven cargados en la base local
cuando ese dataset está disponible.

---

## Endpoints de sistema

| Método | Ruta | Uso |
|--------|------|-----|
| `GET` | `/healthz` | Health con estado de dependencias |
| `GET` | `/ready` | Readiness del runtime |
| `GET` | `/metrics` | Métricas Prometheus |
| `GET` | `/docs` | Swagger UI offline-safe |
| `GET` | `/redoc` | ReDoc offline-safe |

**Ejemplo**

```bash
curl -fsS http://localhost:8090/healthz
curl -fsS http://localhost:8090/ready
```

En `REQUIRE_DATABASE=true`, `healthz` y `ready` reflejan únicamente la salud del plano canónico.

---

## Readiness de interoperabilidad

| Método | Ruta | Uso |
|--------|------|-----|
| `GET` | `/api/v1/interoperability/readiness` | Comprobar si el runtime real tiene catálogos, mappings, cálculo y conversiones para ejecutar flujos operativos manuales |

Este endpoint no carga datos ni modifica estado. Úsalo en Swagger antes de una
prueba manual: si `status=ready`, los checks requeridos están presentes;
si `status=not_ready`, la respuesta indica qué pieza falta o qué dependencia no
se ha podido comprobar. Un fallo de consulta de mappings se informa dentro del
check `csrd_gri_exact_mapping_loaded`; no convierte esta comprobación operativa
en un error HTTP no estructurado.

La guía paso a paso para Swagger y resolución runtime está en
[Runtime Interoperability Guide](interoperability-runtime.md).

Checks esperados:
- `semantic_catalogues_loaded`
- `l_to_m3_unit_conversion`
- `kwh_to_mwh_energy_conversion`
- `gj_to_tco2e_requires_emission_factor`
- `csrd_gri_exact_mapping_loaded`
- `csrd_disclosure_e3_5_calculation_contract_loaded`

**Ejemplo**

```bash
curl -sS "http://localhost:8090/api/v1/interoperability/readiness" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Autenticación

### Login y token

| Método | Ruta | Uso |
|--------|------|-----|
| `POST` | `/auth/login` | Obtener access + refresh token |
| `POST` | `/auth/refresh` | Refrescar access token |
| `POST` | `/auth/logout` | Invalidar token actual |
| `GET` | `/auth/token/info` | Inspeccionar token actual |

**Ejemplo**

```bash
BOOTSTRAP_ADMIN_PASSWORD="$(grep '^BOOTSTRAP_ADMIN_PASSWORD=' api/.env | cut -d= -f2-)"
TOKEN=$(curl -sS -X POST "http://localhost:8090/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"${BOOTSTRAP_ADMIN_PASSWORD}\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
```

### Perfil y credenciales

| Método | Ruta | Uso |
|--------|------|-----|
| `GET` | `/auth/me` | Perfil del usuario actual |
| `PUT` | `/auth/me` | Actualizar perfil del usuario actual |
| `POST` | `/auth/change-password` | Cambiar password |

### Gestión administrativa / API keys

| Método | Ruta | Uso |
|--------|------|-----|
| `POST` | `/auth/users` | Crear usuario (requiere permisos) |
| `POST` | `/auth/api-keys` | Crear API key |
| `GET` | `/auth/api-keys` | Listar API keys del usuario |
| `DELETE` | `/auth/api-keys/{api_key_id}` | Revocar API key |

Las API keys son credenciales acotadas al usuario creador. En
`POST /auth/api-keys`, `permissions` debe ser un subconjunto de los permisos
actuales del usuario; SDS responde `403` si la petición intenta añadir permisos
que el usuario no posee. Si `expires_at` se omite, la clave expira por defecto
según `API_KEY_EXPIRE_DAYS` (365 días salvo configuración distinta). En
runtime DB-backed, la autorización de una API key usa ese subconjunto acotado,
no el rol persistido completo del usuario.

---

## Indicadores (`/api/v1/indicators`)

| Método | Ruta | Uso |
|--------|------|-----|
| `GET` | `/api/v1/indicators` | Listado paginado |
| `GET` | `/api/v1/indicators/search` | Búsqueda textual / filtrada |
| `GET` | `/api/v1/indicators/manifest` | Manifiesto/versionado del slice exportable |
| `GET` | `/api/v1/indicators/export` | Exportar catálogo filtrado en `csv`, `json` o `parquet` |
| `GET` | `/api/v1/indicators/changes` | Feed incremental de snapshots persistidos del catálogo |
| `GET` | `/api/v1/indicators/history` | Historial persistente de snapshots de catálogo |
| `GET` | `/api/v1/indicators/diff` | Diff entre snapshots o snapshot vs catálogo actual |
| `POST` | `/api/v1/indicators/import-csv-validations` | Validar CSV de indicadores sin modificar catálogo |
| `POST` | `/api/v1/indicators/import-csv-jobs` | DB devuelve `503` antes de admisión; H15 abierto |
| `GET` | `/api/v1/indicators/import-jobs/{job_id}` | Consultar estado y resultado de validación/importación |
| `GET` | `/api/v1/indicators/import-jobs/{job_id}/errors` | Consultar errores de fila paginados |
| `GET` | `/api/v1/indicators/{indicator_id}` | Detalle por identificador |
| `GET` | `/api/v1/indicators/esrs/{code}` | Lookup por código ESRS |
| `GET` | `/api/v1/indicators/gri/{code}` | Lookup por código GRI |

La validación síncrona requiere `manage_indicators` y no modifica el catálogo, aunque puede persistir resultado y payload. Con `REQUIRE_DATABASE=true`, la admisión asíncrona rechaza CSV directo y `validation_id` con `503` y JSON `{"detail":"Database-backed async indicator import jobs are unsupported pending H15"}` antes de leer/spooling del cuerpo, autorizar, acceder a estado de jobs/payloads o programar trabajo. Se conservan CORS, cabeceras de seguridad y request ID. Usa el CLI soportado para importar. H15 sigue abierto para leases durables, fencing y recuperación tras reinicio; no se modifican jobs existentes. Ver [Import Packages](import-packages.md).

**Ejemplo**

```bash
curl -sS "http://localhost:8090/api/v1/indicators?limit=1" \
  -H "Authorization: Bearer $TOKEN"
curl -sS "http://localhost:8090/api/v1/indicators/export?format=csv&dimension=E" \
  -H "Authorization: Bearer $TOKEN"
curl -sS "http://localhost:8090/api/v1/indicators/export?format=parquet&dimension=E" \
  -H "Authorization: Bearer $TOKEN" -o indicators.parquet
curl -sS "http://localhost:8090/api/v1/indicators/manifest?dimension=E" \
  -H "Authorization: Bearer $TOKEN"
curl -sS "http://localhost:8090/api/v1/indicators/history?limit=5" \
  -H "Authorization: Bearer $TOKEN"
curl -sS "http://localhost:8090/api/v1/indicators/changes?limit=5" \
  -H "Authorization: Bearer $TOKEN"
curl -sS "http://localhost:8090/api/v1/indicators/export?format=json&changed_since=2026-04-01T00:00:00Z" \
  -H "Authorization: Bearer $TOKEN"
```

Validar un CSV y comprobar el rechazo de admisión asíncrona en DB (el segundo POST devuelve `503`; el GET solo consulta un job existente):

```bash
curl -sS -X POST "http://localhost:8090/api/v1/indicators/import-csv-validations" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@indicators.csv"

curl -sS -X POST "http://localhost:8090/api/v1/indicators/import-csv-jobs" \
  -H "Authorization: Bearer $TOKEN" \
  -F "validation_id=<validation_job_id>"

curl -sS "http://localhost:8090/api/v1/indicators/import-jobs/<job_id>" \
  -H "Authorization: Bearer $TOKEN"
```

**Respuesta paginada de ejemplo**

```json
{
  "items": [],
  "total": 1805,
  "limit": 1,
  "offset": 0
}
```

> `total` depende del snapshot/import actual. Los imports canónicos de catálogo registran un snapshot persistente con `manifest_hash`, `source_ref` y `source_hash` para auditoría y comparación posterior.

`GET /api/v1/indicators/changes` expone ese historial como un feed incremental cursorizable:
- orden ascendente por `created_at` + `snapshot_id`
- `cursor` opaco para reanudar
- cada evento incluye `previous_snapshot_id`, `previous_manifest_hash` y un resumen `diff`

---

## Mappings interoperables (`/api/v1/mappings`)

| Método | Ruta | Uso |
|--------|------|-----|
| `GET` | `/api/v1/mappings/standards` | Estándares soportados |
| `GET` | `/api/v1/mappings` | Listado paginado |
| `GET` | `/api/v1/mappings/search` | Búsqueda textual |
| `GET` | `/api/v1/mappings/manifest` | Manifiesto/versionado del slice exportable |
| `GET` | `/api/v1/mappings/export` | Exportar mappings filtrados en `csv`, `json` o `parquet` |
| `GET` | `/api/v1/mappings/changes` | Feed incremental de snapshots persistidos de mappings |
| `GET` | `/api/v1/mappings/history` | Historial persistente de snapshots de mappings |
| `GET` | `/api/v1/mappings/diff` | Diff entre snapshots o snapshot vs mappings actuales |
| `GET` | `/api/v1/mappings/from/{standard}/{code}` | Mapeos desde un estándar/código |
| `GET` | `/api/v1/mappings/to/{standard}/{code}` | Mapeos hacia un estándar/código |
| `GET` | `/api/v1/mappings/between/{source_standard}/{target_standard}` | Cruce entre dos estándares |

**Ejemplo**

```bash
curl -sS "http://localhost:8090/api/v1/mappings/standards" \
  -H "Authorization: Bearer $TOKEN"
curl -sS "http://localhost:8090/api/v1/mappings/from/ESRS/E1-1" \
  -H "Authorization: Bearer $TOKEN"
curl -sS "http://localhost:8090/api/v1/mappings/export?format=json&source_standard=ESRS" \
  -H "Authorization: Bearer $TOKEN"
curl -sS "http://localhost:8090/api/v1/mappings/export?format=parquet&source_standard=ESRS" \
  -H "Authorization: Bearer $TOKEN" -o mappings.parquet
curl -sS "http://localhost:8090/api/v1/mappings/manifest?source_standard=ESRS" \
  -H "Authorization: Bearer $TOKEN"
curl -sS "http://localhost:8090/api/v1/mappings/history?limit=5" \
  -H "Authorization: Bearer $TOKEN"
curl -sS "http://localhost:8090/api/v1/mappings/changes?limit=5" \
  -H "Authorization: Bearer $TOKEN"
curl -sS "http://localhost:8090/api/v1/mappings/export?format=json&changed_since=2026-04-01T00:00:00Z" \
  -H "Authorization: Bearer $TOKEN"
curl -sS "http://localhost:8090/api/v1/mappings/search?source_standard=ESRS&target_standard=GRI&target_code=GRI%20305&limit=10" \
  -H "Authorization: Bearer $TOKEN"
```

Los endpoints `history` devuelven snapshots persistentes del catálogo. Los endpoints `diff` aceptan `from_snapshot_id` y un `to_snapshot_id` opcional; si no se informa destino, comparan el snapshot base contra el estado actual del dataset en la BD canónica.

`changed_since` permite extraer únicamente filas creadas o actualizadas desde una marca temporal concreta. En modo offline/no estricto, este filtro solo está garantizado cuando la fuente activa es la BD canónica.

En `GET /api/v1/mappings/search`, `source_code` y `target_code` filtran por
prefijo del código canónico almacenado. Para GRI, incluye el prefijo del
estándar en el campo: usa `target_code=GRI 305`, no `target_code=30`, si
quieres explorar mappings ESRS hacia la familia GRI 305.

`export` solo devuelve `304` mediante `If-None-Match` con el `ETag` de
esa exportación y formato (`json`, `csv` o `parquet`). El validador débil
identifica filas equivalentes en el mismo formato, no bytes idénticos; el
`ETag` del endpoint `manifest` es distinto y no valida una exportación.
`X-SDS-Manifest-Hash` identifica las filas lógicas y sí coincide entre
formatos del mismo conjunto. La firma SDS protege el manifiesto canónico de
filas, no el `ETag` débil adicional de cada formato. `Last-Modified` describe
las filas seleccionadas, pero
`If-Modified-Since` no valida estos conjuntos mutables: eliminar una fila
puede hacer retroceder su fecha máxima aunque cambie el contenido.

`GET /api/v1/mappings/changes` usa el mismo contrato cursorizable de snapshots persistidos que indicadores y devuelve además el resumen `diff` frente al snapshot anterior.

---

## Valores ESG (`/api/v1/values`)

| Método | Ruta | Uso |
|--------|------|-----|
| `POST` | `/api/v1/values` | Insertar valor |
| `POST` | `/api/v1/values/import` | Importar lote transaccional de valores |
| `POST` | `/api/v1/values/import-csv` | Importar lote desde CSV estricto |
| `POST` | `/api/v1/values/import-jobs` | Importación asíncrona JSON solo en memoria/demo; DB devuelve `503` pendiente de H15 |
| `POST` | `/api/v1/values/import-csv-jobs` | Importación asíncrona CSV solo en memoria/demo; DB devuelve `503` pendiente de H15 |
| `POST` | `/api/v1/values/resolve` | Resolver un valor por almacenamiento directo, cálculo, mapping equivalente y conversión soportada |
| `GET` | `/api/v1/values` | Listar valores |
| `GET` | `/api/v1/values/changes` | Feed incremental de cambios de valores por `updated_at` |
| `GET` | `/api/v1/values/import-jobs/{job_id}` | Consultar estado y resultado de un job de importación |
| `GET` | `/api/v1/values/manifest` | Manifiesto/versionado del slice exportable |
| `GET` | `/api/v1/values/export` | Exportar valores filtrados en `csv`, `json` o `parquet` |
| `GET` | `/api/v1/values/{value_id}` | Consultar valor |
| `DELETE` | `/api/v1/values/{value_id}` | Eliminar valor |

En modo estricto, SDS valida `entity` contra jerarquias activas antes de
persistir valores. Si una carga devuelve `Unknown entity`, primero crea o activa
esa entidad en `POST /api/v1/hierarchies` y repite la carga. Este rechazo es
intencionado: evita aceptar observaciones sin perimetro organizativo.

**Ejemplo**

```bash
curl -X POST "http://localhost:8090/api/v1/values" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: values-create-001" \
  -d '{
    "concept": "urn:sds:reg:esrs:e1_5_12",
    "entity": "nh_es_valencia_plant",
    "period": "2024-01-31",
    "external_key": "erp:nordhaven-valencia-energy-2024-01",
    "value": 19.995,
    "unit": "MWh"
  }'
```

`POST /api/v1/values`, `POST /api/v1/values/import` y `POST /api/v1/values/import-csv` aceptan la cabecera opcional `Idempotency-Key`. Si se reutiliza con el mismo payload, la API devuelve la misma respuesta; si se reutiliza con payload distinto, responde `409`.

`external_key` es opcional y permite **upsert determinista** del valor. Si llega una nueva observación con la misma `external_key`, SDS actualiza el registro existente en vez de crear uno nuevo.

**Ejemplo bulk import**

```bash
curl -X POST "http://localhost:8090/api/v1/values/import" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: values-bulk-001" \
  -d '{
    "items": [
      {
        "concept": "urn:sds:reg:esrs:e1_5_12",
        "entity": "nh_es_valencia_plant",
        "period": "2024-01-31",
        "external_key": "erp:nordhaven-valencia-energy-2024-01",
        "value": 19.995,
        "unit": "MWh"
      },
      {
        "concept": "urn:sds:reg:esrs:e1_5_12",
        "entity": "nh_es_valencia_plant",
        "period": "2024-02-29",
        "external_key": "erp:nordhaven-valencia-energy-2024-02",
        "value": 17.184,
        "unit": "MWh"
      }
    ]
  }'
```

El import por lote es **atómico por request**: si alguna fila falla validación o normalización, el lote completo se rechaza y la respuesta incluye el detalle por fila.

Con `REQUIRE_DATABASE=true`, las dos rutas de envío asíncrono de valores no
están soportadas hasta completar H15. Un guard ASGI de aplicación rechaza los
`POST` a esas dos rutas exactas con
`503 Service Unavailable` y el detalle estable
`Database-backed async value import jobs are unsupported pending H15`, antes de
leer/parsear el body, almacenar temporalmente uploads, autorizar, resolver los
stores, reclamar idempotencia, crear jobs, procesar el CSV o
programar tareas. Usa `/api/v1/values/import`, `/api/v1/values/import-csv` o el
CLI con tenant explícito según la política vigente. H15 sigue abierto: este
rechazo no implementa recuperación ni modifica jobs existentes.

Separadamente, el arranque DB-required reconcilia jobs de valores
`pending`/`running` a `failed`; los GET de jobs muestran un motivo estable que
declara indeterminados los efectos históricos de ejecución. Conserva resultado,
conteos y metadatos, sin retry/replay. Este efecto persistente requiere una
ventana de mantenimiento, instancias/workers antiguos parados y un único
escritor: [procedimiento H15](deployment.md#h15-controlled-value-job-reconciliation-maintenance-required).

**Ejemplo async import job (solo memoria/demo, `REQUIRE_DATABASE=false`)**

```bash
curl -X POST "http://localhost:8090/api/v1/values/import-jobs" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: values-job-001" \
  -d '{
    "items": [
      {
        "concept": "urn:sds:reg:esrs:e1_5_12",
        "entity": "nh_es_valencia_plant",
        "period": "2024-01-31",
        "external_key": "erp:nordhaven-valencia-energy-job-2024-01",
        "value": 19.995,
        "unit": "MWh"
      }
    ]
  }'

curl -sS "http://localhost:8090/api/v1/values/import-jobs/<job_id>" \
  -H "Authorization: Bearer $TOKEN"
```

En memoria/demo, los endpoints asíncronos devuelven `202 Accepted`, mantienen
el estado del job en memoria y permiten consultar:

- `pending`
- `running`
- `completed`
- `failed`

Cuando el job termina, `result_body.import_job` resume la ejecución operativa:
`elapsed_seconds`, `total_rows`, `accepted_rows`, `rejected_rows` y `committed`.
El estado del job en memoria no es durable ni recuperable tras reinicio.

**Contrato CSV estricto**

Cabeceras permitidas:
- `concept`
- `entity`
- `period`
- `external_key` opcional
- `value`
- `value_type` opcional
- `unit`
- `currency` opcional
- `value_date` opcional
- `period_start` opcional
- `period_end` opcional
- `expected_unit` opcional
- `expected_currency` opcional
- `fx_policy_id` opcional
- `metadata_json` opcional

Ejemplo:

```csv
concept,entity,period,external_key,value,value_type,unit,currency,period_start,period_end,expected_unit,expected_currency,fx_policy_id,metadata_json
finance:Revenue,nh_group,2024-12-31,erp:nordhaven-revenue-2024,100,numeric,CurrencyMillion,GBP,2024-01-01,2024-12-31,,EUR,ecb-reference-monthly-average,"{""source"": ""csv_import""}"
```

`period` en importaciones de valores es una fecha exacta `YYYY-MM-DD`. Para datos anuales usa la fecha de cierre del periodo, por ejemplo `2024-12-31`. Si una fila solicita conversion monetaria, `expected_currency` y `fx_policy_id` deben aparecer juntos y la fila debe aportar `currency` mas `value_date` o `period_start` + `period_end`. Para importar paquetes completos de indicadores + valores, ver [Import Packages](import-packages.md).

En valores monetarios puros, no codifiques el cambio de divisa en `expected_unit`: la unidad puede representar la escala semantica (`Currency`, `CurrencyMillion`, etc.) y la divisa real vive en `currency` / `expected_currency`. Usa `expected_unit` solo cuando tambien haya una conversion fisica real.

**Ejemplo export**

```bash
curl -sS "http://localhost:8090/api/v1/values/export?format=csv&concept=urn:sds:reg:esrs:e1_5_12&entity=nh_group" \
  -H "Authorization: Bearer $TOKEN"
curl -sS "http://localhost:8090/api/v1/values/export?format=json&changed_since=2026-04-01T00:00:00Z" \
  -H "Authorization: Bearer $TOKEN"
curl -sS "http://localhost:8090/api/v1/values/export?format=parquet&concept=urn:sds:reg:esrs:e1_5_12&entity=nh_group" \
  -H "Authorization: Bearer $TOKEN" -o values.parquet
curl -sS "http://localhost:8090/api/v1/values/manifest?concept=urn:sds:reg:esrs:e1_5_12&entity=nh_group" \
  -H "Authorization: Bearer $TOKEN"
curl -sS "http://localhost:8090/api/v1/values/changes?entity=nh_group&limit=5" \
  -H "Authorization: Bearer $TOKEN"
```

El export usa el mismo conjunto de filtros que `GET /api/v1/values`, devuelve un dataset sin envoltorio paginado y expone:
- `ETag`
- `Last-Modified` cuando aplica
- `X-SDS-Manifest-Hash`
- `X-SDS-Contract-Version`
- `X-SDS-Signature-Alg`
- `X-SDS-Signature-Key-Id`
- `X-SDS-Signature`
- `X-SDS-Signed-At`
- `X-SDS-Signed-Payload-Hash`

Para evitar descargar el mismo slice, envía el `ETag` de esa misma
exportación y formato en `If-None-Match`; no reutilices el de `manifest` ni
el de otro formato.
`If-Modified-Since` sin `If-None-Match` recibe el contenido (no un `304`),
porque la fecha de las filas restantes no acredita que no hubo eliminaciones.

Las respuestas `manifest` exponen además la firma desacoplada del payload canónico:
- `signature_algorithm`
- `signature_key_id`
- `signature`
- `signed_at`
- `signed_payload_hash`

`GET /api/v1/values` mantiene `limit` + `offset` por compatibilidad y añade `cursor` como opción preferida para lecturas largas y estables. Cuando se usa `cursor`, la respuesta incluye `next_cursor` y `has_more`.
El cursor público está autenticado y vinculado a la identidad/tenant efectivos,
los filtros normalizados y el orden. Un cursor de otro usuario/tenant, con
filtros alterados, modificado o heredado sin firma recibe `400`; el `limit`
puede cambiar, pero `offset` y `cursor` no se combinan. Esta protección no
convierte una lectura con datos mutables en un snapshot inmutable.
La primera página y las continuaciones comparten el orden descendente
`(periodo efectivo, created_at, identificador único)`, también al utilizar
`offset`. En la lectura por revisiones, el periodo efectivo es `period_end`
o, si falta, `period_start`; el último desempate usa el ID de revisión, no
el `source_record_id` que pueden compartir distintas filas. Los IDs visibles
de valores no cambian. Un cursor de la antigua ordenación por ID proyectado
se rechaza con `400`: empieza una nueva lectura en lugar de reutilizarlo.
Las páginas iniciales, con o sin `offset`, emiten la misma posición de
revisión que espera la continuación; el ámbito firmado cambia de versión
cuando se activa la lectura por revisiones.

En Swagger, `cursor` no tiene ejemplo ejecutable. En la primera lectura deja
`cursor` vacío; solo pega un valor si viene literalmente de `next_cursor` en
una respuesta anterior. Para una primera lectura Nordhaven usa
`entity=nh_group`, `period_start=2024-12-31`,
`period_end=2024-12-31` y `limit=5`; deja `concept`, `unit`,
`changed_since`, `offset` y `cursor` en blanco salvo que estés probando un
filtro específico.

`changed_since` permite lecturas incrementales de valores según `created_at/updated_at`, tanto en listado como en export.

`GET /api/v1/values/changes` es un feed incremental de compatibilidad basado
en el estado actual de `ESGValue`:
- orden ascendente por `updated_at` + `id`
- `cursor` opaco para reanudar la lectura; nunca se inventa, siempre se
  copia desde el `next_cursor` anterior
- `operation` (`created` / `updated`)
- `record` con el payload actual completo del valor

Este feed no es un log CDC append-only: `updated_at` no es una secuencia de
commit. Para sincronizacion operativa que no pueda tolerar ambiguedad de orden
por transacciones concurrentes o largas, usa
`GET /api/v1/values/revisions/changes`, que expone eventos append-only con
`event_seq` por tenant cuando el runtime de revisiones esta activo.

### Resolución runtime de valores

`POST /api/v1/values/resolve` devuelve una respuesta estructurada con:
- `status`: `resolved` o una negativa cerrada como `not_transformable`,
  `not_enough_evidence`, `ambiguous_mapping` o `unsupported_conversion`.
- `method`: ruta usada, por ejemplo `direct_stored`, `calculation`,
  `equivalent_mapping`, `converted`, `mapping_then_conversion` o
  `calculation_then_mapping`.
- `trace`: evidencia de valor fuente, mapping, cálculo y conversión cuando
  `include_trace=true`.
- `execution_authority`: autoridad de ejecución cuando la ruta usa cálculo,
  por ejemplo `native_contract`, `equivalent_mapping` o `certified_bridge`.
- `bridge_id`: identificador del puente certificado cuando SDS calcula un
  objeto de reporting desde inputs más estrechos autorizados.

La resolución prueba rutas genéricas contra el runtime activo. No está limitada
a los ejemplos de agua/energía, pero cada indicador necesita evidencia real:
valor operativo, contrato/fórmula ejecutable, mapping exacto/equivalente o
conversión compatible. Para relaciones no equivalentes, como componentes
`narrower`, SDS solo devuelve valor si un contrato con puente certificado
autoriza explícitamente el cálculo. Si falta evidencia, SDS devuelve una
negativa cerrada en lugar de fabricar un resultado.

En los conceptos de ontología, `formula`, `related_variables`, `dimensions`,
`aggregation` y `calculation_contract_*` solo aparecen cuando hay un contrato
de cálculo público o una fórmula pública proyectada para ese concepto. En
datapoints/disclosures de estándar fuente es normal que esos campos sean
`null`: no significa que SDS haya perdido la relación, sino que ese nodo no es
por sí mismo una fórmula pública ejecutable. `taxonomy=Sygris` muestra el
catálogo operacional unificado de SDS/Sygris y las equivalencias Sygris son la
cobertura curada/importada disponible, no una promesa de equivalencia universal
automática para cada nodo de estándar.

Flujos manuales recomendados en Swagger:
- empezar por un código estándar y descubrir inputs:
  `GET /api/v1/calculate/dependencies/urn:sds:disclosure:csrd:e3-5`
- descubrir equivalencias externas:
  `GET /api/v1/mappings/from/ESRS/E3-4_05`
- valor directo con conversión física solo después de descubrir o seleccionar
  el datapoint operativo: `urn:sds:reg:esrs:e3_5_01`
- cálculo con contrato: `urn:sds:disclosure:csrd:e3-5`
- mapping equivalente: `csrd:E3-4_05 -> gri:303-5.c`
- GRI 302-1.e desde componentes ESRS autorizados: ejemplo Swagger
  `resolve_gri_302_1e_from_certified_bridge`; la respuesta debe mostrar
  `execution_authority = "certified_bridge"` y `bridge_id`
- energía física: `urn:sds:reg:esrs:e1_5_12`, `MWh -> MWh`
- negativa cerrada: `GJ -> t CO2e` sin contrato de factor de emisión

El usuario no necesita conocer los datapoints técnicos antes de empezar. Para
cálculos, `/calculate/dependencies/{concept}` devuelve las variables internas
que SDS necesita para ejecutar la fórmula. En estos ejemplos, `urn:sds:disclosure:csrd:e3-5` es
un concepto calculable de SDS para ESRS E3-5, no un datapoint granular IG3;
datapoints granulares de E3-5 usan códigos como `E3-5_01`.

**Ejemplo energía física**

```bash
curl -sS -X POST "http://localhost:8090/api/v1/values/resolve" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "target_concept": "urn:sds:reg:esrs:e1_5_12",
    "entity": "nh_group",
    "period": "2024",
    "granularity": "annual",
    "target_unit": "MWh",
    "include_trace": true
  }'
```

`GJ -> t CO2e` no es una conversión de unidad. Solo debe resolverse mediante
un contrato de cálculo explícito con factor de emisión, fuente, periodo,
geografía/metodología y trazabilidad.

---

## Cálculo (`/api/v1/calculate`)

| Método | Ruta | Uso |
|--------|------|-----|
| `POST` | `/api/v1/calculate` | Calcular un indicador |
| `GET` | `/api/v1/calculate/dependencies/{concept}` | Ver dependencias |
| `GET` | `/api/v1/calculate/batch` | Calcular varios indicadores |

**Ejemplo**

```bash
curl -sS "http://localhost:8090/api/v1/calculate/dependencies/urn:sds:disclosure:csrd:e3-5" \
  -H "Authorization: Bearer $TOKEN"

curl -sS -X POST "http://localhost:8090/api/v1/calculate" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "concept": "urn:sds:disclosure:csrd:e3-5",
    "entity": "nh_group",
    "period": "2024",
    "granularity": "annual",
    "include_trace": true
  }'
```

Si falta el contrato ejecutable, el endpoint devuelve `404`. Si el contrato
existe pero faltan observaciones requeridas, devuelve `422` con el detalle de
las variables ausentes, por ejemplo `Missing required observations: ...`.

`GET /api/v1/calculate/dependencies/{concept}` indica `dependency_source`:
`canonical_contract` cuando el runtime DB-first resuelve un contrato ejecutable
canónico, u `ontology_graph` cuando se usa la ruta semántica local.

`GET /api/v1/calculate/batch` devuelve siempre una entrada por concepto
solicitado. Cada entrada incluye `concept`, `status` (`success` o `failed`),
`result` con la respuesta normal de cálculo cuando ha pasado, y `error` cuando
ese concepto falla. Un concepto fallido no oculta ni elimina el resto de
resultados del lote.

Cuando `include_trace=true`, la respuesta incluye:
- `trace.variables_used`: nombres de variables tal como aparecen en la fórmula.
- `trace.variable_values`: valores concretos usados para evaluar la fórmula.
- `trace.conversions_applied`: conversiones fisicas y FX aplicadas antes de agregar.
- `trace.warnings`: avisos no fatales del cálculo.
- `trace.dependencies_resolved`: CURIEs de las dependencias resueltas.

Ejemplo parcial:

```json
{
  "formula_used": "e3_5_01",
  "trace": {
    "variables_used": ["e3_5_01"],
    "variable_values": {
      "e3_5_01": 139.241
    },
    "conversions_applied": [],
    "warnings": [],
    "dependencies_resolved": ["urn:sds:reg:esrs:e3_5_01"]
  }
}
```

---

## Unidades (`/api/v1/units`)

| Método | Ruta | Uso |
|--------|------|-----|
| `POST` | `/api/v1/convert` | Convertir unidades |
| `GET` | `/api/v1/units` | Listar unidades soportadas |
| `GET` | `/api/v1/units/validate` | Validar compatibilidad |

**Ejemplo**

```bash
curl -sS -X POST "http://localhost:8090/api/v1/convert" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "value": 1000,
    "from_unit": "kWh",
    "to_unit": "MWh"
  }'
```

Conversiones de referencia cubiertas por la suite:
- `1000 kWh -> MWh = 1`
- `1 GJ -> MWh = 0.2778`
- `psi -> kPa = 6.894757`
- `hp -> kW = 0.7457`
- `gal/min -> L/min = 3.785412`
- `short ton -> kg = 907.18474`

Las emisiones no pertenecen a la misma dimensión física que la energía:
`GJ -> t CO2e` debe fallar como conversión directa y pasar por cálculo con
factor de emisión.

## FX (`/api/v1/fx`)

La conversion monetaria historica vive bajo `/api/v1/fx`, separada de las conversiones fisicas de `/api/v1/units`. Todas las rutas fallan cerradas: no hay fallback a ultimo tipo disponible y toda conversion requiere una politica FX explicita.

| Método | Ruta | Uso |
|--------|------|-----|
| `GET` | `/api/v1/fx/currencies` | Listar divisas activas |
| `GET` | `/api/v1/fx/policies` | Listar politicas FX activas |
| `GET` | `/api/v1/fx/rates/coverage` | Comprobar cobertura de tipos para par/periodo |
| `POST` | `/api/v1/fx/convert` | Previsualizar conversion FX con traza |
| `POST` | `/api/v1/fx/rates/import` | Importar tipos FX controlados |

`/fx/rates/import` requiere `manage_fx_rates`, concedido solo a `ADMIN` y `DATA_MANAGER`. Las rutas de lectura y conversion requieren `convert_units`.

La importacion de tipos exige `source_hash` SHA-256 de 64 caracteres hexadecimales, normalizado a minusculas, y limita cada batch a `10000` filas. Cada fila debe aportar `quote_currency`, `rate_date` y `rate_value > 0`; `provider`, `rate_type` y `base_currency` se declaran una vez en el batch.

**Ejemplo**

```bash
curl -sS -X POST "http://localhost:8090/api/v1/fx/convert" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "value": 100,
    "from_currency": "GBP",
    "to_currency": "EUR",
    "value_date": "2024-03-15",
    "period_start": "2024-03-01",
    "period_end": "2024-03-31",
    "fx_policy_id": "ecb-reference-monthly-average"
  }'
```

---

## Conceptos y ontología

### Conceptos (`/api/v1`)

Rutas principales:
- `GET /api/v1/concepts`
- `GET /api/v1/concepts/{id}`
- `GET /api/v1/equivalences`
- `GET /api/v1/taxonomies`
- `POST /api/v1/sparql`

Con `REQUIRE_DATABASE=true`, `/api/v1/sparql` no ejecuta consultas contra el
grafo local. Si no hay semántica canónica DB-backed disponible devuelve `503`;
si la hay, devuelve `501` hasta que exista ejecución SPARQL DB-backed. Con
`REQUIRE_DATABASE=false`, la ruta conserva la ejecución rdflib local.

La búsqueda se hace sobre `GET /api/v1/concepts` con parámetros de query:
- `taxonomy`
- `concept_type`
- `search`
- `limit`
- `offset`

**Ejemplo SPARQL**

```bash
curl -sS -X POST "http://localhost:8090/api/v1/sparql" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "query": "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 10"
  }'
```

---

## Exportaciones semánticas de políticas

Las exportaciones NGSI-LD y DCAT-AP usan el registro canónico de políticas E6
para seleccionar política primaria por `purpose`, `region` y `policyId`
opcional. Si una política aditiva aplica, por ejemplo `policy-geofence-eu`, se
publica junto a la política primaria en `hasPolicy.object` u `odrl:hasPolicy`.
Cuando solo hay una política aplicable, la forma escalar anterior se mantiene.

## Jerarquías (`/api/v1/hierarchies`)

Rutas principales:
- `POST /api/v1/hierarchies`
- `GET /api/v1/hierarchies`
- `GET /api/v1/hierarchies/{id}`
- `PUT /api/v1/hierarchies/{id}`
- `DELETE /api/v1/hierarchies/{id}`

Las jerarquias registran las entidades operativas que luego pueden recibir
valores. El body editable `nordhaven_perimeter` muestra la forma organizativa
del dataset Nordhaven cargado:

```json
{
  "company_id": "nordhaven_components_group",
  "hierarchy_type": "organizational",
  "name": "Nordhaven Components Group organizational perimeter",
  "description": "Full ESRS/GRI catalog organizational perimeter.",
  "levels": [
    {"id": "nh_group", "name": "Group", "level": 0},
    {"id": "nh_region_emea", "name": "Region Emea", "parent": "nh_group", "level": 1},
    {"id": "nh_country_es", "name": "Spain", "parent": "nh_region_emea", "level": 2},
    {"id": "nh_es_valencia_plant", "name": "Valencia Plant", "parent": "nh_country_es", "level": 3}
  ],
  "active": true
}
```

---

## Códigos de respuesta más frecuentes

| Código | Significado |
|--------|-------------|
| `200` | OK |
| `201` | Recurso creado |
| `400` | Input inválido |
| `401` | No autenticado |
| `403` | Sin permisos |
| `404` | Recurso o dependencia no encontrada |
| `422` | Error de validación |
| `500` | Error interno |
| `503` | Dependencia requerida no disponible |

---

## Referencias

- [Getting Started](getting-started.md) — cómo levantar el entorno
- [Configuration](configuration.md) — variables de entorno
- [Architecture](architecture.md) — visión del runtime y dependencias
