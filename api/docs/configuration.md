# Configuration — SustainabilityDataSpace API

> **Versión documento:** 1.2 | **Última actualización:** 2026-04-13

Referencia completa de variables de entorno usando `pydantic-settings`.

---

## 🔴 Requeridas (Producción)

| Variable | Descripción | Ejemplo |
|----------|-------------|---------|
| `JWT_SECRET_KEY` | Clave secreta para firmar tokens JWT. **Mínimo 32 caracteres.** | `openssl rand -hex 32` |
| `DATABASE_URL` | URL de conexión PostgreSQL | `postgresql://user:[REDACTED]@host:5432/db` |

---

## 🟡 Importantes

| Variable | Descripción | Default |
|----------|-------------|---------|
| `BOOTSTRAP_ADMIN_PASSWORD` | Contraseña inicial del usuario admin | - |
| `ALLOWED_ORIGINS` | Orígenes CORS permitidos (JSON array recomendado; también acepta origen único o CSV) | `["http://localhost:8090"]` |
| `LOG_LEVEL` | Nivel de logging | `INFO` |

---

## 📋 Variables por Categoría

### Servidor HTTP

```env
HOST=127.0.0.1            # Local por defecto; usar 0.0.0.0 solo tras firewall/proxy
PORT=8090                 # Puerto del API
WORKERS=1                 # Migraciones y bootstrap en el inicio: un solo worker

# Proxies inversos autorizados para cabeceras de cliente
TRUSTED_PROXY_IPS=10.0.0.10,127.0.0.1
# Lista CSV de IPs o CIDR. Vacío por defecto.

```

`TRUSTED_PROXY_IPS` controla cuándo SDS acepta `X-Forwarded-For` para
identificar el cliente real en rate limiting. Si el peer inmediato no está en
esa lista, la API ignora la cabecera y usa `request.client.host`. Configura esta
lista solo con proxies que limpian o reescriben las cabeceras forwarded antes de
reenviar tráfico al API.

### Base de Datos

```env
# URL completa (recomendado para compose local)
DATABASE_URL=postgresql://sds:[REDACTED]@localhost:5432/sds

# O configurar por partes
POSTGRES_USER=sds
POSTGRES_PASSWORD=tu_password_seguro
POSTGRES_DB=sds
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

# Modo de operación local/API
REQUIRE_DATABASE=true
# true = modo DB-backed obligatorio; falla si no hay DB y usa PostgreSQL como backend de unidades

# Seed de referencia en DB vacía
SEED_REFERENCE_DATA_ON_STARTUP=true

# Pool de conexiones
DB_POOL_SIZE=10           # Conexiones base
DB_MAX_OVERFLOW=20        # Conexiones extra bajo carga
DB_POOL_TIMEOUT=30        # Segundos de espera
```

### Autenticación JWT

```env
# Clave secreta (REQUERIDA en producción)
# Generar: openssl rand -hex 32
JWT_SECRET_KEY=cambia_esto_usa_32_caracteres_minimo

# Algoritmo de firma
JWT_ALGORITHM=HS256       # Opciones: HS256, HS384, HS512

# Tiempo de expiración
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60

# Refresh tokens
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
```

### API Keys

```env
# Expiración por defecto de nuevas API keys cuando la petición omite expires_at
API_KEY_EXPIRE_DAYS=365
# 0 o negativo permite API keys sin expiración por defecto solo si se acepta
# explícitamente en la configuración operativa.
```

Una API key nunca puede ampliar los permisos del usuario que la crea. La lista
`permissions` de `POST /auth/api-keys` debe ser un subconjunto de los permisos
actuales del usuario; SDS rechaza con `403` cualquier permiso adicional. En
modo DB-backed, las peticiones firmadas con API key conservan ese subconjunto
acotado aunque el usuario persistido tenga un rol más amplio.

