# Backup/Restore — PostgreSQL

Procedimientos de backup y restore para la base de datos PostgreSQL del API.

---

## Consideraciones de Seguridad

> ⚠️ **Advertencia:** Los backups contienen datos potencialmente sensibles. No los subas a Git ni los compartas sin revisar.

Para cualquier drill de restore, usa primero un proyecto desechable y un
directorio local fuera de la raiz publica del repo (`<operator-drill-dir>`). No
restaures sobre una BD activa de evaluacion. Los restores automatizados deben
usar manifiesto (`--require-manifest` / `-RequireManifest`) y confirmacion
explicita (`--yes` / `-Force`); sin esa confirmacion los scripts fallan cerrado.
Si el entorno local no tiene Docker/PostgreSQL disponible, deja el drill vivo
como evidencia pendiente: no conviertas una revision estatica de scripts en una
validacion de volumen o restore real.

**Buenas prácticas:**
- Almacenar backups en ubicación segura (cifrada)
- Limitar acceso a backups solo a personal autorizado
- Retención: mantener backups diarios por 30 días, semanales por 12 semanas
- Probar restores periódicamente

---

## Cadencia y evidencia de drills

Para el self-host de referencia, usa esta base operativa salvo que el entorno
real tenga SLA propio:

- Backup automático diario y retención mínima de 30 días diarios + 12 semanas.
- Drill de restore mensual en un proyecto Compose desechable, nunca sobre una
  BD activa de evaluacion.
- RPO objetivo: completar el objetivo del entorno en este campo antes de salir a
  producción; para pilotos sin SLA, documentar el backup diario como límite
  máximo esperado de pérdida.
- RTO objetivo: completar el objetivo del entorno en este campo antes de salir a
  producción; para pilotos sin SLA, documentar el tiempo real medido en cada
  drill.
- Operador responsable: persona o rol que ejecuta backup, restore y verificación.
- Aprobacion: persona o rol que acepta el resultado del drill o abre incidencia.

Registra cada drill fuera de la raíz pública del repo en:

```text
<operator-drill-dir>/a06-backup-restore/drill-log.md
```

Plantilla mínima de evidencia:

```markdown
## YYYY-MM-DD restore drill

- operador:
- aprobacion:
- commit desplegado:
- proyecto Compose:
- backup file:
- SHA-256:
- manifiesto:
- RPO objetivo:
- RTO objetivo:
- inicio restore:
- fin restore:
- verificación post-restore: PASS/FAIL
- `/healthz`: PASS/FAIL
- `/ready`: PASS/FAIL
- incidencias y acciones:
```

Si el drill falla, no reutilices el destino como validado. Recrea el volumen o
repite el restore desde un backup con manifiesto verificado.

---

## Backup

### Opción 1: Script Automatizado (Recomendado)

```bash
cd api
bash scripts/backup.sh
```

```powershell
cd api
.\scripts\backup.ps1
```

Tambien puedes usar el wrapper operativo principal:

```powershell
cd api
.\scripts\dev.ps1 Backup
```

El script genera:
- Archivo SQL en `backups/postgres_YYYYMMDDTHHMMSSZ.sql`
- Manifiesto JSON lateral `backups/postgres_YYYYMMDDTHHMMSSZ.sql.manifest.json`
  con tamaño, SHA-256, proyecto Compose, fichero env/compose y BD/usuario
- Mensaje de salida con la ruta escrita

Si `pg_dump` falla o se corta por timeout, los scripts eliminan el SQL y el
manifiesto incompletos. No uses un backup incompleto como fuente de restore.

Para un drill desechable, apunta explícitamente a un proyecto Compose aislado:

```bash
cd api
bash scripts/backup.sh \
  --project-name sds-restore-drill \
  --env-file <operator-drill-dir>/a06-backup-restore/.env.source \
  --compose-file compose.yml \
  --out-dir <operator-drill-dir>/a06-backup-restore/backups
```

```powershell
cd api
.\scripts\backup.ps1 `
  -ProjectName sds-restore-drill `
  -EnvFile <operator-drill-dir>\a06-backup-restore\.env.source `
  -ComposeFile compose.yml `
  -OutDir <operator-drill-dir>\a06-backup-restore\backups
```

El wrapper `dev.ps1 Backup` reenvia los mismos parametros operativos de salida
y timeout cuando se usan en PowerShell:

