# Importar paquetes SDS: indicadores, valores y mapeos

SDS separa el catálogo de indicadores, los valores operativos y los paquetes de
mapeos canónicos.
Estos procedimientos se documentan para evaluación técnica; no conceden
derechos para operar SDS en producción, prestar servicios a terceros ni usar
el contenido de paquetes ajenos sin autorización. Véase «Alcance de uso
previsto» en el `README.md` de la raíz.

## Demo público sintético

El paquete [`../demo/`](../demo/) es una demostración pública, determinista y
totalmente sintética. No es un dump ni una exportación Nordhaven. En una base
PostgreSQL desechable vacía se instala, verifica y elimina mediante el CLI
síncrono soportado:

```bash
make -C api demo-manifest
DATABASE_URL='<local-postgresql-url>' make -C api demo-install
DATABASE_URL='<local-postgresql-url>' make -C api demo-verify
DATABASE_URL='<local-postgresql-url>' make -C api demo-reset
```

Los targets `demo-*` fijan en el proceso CLI host
`VALUE_REVISION_API_ENABLED=true`, `VALUE_REVISION_DUAL_WRITE_ENABLED=true` y
`VALUE_REVISION_PRIMARY_READ_PATH=revision` antes de cargar el importador
estricto. Es una configuración separada de Compose; una llamada directa a
`scripts/demo_runtime.py` requiere las mismas variables y falla cerrada si
faltan o son falsas.

No usa endpoints de jobs asíncronos DB. UC-01 prueba una observación de energía
y conversión compatible; UC-02 se limita a conversión compatible de unidades de
emisiones, no factores ni agregación Scope 1-3; UC-03 conserva además fuente,
destino, estrés y evidencia de agua. UC-04..UC-06 son demostraciones acotadas
de contrato de datos: no aplican política o conectores, no ejecutan motor de
circularidad, no hacen scoring ESG/crédito, ni certificación territorial o
conclusión jurídica.

La verificación local ordinaria (`demo-verify`) es solo DB-backed. La ruta API
es explícita y exclusiva de CI: `--ci-api-principal --api-url ...` autentica al
bootstrap admin únicamente para crear por `POST /auth/users` un
`data_manager` aleatorio ligado a `value_import_gate`. Solo ese principal lista
las filas demo y llama a la conversión; el admin bootstrap no lee valores. La
contraseña/tokens del principal no se imprimen ni persisten fuera del proceso.
No hay endpoint soportado para borrar usuarios: se ejecuta solo sobre un volumen
CI desechable, destruido con `down -v`. `demo-reset` borra filas demo, no
identidades.

- **Indicadores**: definen qué KPIs existen, sus códigos normativos, unidades esperadas, periodicidad, trazabilidad y metadatos.
- **Valores**: son las observaciones concretas para una entidad y periodo.
- **Mapeos canónicos**: declaran releases, datapoints y assertion groups /
  components para alimentar el modelo Sygris/SDS de correspondencias.

Primero importa el catálogo de indicadores. Después podrás cargar valores contra
esos indicadores. Los mapeos canónicos se validan/importan como paquete separado
en tablas shadow antes de cualquier materialización controlada.

La resolución runtime de interoperabilidad (`/api/v1/values/resolve`) consume
estos mismos activos cargados: valores operativos, contratos/fórmulas de cálculo,
mappings exactos/equivalentes materializados y catálogo de unidades. La guía de
uso está en [Runtime Interoperability Guide](interoperability-runtime.md).

## Importar la base de datos de indicadores

La validación síncrona del catálogo está disponible por API; usa el CLI soportado para importar. La admisión asíncrona por API en modo DB está bloqueada pendiente de H15.

### 1. Preparar el CSV de indicadores

El CSV debe seguir el contrato SDS de registro de indicadores. Las columnas mínimas son:

```text
identifier,title,indicator,description,dimension,unitName,unitType,periodicity,periodType,sourceRef,codeESRS,codeGRI,codeGRI_expanded,evidencePath,sourceRow,owner,accessRights,validationMethod,doubleMateriality,valueType
```

Cada fila representa un indicador/KPI. `identifier` debe ser estable y único; los paquetes de valores podrán usar ese mismo `identifier` en la columna `concept`.

Ejemplo mínimo:

```csv
identifier,title,indicator,description,dimension,unitName,unitType,periodicity,periodType,sourceRef,codeESRS,codeGRI,codeGRI_expanded,evidencePath,sourceRow,owner,accessRights,validationMethod,doubleMateriality,valueType
urn:sds:reg:custom:energy_001,Total energy consumption,Total energy consumption,Annual total energy consumption,E,kWh,Energy,annual,fiscal_year,internal-source,E1-5,,,,1,system,Internal,import,,numeric
```

### 2. Validar por API sin escribir indicadores

El endpoint de validación requiere un usuario con permiso `manage_indicators`. Valida cabeceras, identificadores y cambios previstos, pero no modifica el catálogo.

```bash
curl -sS -X POST "http://localhost:8090/api/v1/indicators/import-csv-validations" \
  -H "Authorization: Bearer <access_token>" \
  -F "file=@<path-to-indicator-register.csv>"
```

La respuesta incluye:

- `id`: identificador de validación.
- `status`: `completed` si la validación terminó.
- `result_body.valid`: `true` si el CSV supera la validación; no habilita la admisión asíncrona en DB.
- `result_body.created_rows`, `updated_rows`, `unchanged_rows`: plan contra el catálogo actual.
- `result_body.errors`: primeros errores de fila retenidos.

Si hay muchos errores, consulta la lista paginada:

```bash
curl "http://localhost:8090/api/v1/indicators/import-jobs/<validation_job_id>/errors?limit=100" \
  -H "Authorization: Bearer <access_token>"
```

### 3. Admisión asíncrona bloqueada en DB (H15 abierto)

Con `REQUIRE_DATABASE=true`, `POST /api/v1/indicators/import-csv-jobs`
(incluida su variante con barra final) devuelve HTTP `503`, `application/json`:

```json
{"detail":"Database-backed async indicator import jobs are unsupported pending H15"}
```

El bloqueo cubre CSV directo y `validation_id`, incluso cuerpos vacíos,
malformados o multipart. Ocurre antes de leer/parsing/spooling del cuerpo,
autorización, resolución de sesión/store, validación, locks, consulta o cambio
de jobs/payloads, manejo de estado de idempotencia y scheduling. No se crea
ningún job ni se modifica el catálogo; tampoco se ejecuta la terminalización
por timeout de jobs previos. Se conservan CORS, cabeceras de seguridad y request ID.

La validación síncrona sigue disponible con su política habitual: no modifica el
catálogo, pero puede persistir resultado y payload. Los GET de estado y errores
siguen disponibles para jobs existentes. El comportamiento memoria/demo y el
CLI se conservan; el modo demo no habilita importaciones de catálogo sin DB.

H15 sigue abierto: el modelo/runner aún carece de lease durable de worker,
fencing y recuperación tras reinicio. El lock de admisión y el timeout de jobs
no garantizan ejecución única ni impiden que un worker anterior terminalice un
job. Este cambio solo bloquea nuevas admisiones; no implementa recuperación,
reintentos, migraciones, limpieza ni reparación de jobs existentes.

Los límites `INDICATOR_IMPORT_MAX_BYTES` (10 MiB) e
`INDICATOR_IMPORT_MAX_ROWS` (10.000) siguen aplicándose a la validación síncrona.
Para aplicar el CSV, usa el CLI descrito a continuación.

### 4. Validar por CLI sin escribir en PostgreSQL

Activa el entorno Python de `api` y ejecuta el importador en modo `dry-run`.

Linux/macOS:

```bash
cd api
source .venv/bin/activate
cd ..

python api/scripts/import_indicators.py \
  --csv <path-to-indicator-register.csv> \
  --db-url "postgresql://<db-user>:<db-password>@<db-host>:<db-port>/<db-name>" \
  --dry-run
```

Windows PowerShell:

```powershell
.\api\.venv\Scripts\python.exe .\api\scripts\import_indicators.py `
  --csv <path-to-indicator-register.csv> `
  --db-url "postgresql://<db-user>:<db-password>@<db-host>:<db-port>/<db-name>" `
  --dry-run
```

El `dry-run` valida el CSV y muestra cuántos indicadores se importarían, sin modificar la base de datos.

### 5. Importar indicadores por CLI

Cuando la validación sea correcta, ejecuta el mismo comando sin `--dry-run`.

Linux/macOS:

```bash
python api/scripts/import_indicators.py \
  --csv <path-to-indicator-register.csv> \
  --db-url "postgresql://<db-user>:<db-password>@<db-host>:<db-port>/<db-name>"
```

Windows PowerShell:

```powershell
.\api\.venv\Scripts\python.exe .\api\scripts\import_indicators.py `
  --csv <path-to-indicator-register.csv> `
  --db-url "postgresql://<db-user>:<db-password>@<db-host>:<db-port>/<db-name>"
```

El importador hace upsert: si un indicador ya existe con el mismo `identifier`, se actualiza; si no existe, se crea. Al finalizar, SDS registra un snapshot auditable del catálogo importado.

Para imports largos o interrumpidos puedes usar:

```bash
python api/scripts/import_indicators.py \
  --csv <path-to-indicator-register.csv> \
  --db-url "postgresql://<db-user>:<db-password>@<db-host>:<db-port>/<db-name>" \
  --resume
```

### 6. Importar un paquete SDS completo

Si tu sistema genera un paquete SDS con manifest y CSV de indicadores, usa esta estructura:

```text
sds-package/
├── manifest.json
├── MANIFEST.sha256
├── sds_dataset_register.csv
├── sds_dataset_register.json      # opcional
└── sds_values.csv                 # opcional
```

El helper `scripts/import_sds_package.py` importa cualquier paquete compatible con el contrato SDS. El nombre anterior del helper se conserva solo por compatibilidad con automatizaciones existentes.

Validación:

```bash
python scripts/import_sds_package.py \
  --package-dir <path-to-sds-package> \
  --db-url "postgresql://<db-user>:<db-password>@<db-host>:<db-port>/<db-name>" \
  --dry-run
```

Si el paquete debe traer contrato de calculo, haz que el preflight falle
cerrado cuando falte:

```bash
python scripts/import_sds_package.py \
  --package-dir <path-to-sds-package> \
  --db-url "postgresql://<db-user>:<db-password>@<db-host>:<db-port>/<db-name>" \
  --dry-run \
  --require-calculation-contract
```

Import real:

```bash
python scripts/import_sds_package.py \
  --package-dir <path-to-sds-package> \
  --db-url "postgresql://<db-user>:<db-password>@<db-host>:<db-port>/<db-name>"