Contención P0 de H02: la autenticación conserva el método y el identificador de
la key como procedencia inmutable de la petición, excluida de respuestas y
esquemas públicos. Una key de un usuario ADMIN no hereda la excepción implícita
entre tenants en valores/revisiones, cálculos o jerarquías; las operaciones
soportadas de su propia compañía siguen sujetas a sus permisos efectivos.
Los helpers de rol requieren bearer; los helpers de compañía usan la misma
excepción central, reservada al ADMIN autenticado con bearer.

`POST /auth/api-keys`, `DELETE /auth/api-keys/{id}`, `POST /auth/change-password`,
`POST /auth/users` y `PUT /auth/me` requieren bearer y rechazan keys con `403`,
incluso con `manage_users` o todos los permisos. Esto incluye cambios de perfil,
compañía, rol y estado activo; no hay un router separado de CRUD de compañías en
esta base. Las lecturas de `/auth/me` y `/auth/api-keys` siguen disponibles.
Login y refresh siguen validando sus propias credenciales; logout ya exige
bearer. La prioridad del bearer cuando llegan ambos headers no cambia.

**H02 sigue abierto.** Esta contención no decide un futuro alcance explícito
entre tenants para keys, no cambia la linealización de revocación ni elimina
carreras con cambios de permisos, y no reautoriza trabajos durables. Tampoco
cambia la política offline: el almacén de valores en memoria no aporta una
garantía de aislamiento multi-tenant. El bearer ADMIN conserva las excepciones
existentes; las lecturas de compatibilidad con revisiones siguen vinculadas a
su compañía y las lecturas legacy de valores siguen no disponibles. No hay
migraciones ni cambios de esquema. Evidencia:
[`H02 P0`](../../docs/quality/2026-09-12-h02-api-key-containment.md).

**Rotación de JWT Secret en Producción:**
```bash
# 1. Generar nuevo secret
NEW_SECRET=$(openssl rand -hex 32)

# 2. Actualizar en entorno
export JWT_SECRET_KEY=$NEW_SECRET

# 3. Reiniciar servicio (sin downtime con múltiples instancias)
# Los tokens antiguos seguirán válidos hasta expirar
```

### Seguridad CORS

```env
# Desarrollo (permisivo; JSON array recomendado)
ALLOWED_ORIGINS=["http://localhost:3000","http://localhost:8080"]

# Producción (restrictivo; JSON array recomendado)
ALLOWED_ORIGINS=["https://miapp.com","https://admin.miapp.com"]

# Permitir credenciales (cookies/auth en CORS)
CORS_ALLOW_CREDENTIALS=true

# Headers y métodos
CORS_ALLOW_HEADERS=content-type,authorization,x-request-id
CORS_ALLOW_METHODS=GET,POST,PUT,DELETE,OPTIONS
```

### Sistema de Unidades

```env
USE_POSTGRES_UNITS=true
# Con REQUIRE_DATABASE=true, PostgreSQL se usa igualmente para unidades.
# Este flag solo conserva compatibilidad interna de tests/herramientas.
# true fuerza PostgreSQL también si una prueba desactiva REQUIRE_DATABASE.

UNITS_JSON_PATH=src/data/units_database.json
```

Con `REQUIRE_DATABASE=true`, el runtime usa PostgreSQL para unidades automáticamente. Si la base está vacía, el arranque rellena `unit_categories`, `units` y `conversion_rules` desde el catálogo bundled, que cubre unidades SI/UCUM, SI derivadas, imperiales/US comunes, CO2e y escalas monetarias. El catálogo conserva metadatos de fuente (`source_system`, `source_version`, `source_url`), vector dimensional y vigencia; el bootstrap los carga también en las columnas DB-backed. Las conversiones dentro de una misma categoría se resuelven por factores de base unit, no con reglas par a par.

El catálogo bundled se reconstruye de forma determinista con:

```powershell
python scripts/build_units_catalog.py
```

### Conversiones FX

