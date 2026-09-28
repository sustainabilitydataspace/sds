# Getting Started — SustainabilityDataSpace API

> **Versión:** 1.5 | **Actualizado:** 2026-09-16
> **Tiempo:** 5 min (local) / 15 min (DB-backed opcional con Docker Compose)

Guía de arranque para evaluación en un entorno Windows/Linux local. El arranque
no autoriza el uso productivo propio ni la prestación de servicios a terceros;
véase «Alcance de uso previsto» en el `README.md` de la raíz. Esta versión ya asume:
- paquetes SDS externos al repo público para indicadores, valores y mapeos,
- API FastAPI validada en Windows,
- bootstrap automático de esquema, datos bundled y unidades PostgreSQL en DB vacía.

---

## ⚡ TL;DR

El servicio admite Python 3.10-3.12. La serialización `sds-canonical-json-v1`
y la suite completa requieren CPython 3.10 / Unicode `13.0.0`; en versiones
con otros datos Unicode la serialización falla cerrada. Para desarrollar y
ejecutar tests, usa [el entorno canónico](tests.md#runtime-de-serialización-canónica).

La instalación nativa es la ruta principal y Docker Compose es un quickstart
DB-backed opcional. En cualquiera de las dos, `.env` contiene secretos locales:
cópialo desde `.env.example` y nunca lo versiones.

### Windows PowerShell (local, sin Docker)

```powershell
git clone <repo> sustainabilityDataSpace
Set-Location sustainabilityDataSpace
.\api\scripts\dev.ps1 Setup
# Validar un paquete externo si lo tienes disponible
# ver api\docs\import-packages.md
Set-Location .\api
.\scripts\dev.ps1 Start
```

### Linux / macOS / Git Bash (local, sin Docker)

```bash
git clone <repo> sustainabilityDataSpace
cd sustainabilityDataSpace/api
# macOS/Homebrew: usa Python 3.12 como runtime local más reciente soportado:
# brew install python@3.12
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
cd ..
# Validar un paquete externo si lo tienes disponible
# ver api/docs/import-packages.md
cd api
make start
```

### Optional Docker quickstart installers

```powershell
# Local DB-backed runtime
Set-Location sustainabilityDataSpace\api
.\scripts\dev.ps1 Up

# Optional self-host reference profile for evaluation only
.\scripts\dev.ps1 ProductionInstall -AllowedOrigin https://your-domain.example
```

These Docker helpers are public quickstart/reference packaging for evaluation
and human testing. They are not the required public API dependency-install
model.

The public repository does not include local UI prototypes, historical
`archive/` working copies, session handoff markers, or operational data
packages. Keep those artifacts in ignored/local storage unless they are
deliberately promoted through a publication review.

### Validación local antes de push

Desde la raíz del repo:

```bash
make api-ci-local
make install-hooks
```

El primer comando replica API CI con lint, security scan y tests. El segundo
instala un hook local `pre-push` para que los pushes directos a `main` no salgan
si cambian entradas de API CI y el gate local falla.

For local verification of the self-host profile on the same machine:

```powershell
.\scripts\dev.ps1 ProductionInstall -AllowedOrigin http://localhost:8092 -AllowLocalhostOrigin -ApiPort 8092 -PostgresPort 55433
```

### Optional Docker Compose (API + PostgreSQL)

```bash
cd api
cp .env.example .env
# Editar .env: JWT_SECRET_KEY, POSTGRES_PASSWORD, BOOTSTRAP_ADMIN_PASSWORD
# ALLOWED_ORIGINS recomendado: ["http://localhost:8090"]
make up
NO_START=1 bash scripts/smoke_stack.sh minimal
make down
```

```powershell
Set-Location api
Copy-Item .env.example .env -ErrorAction SilentlyContinue
# Editar .env: JWT_SECRET_KEY, POSTGRES_PASSWORD, BOOTSTRAP_ADMIN_PASSWORD
# ALLOWED_ORIGINS recomendado: ["http://localhost:8090"]
.\scripts\dev.ps1 Up
.\scripts\smoke_stack.ps1 -Profile minimal -NoStart
.\scripts\dev.ps1 Down
```

---

## 📋 Requisitos

### Software

| Componente | Versión | Verificar |
|------------|---------|-----------|
| Python | 3.10-3.12 | `python --version` / `python3 --version` |
| Git | 2.30+ | `git --version` |
| Docker | 20.10+ (opcional) | `docker --version` |

> Python 3.13 no forma parte del runtime soportado: la dependencia fijada
> `SQLAlchemy==2.0.23` no es compatible con Python 3.13. Usa Python 3.10-3.12;
> para la serialización canónica y la suite completa, usa CPython 3.10.

### Hardware mínimo

- **RAM:** 2 GB local / 4 GB si levantas PostgreSQL con persistencia real
- **Disco:** 5 GB libres
- **Puertos:** `8090` (API), `5432` o `POSTGRES_PORT` (PostgreSQL compose)

### Equivalencias Windows

| Bash / Make (service-level) | Windows PowerShell |
|-----------------------------|--------------------|
| `make start` | `.\scripts\dev.ps1 Start` |
| `make up` | `.\scripts\dev.ps1 Up` |
| `make up` | `.\scripts\dev.ps1 Up` (optional Docker quickstart) |
| n/a | `.\scripts\dev.ps1 ProductionInstall -AllowedOrigin https://your-domain.example` (optional self-host reference) |
| `make down` | `.\scripts\dev.ps1 Down` |
| `make test` | `.\scripts\dev.ps1 Test` |
| `bash scripts/smoke_stack.sh minimal` | `.\scripts\smoke_stack.ps1 -Profile minimal` |
| `bash scripts/backup.sh` | `.\scripts\backup.ps1` |
| `bash scripts/restore.sh --yes --require-manifest backups/file.sql` | `.\scripts\restore.ps1 backups\file.sql -Force -RequireManifest` |

Un restore no interactivo (`--yes` / `-Force`) exige verificación de checksum: usa el
manifiesto sidecar (`backups/file.sql.manifest.json`, generado por `backup.sh`) con
`--require-manifest` / `-RequireManifest`. Si el manifiesto registra otra base/usuario que
el contenedor destino, el restore se rechaza salvo `--allow-target-mismatch` /
`-AllowTargetMismatch`; un restore sin verificar solo procede con la bandera explícita de
emergencia `--allow-unverified-restore` / `-AllowUnverifiedRestore`.

Desde la raíz del repo, `make api-ci-local` es el gate combinado para cambios
que deben pasar API CI. `make install-hooks` instala el guard local pre-push.

---

## 1. Clonar el repositorio

```bash
git clone <url-del-repositorio> sustainabilityDataSpace
cd sustainabilityDataSpace
```

Estructura relevante:

```text
sustainabilityDataSpace/
├── data/processed/         # Registro de importación generado
├── data/extracted/analysis # Evidencia y exports derivados/publicados
├── api/       # API FastAPI
└── scripts/                # Helpers de importación, gates y preparación E1/E2/E3/E6
```

---

## 2. Ejecutar en modo local DB completo

El runtime local de SDS usa PostgreSQL como backend obligatorio. No hay modo
demo/offline para validar Swagger, mappings, valores, fórmulas o semántica.

Útil para verificar:
- persistencia real,
- bootstrap automático,
- catálogo de unidades en PostgreSQL,
- login/admin seeded,
- indicadores y mappings persistidos,
- readiness y resolución runtime de interoperabilidad sobre los datos cargados.

Requiere:
- `REQUIRE_DATABASE=true`
- `POSTGRES_PASSWORD`
- `BOOTSTRAP_ADMIN_PASSWORD`
- `JWT_SECRET_KEY`

---

## 3. Configurar `.env`

`.env` es una copia local de `.env.example` para contraseñas, claves y otros
secretos de tu entorno. Está ignorado por git y nunca se versiona.

### Desarrollo local DB-backed

```env
REQUIRE_DATABASE=true
DATABASE_URL=postgresql://sds:[REDACTED]@localhost:5432/sds
POSTGRES_PASSWORD=password
JWT_SECRET_KEY=dev-secret-cambiar-en-produccion-32-chars
ALLOWED_ORIGINS=["http://localhost:8090"]
SEED_DEFAULT_USERS=true
BOOTSTRAP_ADMIN_PASSWORD=admin123
SEED_REFERENCE_DATA_ON_STARTUP=true
```

### Docker Compose DB-backed opcional

```env
REQUIRE_DATABASE=true
POSTGRES_PASSWORD=cambia_esta_password
BOOTSTRAP_ADMIN_PASSWORD=admin123
JWT_SECRET_KEY=cambia_esto_usa_32_caracteres_minimo
ALLOWED_ORIGINS=["http://localhost:8090"]
POSTGRES_PORT=5432
SEED_REFERENCE_DATA_ON_STARTUP=true
SEED_DEFAULT_USERS=true
```

El instalador local deja el administrador como `admin` / `admin123`. No uses
esa contraseña en producción.

Notas:
- `ALLOWED_ORIGINS` se recomienda como JSON array; el runtime actual también acepta un origen único o lista separada por comas.
- Si `5432` está ocupado en tu máquina Windows, usa por ejemplo `POSTGRES_PORT=55432`.
- Con `REQUIRE_DATABASE=true`, el runtime usa PostgreSQL para unidades automáticamente.

---

## 4. Preparar datos

### 4.1 Paquete SDS externo

El repo público de SDS no incluye paquetes operativos de indicadores, valores ni
mapeos canónicos. Genera o descarga un paquete compatible con el contrato SDS
desde tu proceso externo y pásalo a SDS como ruta externa. El paquete de
indicadores/valores puede incluir:

- `sds_dataset_register.csv` para indicadores,
- `sds_dataset_register.json` como sibling opcional,
- `sds_values.csv` como paquete opcional de valores operativos.

La guía completa de paquetes está en [Import Packages](import-packages.md).

Los mapeos canónicos usan un paquete separado con releases, datapoints,
assertion groups y assertion components. Ese flujo se valida/importa en shadow
desde [Import Packages](import-packages.md) antes de materializar mappings.

### 4.2 Validar o consumir el paquete SDS

El helper valida cualquier paquete compatible con el contrato SDS.

```bash
python scripts/import_sds_package.py --package-dir <path-to-sds-package> --dry-run
```

Usa esta opción para validar un paquete externo. Si existe `sds_values.csv`, el dry-run también valida su contrato. Si no existe, el script valida solo indicadores y muestra un aviso de salto de valores. Ejecuta el mismo comando sin `--dry-run` para aplicar la carga.

---

## 4.3 Probar interoperabilidad runtime en Swagger

Para una prueba manual real, levanta SDS con `REQUIRE_DATABASE=true`, carga
catálogos/valores/mappings mediante paquetes o API, entra en `/docs` y sigue
este flujo:

1. Ejecuta `POST /auth/login` y autoriza Swagger con el `access_token`.
2. Ejecuta `GET /api/v1/interoperability/readiness`.
3. Empieza por el código estándar que conoces de ESRS/GRI/GHG. Puede ser un
   disclosure/concepto calculable o un datapoint granular. Para un cálculo,
   ejecuta `GET /api/v1/calculate/dependencies/{concept}`; para una
   equivalencia, ejecuta `GET /api/v1/mappings/from/{standard}/{code}`.
4. SDS devuelve las variables internas, mappings o contratos disponibles. Solo
   si faltan valores, créalos en `POST /api/v1/values`.
5. Ejecuta `POST /api/v1/calculate` o `POST /api/v1/values/resolve` con los
   ejemplos editables.

Pruebas manuales recomendadas:

- cálculo `urn:sds:disclosure:csrd:e3-5` descubriendo antes sus inputs con
  `GET /api/v1/calculate/dependencies/urn:sds:disclosure:csrd:e3-5`,
- mapping equivalente `csrd:E3-4_05 -> gri:303-5.c`,
- conversión física `kWh -> MWh`,
- negativa cerrada `GJ -> t CO2e` cuando no hay contrato con factor de emisión.

La guía completa está en
[Runtime Interoperability Guide](interoperability-runtime.md).

---

## 5. Arrancar la API

### Opción A: local

```bash
cd api
make start
```

```powershell
Set-Location api
.\scripts\dev.ps1 Start
```

### Opción B: Docker Compose opcional

```bash
cd api
make up
make logs
```

```powershell
Set-Location api
.\scripts\dev.ps1 Up
.\scripts\dev.ps1 Logs
```

En un volumen PostgreSQL vacío, el arranque:
- crea el esquema,
- seeda indicadores bundled,
- seeda mappings bundled,
- seeda `unit_categories`, `units` y `conversion_rules`,
- crea el admin inicial si `SEED_DEFAULT_USERS=true`.

### 5.1 Refrescar datos con el paquete actual (opcional)

Hazlo si quieres que la DB refleje un package externo de indicadores/valores, en vez del snapshot bundled del contenedor:

```bash
cd api
source .venv/bin/activate
POSTGRES_PASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
POSTGRES_PORT="$(grep '^POSTGRES_PORT=' .env | cut -d= -f2-)"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
cd ..
python scripts/import_sds_package.py --package-dir <path-to-sds-package> --db-url "postgresql://sds:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/sds"
cd api
# Public /api/v1/mappings is served from materialized_pairwise_mappings, not the legacy
# standard_mappings table; materialize the canonical read model after importing a package.
python scripts/materialize_canonical_pairwise_mappings.py --db-url "postgresql://sds:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/sds"
```

```powershell
Set-Location <repo-root>
.\api\.venv\Scripts\python.exe .\scripts\import_sds_package.py --package-dir <path-to-sds-package>
Set-Location api
.\.venv\Scripts\python.exe scripts\materialize_canonical_pairwise_mappings.py
```

---

## 6. Verificación

### Health y readiness

```bash
curl -fsS http://localhost:8090/healthz
curl -fsS http://localhost:8090/ready
```

### Login

```bash
BOOTSTRAP_ADMIN_PASSWORD="$(grep '^BOOTSTRAP_ADMIN_PASSWORD=' .env | cut -d= -f2-)"
curl -sS -X POST http://localhost:8090/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"${BOOTSTRAP_ADMIN_PASSWORD}\"}"
```

```powershell
$adminPassword = (Get-Content .env | Where-Object { $_ -like 'BOOTSTRAP_ADMIN_PASSWORD=*' } | ForEach-Object { $_.Split('=',2)[1] })
$body = "{""username"":""admin"",""password"":""$adminPassword""}"
Invoke-RestMethod -Uri "http://localhost:8090/auth/login" -Method Post -Body $body -ContentType "application/json"
```

### Indicadores cargados

```bash
curl -sS "http://localhost:8090/api/v1/indicators?limit=1" \
  -H "Authorization: Bearer $TOKEN" | jq '.total'
```

Esperado:
- `> 0` siempre que la DB local tenga el bootstrap de referencia cargado,
- el total exacto depende del paquete externo o del snapshot bundled cargado.

---

## 🆘 Troubleshooting rápido

- `error parsing value for field "allowed_origins"`
  Usa preferiblemente `ALLOWED_ORIGINS=["http://localhost:8090"]`; el runtime también acepta un origen único o CSV.

- `Bind for 0.0.0.0:5432 failed`
  Define `POSTGRES_PORT=55432` en `api/.env`.

- `indicators total = 0`
  Si trabajas con el helper `ImportIndicators`, usa un CSV de indicadores externo compatible. Si importas un paquete SDS externo, valida primero `python scripts/import_sds_package.py --package-dir <path-to-sds-package> --dry-run` y luego ejecuta el mismo script sin `--dry-run`. Desde la API puedes validar con `POST /api/v1/indicators/import-csv-validations`; usa el CLI soportado para importar. En modo DB, `POST /api/v1/indicators/import-csv-jobs` devuelve `503` antes de admisión para CSV y `validation_id`, incluso si `result_body.valid=true`. H15 sigue abierto y no se reparan jobs existentes. Si la DB está vacía, también puedes recrear el stack para que haga bootstrap bundled.

- `login 401` en una DB nueva
  Verifica `SEED_DEFAULT_USERS=true` y `BOOTSTRAP_ADMIN_PASSWORD` en `.env`.

- Más casos: [Troubleshooting](troubleshooting.md)

---

## 📚 Siguientes lecturas

- [Configuration](configuration.md) — variables de entorno y backend de unidades
- [Deployment](deployment.md) — operacion, self-host de referencia y patrones avanzados
- [API Reference](api.md) — endpoints y ejemplos
- [Troubleshooting](troubleshooting.md) — resolución detallada de problemas
