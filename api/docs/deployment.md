# Deployment — SustainabilityDataSpace API

> **Versión documento:** 1.7 | **Última actualización:** 2026-09-25

Guía técnica para instalar, ejecutar y validar SDS API en evaluación. No es
una autorización de explotación propia, despliegue productivo o prestación de
servicios a terceros; véase «Alcance de uso previsto» en el `README.md` de la
raíz. Las referencias a entornos de producción documentan capacidades y
procedimientos técnicos, no permisos de uso.

Esta documentación distingue entre:
- **ruta publica canonica de API**: instalacion nativa de dependencias Python y
  operacion de PostgreSQL segun el entorno de cada usuario,
- **Docker opcional**: quickstart y perfil self-host de referencia,
- **patrones avanzados** (`systemd`, Kubernetes) que siguen siendo válidos como referencia,
  pero requieren validación propia del entorno de destino.

## Perfil de fuente soportado

El perfil público soportado usa **un solo worker de API**. Migraciones y
bootstrap se ejecutan durante el inicio, por lo que no se soporta desplegar
`--workers > 1` ni una topología systemd multi-worker en esta fuente. La
importación de catálogo/valores en modo DB es síncrona por CLI; las admisiones
asíncronas DB siguen rechazadas mientras H15 no tenga worker durable, lease,
fencing y recuperación. Esto no es una re-arquitectura de trabajo asíncrono.

Excepción optativa y desactivada por defecto: el perfil AWS
`api/deploy/aws-ec2-rds/` exige `SCHEMA_MIGRATIONS_EXTERNALLY_MANAGED=true`,
la revisión Alembic aprobada antes de activar el servicio y una comprobación
de cabeceras de esquema solo de lectura en cada arranque. En ese perfil la API
no ejecuta migraciones. El perfil no proporciona un canal de migración privado
ya cualificado ni autorización para desplegar o exponer tráfico en AWS.

Para una cualificación desechable de datos públicos sintéticos:

```bash
make -C api demo-manifest
DATABASE_URL='<postgresql-url-local>' make -C api demo-install
DATABASE_URL='<postgresql-url-local>' make -C api demo-verify
DATABASE_URL='<postgresql-url-local>' make -C api demo-benchmark
DATABASE_URL='<postgresql-url-local>' make -C api demo-reset
```

Los targets `demo-*` establecen explícitamente en el proceso CLI host
`VALUE_REVISION_API_ENABLED=true`, `VALUE_REVISION_DUAL_WRITE_ENABLED=true` y
`VALUE_REVISION_PRIMARY_READ_PATH=revision` antes de importar el cargador
estricto. Esta configuración no se hereda del servicio Compose; la invocación
directa de `scripts/demo_runtime.py` debe aportar las tres variables y falla de
forma cerrada si falta alguna o es falsa.
Los targets `demo-*` obtienen `DATABASE_URL` del entorno; no lo expanden como
argumento visible de Make o en el log de CI. Inyectar el valor desde el gestor
de secretos del entorno de prueba, sin registrarlo en el repositorio ni en
comandos de diagnóstico.
Los scripts operativos que aceptan `--db-url` resuelven primero esa opción.
Sin ella, utilizan una URL de configuración válida, luego `DATABASE_URL` y,
solo si se facilita `POSTGRES_PASSWORD` explícitamente, construyen la URL a
partir de `POSTGRES_*`. Una URL de ejemplo redactada y una contraseña ausente
o débil no habilitan una conexión predeterminada. `demo-manifest` y `--help`
no necesitan conectarse a PostgreSQL.

El benchmark de 1.000 filas tiene presupuesto de 30 segundos solo como
tripwire de regresión local/CI; no prueba SLA ni concurrencia.

Una base no vacía sin tabla `alembic_version` se rechaza de forma cerrada. El
servicio no intenta reconocerla mediante una huella parcial ni ejecutar
`alembic stamp`: una coincidencia de nombres de tablas o columnas no demuestra
tipos, claves, restricciones, índices ni procedencia. Una instalación heredada
debe restaurarse desde un snapshot versionado ya cualificado o someterse a una
migración explícita y revisada por el operador antes de arrancar el servicio.

La revisión 046 pone en cuarentena **todos** los `esg_values` anteriores a
ella (`tenant_id=NULL`, `ownership_state=quarantined`), aunque una revisión
comparta su `source_record_id`: ese texto no es una relación de titularidad.
Si una revisión heredada apunta mediante `source_record_id` a un valor
heredado, 046 **aborta antes del DDL**: la vía de lectura de revisiones podría
seguir publicando esos datos bajo un tenant no acreditado. Se necesita una
disposición documental y técnica de ese grafo, revisada por el operador, y
una prueba del procedimiento de restauración antes de reintentar la migración;
no se deben borrar referencias solo para superar el preflight.
También se aborta si una fuente histórica marcada como interna
(`sds_values` o `legacy_esg_values`) no encuentra su valor local, incluso
cuando su ID sea nulo. La relación incompleta tampoco acredita procedencia.
El preflight bloquea escrituras a valores y revisiones durante la migración.
Los triggers instalados antes de liberar esos bloqueos exigen que
`source_system=sds_values` o `legacy_esg_values` apunte a un valor local
resuelto del mismo tenant; una fuente externa no puede colisionar con ningún
ID local, ni siquiera del mismo tenant. No se puede borrar ni truncar un valor
enlazado mediante escrituras directas ordinarias,
reasignar su titularidad ni crear después un valor con el ID de una fuente
externa. Las migraciones 046/048 y estas escrituras rechazan transacciones
distintas de PostgreSQL `READ COMMITTED`: un snapshot anterior a la espera por
un bloqueo no demuestra la ausencia de enlaces nuevos. El perfil actual no
habilita RLS; si se introduce RLS, se exige un ensayo específico que pruebe
que el rol de la API ve los conflictos de todos los tenants antes de operar.
Esas filas no alimentan lecturas tenant ni pueden reclamarse automáticamente.
Antes de habilitar tráfico sobre una instalación heredada, el operador debe
inventariar la cuarentena y resolver cada valor requerido con evidencia
independiente y revisada que vincule ID exacto, tenant y origen; este repo
no incluye una promoción automática. Si faltan esas pruebas, mantener NO-GO
para los datos heredados y conservar snapshot/restauración versionados.