```

Si el paquete no contiene `sds_values.csv` ni `values.csv`, el helper importa solo indicadores y muestra un aviso indicando que los valores se han saltado.

Controles de batching en paquete completo:

- `--batch-size` ajusta solo el import de indicadores.
- `--values-batch-size` ajusta el import de valores operativos y se reenvia al
  `--batch-size` de `api/scripts/import_values_csv.py`. Si se omite, el
  importador de valores conserva su default (`250`).
- Usar batches mas pequenos reduce memoria por lote, pero aumenta los puntos
  intermedios de commit en el import de valores. La recuperacion por
  re-ejecucion sigue dependiendo de `external_key` estable.

### Recuperacion por re-ejecucion idempotente

El helper de paquete SDS es un flujo por etapas, no es un rollback transaccional
global. Valida primero la metadata de versiones, despues ejecuta el importador
de indicadores, los contratos de calculo y finalmente valores cuando el paquete
los trae. Algunas etapas escriben mediante procesos separados y pueden haber
confirmado cambios antes de que una etapa posterior falle por validacion,
conectividad o configuracion.

Por eso el contrato operativo es:

- Ejecutar siempre `--dry-run` antes del import real. El dry-run valida todo el
  registro de indicadores y prepara valores contra la misma ruta estricta
  DB-backed que usa el import real, sin escribir filas de valores. En dry-run
  de paquete completo, SDS pasa al importador de valores el registro y el
  contrato de calculo del mismo paquete como contexto solo de validacion: asi
  puede validar valores que apuntan a identificadores o conceptos del propio
  paquete aunque esos indicadores/contratos todavia no se hayan escrito en DB.
  Esa excepcion existe solo para `--dry-run`; el import real sigue validando
  contra DB despues de confirmar el registro y los contratos.
- Si el paquete declara `standard-versioning-v1`, el manifiesto debe exigir
  `tenant_activation_policy.dry_run_required=true` y
  `real_import_requires_explicit_approval=true`. El import real solo debe
  ejecutarse tras revisar el dry-run del entorno objetivo y registrar una
  aprobacion humana/proceso; SDS valida esos campos, pero no convierte una
  presencia de manifiesto en promocion automatica.
- Si falla una etapa posterior al dry-run, corregir la causa y re-ejecutar el
  mismo paquete. Las etapas convergen por claves distintas: indicadores por
  `identifier`, contratos por hash/identificador de paquete, mapeos canonicos
  por `assertion_hash` y valores operativos por `external_key`. No asumas
  idempotencia global para filas sin clave estable.
- Un dry-run correcto prueba que el paquete es coherente con la DB objetivo y
  con su propio contexto de paquete. No prueba que todos los conceptos existan
  ya en DB antes del import real.
- No interpretar la re-ejecucion como rollback. Si se ejecuto un import real con
  `--retire-prefix` o una etapa no idempotente futura, la recuperacion requiere
  revisar el snapshot/auditoria y, si aplica, restaurar desde backup o aplicar
  una correccion operativa explicita.
- El retiro por alcance cerrado nunca es implicito. Cualquier import real que
  use `--retire-indicator-prefix` debe incluir tambien
  `--approve-retirement-impact <prefix>=<count>`, donde `<count>` es el impacto
  revisado en la DB objetivo. SDS compara ese numero con el plan real de
  retiro antes de desactivar filas; si falta o no coincide, el retiro se bloquea.
  Ejemplo para una promocion GHG ya revisada donde el plan aprobado son `247`
  filas:

  ```powershell
  python scripts/import_atomizer_sds_package.py `
    --package-dir <path-to-sds-package> `
    --retire-indicator-prefix urn:sds:reg:ghg: `
    --approve-retirement-impact urn:sds:reg:ghg:=247
  ```

  El `247` anterior no es una constante del paquete; es el conteo aprobado para
  esa DB objetivo tras revisar backup, dry-run/import rehearsal y rollback.
- Mantener el mismo `manifest.json` / `MANIFEST.sha256` durante la
  recuperacion. Si cambia el contenido del paquete, tratarlo como una nueva
  entrega y volver a dry-run desde cero.

Los payloads CSV subidos para validacion API se conservan solo durante la
ventana `INDICATOR_IMPORT_VALIDATION_PAYLOAD_RETENTION_HOURS` (24 horas por
defecto). Al expirar, confirmar una validacion antigua devuelve
`Validation payload is no longer available; upload the CSV again.` Los jobs de
import real limpian su `source_payload` al terminar en `completed` o `failed`;
se conservan hashes, nombre, tamano, resultado y errores para diagnostico.

### Wrapper de registro SDS

Para paquetes register-first que solo necesitan pasar `sds_dataset_register.csv`
por el importador canónico de indicadores, usa:

```bash
python scripts/import_atomizer_register_package.py \
  --package-dir <path-to-sds-package> \
  --dry-run
```

También puedes apuntar directamente al CSV:

```bash
python scripts/import_atomizer_register_package.py \
  --csv <path-to-sds_dataset_register.csv> \
  --dry-run
```

El wrapper resuelve el intérprete en este orden: `api/.venv/Scripts/python.exe`
en Windows, `api/.venv/bin/python` en Linux/macOS y, si no existe ninguno, el
Python que ejecutó el wrapper. Esto mantiene el mismo comando documentado en
workstations Windows y runners Linux.

### Contrato opcional de conversiones

Los paquetes SDS pueden declarar metadatos de conversion para que el runtime bloquee resultados calculados cuando falte una conversion explicita. Estos campos son opcionales, pero cuando aparecen forman parte del contrato fail-closed del paquete.

Campos soportados en `sds_calculation_contract.json`:

- Nodo de calculo: `result_currency`, `conversion_policy`.
- `formula.component_refs[]`: `currency`, `expected_currency`, `fx_policy_id`, `conversion_policy`.

Campos soportados en `sds_values.csv`:

```text
currency,value_date,period_start,period_end,expected_unit,expected_currency,fx_policy_id
```

Reglas de validacion:

- `currency` se normaliza y debe quedar como codigo ISO 4217 de 3 letras en mayusculas, por ejemplo `EUR`, `GBP` o `USD`.
- `expected_currency` y `fx_policy_id` deben aparecer juntos. Una moneda esperada sin politica FX, o una politica FX sin moneda esperada, bloquea la carga.
- `conversion_policy` acepta actualmente solo `fail_closed`.
- Los valores que requieren FX deben declarar `value_date` o el par `period_start` + `period_end`, para seleccionar una ventana de tipo de cambio reproducible.
- No existe fallback a "ultimo tipo disponible" o latest-rate. Si falta la fecha o la politica FX, SDS debe rechazar o bloquear el calculo en lugar de inventar una conversion.

### Validacion de contratos de calculo ejecutables

Antes de confirmar un `sds_calculation_contract.json`, SDS valida las formulas
ejecutables con el mismo parser seguro que usa el runtime. `runtime_expression`
debe ser una expresion aritmetica segura, sus constantes y resultado deben ser
numericos y finitos, y funciones no autorizadas como imports, llamadas a
metodos o colecciones literales quedan bloqueadas. `weighted_average(value,
weight)` es una forma reconocida de contrato y se ejecuta por la ruta
especializada de agregacion ponderada.

