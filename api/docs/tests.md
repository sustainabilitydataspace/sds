# Test Suite Documentation

> **Última actualización:** 2026-09-16

Documentación operativa de la suite de tests del API.

Objetivos de la suite:
- **regresión amplia sobre rutas críticas del API**
- objetivo de cobertura **≥97,01%**
- cobertura material sobre rutas offline-safe y DB-backed relevantes para operación y gates repo-locales

---

## Qué cubre la suite

### 1. Contrato HTTP y seguridad

Cobertura de:
- auth JWT y API keys,
- RBAC y permisos,
- validación de errores,
- observabilidad (`/healthz`, `/metrics`, request-id),
- routers principales de la API.

### 2. Lógica de negocio

Cobertura de:
- cálculo de indicadores,
- resolución runtime de valores interoperables,
- agregaciones,
- stores y repositorios,
- ontología local,
- mapeos E1/E2,
- persistencia de valores.

### 3. Runtime DB-backed

Cobertura específica:
- bootstrap automático de esquema,
- seeding bundled de referencia,
- catálogo de unidades PostgreSQL,
- comportamiento de `REQUIRE_DATABASE=true`,
- bootstrap de la semilla bundled de unidades y su selección de backend,
- helpers Windows/compose y smoke metadata.

### 4. Deliverables / gates repo-locales

Cobertura offline-safe de:
- `E1`
- `E2`
- `E8`
- `E9`
- `E10`
- validadores `E6`

---

## Comandos de ejecución

### Gate local equivalente a API CI

Desde la raíz del repo:

```bash
make api-ci-local
make install-hooks
```

`make api-ci-local` ejecuta el gate fuente de API CI: instala/actualiza las
dependencias dev si faltan, lint, security scan y suite completa.
`make install-hooks` instala un hook local `pre-push` que lo dispara
automáticamente para pushes directos a `main` cuando el cambio toca `api/**`,
`.github/workflows/api-ci.yml`, `.github/workflows/docker-quickstart-smoke.yml`,
`Makefile`, `README.md`, `scripts/api_ci_pre_push.py`,
`scripts/install_api_ci_pre_push_hook.py` o `docs/quality/acceptance_gates.md`.

### Carriles PostgreSQL desechables de administración

Con `SDS_MIGRATION_TEST_DATABASE_URL` y `SDS_MIGRATION_TEST_ALLOW_RESET=true`
(base desechable, nunca operativa) se ejecutan además:

- `tests/test_admin_catalog_postgres.py` y
  `tests/test_admin_catalog_followup_postgres.py`: reparaciones, correcciones de
  factor, verificación dentro de la transacción e informe de impacto sobre las
  tablas de unidades;
- `tests/test_error_diagnostics_postgres.py`: retención concurrente, caducidad
  y campos permitidos de errores reales de PostgreSQL;
- `tests/test_demo_package_postgres.py`: migra una base plantilla hasta la
  cabeza y crea una copia por test (requiere un servidor con `btree_gist` y
  privilegio `CREATEDB`); comprueba la instalación del paquete A2.3 con V1-V5,
  la reinstalación sin cambios, conflictos `409` sin escrituras, fallos en cada
  paso, concurrencia y la coexistencia de perfiles de correspondencias.

### Cualificación runtime HTTP/FX con PostgreSQL desechable

`tests/test_runtime_fx_http_postgres.py` conecta `TestClient`, autenticación real,
sesiones por petición, contrato público persistido, valores por tenant y motor
FX con política/tasa persistidas. Verifica resultado y procedencia, aislamiento
con bearer y API key limitada, y rechazo sin escrituras de valores cuando falta
la tasa mensual, aunque existan observaciones diarias.

La prueba requiere `SDS_MIGRATION_TEST_DATABASE_URL` explícita con backend
PostgreSQL y `SDS_MIGRATION_TEST_ALLOW_RESET=true` exacto. **Borra el esquema
`public` de esa base** y aplica las migraciones existentes. Usa únicamente una
base desechable y ejecuta en serie con las demás pruebas que reinician esquemas.
No toma la URL de la aplicación como alternativa. El runner debe limpiar su
base/servidor al terminar; el fixture solo libera conexiones.