```powershell
cd api
.\scripts\dev.ps1 Backup `
  -ProjectName sds-restore-drill `
  -EnvFile <operator-drill-dir>\a06-backup-restore\.env.source `
  -OutDir <operator-drill-dir>\a06-backup-restore\backups `
  -TimeoutSeconds 1800
```

En Bash y PowerShell puedes acotar un backup largo con
`--timeout-seconds` / `-TimeoutSeconds`. El valor por defecto es `0`, que conserva
el comportamiento historico sin limite. En Bash el timeout usa la herramienta
del sistema `timeout`; si no existe, el script falla antes de ejecutar Docker.
Matar el cliente `docker compose exec` no garantiza que el proceso interno del
contenedor haya parado instantaneamente, por lo que todo resultado cortado debe
tratarse como no verificado.

### Opción 2: Docker Compose

```bash
cd api
mkdir -p backups

# Backup completo
docker compose exec -T postgres sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' \
  > "backups/postgres_$(date -u +%Y%m%dT%H%M%SZ).sql"

# Backup solo datos (sin esquema)
docker compose exec -T postgres sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --data-only' \
  > "backups/postgres_data_$(date -u +%Y%m%dT%H%M%SZ).sql"

# Backup solo esquema (sin datos)
docker compose exec -T postgres sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --schema-only' \
  > "backups/postgres_schema_$(date -u +%Y%m%dT%H%M%SZ).sql"
```

### Opción 3: PostgreSQL Directo

```bash
# Si PostgreSQL está instalado localmente
export PGPASSWORD="tu_password"
pg_dump -h localhost -U sds -d sds \
  --clean --if-exists \
  > "backups/postgres_$(date -u +%Y%m%dT%H%M%SZ).sql"
```

### Opción 4: Backup Automático (Cron)

```bash
# Editar crontab
sudo crontab -e

# Backup diario a las 2:00 AM
0 2 * * * cd /opt/sds/sustainabilityDataSpace/api && bash scripts/backup.sh >> /var/log/sds/backup.log 2>&1

# Backup semanal completo (domingos a las 3:00 AM)
0 3 * * 0 cd /opt/sds/sustainabilityDataSpace/api && bash scripts/backup.sh >> /var/log/sds/backup-full.log 2>&1

# Limpieza de backups antiguos (mantener 30 días)
0 4 * * * find /opt/sds/backups -name "postgres_*.sql" -mtime +30 -delete
```

---

## Restore

### Precauciones

> ⚠️ **Advertencia:** `--clean --if-exists` en el dump incluye sentencias `DROP`. Esto eliminará objetos existentes antes de recrearlos.

**Antes de restaurar:**
1. Verificar que el backup es válido
2. Considerar backup de la BD actual (si tiene datos importantes)
3. Detener el servicio `api` antes de restaurar. Los scripts `restore` fallan
   cerrado si detectan el servicio Compose `api` en ejecución, porque un
   restore con conexiones activas puede bloquearse por locks y dejar el destino
   como no verificado tras un timeout.

### Opción 1: Script Automatizado

```bash
cd api
docker compose --env-file .env -f compose.yml stop api
bash scripts/restore.sh --yes --require-manifest backups/postgres_20260218T120000Z.sql
docker compose --env-file .env -f compose.yml up -d api
```

```powershell
cd api
docker compose --env-file .env -f compose.yml stop api
.\scripts\restore.ps1 .\backups\postgres_20260218T120000Z.sql -Force -RequireManifest
docker compose --env-file .env -f compose.yml up -d api
```

Wrapper equivalente:

```powershell
cd api
docker compose --env-file .env -f compose.yml stop api
.\scripts\dev.ps1 Restore `
  -BackupFile .\backups\postgres_20260218T120000Z.sql `
  -Force `
  -RequireManifest
docker compose --env-file .env -f compose.yml up -d api
```

Sin `--yes` / `-Force`, los scripts piden confirmar escribiendo `RESTORE`
antes de tocar la base activa.

Por defecto el restore valida el manifiesto lateral si existe y ejecuta una
verificación post-restore contra `alembic_version`, `indicators`, `units`,
`concepts` y `user_accounts`. Tras restaurar una base con catálogo, ejecuta
`make -C api project-semantic-catalog` y `make -C api gate-semantic-catalog-projection`
contra la DB restaurada antes de aceptar tráfico. Para drills y automatización usa `--require-manifest` /
`-RequireManifest`; `--skip-verify` / `-SkipVerify` existe solo para casos
manuales excepcionales.

El wrapper `dev.ps1 Restore` reenvia `-ManifestPath`, `-RequireManifest`,
`-SkipVerify` y `-TimeoutSeconds` a `restore.ps1`. Ejemplo con manifiesto
explicito y timeout:

```powershell
cd api
docker compose --env-file .env -f compose.yml stop api
.\scripts\dev.ps1 Restore `
  -BackupFile .\backups\postgres_20260218T120000Z.sql `
  -Force `
  -ManifestPath .\backups\postgres_20260218T120000Z.sql.manifest.json `
  -RequireManifest `
  -TimeoutSeconds 1800
docker compose --env-file .env -f compose.yml up -d api
```