Si un `component_ref` declara `local_variable` o `runtime_variable`, SDS exige
que las variables de la formula queden enlazadas a los componentes declarados.
Los componentes sin nombre explicito siguen el orden de variables de la formula,
igual que el resolvedor runtime. Las politicas de agregacion canonicas tambien
son fail-closed: un token desconocido como `median` bloquea el contrato en vez
de degradar silenciosamente a `sum`.

Las expresiones derivadas o intermedias declaradas dentro de la formula se
validan antes de confirmar el paquete: cada nodo derivado debe declarar una
variable local valida, una expresion aritmetica segura, y dependencias enlazadas
a componentes o a nodos derivados previos. Si declara una lista explicita
`dependencies`, cada dependencia tambien debe quedar enlazada antes de persistir
el contrato.

Ejemplo de paquete con conversion:

```text
conversion-contract-v1/
├── sds_dataset_register.csv
├── sds_calculation_contract.json
└── sds_values.csv
```

El caso minimo esperado es un valor de ingresos en millones de `GBP`, un valor de emisiones en `tCO2e`, y un resultado de intensidad con unidad semantica `tCO2e/EURm`. El componente de ingresos declara `currency=GBP`, `expected_currency=EUR` y `fx_policy_id=ecb-reference-monthly-average`; no usa `expected_unit` para cambiar divisa. El contrato usa `conversion_policy=fail_closed` y el valor aporta la ventana temporal reproducible para FX.

Este fixture prueba el contrato de conversion y requiere que la DB objetivo
tenga la jerarquia de entidades y la configuracion FX necesarias antes de
validar sus valores operativos. Si solo quieres validar la estructura de
registro + contrato en una DB que no tiene esas entidades, ejecuta el paquete
con `--skip-values`.

El fixture `calculation-contract-regression-v1` se usa para pruebas de motor de
calculo y contiene periodos mensuales abreviados como `2024-01` en
`sds_values.csv`. No es el smoke universal de valores de paquete estricto:
para un preflight completo de valores contra `ValueCreate`, usa un fixture con
fechas ISO completas o valida ese paquete con `--skip-values` cuando quieras
probar solo registro + contrato.

### 7. Verificar la importación por API

Haz login y copia el `access_token`:

```bash
curl -X POST "http://localhost:8090/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"<admin-user>","password":"<admin-password>"}'
```

Consulta los indicadores:

```bash
curl "http://localhost:8090/api/v1/indicators?limit=10" \
  -H "Authorization: Bearer <access_token>"
```

Buscar por dimensión o código:

```bash
curl "http://localhost:8090/api/v1/indicators/search?dimension=E&limit=10" \
  -H "Authorization: Bearer <access_token>"
```

Exportar el catálogo:

```bash
curl "http://localhost:8090/api/v1/indicators/export?format=csv&limit=1000" \
  -H "Authorization: Bearer <access_token>" \
  -o indicators.csv
```

## Importar valores operativos

No uses el CSV de indicadores en `/api/v1/values/import-csv`. Ese endpoint es solo para valores operativos.

Cabeceras obligatorias:

```text
concept,entity,period,value,unit
```

Cabeceras opcionales:

```text
external_key,value_type,metadata_json,currency,value_date,period_start,period_end,expected_unit,expected_currency,fx_policy_id
```

Ejemplo:

```csv
concept,entity,period,external_key,value,unit,value_type,metadata_json
urn:sds:reg:custom:energy_001,company_001,2024-12-31,erp:company-001:energy:2024,12500,kWh,numeric,"{""source"":""erp_export""}"
urn:sds:reg:custom:policy_001,company_001,2024-12-31,erp:company-001:policy:2024,true,Boolean,boolean,"{""source"":""governance_system""}"
urn:sds:reg:custom:narrative_001,company_001,2024-12-31,erp:company-001:narrative:2024,"Board approved the transition plan",Text,narrative,"{""source"":""annual_report""}"
urn:sds:reg:custom:mixed_001,company_001,2024-12-31,erp:company-001:mixed:2024,"See table 2 and 2024 baseline",Text,semi-narrative,"{""source"":""annual_report""}"
```

Reglas importantes:

- `period` en valores es una fecha exacta `YYYY-MM-DD`. Para un dato anual usa, por ejemplo, `2024-12-31`.
- `concept` puede ser un CURIE/concepto resoluble por el runtime SDS o un `identifier` del catálogo de indicadores ya importado, por ejemplo `urn:sds:reg:custom:energy_001`.
- Si usas identificadores del catálogo en import API/CLI de valores, importa primero el registro de indicadores; SDS validará que el `identifier` existe antes de aceptar el valor. El helper de paquete completo es la excepcion controlada: en `--dry-run` puede validar valores contra el registro del mismo paquete sin escribirlo.
- Un indicador puede tener varias filas de valores para el mismo periodo. Es lo normal cuando la empresa reporta consolidado, país, centro, alcance, categoría, género, edad, fuente de energía, corriente de residuo u otro desglose.
- Mantén esos desgloses en `metadata_json` con campos como `observation_scope`, `breakdown_axis` y `breakdown_value`; no añadas columnas fuera del contrato enumerado si vas a importar por `/api/v1/values/import-csv`.
- Si una fila necesita conversion FX, usa `currency`, `expected_currency`, `fx_policy_id` y una fecha reproducible (`value_date`) o una ventana (`period_start` + `period_end`). `expected_currency` y `fx_policy_id` son inseparables.
- No uses `expected_unit` para representar una divisa destino. `expected_unit` es para normalizacion fisica/de escala cuando el motor de unidades soporte esa conversion; la divisa destino siempre va en `expected_currency`.
- `value` acepta todos los tipos de dato del contrato SDS: `numeric`, `boolean`, `narrative` y `semi-narrative`. Usa `value_type` si quieres evitar inferencia.
- Las conversiones de unidad solo se aplican a valores numéricos asociados a conceptos runtime con unidad estándar en la ontología. Los valores contra identificadores de catálogo se almacenan con la unidad enviada. Booleanos y narrativos se almacenan tal como llegan, con su explicación o fuente en `metadata_json`.
- Durante la transición de aislamiento multiempresa, una colisión con una
  `external_key` global existente se rechaza de forma atómica y genérica; no se
  reutiliza para actualizar ni revelar una fila previa. El upsert por clave
  dentro de tenant se habilitará solo tras la migración tenant-bound y la
  validación de históricos.