El repo público no incluye prototipos UI locales, carpetas `archive/`,
marcadores de sesión/congelación ni paquetes operativos. Esos materiales se
mantienen en almacenamiento local/privado ignorado salvo promoción explícita.

---

## H15 controlled value-job reconciliation: maintenance required

Every startup with `REQUIRE_DATABASE=true` now has a persistent side effect:
after `ping_db()` and `init_db()`, before bootstrap/serving requests, it
terminalizes **only** `value_import_jobs` in `pending` or `running` as `failed`.
This also applies to subsequent restarts; there is no age cutoff or opt-out.
Memory/demo mode and optional PostgreSQL-units mode with
`REQUIRE_DATABASE=false` do not reconcile jobs.

**Precondition: stop all old instances and workers and enforce a single writer
at deployment. Do not use an overlapping rolling, blue/green or multi-worker
startup for this rollout.** Row locks serialize reconciliation calls; they do
not fence an old worker that is still executing an import. The application
cannot establish this operational precondition itself.

The dedicated repository/store operation changes exactly `status`,
`error_message`, `completed_at` and `updated_at`. It preserves `result_body`,
counts, `committed`, source/request metadata, `created_at` and `started_at`.
The stable reason is:

> H15 controlled legacy value job reconciliation: historical execution effects are indeterminate; no retry or replay was performed.

The new completion/update times record reconciliation, not historical execution
completion. `failed` does not mean that values were never committed or were
rolled back; a preserved `committed=true`, `false` or `null` is retained evidence,
not a new determination. Terminal and other non-active statuses are untouched.
The reconciliation operation does not write indicators, indicator jobs, values,
revisions, catalogs, mappings, snapshots or payloads. Existing schema/bootstrap
behavior elsewhere in startup is unchanged and is not covered by that isolation
claim. Both existing DB async admission guards remain in place.

Controlled deployment procedure (operator action; not performed by this change):

1. Prepare and review the release and focused/full CI evidence before the
   maintenance window. Keep H15 open for durable leases, fencing, execution
   recovery and determination of historical effects; indicator-job
   reconciliation is outside this slice.
2. Put ingress into maintenance, drain requests, stop **every** old API instance
   and background worker, and suspend supervisors/autoscaling/schedulers that
   could restart them. Stop concurrent import/CLI writers as well. Verify no old
   process or in-flight import transaction remains, and enforce one writer
   through the deployment controls. If this cannot be verified, do not start
   the new release.
3. Take the normal consistent backup and retain a protected pre-rollout record
   of active value-job counts/evidence outside the public repository. The old
   job error and completion/update timestamps will be replaced; do not export
   job bodies, identifiers, credentials or error text into public QA/logs.
4. Start one instance with one API worker and `REQUIRE_DATABASE=true`, behind
   maintenance ingress. Leave job admission policy unchanged. Startup runs the
   normal DB initialization, then one dedicated reconciliation transaction.
5. Require `Legacy value job reconciliation completed` with the numeric
   `reconciled_count` (including zero), then successful application startup and
   normal readiness. Compare the count against the protected active-job
   inventory and verify active value jobs are gone. Do not infer unchanged
   business data from the count alone. Existing GET job reads expose the failed
   status/reason and preserved evidence; async POST admission still returns 503.
6. On reconciliation failure, startup refuses to serve with the fixed message
   `Legacy value job reconciliation could not complete; startup refused`.
   Keep maintenance and quiescence, diagnose through protected DB operations,
   and restart only after resolving the cause. No success count is emitted on
   failure. A later startup failure or lost commit acknowledgement may follow
   a committed reconciliation; rerunning safely leaves terminal rows unchanged.
7. Restore ingress only after verification, retaining the single-writer
   deployment constraint. Never retry/replay an affected job or resubmit its
   input merely because it is now failed. A binary rollback does not undo the
   reconciliation; do not reactivate historical workers. Data restoration or
   historical-effect investigation requires its own reviewed procedure.

Validation and scope: [dated H15 reconciliation evidence](../../docs/quality/2026-09-12-h15-values-only-reconciliation.md).

---

## 🎯 Recomendación por perfil

La tabla recoge opciones técnicas de instalación, no derechos de uso. Los
perfiles productivos requieren además un acuerdo separado y por escrito con el
titular del código SDS; las instrucciones disponibles no lo sustituyen.

| Perfil | Método recomendado | Estado |
|--------|--------------------|--------|
| Desarrollo local | `make start` / `.\scripts\dev.ps1 Start` | Verificado |
| Staging ligero / quickstart DB-backed | `.\scripts\dev.ps1 Up` | Docker opcional |
| VM persistente de referencia | `docker compose` en `api/compose.yml` | Docker opcional |
| Self-host de referencia | `.\scripts\dev.ps1 ProductionInstall -AllowedOrigin ...` | Docker opcional |
| Producción gestionada | `systemd`/native Python behind HTTPS reverse proxy, or Kubernetes | Native path verified for hosted SDS API; Kubernetes remains reference |

---

## ✅ Checklist pre-despliegue