La conversion monetaria es historica y fail-closed. Produccion debe cargar proveedores, politicas y tipos aprobados antes de ejecutar calculos que requieran cambio de divisa. Ninguna ruta runtime usa el ultimo tipo disponible por defecto; cualquier politica de fallback distinta tendria que aprobarse en un ADR futuro y quedar trazada.

La semilla DB-backed de divisas incluye ISO 4217 actual e historico desde SIX (`list-one.xml` y `list-three.xml`) y se reconstruye con:

```powershell
python scripts/build_iso4217_currency_seed.py --output api/src/data/currencies_iso4217_seed.json
```

El bootstrap crea la politica real `ecb-reference-monthly-average` sin inventar
observaciones ECB. Los tipos de cambio operativos se cargan de forma explícita
con el loader DB-backed; el arranque no crea filas FX de ejemplo.

Para preparar histórico ECB como filas SDS `base_currency -> EUR`, usa:

```powershell
python scripts/import_ecb_fx_rates.py --output ../operator-data/fx/ecb-reference-history-sds.csv
```

Ese CSV es preparacion de datos: no instala nada en DB y no permite afirmar que el historico FX este cargado en SDS. Es artefacto operativo/local y no se versiona como seed publica.

Para cargar el historico ECB en las tablas FX operativas, usa el loader DB-backed:

```powershell
# Validacion sin escrituras de todo el historico generado
python scripts/load_ecb_fx_history.py --input-csv ../operator-data/fx/ecb-reference-history-sds.csv --start-date 1999-01-04 --end-date 2026-06-22 --materialize-monthly

# Escritura real en la DATABASE_URL configurada
python scripts/load_ecb_fx_history.py --input-csv ../operator-data/fx/ecb-reference-history-sds.csv --start-date 1999-01-04 --end-date 2026-06-22 --materialize-monthly --apply
```

Sin `--start-date`, el loader usa `2020-01-01`; para una carga completa del CSV ECB generado hay que pasar explicitamente `--start-date 1999-01-04`. Sin `--end-date`, usa la fecha local del dia de ejecucion. La ultima observacion cargada puede ser anterior a `--end-date` cuando ECB aun no haya publicado un dia no habil; en la carga local del `2026-06-22`, la ultima fecha diaria disponible fue `2026-06-19` y se materializaron periodos mensuales hasta `2026-06-30`. El loader agrupa por `provider`, `rate_type` y `base_currency`, parte lotes de maximo `10000` filas, calcula un `source_hash` SHA-256 estable por grupo de moneda/fuente, y hace upsert sobre la clave unica `(provider, rate_type, base_currency, quote_currency, rate_date)`. La reejecucion con los mismos datos no duplica observaciones; si el proveedor corrige un valor, se actualiza la observacion existente y se conserva el nuevo hash de fuente. `--materialize-monthly` crea o refresca periodos `monthly_average` solo para la ventana de fechas cargada.

Publica conteos de cobertura FX solo desde una ejecucion fresca del loader o
desde un informe de calidad fechado.

### Importación de Indicadores

```env
# Guardas del endpoint POST /api/v1/indicators/import-csv-validations
INDICATOR_IMPORT_MAX_BYTES=10485760
INDICATOR_IMPORT_MAX_ROWS=10000
```

Estos límites protegen la validación síncrona del catálogo de indicadores, que no modifica el catálogo pero puede persistir resultado y payload. Con `REQUIRE_DATABASE=true`, `/api/v1/indicators/import-csv-jobs` devuelve `503` antes de leer el cuerpo o acceder al estado de jobs, tanto para CSV directo como para `validation_id`. No hay un ajuste de timeout que habilite admisión segura: H15 sigue abierto para leases durables, fencing y recuperación tras reinicio. Usa el CLI soportado para importar; no se reparan jobs existentes.

### Importación de valores

```env
# Guardas de CSV para /api/v1/values/import-csv y el modo memoria/demo
VALUE_IMPORT_MAX_BYTES=10485760
VALUE_IMPORT_MAX_ROWS=1000
```