- `metadata_json` debe ser un objeto JSON.
- Los endpoints CSV admiten como máximo `VALUE_IMPORT_MAX_BYTES` bytes (10 MiB
  por defecto) y `VALUE_IMPORT_MAX_ROWS` filas de datos (1.000 por defecto).
  El exceso de tamaño devuelve `413`; el exceso de filas devuelve un error de
  contrato `400`. Ambos controles se aplican antes de idempotencia, creación de
  jobs o escritura.
- El importador CLI (`api/scripts/import_values_csv.py`) exige `--tenant-id`
  explícito para una importación real; `--dry-run` no escribe. Procesa lotes
  configurables con `--batch-size`, mantiene `strict=True` y usa el store
  auditado normal. En paquete completo, configura este valor con
  `--values-batch-size`; `--batch-size` queda reservado para indicadores. Si
  un batch falla, el error incluye la fila CSV y el `external_key` cuando
  existe.
- Las opciones CLI `--known-register-csv` y
  `--known-calculation-contract-json` son internas del preflight de paquete
  completo y solo son validas con `--dry-run`. No las uses para import real ni
  para suavizar validacion estricta.

Ejemplo de varias observaciones para el mismo indicador:

```csv
concept,entity,period,external_key,value,unit,value_type,metadata_json
urn:sds:reg:custom:water_001,nh_group,2024-12-31,erp:water-001:group:2024,18500,m³,numeric,"{""observation_scope"":""consolidated_group""}"
urn:sds:reg:custom:water_001,nh_country_es,2024-12-31,erp:water-001:spain:2024,13690,m³,numeric,"{""observation_scope"":""country_operations"",""breakdown_axis"":""country"",""breakdown_value"":""spain""}"
urn:sds:reg:custom:water_001,nh_es_valencia_plant,2024-12-31,erp:water-001:valencia:2024,7955,m³,numeric,"{""observation_scope"":""facility_operations"",""breakdown_axis"":""facility"",""breakdown_value"":""valencia_plant""}"
```

CSV síncrono:

```bash
curl -X POST "http://localhost:8090/api/v1/values/import-csv" \
  -H "Authorization: Bearer <access_token>" \
  -H "Idempotency-Key: values-csv-<unique-key>" \
  -F "file=@<path-to-values.csv>"
```

CSV asíncrono (solo memoria/demo, `REQUIRE_DATABASE=false`):

```bash
curl -X POST "http://localhost:8090/api/v1/values/import-csv-jobs" \
  -H "Authorization: Bearer ***" \
  -H "Idempotency-Key: values-csv-job-<unique-key>" \
  -F "file=@<path-to-values.csv>"
```