- Python 3.10-3.12 para el servicio local/systemd. La serialización
  `sds-canonical-json-v1` y la suite completa requieren CPython 3.10 / Unicode
  `13.0.0`; con otros datos Unicode la serialización falla cerrada. Docker usa
  CPython 3.10. Ver [runtime de tests](tests.md#runtime-de-serialización-canónica).
- `JWT_SECRET_KEY` definido y fuerte
- `EXPORT_SIGNING_SECRET` definido, fuerte y distinto de `JWT_SECRET_KEY`
- `POSTGRES_PASSWORD` definido y seguro para un componente de usuario de URI
  (por ejemplo, generado como hexadecimal); ambos perfiles Compose interpolan
  esa misma variable para PostgreSQL y para `DATABASE_URL`, y no incluyen una
  contraseña literal en el repositorio. No uses caracteres reservados de URL
  sin preparar una configuración externa que los codifique correctamente.
- `BOOTSTRAP_ADMIN_PASSWORD` definido
- `ALLOWED_ORIGINS` configurado preferiblemente como JSON array válido
- `REQUIRE_DATABASE=true` para despliegues persistentes
- `VALUE_REVISION_API_ENABLED=true`, `VALUE_REVISION_DUAL_WRITE_ENABLED=true`
  y `VALUE_REVISION_PRIMARY_READ_PATH=revision` para el servicio operativo de
  valores
- `POSTGRES_PORT` ajustado si `5432` ya está ocupado en el host
- `PORTAL_MOUNT_ENABLED=false`; el repo público no incluye un portal estático
  montable por defecto
- paquete SDS externo de indicadores/valores elegido y validado antes del refresco manual:
  - `python scripts/import_sds_package.py --package-dir <path-to-sds-package> --dry-run`
  - el helper valida el contrato SDS documentado en [Importar paquetes](import-packages.md)
- paquete canónico de mapeos elegido y validado si el despliegue necesita
  refrescar correspondencias estándar:
  - `python api/scripts/validate_canonical_mapping_package.py --package-dir <path-to-canonical-mapping-package>`

---

## ✅ Helpers opcionales de Docker Compose

### Quickstart local DB-backed

```powershell
cd api
.\scripts\dev.ps1 Up
```

Usa `.env` y `compose.yml`. Arranca PostgreSQL + API para validación funcional
rápida con `/docs`, admin login y datos cargados en la base activa. No es el
modelo obligatorio de instalacion de dependencias para usuarios de la API
publica.

### Self-host de referencia

Este perfil sirve para ensayos técnicos en un entorno controlado. El nombre
`ProductionInstall` describe la configuración técnica, no concede permiso para
explotar SDS en producción. Para un servicio propio o para terceros se requiere
un acuerdo separado y por escrito con el titular de los derechos.

```powershell
cd api
.\scripts\dev.ps1 ProductionInstall -AllowedOrigin https://your-domain.example
```

Usa `.env.production` y el nombre historico `compose.production.yml` como
perfil Docker Compose de referencia. Esta ruta:
- genera secretos fuertes si faltan o siguen como placeholders,
- exige `ALLOWED_ORIGINS` explícito,
- arranca PostgreSQL + API,
- crea solo el admin inicial,
- deja el montaje de portal local desactivado por defecto,
- verifica `/healthz`, `/docs`, admin login, `/ready` autenticado y listado de conceptos.
  No aceptes un stack donde `/api/v1/concepts` queda con una tabla parcial:
  `make -C api gate-semantic-catalog-projection` debe confirmar que todos los
  indicadores activos tienen concepto semántico determinista en PostgreSQL.

El contenedor Docker de referencia arranca con un solo worker porque las
migraciones y el bootstrap siguen ejecutándose en el lifespan de la app. No
trates `--workers > 1` como soportado en este perfil hasta externalizar o
validar explícitamente la orquestación de migraciones/bootstrap.

No convierte Docker en requisito de despliegue ni en gate autoritativo de la
API: quienes dispongan de autorización contractual para operar SDS en un
entorno público podrán instalar Python, PostgreSQL y el proceso FastAPI con
sus propios mecanismos, previa cualificación técnica y de seguridad.

Para probar el perfil self-host en la misma máquina sin chocar con otro stack:

```powershell
.\scripts\dev.ps1 ProductionInstall `
  -AllowedOrigin http://localhost:8092 `
  -AllowLocalhostOrigin `
  -ApiPort 8092 `
  -PostgresPort 55433
```

Para un drill desechable mientras existe un stack persistente `sds-api-prod`,
usa nombre de proyecto y env propios. `<operator-drill-dir>` debe estar fuera
de la raiz publica del repo y servir solo para envs/salidas locales del drill:

```powershell
.\scripts\dev.ps1 ProductionInstall `
  -AllowedOrigin http://localhost:8093 `
  -AllowLocalhostOrigin `
  -ApiPort 8093 `
  -PostgresPort 55434 `
  -ProjectName sds-a02-prod `
  -EnvFile <operator-drill-dir>\a02-prod\.env.production

.\scripts\dev.ps1 ProductionPs `
  -ProjectName sds-a02-prod `
  -EnvFile <operator-drill-dir>\a02-prod\.env.production

.\scripts\dev.ps1 ProductionDown `
  -ProjectName sds-a02-prod `
  -EnvFile <operator-drill-dir>\a02-prod\.env.production `
  -RemoveVolumes
```

`-ProjectName` y `-EnvFile` deben repetirse en `ProductionLogs`,
`ProductionPs` y `ProductionDown` para operar el mismo proyecto. `-RemoveVolumes`
solo debe usarse en proyectos desechables seleccionados explícitamente; sin ese
switch, `ProductionDown` conserva el volumen PostgreSQL.

Operación:

```powershell
.\scripts\dev.ps1 ProductionLogs
.\scripts\dev.ps1 ProductionPs
.\scripts\dev.ps1 ProductionDown
```

`-ResetDatabase` existe, pero es destructivo y solo debe usarse si aceptas borrar
el volumen PostgreSQL del compose de referencia.

---

## 🌐 Verificación de target Virtualmin

Para dominios servidos por Virtualmin, el repo incluye un helper genérico que
verifica el document root por SSH, la URL pública con `curl` y, si se indican,
los logs de Apache/PHP. No despliega contenido ni cambia ownership, SELinux,
Apache o PHP-FPM:

```powershell
cd api
.\scripts\verify_virtualmin_target.ps1 `
  -PublicUrl "https://sds.example.com/" `
  -SshHost "<host-or-ip>" `
  -SshUser "<ssh-user>" `
  -SshPort 2227 `
  -IdentityFile "$HOME\.ssh\<key-name>" `
  -DocRoot "/home/<owner>/domains/sds.example.com/public_html" `
  -AccessLog "/var/log/virtualmin/sds.example.com_access_log" `
  -ErrorLog "/var/log/virtualmin/sds.example.com_error_log" `
  -PhpLog "/home/<owner>/domains/sds.example.com/logs/php_log"
```

No guardes claves privadas ni secretos en el repo. Si aparece `403` tras subir
archivos a un sub-server Virtualmin, verifica primero ownership/SELinux del
document root y reetiqueta con `restorecon` antes de cambiar Apache.

### Current public production API

The public production API surface is
`https://api.sustainabilitydataspace.com/`. It is distinct from the official
E11 communications website at `https://sustainabilitydataspace.com/`: API
availability does not close website content/impact acceptance or
subsidy/dossier residuals.

Minimum public no-secret production probes:

```powershell
curl.exe -I -L --max-time 20 https://api.sustainabilitydataspace.com/docs
curl.exe -I -L --max-time 20 https://api.sustainabilitydataspace.com/openapi.json
curl.exe -I -L --max-time 20 https://api.sustainabilitydataspace.com/healthz
curl.exe -I -L --max-time 20 https://api.sustainabilitydataspace.com/ready
```

The documentation, OpenAPI, health and readiness routes are public. API
business routes under `/api/v1/*` require application authentication. Run
credentialed API smoke checks only from a private operator environment; never
write passwords, tokens, authentication headers, or production environment
values into this repository.

### Application-user provisioning for CI

For an existing database, provision application users with the generic
`scripts/provision_application_user.py` command. It uses the application's
configured database session and intentionally has no password option. Supply
the password only through standard input; Jenkins Credentials must be the
secret source, and the pipeline must not echo the binding, put it in an
argument, or persist it in build output.

```powershell
<private-credential-binding> | python scripts/provision_application_user.py `
  --username <application-username> `
  --email <application-email> `
  --full-name <application-full-name> `
  --company-id <optional-company-id> `
  --role <existing-role> `
  --active true
```

The command emits only one machine-readable JSON result. It creates a missing
account with the supplied exact fields, or verifies an existing account only
when its non-secret profile and supplied password already match. A password
mismatch, profile/role/active-state drift, duplicate write, or database failure
is fail-closed and does not disclose credentials, hashes, database details, or
the requested account fields.

Password rotation is deliberate and one-time: add `--rotate-password` only to
the approved rotation invocation. Rotation changes the password credential and
increments its session version, invalidating existing sessions. Subsequent
deployments must omit that flag and remain verify-only; they must never rotate a
password implicitly.

After a successful CI step, perform the private operator smoke check with the
intended analyst account. Verify these concrete A2.3 authorization checks:

- `GET /auth/me` returns `200` and identifies an active analyst profile.
- `GET /api/v1/indicators?limit=1` returns `200`.
- `GET /api/v1/values` returns `200` using query parameters
  `entity=nh_group`, `period_start=2024-01-01`, `period_end=2024-12-31`, and
  `limit=10`.
- `POST /api/v1/calculate` with `concept=urn:sds:reg:esrs:e1_5_02`,
  `entity=nh_group`, `period=2024`, and `include_trace=true` returns `200`
  and includes `trace` in the response.
- `GET /api/v1/mappings/search?source_standard=ESRS&target_standard=GRI&target_code=GRI%20305&limit=10`
  returns `200`.
- `GET /api/v1/calculate/dependencies/urn:sds:reg:esrs:e1_5_02` returns
  `200`.
- `POST /api/v1/values` with an otherwise valid value request returns `403`.

Do not add a crosswalk endpoint check: no crosswalk router is registered. Keep
credentials, tokens, request headers, and account-specific response bodies out
of Jenkins logs and this repository.

This CLI cannot deactivate or repair a drifted account; that is intentional.
Use the separately approved administrative procedure for deactivation or
profile changes, then run this command in verify-only mode. A database rollback
that restores a pre-rotation user row or `auth_version` can make unexpired old
access or refresh tokens valid again. Fail closed: before reopening access,
reapply or advance the credential epoch and the approved current password, or
rotate and revoke credentials through the approved procedure. Never assume an
old session remains invalid after rollback.

### Managing another user's profile, tenant, role, state and password

An administrator manages other accounts through two endpoints. Both accept only
a bearer token of an `admin` holding `manage_users`; API keys and other roles
receive `403`, targeting yourself returns `400` (use `/auth/me` or
`/auth/change-password`), an unknown user returns `404`, and each is limited to
5 requests per minute per client. Validation errors (`422`) never echo the
submitted values.

- `PUT /auth/users/{username}` sets any of `email`, `full_name`, `company_id`
  (tenant), `role` and `is_active`. At least one field is required; `username`
  cannot be changed; blank or placeholder tenants are rejected. An email already
  used by another account (exact match, as enforced by the database) returns
  `409` without changes.
- `POST /auth/users/{username}/reset-password` with `{"new_password": "..."}`
  sets a password that meets the application policy (at least 12 characters,
  three of lowercase/uppercase/digits/symbols, at most 72 UTF-8 bytes, no
  placeholder words). The response never contains the password. An inactive
  user stays inactive; a concurrent change returns `409`.

```powershell
curl.exe -X PUT https://<api-host>/auth/users/analyst `
  -H "Authorization: Bearer <admin-access-token>" `
  -H "Content-Type: application/json" `
  -d '{"company_id": "<tenant-id>"}'
```

Every successful change advances the target's `auth_version`, so the target's
existing access and refresh tokens stop working and the user must log in
again. There is no tenant registry: confirm the exact `company_id` that owns the
data before assigning it. Run these calls only from a private operator
environment, deliver reset passwords through an approved secure channel, and
keep admin tokens and passwords out of logs and this repository.

### Admin catalog operations (calculation contracts and unit catalog)

Bearer `admin` tokens holding `manage_system` can maintain calculation
semantics and the unit catalog without server access. API keys and every other
role receive `403`; validation errors never echo submitted values; bodies above
`REQUEST_MAX_BODY_BYTES` (10 MiB by default) receive `413`.

- `POST /api/v1/admin/calculation-contracts/validations` dry-runs an
  `sds_calculation_contract.json` package (sent as the raw JSON body) against
  the live indicator catalog and writes no contract rows.
- `POST /api/v1/admin/calculation-contracts/imports?confirm=true` imports it in
  one transaction under a per-package advisory lock. The same package hash
  returns `already_imported` without new rows. `retirement_scope` accepts
  `incoming_keys` (default) or `incoming_models`.
- `GET /api/v1/admin/unit-catalog/conflicts` lists active-unit conflicts exactly
  as the calculation converter detects them (for example a separate `m3` unit
  colliding with the `m3` alias of `m³`).
- `POST /api/v1/admin/unit-catalog/repairs/preview` checks a deactivation of one
  duplicate in favour of another unit and returns a plan plus `plan_digest`.
  It refuses different categories, factors, offsets or dimensions, a category
  base unit, active conversion rules that reference the duplicate, any symbol
  or alias of the duplicate the retained unit does not cover, and a unit name
  the retained unit does not cover when stored values or active conversion
  rules still use it (names are resolved case-insensitively). `POST .../repairs/commit?confirm=true` applies an unexpired, unmodified
  plan signed by the server (`plan_digest` is an HMAC; edited plans are
  refused); `POST .../repairs/{repair_id}/reverse?confirm=true` restores the exact
  previous row when no later unit-catalog operation exists.

Commits and reversals verify the catalog inside the same transaction, while
holding the catalog lock and before `COMMIT`: the active-unit conflicts after
the change must be exactly the set the plan predicted (for a reversal, the set
recorded before the repair), and when that set is empty the physical converter
must build. Other unrelated conflicts may remain, so duplicates can be repaired
one at a time. Any mismatch returns `409` and nothing is persisted.

- `POST /api/v1/admin/unit-catalog/factor-corrections/preview` handles two
  colliding units whose factors differ (for example a legacy `kgCO2e` with
  factor `0.001` against the `kg CO2e` base). The body adds an
  `acknowledgement` that must be exactly `I confirm unit <target> (factor
  <F>) is incorrect and unit <retained> (factor <F>) is correct relative to
  base <base>`; a mismatch returns `422` with the expected text. Besides the
  duplicate-repair checks (except equal factors) it requires zero offsets, an
  active category base unit with factor 1, and proof from the bundled
  `src/data/units_database.json`: the retained unit exists there in a category
  with the same name and base, with the same factor, lists the target symbol
  as an alias, and the target symbol is not a unit of its own. The response
  includes the signed plan (separate signing domain, bound to the reference
  catalog SHA-256) and an impact report. `POST
  .../factor-corrections/commit?confirm=true` needs the plan, `plan_digest` and
  the same acknowledgement; it soft-deactivates the target only (no factor is
  rewritten) and is reversed with the repair reverse endpoint.
- `GET /api/v1/admin/unit-catalog/units/{unit_id}/impact` reports, in a
  read-only transaction with a 5 s statement timeout, how many stored values
  (`esg_values`) and value revisions use any identifier of the unit (symbol,
  aliases, name; case-insensitive) in `unit` or `original_unit`, split by
  conversion signal, plus opaque row ids (at most 50 per page,
  `next_cursor`). Rows are only "possibly affected"; values, metadata, traces,
  entities and concepts are never returned. `truncated` and `timed_out` are
  explicit.

Stored values keep their unit text. Every operation is recorded in
`admin_catalog_operations` (migration `049`; factor corrections carry
`operation`, old/new factor and offset, base, impact counts and the reference
SHA-256);
each API process reloads its unit catalog within one second of a committed
repair or reversal.

### Admin error diagnostics

Server errors (`500`) keep their generic response with a `request_id`. The API
also records a sanitized diagnostic for that `request_id` in
`admin_error_diagnostics` (migration `050`) and logs the same record: exception
types along the cause chain, file/line/function of frames under `api/src`, the
route template, a fixed `classification` (for example `db_unique_violation`,
`db_trigger_exception`, `unit_conversion_error`, `unknown`) and the PostgreSQL
`pgcode`, constraint, table and column. A trigger message is kept only when it
equals a static `RAISE EXCEPTION` text from the migrations. Exception messages
and arguments, headers, bodies, query values, SQL text and parameters are never
stored or logged. Capture is fail-open and uses its own short transaction.

Retention is 14 days and the newest 500 records, enforced under an advisory
lock by every capture and before every read; expired rows are never returned.
Bearer `admin` tokens holding `manage_system` read them with `GET
/api/v1/admin/diagnostics/errors/{request_id}` (uniform `404` when absent) or
`GET /api/v1/admin/diagnostics/errors?limit=20` (newest first, at most 50).

### Native hosted release helper

For the native hosted API, use the checked-in helper
`api/scripts/deploy_native_release.sh` instead of ad-hoc shell fragments. The
helper resolves an explicit `--python`/`PYTHON_BIN` first, then the systemd
service ExecStart, the current release virtualenv, and explicit Python
3.12/3.11/3.10 candidates. It validates Python `>=3.10` before creating the
virtualenv or running `pip install`, so a host where `/bin/python3` still points
to Python 3.9 cannot fail midway through release preparation.

Before creating a backup directory, extracting files, installing dependencies,
checking database state, or touching the service, the helper requires `current` to
be a valid symlink to an existing release and `--release` to name a fresh direct
child of that active release's canonical container. Existing destinations,
aliases of the active release (including `..` and symlink aliases), broken
active links, and destinations outside that container are rejected. The helper
serializes deployments sharing the current-link parent and never deletes an
existing release directory as recovery; choose a new release name after any
failed preparation. `--skip-restart` skips the link swap and service restart;
all remaining database operations are read-only qualification checks.

The archive is an authenticated release input. Supply its complete lowercase
SHA-256 using `--tarball-sha256`. The public `api/requirements.lock` binds
the reviewed Linux amd64 wheels used by the Python 3.10 Docker image and the
Python 3.12 hosted native runtime by exact version and SHA-256. The archive must
include those reviewed lock bytes and an externally assembled `api/wheelhouse/`
with every required distribution for the target interpreter. Installation uses
`pip --no-index --require-hashes`; the deployment host does not upgrade tools or
access an index. Generate/verify the wheelhouse for the same Python/platform
target in the controlled release-build lane; keep wheels out of Git and never
hand-edit the lock. The public Dockerfile currently downloads hashed wheels
during build, so its successful build alone is not evidence of an offline AWS
release.

Both forward and rollback current-link swaps require GNU `ln -sfnT`, followed
immediately by `readlink -f` verification against the exact expected canonical
target. `-T` refuses a directory substituted at `current` instead of creating a
link inside it. A failed forward swap or target verification aborts without a
service restart. A failed rollback swap or verification reports the failure
and returns without another restart or a healthy-restore claim; operators must
inspect the link and service state. The existing active-target checks and
deployment lock remain in place. This is narrow H08 race containment, not full
fencing of non-cooperating writers: a writer can still change the link after
verification and before restart. Keep external link writers serialized; this
does not make filesystem switching and service restart atomic.

The helper never migrates or repairs the live database. It requires the Alembic
`current` revision set to match the `heads` revision set and requires the
persisted projection gate to pass. It does not run the projector, including its
nominal dry-run, because project initialisation can mutate schema state.
`--bootstrap-semantic-model` is deliberately rejected. Perform schema
migration or projection changes only in a separate authorised maintenance lane:
quiesce the old service, take and verify a database backup, apply the change,
qualify it with both old/new compatibility rules or an explicit cutover, and
restore the database before resuming old code if that lane fails. This separation
means the release helper's application rollback never pretends to undo database
state. The maintenance lane must leave the database at the qualified head;
deployment refuses migration or semantic-projection drift.
`--skip-semantic-projector` remains an explicit bypass and must not be used for a
production cutover.

From Windows, upload a clean archive built from a commit and copy the helper to
the host:

```powershell
New-Item -ItemType Directory -Force <operator-work-dir>\deploy | Out-Null
<# The approved release-build lane verifies the tracked api/requirements.lock,
   adds the matching api/wheelhouse externally to a clean archive, and never
   adds the wheel binaries to Git. #>
$digest = (Get-FileHash -Algorithm SHA256 <operator-work-dir>\deploy\sds-api-<commit>.tar).Hash.ToLowerInvariant()
scp -i $HOME\.ssh\<key-name> -P 2227 <operator-work-dir>\deploy\sds-api-<commit>.tar <user>@<host>:/tmp/sds-api-<commit>.tar
scp -i $HOME\.ssh\<key-name> -P 2227 api\scripts\deploy_native_release.sh <user>@<host>:/tmp/deploy_native_release.sh
```

For a self-hosted/reference systemd service that binds the API on the default
`8090` port, the helper can use its generic fallback:

```powershell
ssh -i $HOME\.ssh\<key-name> -p 2227 <user>@<host> "sudo -n bash /tmp/deploy_native_release.sh --tarball /tmp/sds-api-<commit>.tar --tarball-sha256 $digest --release /opt/sds-api/releases/<release-name> --commit <commit>"
```

For a managed production target, never call plain `python3 -m venv` in a
deployment fragment. Use the release helper so its interpreter-floor and
lockfile checks remain in force.

---

## 🐳 Docker Compose (quickstart opcional)

### 1. Configurar `.env`

```bash
cd api
cp .env.example .env
```

Valores mínimos recomendados:

```env
REQUIRE_DATABASE=true
POSTGRES_PASSWORD=cambia_esta_password
BOOTSTRAP_ADMIN_PASSWORD=cambia_esta_password_admin
JWT_SECRET_KEY=cambia_esto_usa_32_caracteres_minimo
ALLOWED_ORIGINS=["http://localhost:8090"]
POSTGRES_PORT=5432
SEED_REFERENCE_DATA_ON_STARTUP=true
SEED_DEFAULT_USERS=true
```

Notas:
- Si `5432` está ocupado en Windows, cambia a `POSTGRES_PORT=55432`.
- `ALLOWED_ORIGINS` se recomienda como JSON array; el runtime también acepta un origen único o lista separada por comas.
- Con `REQUIRE_DATABASE=true`, el backend de unidades pasa automáticamente a PostgreSQL.

### 2. Levantar el stack opcional

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

### 3. Qué hace el arranque en una DB vacía

El arranque de la API:
- crea el esquema (`init_db()`),
- seeda indicadores bundled,
- seeda mappings bundled,
- seeda `unit_categories`, `units` y `conversion_rules`,
- crea el admin inicial si `SEED_DEFAULT_USERS=true`.

Eso significa que el stack queda usable aunque todavía no hayas refrescado la DB desde un paquete externo.

Las migraciones incluyen indices aditivos para las rutas de lectura de alto
volumen de valores y mappings (`ix_esg_values_read_cursor`,
`ix_esg_values_change_cursor` y los indices JSONB de dimension en
`materialized_pairwise_mappings`). Estos indices reducen el coste de las rutas
comunes de cursor/filtro, pero no sustituyen una validacion con
`EXPLAIN (ANALYZE, BUFFERS)` en el PostgreSQL real cuando el despliegue tenga
volumenes propios. Las respuestas paginadas mantienen `total` y `pages` exactos
por compatibilidad de contrato; en filtros muy amplios ese conteo sigue siendo
un coste deliberado del contrato actual.

Como estas migraciones se ejecutan dentro de Alembic, la creación de índices no
usa `CONCURRENTLY`. En tablas ya muy grandes, programa la migración en una
ventana de mantenimiento o prepara una migración operativa específica con
índices concurrentes validada para ese entorno.

### 4. Refrescar desde paquetes SDS externos (opcional)

Hazlo si quieres que el despliegue use un catálogo externo de indicadores y, si
existe, valores operativos:

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
cd api
# Public /api/v1/mappings is served from materialized_pairwise_mappings, not the legacy
# standard_mappings table; materialize the canonical read model after importing a package.
python scripts/materialize_canonical_pairwise_mappings.py --db-url "postgresql://sds:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/sds"
```

```powershell
Set-Location <repo-root>
.\api\.venv\Scripts\python.exe .\scripts\import_sds_package.py --package-dir <path-to-sds-package> --dry-run
.\api\.venv\Scripts\python.exe .\scripts\import_sds_package.py --package-dir <path-to-sds-package>
Set-Location .\api
.\.venv\Scripts\python.exe scripts\materialize_canonical_pairwise_mappings.py
```

Si el paquete incluye `sds_values.csv`, el mismo helper carga también los valores operativos. Para importar solo un CSV de indicadores por API, usa el flujo de validación y confirmación de [Importar paquetes](import-packages.md).

Si el despliegue necesita mapeos canónicos, valida primero el paquete de mapeos:

```bash
python api/scripts/validate_canonical_mapping_package.py --package-dir <path-to-canonical-mapping-package>
python api/scripts/import_canonical_mapping_package.py --package-dir <path-to-canonical-mapping-package> --db-url "postgresql://sds:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/sds" --dry-run --json
```

La importación real escribe en tablas canónicas/shadow y no cambia
automáticamente `/api/v1/mappings` sin una materialización/cutover explícitos.

### 5. Verificación post-arranque

```bash
curl -fsS http://localhost:8090/healthz
curl -fsS http://localhost:8090/ready
docker compose --env-file .env -f compose.yml exec -T postgres psql -U sds -d sds -c "SELECT COUNT(*) FROM indicators;"
docker compose --env-file .env -f compose.yml exec -T postgres psql -U sds -d sds -c "SELECT COUNT(*) FROM units;"
docker compose --env-file .env -f compose.yml exec -T postgres psql -U sds -d sds -c "SELECT taxonomy, concept_type, COUNT(*) FROM concepts GROUP BY 1, 2 ORDER BY 1, 2;"
make -C api gate-semantic-catalog-projection
```

---

## Smoke tests (ruta verificada)

```bash
cd api
bash scripts/smoke_stack.sh minimal
```

```powershell
Set-Location api
.\scripts\smoke_stack.ps1 -Profile minimal
```

La ruta mínima es la recomendada para validación rápida antes de una presentación.

---

## Monitoring y alerting

El API expone `/healthz`, `/ready` y `/metrics`. En self-host, trata esos tres
endpoints como el contrato mínimo de observabilidad; el repo no despliega un
stack Prometheus/Grafana gestionado. `/ready` y `/metrics` no son públicos:
exigen una
identidad activa con el permiso `view_system_health`, mediante bearer o una
API key limitada a ese permiso. Guarde la credencial del scraper fuera de Git
y rótela; no la escriba directamente en `prometheus.yml`.

Ejemplo mínimo de scrape Prometheus:

```yaml
scrape_configs:
  - job_name: sds-api
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /run/secrets/sds-metrics-bearer
    static_configs:
      - targets: ["localhost:8090"]
```

El fichero anterior debe contener únicamente un access token vigente de la
cuenta técnica de monitorización y actualizarse antes de su expiración. Si se
usa una API key limitada, el proxy local del scraper debe inyectarla como
`X-API-Key` sin registrarla ni exponerla en argumentos de proceso.

Alertas mínimas para el operador:

| Señal | Condición | Acción |
|---|---|---|
| API no responde | `up{job="sds-api"} == 0` durante 2 minutos | Revisar `ProductionLogs` / proceso systemd, reiniciar solo tras capturar logs. |
| Readiness degradada | `sds_api_readiness == 0` o `/ready` no devuelve `status=ready` | Revisar dependencia PostgreSQL, migraciones y catálogo canónico antes de aceptar tráfico. |
| BD no saludable | `/healthz` muestra `dependencies.database.status != healthy` | Verificar PostgreSQL, credenciales y conectividad; no ejecutar imports hasta recuperar salud. |
| Errores 5xx sostenidos | aumento de `sds_api_requests_total` con códigos `5xx` durante 5 minutos | Revisar logs, payloads recientes, jobs de importación y cambios de configuración. |
| Backup/restore fallido | script `backup`/`restore` devuelve código distinto de 0 | No reutilizar salidas incompletas; seguir [Backup/Restore](backup_restore.md) y registrar el incidente. |

Cuando se dispare una alerta, conserva logs, timestamp, versión git desplegada y
estado de `/healthz` + `/ready`. Para incidentes de seguridad o política, sigue
también el proceso de respuesta definido en la evidencia de gobernanza E6.

---

## 🖥️ Systemd (referencia avanzada)

Usa esta ruta solo si necesitas un host Linux dedicado sin Docker.

Pasos recomendados:
1. Clonar el repo en una ruta estable.
2. Instalar Python 3.10-3.12 y crear `.venv` en `api` con esa versión.
3. Crear `.env` con `REQUIRE_DATABASE=true`.
4. Garantizar PostgreSQL externo o local.
5. Validar/importar un paquete SDS externo si quieres refrescar indicadores o valores fuera del bootstrap bundled.
   El arranque seeda los conceptos semánticos mínimos bundled que falten y
   ejecuta el proyector determinista para materializar cada indicador activo
   como concepto DB-backed en `concepts`.
6. Ejecutar `uvicorn src.api.main:app --host 0.0.0.0 --port 8090 --no-proxy-headers`.

Ejemplo de `ExecStart`:

```ini
ExecStart=/opt/sds/sustainabilityDataSpace/api/.venv/bin/python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8090 --no-proxy-headers
```

Notas:
- la lógica de bootstrap sigue viviendo en el arranque de la app,
- usa un único worker; `--workers > 1` no está soportado en este perfil,
- esta ruta requiere una validación propia en el host de destino.
- desactiva la reescritura previa de `scope.client` por Uvicorn: la aplicación
  solo usa `X-Forwarded-For` cuando el par de conexión figura en
  `TRUSTED_PROXY_IPS`. Exige que el proxy añada el salto real y no exponga
  directamente el puerto de la API; comprueba esta cadena en el host antes
  de calificar la limitación de peticiones.
- el middleware admite como máximo ocho lecturas concurrentes y limita la
  suma lógica de bytes de cuerpos propios en lectura o retenidos durante
  handlers
  a `8 × REQUEST_MAX_BODY_BYTES`; corta cada lectura a los 30 segundos.
  Un `receive()` ASGI que ignore su cancelación retiene una plaza del cupo
  de lecturas pendientes hasta finalizar. Los fragmentos que entregue el
  servidor y la copia transitoria al crear el cuerpo inmutable requieren
  margen de memoria adicional: no es un techo de memoria residente. Ajusta
  la memoria y el límite del proxy al perfil medido en el host antes de aceptar
  un despliegue. `GET/HEAD /healthz` sin cuerpo declarado ni
  `Transfer-Encoding` evitan este cupo para que ocho cargas lentas no
  provoquen un falso fallo de liveness; valida en el proxy el rechazo de
  cuerpos en ese endpoint y el límite de bytes de ingreso.
- El SPARQL local acepta solo `SELECT`/`ASK` y responde en JSON plano con
  valores léxicos (no conserva idioma/tipo RDF en la respuesta). El límite
  de consulta incluye la comprobación cooperativa del grafo y la ejecución
  del proceso hijo, pero el iterador del grafo y `Process.start()` no se
  pueden interrumpir a mitad de llamada: no presupongas un plazo estricto
  extremo a extremo ni disponibilidad de esta ruta con `REQUIRE_DATABASE=true`.

---

## ☸️ Kubernetes (referencia avanzada)

Kubernetes sigue siendo una opción válida, pero no hay un manifiesto mantenido/validado por smoke en este repo actual.

Si despliegas en Kubernetes:
- usa PostgreSQL gestionado externo,
- inyecta `JWT_SECRET_KEY`, `DATABASE_URL`, `BOOTSTRAP_ADMIN_PASSWORD` como secrets,
- trata el refresco desde un paquete SDS externo como un paso explícito de job/init task,
- valida `/healthz` como probe público barato; consulta `/ready` con una
  identidad interna que tenga `VIEW_SYSTEM_HEALTH`, no desde el balanceador.

Recomendación práctica: no uses Kubernetes como primera ruta para una prueba cercana.

---

## 🔄 Rollback y recuperación

### Rollback de aplicación

```bash
cd api
docker compose --env-file .env -f compose.yml logs > rollback-$(date +%Y%m%d-%H%M%S).log
git checkout <commit-o-tag-anterior>
docker compose --env-file .env -f compose.yml up -d --build
```

### Rollback con migraciones

`git checkout` no deshace el esquema de PostgreSQL. Si la base ya está migrada
al head actual y vuelves a un commit anterior, confirma antes que ese código es
compatible con el esquema ya aplicado.

Antes de cualquier rollback con cambio de migraciones:

1. Crea un `pre-migration backup` con manifiesto y SHA-256.
2. Registra el head Alembic aplicado y el commit al que quieres volver.
3. Valida en un proyecto desechable si basta con revertir aplicación sobre el
   esquema actual.
4. Usa `Alembic downgrade` solo como operación explícita, revisada y probada;
   no lo trates como consecuencia automática de cambiar de commit.
5. Si el código anterior no es compatible con el esquema actual, restaura desde
   un backup anterior a la migración o prepara una migración operativa validada.

Tras el rollback, ejecuta `/healthz`, `/ready`, login admin, lectura de catálogo
y el smoke mínimo antes de devolver tráfico.

### Recuperación de datos

Usa los procedimientos de [Backup/Restore](backup_restore.md).

En entornos compose:
- crea backup antes de cambiar credenciales o destruir volúmenes,
- usa `docker compose ... down -v` solo si aceptas recrear la DB desde cero o restaurar desde backup.

---

## 📊 Verificación post-despliegue

Checklist mínimo:

```bash
curl -fsS http://localhost:8090/healthz
curl -fsS http://localhost:8090/ready
curl -sS http://localhost:8090/docs > /dev/null
```

Checklist ampliado:
- login con `admin` y `BOOTSTRAP_ADMIN_PASSWORD`,
- `GET /api/v1/indicators?limit=1`,
- `GET /api/v1/mappings/standards`,
- `POST /api/v1/convert`,
- `POST /api/v1/values`,
- `POST /api/v1/calculate`.

---

## 📚 Referencias

- [Getting Started](getting-started.md) — puesta en marcha rápida
- [Configuration](configuration.md) — `.env` y backend de unidades
- [Backup/Restore](backup_restore.md) — backups y restores PostgreSQL
- [Troubleshooting](troubleshooting.md) — resolución de incidencias