Ambos valores deben ser enteros positivos. El límite de tamaño se aplica antes
de retener el CSV y el límite de filas antes de crear una reclamación de
idempotencia, un job o una escritura. Los CLI controlados permanecen fuera de
estos límites de endpoint para cargas operativas planificadas.

### Logging

```env
LOG_LEVEL=INFO            # DEBUG, INFO, WARNING, ERROR
```

`LOG_LEVEL` se aplica al logging estándar y a `structlog`. El runtime actual
emite por consola; no implementa `LOG_FORMAT`, `LOG_TO_FILE` ni
`LOG_FILE_PATH`. Redirige y rota la salida con el gestor de procesos o la
plataforma de contenedores.

### Seeding

```env
# Crear usuario admin si BD vacía
SEED_DEFAULT_USERS=true
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=cambia_esta_password_segura

PORTAL_MOUNT_ENABLED=false
```

La ruta opcional `.\scripts\dev.ps1 ProductionInstall -AllowedOrigin https://your-domain.example`
crea `.env.production`, arranca el perfil Docker Compose de referencia y
verifica `/healthz`, `/ready`, `/docs`, login admin y listado de conceptos. Es
un perfil de self-host opcional, no el modelo obligatorio de instalacion de
dependencias del API publico. En este repositorio se ofrece como referencia
para evaluación técnica; su nombre no autoriza explotación en producción.
El uso productivo propio o para terceros requiere un acuerdo separado y por
escrito (véase «Alcance de uso previsto» en el `README.md` de la raíz).

---

## 📄 Templates de `.env`

### Desarrollo Local

```bash
cat > api/.env << 'EOF'
# === DESARROLLO ===
ENVIRONMENT=development
DEBUG=true
LOG_LEVEL=DEBUG

# PostgreSQL local requerido
REQUIRE_DATABASE=true

# JWT (cambiar en producción)
JWT_SECRET_KEY=dev-secret-no-usar-en-produccion-32-chars

# CORS permisivo (JSON array)
ALLOWED_ORIGINS=["http://localhost:3000","http://localhost:8080","http://127.0.0.1:3000"]

# Crear admin por defecto
SEED_DEFAULT_USERS=true
BOOTSTRAP_ADMIN_PASSWORD=admin123  # Solo desarrollo local
EOF
```

### Self-host de referencia (Docker opcional; evaluación técnica)

```bash
cat > api/.env << 'EOF'
# === PRODUCCIÓN ===
ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=INFO

# PostgreSQL obligatorio
REQUIRE_DATABASE=true
DATABASE_URL=postgresql://sds:${DB_PASSWORD}@postgres:5432/sds
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=30
SEED_REFERENCE_DATA_ON_STARTUP=true

# Seguridad JWT
JWT_SECRET_KEY=${JWT_SECRET_KEY}  # Inyectar desde secret manager
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30

# CORS restrictivo (JSON array)
ALLOWED_ORIGINS=["https://miapp.com"]
CORS_ALLOW_CREDENTIALS=true

# Admin (solo primer arranque)
SEED_DEFAULT_USERS=true
BOOTSTRAP_ADMIN_PASSWORD=${ADMIN_PASSWORD}

# Logging estructurado
LOG_FORMAT=json
EOF
```

### Configuración de referencia Systemd (no autorización de uso productivo)

```bash
cat > /opt/sds/sustainabilityDataSpace/api/.env << 'EOF'
# === PRODUCCIÓN SYSTEMD ===
HOST=0.0.0.0
PORT=8090
WORKERS=1

REQUIRE_DATABASE=true
DATABASE_URL=postgresql://sds:[REDACTED]@localhost:5432/sds
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=30

JWT_SECRET_KEY=GENERAR_CON_OPENSSL_RAND_HEX_32
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30

ALLOWED_ORIGINS=["https://tu-dominio.com"]
CORS_ALLOW_CREDENTIALS=true

SEED_DEFAULT_USERS=true
BOOTSTRAP_ADMIN_PASSWORD=STRONG_ADMIN_PASS

LOG_LEVEL=INFO
LOG_FORMAT=json
EOF
chmod 600 /opt/sds/sustainabilityDataSpace/api/.env  # Proteger permisos
```