Un restore con timeout o interrupcion deja el destino como no confiable: recrea
el volumen/base de datos desechable o restaura de nuevo desde un backup validado
antes de reutilizarlo. No des por buena una BD parcialmente restaurada, aunque
el contenedor siga vivo.

Restore a un proyecto desechable:

```bash
cd api
docker compose --env-file <operator-drill-dir>/a06-backup-restore/.env.restore \
  -f compose.yml -p sds-restore-drill stop api
bash scripts/restore.sh --yes --require-manifest \
  --project-name sds-restore-drill \
  --env-file <operator-drill-dir>/a06-backup-restore/.env.restore \
  --compose-file compose.yml \
  --timeout-seconds 1800 \
  <operator-drill-dir>/a06-backup-restore/backups/postgres_YYYYMMDDTHHMMSSZ.sql
docker compose --env-file <operator-drill-dir>/a06-backup-restore/.env.restore \
  -f compose.yml -p sds-restore-drill up -d api
```

```powershell
cd api
docker compose --env-file <operator-drill-dir>\a06-backup-restore\.env.restore `
  -f compose.yml -p sds-restore-drill stop api
.\scripts\restore.ps1 `
  <operator-drill-dir>\a06-backup-restore\backups\postgres_YYYYMMDDTHHMMSSZ.sql `
  -Force `
  -RequireManifest `
  -ProjectName sds-restore-drill `
  -EnvFile <operator-drill-dir>\a06-backup-restore\.env.restore `
  -ComposeFile compose.yml `
  -TimeoutSeconds 1800
docker compose --env-file <operator-drill-dir>\a06-backup-restore\.env.restore `
  -f compose.yml -p sds-restore-drill up -d api
```

### Opción 2: Docker Compose

```bash
cd api

# Detener API (obligatorio para evitar locks/conexiones activas durante restore)
docker compose stop api

# Restaurar backup
cat backups/postgres_YYYYMMDDTHHMMSSZ.sql | \
  docker compose exec -T postgres sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" --set ON_ERROR_STOP=on'

# Reiniciar API
docker compose start api
```

### Opción 3: PostgreSQL Directo

```bash
# Verificar que la base existe (crear si es necesario)
export PGPASSWORD="tu_password"
psql -h localhost -U postgres -c "CREATE DATABASE sds;"

# Restaurar
psql -h localhost -U sds -d sds \
  --set ON_ERROR_STOP=on \
  < backups/postgres_YYYYMMDDTHHMMSSZ.sql
```

### Restore a Base de Datos Diferente

```bash
# Crear nueva base de datos
export PGPASSWORD="tu_password"
psql -h localhost -U postgres -c "CREATE DATABASE sds_restore_test;"

# Restaurar a la nueva BD
psql -h localhost -U sds -d sds_restore_test \
  --set ON_ERROR_STOP=on \
  < backups/postgres_YYYYMMDDTHHMMSSZ.sql
```

---

## Verificación Post-Restore

### 1. Verificar conexión

```bash
# Health check del API
curl -sS http://localhost:8090/healthz | python3 -m json.tool

# Debe retornar:
# {
#   "status": "healthy",
#   "timestamp": "...",
#   "dependencies": {
#     "database": {"status": "healthy"}
#   }
# }
```

### 2. Verificar datos

```bash
# Obtener token
BOOTSTRAP_ADMIN_PASSWORD="$(grep '^BOOTSTRAP_ADMIN_PASSWORD=' .env | cut -d= -f2-)"
TOKEN=$(curl -sS -X POST http://localhost:8090/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"${BOOTSTRAP_ADMIN_PASSWORD}\"}" \
  | jq -r '.access_token')

# Contar indicadores
curl -sS "http://localhost:8090/api/v1/indicators?limit=1" \
  -H "Authorization: Bearer $TOKEN" | jq '.total'

