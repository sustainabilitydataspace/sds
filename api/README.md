# SustainabilityDataSpace API

API FastAPI para ontología ESG unificada, cálculo, mapeos interoperables y runtime DB-first validado en Windows.

Las instrucciones siguientes son para evaluación técnica. Instalar SDS no
autoriza el uso productivo propio ni prestar servicios a terceros. La licencia
aplicable al código SDS propio está en `../LICENSE`; la explicación pública está
en `../docs/license-and-publication.md`.

---

## 🚀 Inicio Rápido

El servicio admite Python 3.10-3.12. La serialización `sds-canonical-json-v1`
y la suite completa requieren CPython 3.10 / Unicode `13.0.0`; en versiones
con otros datos Unicode la serialización falla cerrada. Para desarrollar y
ejecutar tests, usa [el entorno canónico](docs/tests.md#runtime-de-serialización-canónica).

### Windows PowerShell — Native Local Dev

```powershell
cd api
.\scripts\dev.ps1 Setup
.\scripts\dev.ps1 Start
```

This is the canonical API evaluation install path: install Python dependencies,
configure `.env`, and run the FastAPI service directly in a test environment.
Evaluators may install PostgreSQL and other technical dependencies however
they choose; this does not grant production-use rights.
Copy `.env.example` to a local `.env` before starting; secrets stay in that
ignored local file and are never versioned.

### Linux / macOS / Git Bash

```bash
# Local (sin Docker)
cd api
# macOS/Homebrew: usa Python 3.12 como runtime local más reciente soportado:
# brew install python@3.12
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp -n .env.example .env
make start
```

**URLs**: [API](http://localhost:8090) | [Docs](http://localhost:8090/docs) | [ReDoc](http://localhost:8090/redoc) *(offline-safe, no CDN)*

Swagger UI keeps the stock offline-safe layout and starts with **Try it out**
enabled, so parameter fields and high-value JSON bodies carry editable example
values that users can run, delete, or change directly in `/docs`.

### Optional Docker Quickstart

```bash
cd api
cp -n .env.example .env
# Define local JWT_SECRET_KEY, POSTGRES_PASSWORD, EXPORT_SIGNING_SECRET
# and BOOTSTRAP_ADMIN_PASSWORD before starting; never commit .env.
make up
NO_START=1 bash scripts/smoke_stack.sh minimal
make down
```

### Public synthetic demo and regression benchmark

The top-level files of the versioned [`demo/`](demo/) package (those listed in
its `manifest.json`) are wholly synthetic public data; they are not a Nordhaven
export or dump. `demo/a23/` is the separate A2.3 verification package, installed
through the admin API (see [Despliegue](docs/deployment.md#demo-a23-package)). With an empty disposable PostgreSQL runtime,
use the supported synchronous strict CSV importer and then verify/reset it:

```bash
cd api
make demo-manifest
DATABASE_URL='postgresql://sds:[REDACTED]@127.0.0.1:5432/sds' make demo-install
DATABASE_URL='postgresql://sds:[REDACTED]@127.0.0.1:5432/sds' make demo-verify
DATABASE_URL='postgresql://sds:[REDACTED]@127.0.0.1:5432/sds' make demo-benchmark
DATABASE_URL='postgresql://sds:[REDACTED]@127.0.0.1:5432/sds' make demo-reset
```

The Make targets explicitly set `VALUE_REVISION_API_ENABLED=true`,
`VALUE_REVISION_DUAL_WRITE_ENABLED=true`, and
`VALUE_REVISION_PRIMARY_READ_PATH=revision` for their host CLI process before
the strict importer is imported. This is intentionally separate from Compose
service settings; invoking `scripts/demo_runtime.py` directly must provide the
same three settings and fails closed if any is absent or false.

UC-01 verifies an energy observation and kWh-to-MWh conversion; UC-02 verifies
only the kg CO2e-to-t CO2e compatible-unit boundary for a Scope 1 observation,
not factors, inventory aggregation, or completeness; UC-03 verifies water
withdrawal/discharge conversion plus persisted source, destination, stress, and
evidence dimensions. Ordinary `demo-verify` is DB-backed only, even if an API
URL happens to be present in the environment. The Docker workflow separately
uses `--ci-api-principal --api-url ...`: it logs in as bootstrap admin only to
create a runtime-random `data_manager` bound to `value_import_gate`, then uses
that account (never bootstrap admin) for the exact demo-row list and conversion
route checks. The generated password and tokens remain process-local.

The API has no supported user-deletion endpoint. Therefore this API-principal
path is CI-only and requires a disposable volume; the workflow's `down -v`
removes that volume. `demo-reset` refuses to remove demo rows once immutable
revisions exist; it does not remove identities. Discard the CI-only volume
instead of attempting to delete its audit history.

`demo-benchmark` is disabled on ordinary/demo databases: the current 1,000-row
gate requires a separately provisioned PostgreSQL 15 database that is discarded
whole after testing. It cannot purge revision-history rows. Its 30-second
threshold is a local/CI regression tripwire, not an SLA or concurrency proof.
UC-04..UC-06 are bounded data-contract
demonstrations only, not policy enforcement, connector control, a circularity
engine, an ESG score, a credit decision, territorial/legal certification, or a
compliance finding.

```powershell
cd api
.\scripts\dev.ps1 Up
.\scripts\smoke_stack.ps1 -Profile minimal -NoStart
```

This optional helper starts PostgreSQL + the API with Docker Compose using the
active `.env`. It is a quickstart for evaluation and human testing, not the
required public API dependency model. When the local Nordhaven dataset is
loaded, `/docs` and `/openapi.json` expose real Nordhaven-backed examples.

Useful recovery flags:

```powershell
.\scripts\dev.ps1 Down
.\scripts\dev.ps1 Up -Rebuild
.\scripts\dev.ps1 Smoke
```

### Optional Docker Self-Host Reference (evaluation only)

```powershell
cd api
.\scripts\dev.ps1 ProductionInstall -AllowedOrigin https://your-domain.example  # optional self-host reference
```

This historical action uses `compose.production.yml` and `.env.production` as
an optional self-host reference profile for evaluation; the command's name
does not grant permission to operate SDS in production. It disables the portal mount by
default and verifies `/healthz`, `/docs`, admin login, authenticated `/ready`, and concept
listing. For local verification only:

```powershell
.\scripts\dev.ps1 ProductionInstall -AllowedOrigin http://localhost:8092 -AllowLocalhostOrigin -ApiPort 8092 -PostgresPort 55433
```

The Docker reference starts one Uvicorn worker because migrations/bootstrap run
inside app startup. For disposable production drills alongside an existing
`sds-api-prod` stack, use a unique project and env file:

```powershell
.\scripts\dev.ps1 ProductionInstall -AllowedOrigin http://localhost:8093 -AllowLocalhostOrigin -ApiPort 8093 -PostgresPort 55434 -ProjectName sds-a02-prod -EnvFile ..\.local_artifacts\a02-prod\.env.production
.\scripts\dev.ps1 ProductionDown -ProjectName sds-a02-prod -EnvFile ..\.local_artifacts\a02-prod\.env.production -RemoveVolumes
```

### VM / Docker Compose (opcional para human testing con persistencia real)

```bash
cd api
cp .env.example .env
make up
make logs
```

```powershell
cd api
Copy-Item .env.example .env -ErrorAction SilentlyContinue
.\scripts\dev.ps1 Up
.\scripts\dev.ps1 Logs
```

Luego abre: `http://localhost:8090/docs`

**Requerido en `.env` (VM/compose con DB):**
- `JWT_SECRET_KEY` (clave fuerte, no usar valores por defecto)
- `POSTGRES_PASSWORD` (password de la BD local de compose)
- `EXPORT_SIGNING_SECRET` (clave de firma independiente de la JWT)
- `BOOTSTRAP_ADMIN_PASSWORD` (contraseña inicial del admin)
- `ALLOWED_ORIGINS` (JSON array, p. ej. `["http://localhost:8090"]`)
- `POSTGRES_PORT` (opcional; puerto host para PostgreSQL compose, por defecto `5432`)
- `REQUIRE_DATABASE=true`

La plantilla pública deja vacías las claves y contraseñas: complétalas con
valores propios generados en tu equipo antes de ejecutar `make up`. Compose
construye `DATABASE_URL` a partir de `POSTGRES_PASSWORD`; en una instalación
nativa debes definir además `DATABASE_URL` para tu PostgreSQL. No publiques
tu archivo `.env` ni reutilices estas credenciales en otros entornos.

> Nota: cuando `REQUIRE_DATABASE=true`, el runtime usa PostgreSQL como backend autoritativo de unidades automáticamente. `USE_POSTGRES_UNITS=true` solo es necesario si quieres forzar el backend PostgreSQL en un modo donde `REQUIRE_DATABASE=false`.

> Si cambias `POSTGRES_PASSWORD` y ya existía volumen previo, reinicia con:
> `docker compose --env-file .env -f compose.yml down -v && make up`
>
> Si `5432` ya está ocupado en Windows, define `POSTGRES_PORT=55432` en `.env` antes de levantar compose.

#### Cargar o refrescar indicadores, valores y mapeos en PostgreSQL

En una base vacía, el arranque ya:
- crea el esquema,
- seeda indicadores y mappings bundled,
- seeda el catálogo bundled de unidades.

Usa la importación manual cuando quieras refrescar la base con:
- un paquete SDS externo compatible,
- valores operativos opcionales en `sds_values.csv`, o
- un CSV de indicadores compatible con el contrato SDS,
- un paquete canónico de mapeos compatible con el contrato SDS.

La API permite validar indicadores de forma síncrona. La admisión asíncrona en DB está bloqueada pendiente de H15; el segundo ejemplo devuelve `503`:

```bash
curl -sS -X POST "http://localhost:8090/api/v1/indicators/import-csv-validations" \
  -H "Authorization: Bearer <access_token>" \
  -F "file=@<path-to-indicator-register.csv>"

curl -sS -X POST "http://localhost:8090/api/v1/indicators/import-csv-jobs" \
  -H "Authorization: Bearer <access_token>" \
  -F "validation_id=<validation_job_id>"
```

El primer endpoint requiere `manage_indicators` y valida sin modificar el catálogo, aunque persiste resultado y payload de validación. Con `REQUIRE_DATABASE=true`, el segundo devuelve `503` con `Database-backed async indicator import jobs are unsupported pending H15` antes de leer el cuerpo, autorizar o acceder al estado de jobs, tanto para `validation_id` como para CSV directo. Usa el CLI soportado para importar. H15 sigue abierto; este cambio no recupera ni terminaliza jobs existentes y conserva el comportamiento memoria/demo.

```bash
# Validar/importar un paquete externo (indicadores + valores si existen)
cd <repo-root>
python scripts/import_sds_package.py --package-dir <path-to-sds-package> --dry-run
python scripts/import_sds_package.py --package-dir <path-to-sds-package>

# Importar un CSV de indicadores suelto en PostgreSQL (desde api)
cd api
# Si no existe .venv todavía:
#   PYTHON=python3 make setup      # macOS/Linux; soporta Python 3.10-3.12
source .venv/bin/activate
PROJECT_ROOT="$(cd .. && pwd)"
POSTGRES_PASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
POSTGRES_PORT="$(grep '^POSTGRES_PORT=' .env | cut -d= -f2-)"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
python scripts/import_indicators.py \
  --csv "<path-to-indicator-register.csv>" \
  --db-url "postgresql://sds:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/sds"
```

```powershell
# Validar/importar un paquete externo (indicadores + valores si existen)
Set-Location <repo-root>
.\api\.venv\Scripts\python.exe .\scripts\import_sds_package.py --package-dir <path-to-sds-package> --dry-run
.\api\.venv\Scripts\python.exe .\scripts\import_sds_package.py --package-dir <path-to-sds-package>

# Importar un CSV de indicadores suelto desde api
Set-Location .\api
.\scripts\dev.ps1 Setup
.\.venv\Scripts\python.exe .\scripts\import_indicators.py --csv <path-to-indicator-register.csv>
```

Validar carga:
```bash
docker compose --env-file .env -f compose.yml exec -T postgres \
  psql -U sds -d sds -c "SELECT COUNT(*) FROM indicators;"
make -C api gate-semantic-catalog-projection
```

Los paquetes canónicos de mapeos se validan con
`api/scripts/validate_canonical_mapping_package.py` y se cargan en tablas
shadow con `api/scripts/import_canonical_mapping_package.py`. La guía completa
está en [Import Packages](docs/import-packages.md).

**Unidades**: en despliegues con base de datos, el arranque usa unidades respaldadas por PostgreSQL y si las tablas están vacías las rellena desde el catálogo bundled. Ese catálogo incluye unidades SI, SI derivadas y unidades imperiales/US comunes. El modo offline sigue usando `src/data/units_database.json`.

#### Smoke test (workflow reproducible)

Para validar que el stack arranca y que los flujos principales funcionan (docs, auth, values, concepts):

```bash
cd api
bash scripts/smoke_stack.sh minimal
```

```powershell
cd api
.\scripts\smoke_stack.ps1 -Profile minimal
```

**Credenciales iniciales (solo si la BD está vacía):**
- `admin` / valor de `BOOTSTRAP_ADMIN_PASSWORD` en `.env`

> Importante: define `BOOTSTRAP_ADMIN_PASSWORD` en `.env` antes de desplegar en un servidor real.

---

## 📚 Documentación

| Documento | Descripción |
|-----------|-------------|
| **[Getting Started](docs/getting-started.md)** | Instalación, configuración y primeros pasos |
| **[Architecture](docs/architecture.md)** | Arquitectura del sistema y decisiones de diseño |
| **[API Reference](docs/api.md)** | Documentación completa de la API |
| **[Import Packages](docs/import-packages.md)** | Importar paquetes de indicadores y paquetes de valores por CLI/API |
| **[Configuration](docs/configuration.md)** | Guías de configuración (jerarquías, ontologías, unidades) |
| **[Deployment](docs/deployment.md)** | Deployment, administración y monitoreo |
| **[Tests](docs/tests.md)** | Estado, comandos y suites recomendadas |
| **[Backup/Restore](docs/backup_restore.md)** | Procedimiento de backup/restore de PostgreSQL (compose/VM) |
| **[Troubleshooting](docs/troubleshooting.md)** | Solución de problemas comunes |

---

## ⚡ Comandos Esenciales

`make test` y `dev.ps1 Test` requieren el entorno CPython 3.10 descrito en
[Tests](docs/tests.md#runtime-de-serialización-canónica).

```bash
# Desarrollo
make start          # Iniciar servidor
make stop           # Parar API local (best-effort; si está en foreground: Ctrl+C)
make test           # Ejecutar tests

# Servicios
make up             # Levantar stack VM (API + PostgreSQL)
make down           # Bajar servicios
make logs           # Ver logs

# Información
make status-dev     # URLs útiles para desarrollo
make status-extra   # Estado proceso/puerto (best-effort)
make help           # Ver todos los comandos
```

```powershell
# Windows PowerShell
.\scripts\dev.ps1 Start
.\scripts\dev.ps1 Stop
.\scripts\dev.ps1 Test
.\scripts\dev.ps1 Up
.\scripts\dev.ps1 ProductionInstall -AllowedOrigin https://your-domain.example
.\scripts\dev.ps1 Down
.\scripts\dev.ps1 Logs
.\scripts\dev.ps1 ProductionLogs
.\scripts\dev.ps1 Status
```

---

## 🏗️ Arquitectura

```
┌─────────────────────────────────────────────────────────────┐
│                    API REST (FastAPI)                       │
│                   http://localhost:8090                     │
└─────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼
┌──────────────┐    ┌──────────────────────────┐
│  PostgreSQL  │    │ Generated ontology split │
│  Port 5432   │    │ core_tbox + projection   │
│              │    │                          │
│ • Valores    │    │ • Conceptos             │
│ • Unidades   │    │ • Equivalencias         │
│ • Config     │    │ • Fórmulas / variables  │
│ • Semántica  │    │ • SPARQL local rdflib   │
│              │    │                          │
│ CANÓNICO     │    │ DERIVADO DEL MODELO DB   │
│ runtime      │    │ runtime                  │
└──────────────┘    └──────────────────────────┘
```

> El runtime de producto soportado es PostgreSQL + proyección ontológica generada. No hay un segundo plano semántico operativo.

**Ver**: [docs/architecture.md](docs/architecture.md) para detalles completos.

---

## ✨ Características

- 🔄 **Interoperabilidad ESG**: cruza ESRS y GRI sobre el modelo interno Sygris/SDS
- 📊 **Cálculo Automático**: Calcula indicadores desde variables base
- 🏢 **Jerarquías Dinámicas**: Granularidades organizativas y temporales
- ⚖️ **Conversión de Unidades**: semilla bundled cargada en PostgreSQL en modo DB-backed, con fallback JSON solo offline/no estricto
- 🔧 **Agregaciones Inteligentes**: Suma, promedio, máximo con reglas específicas
- 🧠 **Runtime semántico DB-first**: `/api/v1/concepts` y equivalencias salen del modelo canónico/proyectado en BD; `base.owl` ya no es el runtime estricto y cada indicador activo debe estar proyectado como concepto determinista
- 🧾 **Snapshots auditables de catálogo**: imports de indicadores y mappings registran `manifest_hash`, `source_ref` y `source_hash`, con endpoints públicos de `history`/`diff`
- 📦 **Exports incrementales y analíticos**: datasets y valores soportan filtros `changed_since`, conditional GET y formatos `csv` / `json` / `parquet`
- 🛰️ **Feeds incrementales de sincronización**: indicadores y mappings exponen cambios por snapshot persistido; valores exponen cambios por `updated_at` con `cursor` reanudable
- 🔏 **Manifiestos firmados**: los exports y manifests publican firma desacoplada y metadatos verificables del payload canónico
- 🧵 **Imports de valores en DB**: el carril soportado es CSV síncrono/CLI;
  los jobs asíncronos JSON/CSV DB devuelven `503` pendiente de H15
- ✅ **Sin datos simulados**: `/api/v1/calculate` requiere valores reales (si faltan, devuelve `404`)
- 🛠️ **Administración por API** (admin bearer con `manage_system`): gestión de
  usuarios, validación e importación de contratos de cálculo, reparación y
  corrección de factores del catálogo de unidades con informe de impacto,
  diagnóstico saneado de errores `500` e instalación del paquete de
  demostración A2.3 (véase [Despliegue](docs/deployment.md))

---

## 📊 Estado Técnico Actual

### Completado ✅
- Motor de Ontologías DB-first (proyección generada, conceptos, equivalencias, SPARQL local)
- Motor de Cálculo (fórmulas y agregaciones)
- Conversión de Unidades (catálogo bundled amplio en PostgreSQL; JSON offline como fallback)
- API REST (valores, conversión, ontología)
- Inicialización automática con bootstrap de referencia en DB vacía

### Pendiente de producto / capas posteriores 🚧
- Superficie adicional de configuración vía API
- Dashboard de administración
- Ampliación de algunos flujos de cálculo y operación

### Calidad y validación 📈
- **Suite de regresión amplia** sobre rutas críticas del API, runtime DB-backed, seguridad y operación
- **Cobertura objetivo**: `≥90%` para la suite material
- **Semilla bundled de unidades** disponible para bootstrap PostgreSQL y modo offline/no estricto
- **16 categorías** de unidades en el catálogo bundled actual
- **2 estándares ESG externos** integrados en el alcance público actual: ESRS y GRI
- **1 taxonomía interna operativa**: Sygris/SDS (`syg:*`) para variables de cálculo y correspondencias semánticas cuando existen

### Próxima evolución

La evolución posterior del producto debe mantener la ruta DB-first, los
contratos de paquete SDS y las garantías de seguridad/operación documentadas
como límites públicos.

---

## 🔧 Tecnologías

- **Backend**: Python 3.10-3.12, FastAPI
- **Bases de Datos**: PostgreSQL
- **OWL/RDF**: rdflib, owlready2
- **Testing**: pytest
- **Deployment**: optional Docker quickstart/self-host reference, plus native Python operation patterns

---

## 📝 Ejemplo de Uso

### Insertar Valor ESG
```bash
BOOTSTRAP_ADMIN_PASSWORD="$(grep '^BOOTSTRAP_ADMIN_PASSWORD=' .env | cut -d= -f2-)"
TOKEN=$(curl -sS -X POST "http://localhost:8090/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"${BOOTSTRAP_ADMIN_PASSWORD}\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

curl -X POST "http://localhost:8090/api/v1/values" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "concept": "urn:sds:reg:esrs:e1_5_12",
    "entity": "nh_es_valencia_plant",
    "period": "2024-01-31",
    "value": 19.995,
    "unit": "MWh"
  }'
```

### Calcular un Indicador (requiere valores previos)

Primero revisa las variables necesarias:

```bash
curl -sS "http://localhost:8090/api/v1/calculate/dependencies/csrd:E3_5"
```

Luego ejecuta:

```bash
curl -X POST "http://localhost:8090/api/v1/calculate" \
  -H "Content-Type: application/json" \
  -d '{
    "concept": "csrd:E3_5",
    "entity": "nh_group",
    "period": "2024",
    "granularity": "annual",
    "include_trace": true
  }'
```

Con `include_trace=true`, la respuesta muestra `trace.variable_values` con los valores usados para cada variable de la fórmula, por ejemplo `Water_Industrial` y `Water_Cooling`.

### Convertir Unidades
```bash
curl -X POST "http://localhost:8090/api/v1/convert" \
  -H "Content-Type: application/json" \
  -d '{
    "value": 1000,
    "from_unit": "kg",
    "to_unit": "t"
  }'
```

**Ver más ejemplos**: [docs/api.md](docs/api.md)

---

## 🤝 Contribuir

Se pueden comunicar incidencias por GitHub Issues. No se solicitan
contribuciones de código ni pull requests hasta que exista una política
publicada de derechos y aceptación de aportaciones. `../LICENSE` permite forks
de GitHub para evaluación, con avisos intactos, pero no concede uso productivo,
explotación comercial ni redistribución fuera del marco permitido. Las
vulnerabilidades no deben abrirse como Issues públicas; véase `../SECURITY.md`.

---

## 📄 Licencia

El código SDS propio se publica bajo la `Sustainability Data Space Software
Evaluation License v1.0` incluida en `../LICENSE`. Es una licencia de fuente
disponible para evaluación, no una licencia open source. Permite revisar,
instalar y probar SDS en entornos no productivos; no concede derechos de
explotación productiva, comercial, SaaS, servicio gestionado, reventa ni
prestación de servicios a terceros. Los componentes de terceros conservan sus
licencias propias; véase `../docs/third-party-notices.md`.

---

## 📞 Soporte

- **Documentación**: [docs/](docs/)
- **Issues**: GitHub Issues
- **Changelog**: [CHANGELOG.md](../CHANGELOG.md)

---

**Versión**: 2.1.0
**Última actualización**: 2026-09-16