Desde la raíz del repo, con ambos gates configurados fuera del repositorio:

```sh
python -m pytest api/tests/test_runtime_fx_http_postgres.py -q -rs --tb=short
```

Sin ambos gates, el caso PostgreSQL se omite; seis casos offline comprueban
que gates ausentes/incorrectos no llegan a crear un engine. Resultados y límites:
[nota de cualificación runtime](../../docs/quality/2026-09-12-runtime-fx-http-qualification.md).
Esta evidencia no establece cierre H07, readiness, aceptación de historia de
mercado ni cualificación de producción.

### Runtime de serialización canónica

El servicio admite Python 3.10-3.12. La serialización `sds-canonical-json-v1`
y la suite completa (incluidas las pruebas VARCH) requieren CPython 3.10 /
Unicode `13.0.0` fijado por el perfil. Python 3.11 y 3.12 se cualifican en una
matriz de compatibilidad acotada; Python 3.13 no está soportado porque la
dependencia fijada `SQLAlchemy==2.0.23` falla al importarse. La serialización
canónica falla cerrada si la versión Unicode no coincide, tanto en el servicio
como en los tests.
No cambies el pin ni hashes históricos para forzar un resultado verde: ejecuta
el gate completo en un entorno aislado Python 3.10 y registra por separado las
pruebas PostgreSQL, que requieren una base desechable explícitamente autorizada.

API CI ejecuta `make test-coverage` con CPython 3.10. Los comandos `make test`,
`make test-coverage`, `make api-ci-local`, `dev.ps1 Test`/`TestCoverage` y pytest
directo sobre la suite completa requieren ese mismo entorno.

En Linux/macOS, desde la raíz del repo, el bootstrap reproducible es
(recrea `api/.venv` si ya existe):

```bash
uv python install 3.10
uv venv --clear --python 3.10 api/.venv
uv pip install --python api/.venv/bin/python -r api/requirements-dev.txt
make -C api test
```

`uv venv` puede crear el entorno sin `pip`; por eso la instalación usa
explícitamente `uv pip`. No promociones los skips de PostgreSQL como cobertura
de una base de datos real.

`requirements-dev.txt` exige wheels para todas las dependencias (incluidas las
runtime): si no existe un wheel compatible, la instalación falla sin intentar
compilar fuentes. Se conservan los pins runtime y de seguridad y todas las
herramientas dev; esta política no es un lock completo de dependencias transitivas.

Para un entorno externo con `pip` instalado, el bootstrap de Make usa el mismo
intérprete seleccionado por `PYTHON` (también si existe `api/.venv`):

```bash
make -C api install-dev PYTHON=/ruta/al/venv/bin/python
make api-ci-local PYTHON=/ruta/al/venv/bin/python
```

`PIP` sigue admitiendo un override explícito; si se usa, debe apuntar al mismo
entorno que `PYTHON`.

### Auditoría del entorno resuelto

`security-scan` requiere instalar primero `requirements-dev.txt` en el entorno
seleccionado por `PYTHON`; incluye `requirements.txt`. API CI y `api-ci-local`
ya ejecutan ese paso y detienen el gate si la instalación falla. Para verificar
el gate completo desde un entorno nuevo, usa el bootstrap `uv` anterior y luego:

```bash
uv pip check --python api/.venv/bin/python
api/.venv/bin/python -c 'import sys, unicodedata; assert sys.implementation.name == "cpython"; assert sys.version_info[:2] == (3, 10); assert unicodedata.unidata_version == "13.0.0"'
api/.venv/bin/python -m pytest api/tests/test_security_makefile.py -q
make api-ci-local
```