# Debe coincidir con el número esperado
```

### 3. Verificar integridad (SQL)

```bash
# Conectar a PostgreSQL
docker compose exec postgres psql -U sds -d sds

# Verificar tablas principales
\dt

# Contar registros por tabla
SELECT 'indicators' as table, COUNT(*) as count FROM indicators
UNION ALL
SELECT 'esg_values', COUNT(*) FROM esg_values
UNION ALL
SELECT 'user_accounts', COUNT(*) FROM user_accounts;

# Salir
\q
```

---

## Backup/Restore Selectivo

### Backup de Tablas Específicas

```bash
# Solo indicadores
docker compose exec -T postgres sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t indicators' \
  > backups/indicators_only.sql

# Múltiples tablas
docker compose exec -T postgres sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t indicators -t esg_values' \
  > backup_tables.sql
```

### Exportar/Importar CSV

```bash
# Exportar indicadores a CSV
docker compose exec postgres psql -U sds -d sds \
  -c "COPY (SELECT * FROM indicators) TO STDOUT WITH CSV HEADER" \
  > backups/indicators.csv

# Importar desde CSV
docker compose exec -T postgres psql -U sds -d sds \
  -c "COPY indicators FROM STDIN WITH CSV HEADER" \
  < backups/indicators.csv
```

---

## Recuperación ante Desastres

### Escenario 1: Pérdida Total de Datos

```bash
# 1. Detener servicios
cd api
docker compose down

# 2. Eliminar volumen corrupto (⚠️ irreversible)
volume_id="$(docker volume ls -q \
  --filter "label=com.docker.compose.project=sds-api" \
  --filter "label=com.docker.compose.volume=postgres_data")"
test -n "$volume_id" && docker volume rm "$volume_id"

# 3. Recrear volumen e iniciar
docker compose up -d postgres

# 4. Esperar a que PostgreSQL esté listo
sleep 10

# 5. Restaurar backup
cat backups/postgres_ULTIMO_BACKUP.sql | \
  docker compose exec -T postgres sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" --set ON_ERROR_STOP=on'

# 6. Iniciar API
docker compose up -d api
```

### Escenario 2: Migración a Nuevo Servidor con Docker opcional

```bash
# En servidor origen
cd api
bash scripts/backup.sh
scp backups/postgres_*.sql usuario@nuevo-servidor:/tmp/

# En servidor destino
# 1. Instalar Docker y Docker Compose si eliges la ruta self-host opcional
# 2. Clonar repositorio
# 3. Configurar .env
cd api
docker compose up -d postgres

# 4. Restaurar
cat /tmp/postgres_*.sql | docker compose exec -T postgres sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" --set ON_ERROR_STOP=on'

# 5. Iniciar API
docker compose up -d
```

---

## Almacenamiento de Backups

Las opciones de esta sección son ejemplos operativos externos. **Opcional fuera de los scripts:** el repositorio no implementa ni verifica S3, cron, cifrado ni política formal de retención.

### Opción A: Local + Sync Remoto

```bash
# Directorio local
/opt/sds/backups/

# Sync a almacenamiento remoto (S3, etc.)
aws s3 sync /opt/sds/backups/ s3://mi-bucket-sds/backups/
```

### Opción B: Backup Directo a S3

```bash
# Usar pg_dump con pipe a S3
docker compose exec -T postgres sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' \
  | aws s3 cp - s3://mi-bucket-sds/backups/postgres_$(date -u +%Y%m%dT%H%M%SZ).sql
```

---

## Troubleshooting

### Error: "database does not exist"

```bash
# Crear base de datos primero
docker compose exec postgres createdb -U postgres sds
```

### Error: "permission denied"

```bash
# Verificar usuario y permisos
docker compose exec postgres psql -U postgres -c "\du"

# Otorgar permisos si es necesario
docker compose exec postgres psql -U postgres -c \
  "GRANT ALL PRIVILEGES ON DATABASE sds TO sds;"
```

### Error: "out of memory" durante restore

```bash
# Usar restore por lotes
cat backup.sql | docker compose exec -T postgres psql -U sds \
  --set work_mem='256MB' \
  --set maintenance_work_mem='512MB'
```

---

## Referencias

- **[Deployment](deployment.md)** - Despliegue y configuración
- **[Troubleshooting](troubleshooting.md)** - Solución de problemas
- PostgreSQL Docs: [pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html), [pg_restore](https://www.postgresql.org/docs/current/app-pgrestore.html)