---

## ⚠️ Validación

El sistema valida la configuración al iniciar:

### Error: JWT_SECRET_KEY inseguro
```
ValidationError: JWT_SECRET_KEY must be at least 32 characters
```
**Solución:** `openssl rand -hex 32`

### Error: Base de datos no disponible
```
RuntimeError: PostgreSQL is required but not available
```
**Solución:** Verificar `DATABASE_URL`, arrancar PostgreSQL local y confirmar que la base `sds` existe.

### Error: CORS no configurado en producción
```
Warning: ALLOWED_ORIGINS is '*' in production environment
```
**Solución:** Especificar orígenes explícitos

### Error: `error parsing value for field "allowed_origins"`
```
pydantic_settings.sources.SettingsError: error parsing value for field "allowed_origins"
```
**Solución:** usar preferiblemente un JSON array válido en `.env`, por ejemplo:
`ALLOWED_ORIGINS=["http://localhost:8090","https://app.example.com"]`

---

## 🔧 Configuración en Runtime

No hay endpoints públicos para mutar la configuración del runtime semántico en caliente. Los cambios relevantes se aplican por variables de entorno y reinicio controlado del servicio. En `REQUIRE_DATABASE=true`, `/healthz` y `/ready` reflejan solo la salud del plano canónico.

Cambios que **sí requieren restart**:
- `JWT_SECRET_KEY`
- `DATABASE_URL`
- `HOST`, `PORT`, `WORKERS`
- `LOG_LEVEL`

---

## Contención de tenants y estado de H01

La contención técnica de valores **no cierra formalmente H01**. En el store
DB-backed, las lecturas de revisiones y las escrituras requieren un `tenant_id`
explícito y no vacío. `VALUE_REVISION_DEFAULT_TENANT_ID` no asigna ownership a
estas operaciones. Un caller interno sin tenant recibe `503 Value service
unavailable` antes de consultar revisiones o escribir valores. La construcción
sin tenant sigue permitida para preparación sin acceso a valores, como el
dry-run del CLI existente; no habilita lecturas ni persistencia.

Las rutas autenticadas de valores y cálculo conservan su resolución de tenant.
Los caminos legacy deshabilitados siguen fallando cerrados; pertenecer a una
jerarquía no autoriza la lectura de filas históricas sin atribución de tenant.
No usar el tenant configurado, el nombre de la entidad o la jerarquía como
evidencia suficiente de ownership histórico.

H01 sigue pendiente de estas decisiones de los responsables:

- **Central-admin cross-tenant reads**: política explícita sobre el alcance de
  lectura entre tenants del administrador central.
- **Self-company reassignment**: política explícita sobre quién puede cambiar
  su propia empresa, con qué autorización y cómo se aplica al acceso posterior.
- **Legacy ownership migration**: atribución histórica aprobada por los
  responsables, tratamiento de filas ambiguas o sin dueño, y plan de migración
  revisado con validación por tenant antes de habilitar acceso a esos datos.

Este cambio no modifica esas políticas, roles/RLS de base de datos, migraciones
ni imports. Tampoco acredita despliegue o validación en producción. Evidencia
local y límites: [H01 tenant containment — 2026-09-11](../../docs/quality/2026-09-11-h01-tenant-containment.md).

## 📚 Referencias

- **[Deployment](deployment.md)** — Despliegue con diferentes configs
- **[Getting Started](getting-started.md)** — Configuración inicial
- **[Troubleshooting](troubleshooting.md)** — Problemas de configuración
