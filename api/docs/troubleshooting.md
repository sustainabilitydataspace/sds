# Troubleshooting — SustainabilityDataSpace API

> **Versión:** 1.4 | **Actualizado:** 2026-05-26

Guía de resolución para entornos locales Windows/Linux.
La ruta `ProductionInstall` de esta guía es una referencia técnica de
evaluación, no una autorización de uso productivo propio ni de servicios a
terceros; véase «Alcance de uso previsto» en el `README.md` de la raíz.

---

## 🆘 Atajos rápidos

### Repo root

```bash
make api-ci-local
make install-hooks
```

`make api-ci-local` ejecuta el mismo gate fuente que API CI: lint, auditoría de
seguridad y tests. `make install-hooks` instala el guard local `pre-push` para
pushes directos a `main` que toquen entradas de API CI.

### Windows PowerShell

El servicio admite Python 3.10-3.12. La serialización `sds-canonical-json-v1`
y la suite completa requieren CPython 3.10 / Unicode `13.0.0`; en versiones
con otros datos Unicode la serialización falla cerrada. Antes de `Test` o
`make test`, prepara [el entorno canónico](tests.md#runtime-de-serialización-canónica).

```powershell
Set-Location api
.\scripts\dev.ps1 Setup
.\scripts\dev.ps1 Start
.\scripts\dev.ps1 Up
.\scripts\dev.ps1 ProductionInstall -AllowedOrigin https://your-domain.example
.\scripts\dev.ps1 Test
.\scripts\smoke_stack.ps1 -Profile minimal
```

Use `Up` for the local DB-backed quickstart and `smoke_stack.ps1` for a compact
runtime check. Use `ProductionInstall` for the optional self-host reference
profile; Docker logs use `.\scripts\dev.ps1 ProductionLogs`. Native `Setup` +
`Start` remains the canonical public API install path.

### Linux / macOS / Git Bash

```bash
cd api
PYTHON=python3 make setup   # supported: Python 3.10-3.12
make start
make up
make test
bash scripts/smoke_stack.sh minimal
```

---

## 🔍 Problemas comunes

### GitHub muestra `API CI: All jobs have failed`

**Causas típicas**

1. Se ejecutó solo `make test` localmente, pero API CI también exige lint y
   security scan.
2. El fallo aparece solo en Linux/macOS por una suposición de ruta Windows.
3. El push tocó `api/**`, `.github/workflows/api-ci.yml` o
   `docs/quality/acceptance_gates.md` sin pasar el gate local combinado.

**Solución**

Desde la raíz del repo:

```bash
make api-ci-local
make install-hooks
```

Corrige el primer fallo que aparezca y vuelve a ejecutar `make api-ci-local`.
El hook instalado por `make install-hooks` bloquea pushes directos a `main` si
ese gate falla. El wrapper de importación de registros SDS ya detecta tanto
`api/.venv/Scripts/python.exe` como `api/.venv/bin/python` antes de usar el
Python actual, para que los tests no dependan de rutas exclusivas de Windows.

---

### `error parsing value for field "allowed_origins"`

**Síntoma**

```text
pydantic_settings.sources.SettingsError: error parsing value for field "allowed_origins"
```

**Causa**

`ALLOWED_ORIGINS` tiene una lista mal formada o caracteres sin escapar.

**Solución**

```env
ALLOWED_ORIGINS=["http://localhost:8090"]
```

El runtime actual también acepta un único origen o una lista separada por
comas, pero el formato JSON array es el recomendado para evitar ambigüedades.

---

### `Bind for 0.0.0.0:5432 failed`

**Causa**

Otro servicio ya usa el puerto `5432`.

**Solución**

En `api/.env`:

```env
POSTGRES_PORT=55432
```

Luego reinicia compose:

```powershell
.\scripts\dev.ps1 Down
.\scripts\dev.ps1 Up
```

---

### Login `401` en una base nueva

**Causa**

No se creó el admin inicial o el password no coincide con `.env`.

**Verificar**

```env
SEED_DEFAULT_USERS=true
BOOTSTRAP_ADMIN_PASSWORD=tu_password
```

**Solución**

1. Verifica que `.env` tenga esos valores antes de arrancar.
2. Si la DB ya existía con otro estado, recrea el stack:

```bash
cd api
docker compose --env-file .env -f compose.yml down -v
docker compose --env-file .env -f compose.yml up -d --build
```

---

### `Unknown entity` en `POST /api/v1/values/import`

**Causa**

La ingesta estricta valida que `entity` exista en una jerarquía activa de la
compañía antes de persistir valores. Esto evita aceptar observaciones sin
perímetro organizativo.

**Solución**

En Swagger ejecuta primero `POST /api/v1/hierarchies` y usa una jerarquía activa
que incluya la entidad que vas a importar. Por ejemplo, para Nordhaven:

```json
{
  "id": "nh_es_valencia_plant",
  "name": "Valencia Plant",
  "parent": "nh_country_es",
  "level": 3
}
```

Después repite `POST /api/v1/values/import` con la misma `external_key`. Si la
entidad ya existe, confirma que estás autenticado con el mismo `company_id` y
que la jerarquía está activa.

---

### `indicators total = 0`

**Causas posibles**

1. No se cargó un paquete SDS externo ni un CSV de indicadores
2. No se refrescó la base después de elegir el paquete o CSV
3. El bootstrap inicial no llegó a completarse

**Ruta recomendada**

```bash
cd <repo-root>
cd api
source .venv/bin/activate
POSTGRES_PASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
POSTGRES_PORT="$(grep '^POSTGRES_PORT=' .env | cut -d= -f2-)"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
cd ..
python scripts/import_sds_package.py --package-dir <path-to-sds-package> --dry-run --db-url "postgresql://sds:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/sds"
python scripts/import_sds_package.py --package-dir <path-to-sds-package> --db-url "postgresql://sds:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/sds"
```

```powershell
Set-Location <repo-root>
.\api\.venv\Scripts\python.exe .\scripts\import_sds_package.py --package-dir <path-to-sds-package> --dry-run
.\api\.venv\Scripts\python.exe .\scripts\import_sds_package.py --package-dir <path-to-sds-package>
```

**Nota**

En una DB vacía, el arranque ya seedea indicadores bundled. Si trabajas con un paquete SDS externo, valida primero con `--dry-run` y aplica después el mismo comando sin `--dry-run`.

Si sigues viendo `0`, revisa logs del contenedor API y del arranque de la app.

---

### `mappings total = 0`

**Solución**

```bash
cd api
POSTGRES_PASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
POSTGRES_PORT="$(grep '^POSTGRES_PORT=' .env | cut -d= -f2-)"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
# Public /api/v1/mappings reads the canonical materialized_pairwise_mappings read model.
python scripts/materialize_canonical_pairwise_mappings.py --db-url "postgresql://sds:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/sds"
```

```powershell
Set-Location api
.\.venv\Scripts\python.exe scripts\materialize_canonical_pairwise_mappings.py
```

> `import_standard_mappings.py` / `dev.ps1 ImportMappings` populate the legacy
> `standard_mappings` table and are now only parity/comparison tooling — they do **not**
> repair the public `/api/v1/mappings` surface, which is served from
> `materialized_pairwise_mappings`.

---

### `relation "..." does not exist`

**Causa**

El esquema no llegó a crearse correctamente en el arranque.

**Solución recomendada**

Reinicia la API o el stack para que vuelva a ejecutar `init_db()`:

```bash
cd api
docker compose --env-file .env -f compose.yml down
docker compose --env-file .env -f compose.yml up -d --build
```

**Fallback manual**

```bash
cd api
python -c "from src.database.init_db import init_db; init_db(); print('Schema OK')"
```

```powershell
Set-Location api
& .\.venv\Scripts\python.exe -c "from src.database.init_db import init_db; init_db(); print('Schema OK')"
```

---

### `units = 0` en PostgreSQL

**Interpretación correcta**

- En el runtime local/API soportado, `REQUIRE_DATABASE=true` y PostgreSQL es el
  backend autoritativo. Las tablas de unidades deberían llenarse en el
  arranque.
- `REQUIRE_DATABASE=false` queda reservado para pruebas aisladas; no es un modo
  válido para validar Swagger, mappings, valores, fórmulas o semántica.

**Verificar**

```bash
docker compose --env-file .env -f compose.yml exec -T postgres \
  psql -U sds -d sds -c "SELECT COUNT(*) FROM units;"
```

**Reforzar bootstrap manualmente**

```bash
cd api
python -c "from src.database.session import SessionLocal; from src.database.bootstrap_units import bootstrap_units_if_empty; db=SessionLocal(); print(bootstrap_units_if_empty(db)); db.close()"
```

---

### `No module named src`

**Causa**

No estás en `api` o no activaste/creaste `.venv`.

**Solución**

```bash
cd <repo-root>/api
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

```powershell
Set-Location api
.\scripts\dev.ps1 Setup
```

---

### macOS: entorno creado con Python 3.13

**Síntoma**

La instalación o el arranque falla con Python 3.13. Entre los síntomas posibles
están errores de compilación de dependencias antiguas o un fallo al importar
`SQLAlchemy==2.0.23`.

**Causa**

Python 3.13 no forma parte del runtime soportado. La matriz de servicio cubre
Python 3.10-3.12; la serialización canónica y la suite completa usan CPython
3.10 / Unicode 13.0.0.

**Solución**

```bash
cd <repo-root>/api
deactivate 2>/dev/null || true
rm -rf .venv
git pull
# Instala la versión soportada más reciente si hace falta:
brew install python@3.12
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Verifica antes de continuar:

```bash
python --version  # debe ser 3.10, 3.11 o 3.12
make check-python
```

---

### Puerto `8090` ocupado

**Windows**

```powershell
netstat -ano | findstr :8090
taskkill /PID <PID> /F
```

**Linux / macOS**

```bash
lsof -i :8090
kill -9 <PID>
```

O cambia `PORT` en `.env`.

---

### `smoke_stack` falla

**Diagnóstico mínimo**

```bash
cd api
make logs
curl -fsS http://localhost:8090/healthz
curl -fsS http://localhost:8090/ready
```

**Causas típicas**

- `.env` incompleto
- `POSTGRES_PORT` ocupado
- bootstrap de admin o referencia incompleto
- arranque todavía en progreso

Recomendación: usa primero el perfil `minimal`.

---

### PowerShell execution policy bloquea scripts

**Síntoma**

```text
... cannot be loaded because running scripts is disabled ...
```

**Solución**

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

## Diagnóstico guiado

### 1. API responde

```bash
curl -fsS http://localhost:8090/healthz
curl -fsS http://localhost:8090/ready
```

### 2. La DB responde

```bash
cd api
docker compose --env-file .env -f compose.yml exec -T postgres pg_isready -U sds -d sds
```

### 3. El admin existe

```bash
docker compose --env-file .env -f compose.yml exec -T postgres \
  psql -U sds -d sds -c "SELECT username FROM user_accounts;"
```

### 4. Hay indicadores y unidades

```bash
docker compose --env-file .env -f compose.yml exec -T postgres \
  psql -U sds -d sds -c "SELECT COUNT(*) FROM indicators;"
docker compose --env-file .env -f compose.yml exec -T postgres \
  psql -U sds -d sds -c "SELECT COUNT(*) FROM units;"
```

---

## Recopilación para soporte

Si necesitas abrir un issue o preparar un informe interno, recopila:

```bash
cd api
docker compose --env-file .env -f compose.yml ps
docker compose --env-file .env -f compose.yml logs --tail=100 api
curl -fsS http://localhost:8090/healthz
curl -fsS http://localhost:8090/ready
```

Y adjunta:
- `api/.env` saneado (sin passwords ni secrets),
- los últimos logs del contenedor API,
- el comando exacto que falló.

---

## 📚 Referencias

- [Getting Started](getting-started.md) — arranque inicial
- [Configuration](configuration.md) — `.env` y backend de unidades
- [Deployment](deployment.md) — operacion, self-host de referencia y patrones avanzados