Las tres invocaciones de `pip-audit` usan `$(PYTHON) -m pip_audit --strict`
sobre el entorno instalado, también para CycloneDX. El modo `-r` crea otro
virtualenv mediante `ensurepip`, que puede fallar con CPython 3.10 de `uv`.
Además, los rangos dev como `sphinx>=7.1.0` no son pins exactos ni un lock
transitivo: auditar el entorno ya resuelto conserva las versiones realmente
instaladas de runtime, tooling y todas sus dependencias transitivas. No se usan
`--no-deps`, `--disable-pip`, filtros de paquetes ni excepciones de vulnerabilidades.
`--strict` hace fallar la auditoría si no puede recopilar una dependencia; los
fallos de auditoría y las vulnerabilidades siguen propagándose a Make.
Véase la [documentación de pip-audit 2.10.0](https://github.com/pypa/pip-audit/tree/v2.10.0#usage).

Se conservan los nombres de artefactos por compatibilidad:
`pip-audit-requirements.json`, `pip-audit-requirements-dev.json`,
`runtime-sbom.cdx.json` y `runtime-license-inventory.json`. Todos describen el
entorno instalado completo; en CI incluyen runtime y dev, no una selección
exclusiva de runtime. Los dos JSON de auditoría cubren el mismo conjunto.
La política de licencias sigue aplicándose a todos los componentes del SBOM.
Un entorno incompleto no demuestra cobertura de los requirements: instala
siempre el conjunto dev completo antes de ejecutar estos targets directamente.
Los informes registran versiones resueltas; repetir el bootstrap puede resolver
versiones distintas de los rangos abiertos. Se requiere acceso público a PyPI,
sin credenciales ni secretos.

### Linux / macOS / Git Bash

```bash
cd api
make test
make test-coverage
```

### Windows PowerShell

```powershell
Set-Location api
.\scripts\dev.ps1 Test
.\scripts\dev.ps1 TestCoverage
```

### Optional Docker smoke paths

`api/Dockerfile` usa CPython 3.10 / Unicode `13.0.0`, compatible con el perfil
canónico. La imagen instala solo dependencias de servicio; su build y el
workflow Docker Quickstart Smoke no ejecutan la suite canónica. El workflow
arranca Compose, comprueba `/healthz` y `/ready`, y continúa con
`NO_START=1 bash scripts/smoke_stack.sh minimal`; ante un fallo conserva
diagnóstico de Compose antes de su limpieza garantizada. La conformidad de la
suite canónica se valida en API CI.

```powershell
Set-Location api

# Local DB-backed runtime
.\scripts\dev.ps1 Up

# Self-host reference profile. Localhost flags are for local verification only.
.\scripts\dev.ps1 ProductionInstall -AllowedOrigin http://localhost:8092 -AllowLocalhostOrigin -ApiPort 8092 -PostgresPort 55433
```

### Pytest directo

```bash
cd api
python -m pytest tests/ -v --tb=short
python -m pytest tests/ --cov=src --cov-report=term --cov-report=html
```

---

## Suites recomendadas por objetivo

### Verificación rápida antes de una prueba operativa

```powershell
Set-Location api
.\scripts\dev.ps1 Up
.\scripts\smoke_stack.ps1 -Profile minimal
```

El perfil local expone `/docs` con Try it out y ejemplos editables. El smoke
comprueba health, docs, login admin, jerarquías, valores, importación bulk y
dependencias de cálculo sobre el runtime activo.

### Validación del backend de unidades

```bash
cd api
python -m pytest tests/test_unit_catalog_expansion.py -q
python -m pytest tests/test_bootstrap_units.py -q
python -m pytest tests/test_reference_data_builders.py -q
python -m pytest tests/test_main_lifespan_db_required.py -q
```

### Validación del motor de conversiones

Desde la raiz del repo:

```powershell
make conversion-gate
```

Comandos equivalentes por bloque desde `api/`:

```powershell
Set-Location api
python -m pytest tests/test_conversion_dimensions.py tests/test_unit_expression_parser.py tests/test_physical_conversion_engine.py -q
python -m pytest tests/test_reference_data_builders.py tests/test_unit_catalog_expansion.py tests/test_bootstrap_units.py -q
python -m pytest tests/test_fx_service.py tests/test_fx_repository_service.py tests/test_fx_history_import.py tests/test_conversion_orchestrator.py tests/test_bootstrap_conversion_catalog.py tests/test_fx_api.py -q
python -m pytest tests/test_calculation_engine_conversion_contracts.py tests/test_value_ingest_conversion_trace.py tests/test_value_csv_import.py tests/test_import_*sds_package*.py tests/test_calculation_contract_import.py -q
```

### Validación de indicadores y mappings

```bash
cd api
python -m pytest tests/test_indicator_integration.py -q
python -m pytest tests/test_crosswalk_integration.py -q
```

### Validación de interoperabilidad runtime

```bash
cd api
python -m pytest tests/test_runtime_readiness.py tests/test_interoperability_readiness_api.py -q
python -m pytest tests/test_value_resolution_service.py tests/test_value_resolution_api.py -q
```

Objetivo:
- comprobar que `/api/v1/interoperability/readiness` informa readiness sin
  escribir datos,
- comprobar que `/api/v1/values/resolve` resuelve por valor directo, cálculo,
  mapping equivalente y conversión de unidad,
- comprobar que mappings no equivalentes y conversiones incompatibles fallan
  cerradas.
- comprobar que mappings no equivalentes autorizados por un puente certificado,
  como GRI 302-1.e desde componentes ESRS, exponen `execution_authority` y
  `bridge_id` sin usar el mapping como fórmula.

### Validación de carga operativa de valores

```bash
cd api
python -m pytest tests/test_value_import_batch_stability.py tests/test_value_import_performance_gate.py -q
# Solo tras aprovisionar PostgreSQL 15 aislado y desechable con claves externas:
# export SDS_VALUE_IMPORT_DISPOSABLE_DATABASE_URL='<URL-EXTERNA-DE-PRUEBA>'
# export DATABASE_URL="$SDS_VALUE_IMPORT_DISPOSABLE_DATABASE_URL"
# export SDS_VALUE_IMPORT_DISPOSABLE_ALLOW_WRITE=true
# export VALUE_REVISION_PRIMARY_READ_PATH=revision
# JWT_SECRET_KEY y EXPORT_SIGNING_SECRET: configuración externa, no versionada.
make gate-value-import-performance
```

Objetivo:
- comprobar que la preparación batch mantiene paridad con el flujo fila a
  fila,
- comprobar que los errores de batch identifican fila CSV y `external_key`,
- comprobar que los batches mixtos se persisten por el carril auditado normal,
- comprobar que una reimportación idéntica no crea revisiones/eventos
  espurios,
- comprobar en PostgreSQL 15 desechable que 1.000 valores estrictos se
  importan y verifican por debajo de 30 segundos, y confirmar después la
  eliminación de la instancia/volumen completos; nunca purgar filas del
  historial inmutable.
- El gate es un tripwire de regresión de tamaño fijo sobre un dataset sintético
  favorable (2 conceptos, 1 entidad, `batch_size=250`). No es una garantía de
  throughput productivo, concurrencia ni gran volumen; si PostgreSQL no está
  disponible, el resultado correcto es `not_evaluated`, no una conclusión de
  rendimiento. Las variables de opt-in y la coincidencia de URL son una
  barrera contra errores de operador, no una prueba de desechabilidad: el
  aprovisionamiento y la eliminación de la instancia son responsabilidad del
  arnés externo. El informe predeterminado queda ignorado y local, sin
  convertirse automáticamente en evidencia pública.
- La matriz E04/R8 usa el mismo PostgreSQL 15 desechable y un inquilino
  explícito: `make gate-raw-value-transform-matrix`. Tanto la matriz como su
  informe predeterminados son salidas locales ignoradas. El resultado formal
  anterior es una instantánea histórica, no una validación de los bytes actuales.

### Gates repo-locales desde la raíz

```powershell
make e1-gate
make e2-gate
make e2-gate-detection
make gate-semantics
wsl.exe --cd "<ruta-repo-WSL>" -- "<python-3.10-WSL>" scripts/run_e6_gate.py --register-package-dir "<ruta-paquete-Atomizer-WSL>"
make e8-gate
make e9-gate
make e10-gate
```

---

## Áreas clave de regresión

### Startup y ciclo de vida

Ficheros clave:
- `tests/test_main.py`
- `tests/test_main_lifespan_db_required.py`
- `tests/test_require_database_mode.py`

Objetivo:
- evitar regresiones en bootstrap, health/readiness y selección de backend.

### Unidades

Ficheros clave:
- `tests/test_unit_converter.py`
- `tests/test_unit_converter_extended.py`
- `tests/test_unit_database.py`
- `tests/test_unit_catalog_expansion.py`
- `tests/test_bootstrap_units.py`
- `tests/test_reference_data_builders.py`

Objetivo:
- mantener conversiones, alias, bootstrap PostgreSQL, trazabilidad de fuente y consistencia del catálogo bundled.

### Conversiones fisicas y FX

Ficheros clave:
- `tests/test_conversion_dimensions.py`
- `tests/test_unit_expression_parser.py`
- `tests/test_physical_conversion_engine.py`
- `tests/test_fx_service.py`
- `tests/test_fx_repository_service.py`
- `tests/test_fx_history_import.py`
- `tests/test_conversion_orchestrator.py`
- `tests/test_reference_data_builders.py`
- `tests/test_bootstrap_conversion_catalog.py`
- `tests/test_calculation_engine_conversion_contracts.py`
- `tests/test_value_ingest_conversion_trace.py`
- `tests/test_value_csv_import.py`
- `tests/test_import_*sds_package*.py`
- `tests/test_calculation_contract_import.py`
- `tests/test_fx_api.py`

Objetivo:
- mantener separadas conversiones fisicas y FX historico,
- bloquear dimensiones incompatibles, tipos ausentes, divisas ambiguas, seeds ISO4217 incompletas, metadata temporal incompleta e importaciones FX no idempotentes,
- preservar trazas reproducibles en ingesta, calculo y paquetes SDS externos.

### E1 / E2 / importación

Ficheros clave:
- `tests/test_indicator_integration.py`
- `tests/test_crosswalk_integration.py`
- `tests/test_import_indicators_script.py`
- `tests/test_prepare_*adapter.py`
- `tests/test_sync_*exports.py`

Objetivo:
- proteger adaptadores de compatibilidad, el contrato de paquete SDS y el runtime de indicadores/mappings.

### Seguridad y auth

Ficheros clave:
- `tests/test_auth_system.py`
- `tests/test_api_security.py`
- `tests/test_api_keys.py`
- `tests/test_e2e_auth_flows.py`

Objetivo:
- mantener login, refresh, logout, API keys y autorización por permisos.

---

## Interpretación de resultados

### Si falla la suite completa

Prioridad de diagnóstico:
1. errores de arranque / imports
2. errores de auth
3. errores de DB/bootstrap
4. errores de catálogo de unidades
5. errores de documentación/meta-tests

### Si falla cobertura

La referencia operacional es:
- mantener el objetivo **≥97,01%**,
- priorizar cobertura material de runtime, seguridad, bootstrap y rutas operativas críticas antes que perseguir líneas marginales.
- evitar publicar counts exactos de tests salvo que provengan de una ejecución fresca documentada.

### Si falla un gate repo-local

Revisa:
- `deliverables/`
- `docs/policies/`
- `docs/governance/`
- scripts `e1_*`, `e2_*`, `e3_*`, `e6_*`

---

## Buenas prácticas al añadir tests

1. Prioriza casos que cubran riesgos reales de operación, no solo líneas.
2. Mantén los tests offline-safe salvo que el objetivo sea explícitamente DB-backed.
3. Usa fixtures y overrides de FastAPI en lugar de depender de infraestructura real cuando no haga falta.
4. Cuando documentes nuevas métricas, publícalas solo si tienen verificación reproducible reciente.
5. Si añades un nuevo workflow de operador, añade al menos un test o meta-test que bloquee drift documental o contractual.
6. Antes de empujar cambios que puedan activar API CI, ejecuta `make api-ci-local` desde la raíz o instala el guard con `make install-hooks`.

---

## Referencias

- [Getting Started](getting-started.md) — cómo arrancar el entorno para pruebas manuales
- [Deployment](deployment.md) — como validar self-host de referencia y patrones avanzados
- [Troubleshooting](troubleshooting.md) — fallos típicos durante verificación