Los envíos asíncronos de valores JSON y CSV no están soportados en modo DB
pendiente de H15. Con `REQUIRE_DATABASE=true`, la admisión devuelve `503` con
`Database-backed async value import jobs are unsupported pending H15` antes
de leer/parsear el body, almacenar temporalmente uploads, autorizar, resolver
stores, reclamar idempotencia, crear jobs, procesar el CSV o programar tareas.
No existe todavía una vía durable de recuperación tras reinicio. Use la
importación síncrona o el CLI con `--tenant-id` para flujos operativos admitidos;
no reintente un job DB pendiente suponiendo que conserva sus filas originales.
H15 sigue abierto. El arranque con `REQUIRE_DATABASE=true` terminaliza los jobs
de valores `pending`/`running` como `failed`, conservando el resultado, los
conteos y los metadatos; los efectos históricos de ejecución son indeterminados.
No reejecuta imports. Requiere parar todas las instancias/workers antiguos y
garantizar un único escritor: ver el [procedimiento de mantenimiento H15](deployment.md#h15-controlled-value-job-reconciliation-maintenance-required).

En memoria/demo, la respuesta `202` incluye `id`. Consulta el estado con:

```bash
curl "http://localhost:8090/api/v1/values/import-jobs/<job_id>" \
  -H "Authorization: Bearer <access_token>"
```

## Flujo recomendado

1. Validar el catálogo de indicadores por API o CLI e importarlo por CLI.
2. Verificar `/api/v1/indicators`.
3. Preparar CSV de valores.
4. Importar valores por `/api/v1/values/import-csv` (jobs asíncronos solo en memoria/demo).
5. Verificar `/api/v1/values` y ejecutar cálculos sobre el periodo correspondiente.
6. Para validar rendimiento operativo local, aprovisionar primero una instancia PostgreSQL 15 aislada, recién creada y expresamente desechable; configurar `DATABASE_URL` y `SDS_VALUE_IMPORT_DISPOSABLE_DATABASE_URL` con su mismo URL, `SDS_VALUE_IMPORT_DISPOSABLE_ALLOW_WRITE=true`, `VALUE_REVISION_PRIMARY_READ_PATH=revision` y las claves de aplicación externas. Ejecutar `make -C api gate-value-import-performance`; el gate exige la base `sds_value_import_disposable_<random>` en loopback, carga 1.000 valores, verifica valores/contextos/revisiones/eventos/punteros y deja el informe como salida local ignorada, no como evidencia pública. Descartar la instancia/volumen de prueba después, incluso si el gate falla. La coincidencia de URL no prueba por sí sola que la base sea desechable.
7. Para diagnosticar una carga en ese mismo tipo de instancia recién creada y desechable, ejecutar `python scripts/profile_value_import.py --tenant-id value_import_gate --rows 1000 --batch-size 250 --report <path-local.json>` desde `api/`; el perfil registra tiempo, queries por categoría, refresh explícitos, queries de secuencia, conversiones y memoria pico. No borra filas con historial inmutable: descartar la instancia/volumen completos.

## Importar paquete canónico de mapeos Sygris

El flujo canónico de mapeos Sygris separa dos pasos con efectos distintos:
la importación del paquete carga assertions en tablas canónicas y no cambia por
sí sola `/api/v1/mappings`; la materialización pairwise escribe el read-model
canónico `materialized_pairwise_mappings`, que es la fuente actual de
`/api/v1/mappings`.

La carga shadow escribe solo en:

- `standard_releases`
- `standard_datapoints`
- `canonical_concepts` para pivotes Sygris que aún no existan
- `mapping_assertion_groups`
- `mapping_assertion_components`

No escribe en `standard_mappings`. La materialización pairwise se ejecuta como
paso separado y, si no es `--dry-run`, actualiza el read-model público
canónico servido por `/api/v1/mappings`. Las referencias legacy a
`standard_mappings` en esta guía son para comparación, clasificación o cutover
histórico; no son un fallback runtime para `/api/v1/mappings`.

Estructura esperada:

```text
canonical-mapping-package/
├── manifest.json
├── MANIFEST.sha256
├── sds_standard_releases.csv
├── sds_standard_datapoints.csv
├── sds_mapping_assertion_groups.csv
└── sds_mapping_assertion_components.csv
```

Validación:

```powershell
.\api\.venv\Scripts\python.exe .\api\scripts\validate_canonical_mapping_package.py `
  --package-dir <path-to-canonical-mapping-package>
```

Salida JSON:

```powershell
.\api\.venv\Scripts\python.exe .\api\scripts\validate_canonical_mapping_package.py `
  --package-dir <path-to-canonical-mapping-package> `
  --json
```

La validación comprueba `package_schema_version`, presencia de los cuatro CSVs, rutas de manifest que no escapen del paquete, `MANIFEST.sha256`, checksums, cabeceras mínimas, duplicados, referencias entre releases/datapoints/assertions/components, fechas ISO, confianza `0..1`, `sygris_canonical_uri` bajo el prefijo `syg:` o la IRI `https://sustainabilitydataspace.com/sygris#`, y revisiones Sygris enteras positivas. SDS rechaza paquetes con `package_schema_version` superior al soportado por el runtime.

Dry-run de importación DB shadow:

```powershell
.\api\.venv\Scripts\python.exe .\api\scripts\import_canonical_mapping_package.py `
  --package-dir <path-to-canonical-mapping-package> `
  --db-url "postgresql://<db-user>:<db-password>@<db-host>:<db-port>/<db-name>" `
  --dry-run `
  --json
```

Import real en tablas shadow:

```powershell
.\api\.venv\Scripts\python.exe .\api\scripts\import_canonical_mapping_package.py `
  --package-dir <path-to-canonical-mapping-package> `
  --db-url "postgresql://<db-user>:<db-password>@<db-host>:<db-port>/<db-name>" `
  --json
```

Los imports DB-backed se serializan con un advisory lock PostgreSQL compartido
por el endpoint interno de importación y el CLI de importación directa. Ese lock
evita imports canónicos concurrentes sobre la misma base de datos; en runtimes no
PostgreSQL o modos in-memory, la serialización fuerte no aplica y esos modos no
deben usarse como superficie productiva concurrente. Si un proceso termina de
forma abrupta y deja un job de importación canónica en `pending` o `running`, el
reintento debe esperar a una revisión operativa y a marcar ese job como `failed`
mediante mantenimiento controlado; SDS no auto-expira imports activos para evitar
abrir una segunda carga mientras la primera siga escribiendo.

El importador es idempotente por release, datapoint, concepto Sygris y `assertion_hash` cuando el paquete lo proporciona. Si el paquete referencia un `sygris_canonical_uri` válido que todavía no existe en `canonical_concepts`, SDS crea un concepto Sygris mínimo de tipo `mapping_pivot` para que la huella canónica pueda cargarse sin bloquear el shadow import.

Preview/dry-run de materialización pairwise:

```powershell
.\api\.venv\Scripts\python.exe .\api\scripts\materialize_canonical_pairwise_mappings.py `
  --db-url "postgresql://<db-user>:<db-password>@<db-host>:<db-port>/<db-name>" `
  --dry-run `
  --json
```

Materialización real en el read-model canónico público:

```powershell
.\api\.venv\Scripts\python.exe .\api\scripts\materialize_canonical_pairwise_mappings.py `
  --db-url "postgresql://<db-user>:<db-password>@<db-host>:<db-port>/<db-name>" `
  --json
```

El materializador lee `mapping_assertion_groups` y `mapping_assertion_components`, calcula solapamiento entre huellas Sygris y escribe filas actuales en `materialized_pairwise_mappings`. Genera direcciones `A -> B` y `B -> A` entre estándares distintos, marca como stale las filas current que ya no salen de la última huella dentro del alcance materializado y conserva `generated_from_hash` para reproducibilidad. Por defecto incluye assertions `draft` y `approved`; para un ensayo operativo más estricto usa `--approval-status approved`.

Cuando dos assertions tienen la misma huella Sygris, el materializador solo emite `equivalent` si ambas assertions son `equivalent` con cobertura `complete`. Si una assertion declara una relación direccional explícita (`broader` o `narrower`) frente al concepto Sygris y la otra es equivalente, SDS preserva esa dirección en `materialized_pairwise_mappings` y emite la relación inversa en la dirección contraria. Cualquier otro caso no completo se materializa como `partial` para evitar convertir componentes, agregados o scopes incompletos en equivalencias.

### Paquetes de relaciones no exactas

Los paquetes externos que declaran relaciones no equivalentes como
`partial_overlap` o `component_of` no se deben pasar por el flujo de import DB
shadow ni por la materializacion pairwise persistida solo porque sean `draft`,
`internal` o validen como `shadow_report_only`. En esos paquetes,
`shadow_report_only` significa inspeccion/validacion unicamente.

Regla para paquetes tipo GHG/ESRS/GRI v45:

- no ejecutar import DB real ni materializacion real en
  `materialized_pairwise_mappings`;
- permitir solo validacion CLI, dry-run o base scratch explicitamente marcada
  como no operativa;
- conservar `relationship_type`, `coverage_status`, `confidence`,
  `match_scope`, `mismatch_scope`, `transformation_rule`, `rationale`,
  provenance de paquete/hash y estado de aprobacion/publicacion;
- conservar target explicito (`target_standard_id`, version, codigo,
  datapoint id y direccion); `mapping_profile` es solo perfil/grupo, no el
  locator semantico principal;
- renderizar `partial_overlap` y `component_of` como badges de revision, no
  como equivalencias, aunque un componente tenga `component_role=primary` o
  `coverage_fraction=1.0000`;
- tratar tipos de relacion desconocidos como vocabulario no aceptado: fallan
  gates operativos y solo pueden quedar en revision shadow;
- mantener snapshot/provenance separados de paquetes exactos o baseline, para
  no mezclar filas compartidas por codigo.

Estos paquetes no alimentan `/api/v1/mappings`, autofill, compliance scoring,
legacy overrides, snapshots publicos ni cutover hasta que exista una aprobacion
explicita de contrato operativo no-exacto o las assertions se sustituyan por
`equivalent` + `complete` revisado.

Guardrail tecnico actual:

- `api/src/services/canonical_mapping_relationship_policy.py` define una
  allowlist operacional cerrada: `equivalent`, `partial`, `broader`,
  `narrower`.
- Cualquier `relationship_type` fuera de esa allowlist, incluidos
  `partial_overlap`, `component_of`, `transforms_to` y valores desconocidos,
  marca el paquete o la materializacion como no operacional.
- `validate_canonical_mapping_package.py --json` expone
  `operational_eligible`, `relationship_type_counts`,
  `non_operational_relationship_type_counts` y `operational_blockers`; esta es
  la superficie segura de inspeccion/mostrar antes de cualquier DB write.
- `valid=true` no implica elegibilidad operacional. Los consumidores de CI,
  importacion o workflows deben tratar `operational_eligible=false` como bloqueo
  operativo aunque el paquete sea estructuralmente valido para inspeccion.
- `import_canonical_mapping_package.py` bloquea commits por defecto antes de
  escribir en DB si detecta relaciones no operacionales. `--dry-run` sigue
  permitido para inspeccion; `--allow-non-operational-relationships` queda
  reservado a bases scratch/no operativas.
- `materialize_canonical_pairwise_mappings.py` bloquea por defecto si las
  assertion groups activas contienen relaciones no operacionales, incluso con
  `--skip-import` en el workflow.
- `run_canonical_mapping_shadow_workflow.py` termina con
  `blocked_non_operational_package` o
  `blocked_non_operational_materialization` antes de parity cuando corresponde.
- `import_standard_mappings.py` rechaza relationship overrides legacy con
  relaciones no operacionales, de modo que `partial_overlap` y `component_of`
  tampoco pueden entrar manualmente como `standard_mappings`.
- `inspect_canonical_mapping_package.py` proporciona la superficie read-only de
  inspeccion: lista assertion candidates no operacionales, componentes Sygris,
  rationale, match/mismatch, transformaciones, hash/provenance y
  `target_identity_status`.

Identidad de target:

- `target_identity_status=not_declared_in_package` cuando solo existe
  `mapping_profile=default`;
- `target_identity_status=profile_only` cuando el paquete trae un perfil
  target-scoped, pero no campos target oficiales;
- `target_identity_status=declared_in_package` solo cuando la assertion row trae
  `target_standard_id`, `target_standard_version` y `target_code`, y ese target
  resuelve contra una fila de `sds_standard_datapoints.csv`;
- `target_identity_status=declared_missing_target` cuando la assertion row trae
  campos target, pero SDS no puede resolverlos dentro del paquete.

Los paquetes v44/v45 no declaran campos target explicitos en las assertion
rows. SDS por tanto no debe tratar `mapping_profile` como locator oficial. Un
paquete de revision v46
`canonical_mapping_ghg_esrs_gri_scope3_non_exact_explicit_targets_v46` aisla
solo las 5 assertions Scope 3 no exactas revisadas y declara targets explicitos;
su inspeccion read-only devuelve `declared_in_package=5`, `component_of=2` y
`partial_overlap=3`, con `operational_eligible=false`.

API interna de inspeccion:

```powershell
$env:CANONICAL_MAPPING_INSPECTION_ROOTS = "<sds-mapping-package-root>"

.\api\.venv\Scripts\python.exe -m uvicorn src.api.main:app --app-dir api --host 127.0.0.1 --port 8090
```

Solo un administrador global autenticado mediante bearer y con
`manage_mappings` puede usar esta API interna (inspección, validación,
importación, consulta de jobs y preview). Un `data_manager` de una empresa
puede gestionar otros mapeos, pero no consultar los resultados de paquetes
globales ni activar un paquete compartido. La inspección toma `package_id`
server-side:

```bash
curl -sS \
  -H "Authorization: Bearer <access_token>" \
  "http://127.0.0.1:8090/api/v1/internal/canonical-mapping-packages/canonical_mapping_ghg_esrs_gri_scope3_non_exact_explicit_targets_v46/assertion-inspection"
```

Contrato de salida:

- `api_contract.read_only=true`, `db_write=false`,
  `materialization_write=false`, `operational_mapping_surface=false`;
- `package_id`, `source_version`, `manifest_hash`, `package_schema_version`;
- `valid`, `operational_eligible`, `operational_blockers`;
- `relationship_type_counts` y
  `non_operational_relationship_type_counts`;
- `target_identity_status_counts`;
- `candidates[]` con source identity, target identity, `mapping_direction`,
  `mapping_profile`, `relationship_type`, coverage/confidence, rationale,
  difference evidence, assertion hash, provenance, estados de aprobacion/
  publicacion y componentes Sygris ordenados.

Este endpoint no acepta rutas absolutas desde la request: el cliente envia solo
`package_id`, y SDS lo resuelve bajo `CANONICAL_MAPPING_INSPECTION_ROOTS`. Es
una superficie interna de revision; no alimenta DB import, materializacion,
change feeds, exports publicos, autofill, scoring ni `/api/v1/mappings`.

API interna de validacion/import contra estandares instalados:

```bash
curl -sS -X POST \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  "http://127.0.0.1:8090/api/v1/internal/canonical-mapping-packages/<package_id>/validations" \
  -d '{
    "compatibility_mode": "partial",
    "installed_standard_releases": [
      {"standard_id": "ESRS", "version": "2024"}
    ]
  }'
```

La validacion devuelve un job `completed` con `validation` y `compatibility`.
`compatibility.status_counts` separa, como minimo, filas `installable_now`,
`pending_missing_standard` y `blocked_missing_datapoint`. Este paso no escribe
en DB y puede ser parcial para que un SDS con `ESRS + Sygris` pero sin `GRI`
vea que las assertions ESRS son activables y las GRI quedan pendientes.

Import interno:

```bash
curl -sS -X POST \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  "http://127.0.0.1:8090/api/v1/internal/canonical-mapping-packages/<package_id>/import-jobs" \
  -d '{
    "compatibility_mode": "partial",
    "installed_standard_releases": [
      {"standard_id": "ESRS", "version": "2024"}
    ]
  }'
```

En modo `strict`, cualquier estandar/release/datapoint ausente bloquea el
commit. En modo `partial`, SDS importa solo el slice instalado y deja las filas
ausentes fuera de las tablas canonicas activas como
`pending_missing_standard` en el resultado del job. Un paquete de mapeos nunca
instala automaticamente un estandar ni activa filas de un estandar ausente.

Preview de materializacion:

```bash
curl -sS -X POST \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  "http://127.0.0.1:8090/api/v1/internal/canonical-mapping-packages/materialization-previews" \
  -d '{
    "installed_standard_releases": [
      {"standard_id": "ESRS", "version": "2024"}
    ],
    "mapping_profile": "default",
    "approval_statuses": ["draft", "approved"]
  }'
```

El preview ejecuta `materialize_pairwise_mappings(..., dry_run=True)` filtrado
por estandares instalados. Si `GRI` no esta instalado, no se emiten rows
`ESRS <-> GRI` aunque existan assertions GRI en tablas shadow.

La materializacion de un subconjunto operacional solo es aceptable cuando hay
una decision revisada de promocionar el slice instalado. No convierte filas
pendientes en equivalencias, no salta la validacion de relaciones no
operacionales y no sustituye el dry-run/preview previo al commit real.

Comparación de paridad antes de cutover:

```powershell
.\api\.venv\Scripts\python.exe .\api\scripts\compare_canonical_mapping_parity.py `
  --source-standard ESRS `
  --target-standard GRI `
  --report .\data\extracted\analysis\canonical-mapping-parity-esrs-gri.json `
  --worklist-csv .\data\extracted\analysis\canonical-mapping-parity-esrs-gri-worklist.csv
```

El gate compara `standard_mappings` activo contra `materialized_pairwise_mappings` current usando la clave pública `(source_standard, source_code, target_standard, target_code)`. Esta comparación es una herramienta de paridad/cutover contra el legado; no restaura `standard_mappings` como fuente runtime ni como fallback de `/api/v1/mappings`. Falla si hay claves legacy ausentes en la derivación canónica, claves canónicas extra, duplicados, cambios de `relationship_type`, diferencias de confianza superiores a la tolerancia, o diferencias conservadoras de formato de código que SDS pueda emparejar como `code_mismatch`. La normalización de códigos se limita a datapoints GRI atómicos con ruido de formato (`c-` -> `c`, `c-ii` -> `c.ii`, espacios antes de punto); no explota compuestos con `;`, `/`, `,`, rangos `to` ni guidance. El CSV de worklist deja cada divergencia con `status=unclassified` y una `suggested_classification` inicial para clasificarla como mejora semántica esperada, gap de cobertura, defecto legacy, normalización de código legacy o bug de materialización. Para inspección exploratoria sin bloquear, añade `--allow-drift`; no uses esa opción para aprobar un cutover.

Workflow shadow de punta a punta:

```powershell
.\api\.venv\Scripts\python.exe .\api\scripts\run_canonical_mapping_shadow_workflow.py `
  --package-dir <path-to-canonical-mapping-package> `
  --db-url "postgresql://<db-user>:<db-password>@<db-host>:<db-port>/<db-name>" `
  --source-standard ESRS `
  --target-standard GRI `
  --report .\data\extracted\analysis\canonical-mapping-shadow-esrs-gri.json `
  --worklist-csv .\data\extracted\analysis\canonical-mapping-shadow-esrs-gri-worklist.csv
```

El workflow ejecuta, en orden, importación DB shadow, materialización pairwise y comparación de paridad. Si el paquete es inválido devuelve código `2`. Si la importación y la materialización funcionan pero hay diferencias de paridad devuelve código `1` y deja el JSON/CSV para revisión. `--allow-drift` solo convierte esas diferencias en salida `0` para inspección exploratoria; no aprueba el corte. Para reutilizar tablas ya preparadas puedes añadir `--skip-import` o `--skip-materialize`.

Desde `api/` también existe el target:

```bash
make canonical-mapping-shadow-workflow \
  PACKAGE_DIR=<path-to-canonical-mapping-package> \
  SOURCE_STANDARD=ESRS \
  TARGET_STANDARD=GRI \
  REPORT=../data/extracted/analysis/canonical-mapping-shadow-esrs-gri.json \
  WORKLIST_CSV=../data/extracted/analysis/canonical-mapping-shadow-esrs-gri-worklist.csv
```

## Errores comunes

- `Missing required columns`: el CSV no cumple el contrato de indicadores.
- `invalid URN`: el `identifier` no es estable o no puede normalizarse.
- `value too long`: actualiza SDS a una versión reciente y ejecuta migraciones; algunos estándares usan unidades largas.
- `401` en API: usa el `access_token` en el candado o header Bearer, no el `refresh_token`.
- `invalid_sygris_canonical_uri`: un componente de mapeo no apunta a un concepto Sygris (`syg:` o IRI Sygris).
