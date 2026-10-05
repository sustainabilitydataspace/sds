# Changelog

## 2026-10-05 — Guía documental

- Nueva guía de organización impersonal de anexos en
  `docs/deliverable-annex-organization.md`: asociaciones entre actividades y
  entregables, conservación de fuentes y Word manual, índice con verificación
  de integridad y vínculos relativos de evidencia.
- Distingue registros históricos de pruebas, ejecuciones operativas,
  comunicación enviada, recepción, propagación cloud y decisiones de
  aceptación. La guía no publica ni importa anexos controlados ni acredita
  recepción o nuevas decisiones administrativas.

## 2026-10-05

- Paquete de demostración A2.3 versionado en `api/demo/a23` (`sds-demo-a23-v1`)
  con lo necesario para la verificación V1–V5 de la instancia pública:
  22 indicadores ESRS E1-5 (textos de EFRAG con atribución), el contrato de
  cálculo del modelo E1-5, la jerarquía `nh_group`, los 9 valores sintéticos
  NordHaven 2024 y tres correspondencias ESRS E1-6 → GRI 305 aprobadas por el
  operador en un perfil propio.
- Endpoints de administración `GET /api/v1/admin/demo-packages/a23` (estado,
  comprobaciones V1–V5 e historial) y `POST .../install?confirm=true`:
  verifican el paquete contra un digest fijado en el código, lo instalan en una
  única transacción y, si algo difiere de lo que es de la demo, devuelven `409`
  sin escribir nada. Los datos compartidos existentes se reutilizan sin
  modificarlos.
- Migración `051`: registro `admin_demo_package_installs`.
- La materialización de correspondencias respeta perfiles: solo marca como
  obsoletas filas de su propio perfil y el perfil `default` prevalece en un par
  compartido. La importación de correspondencias admite
  `reference_data_mode="insert_only"` y, sin `commit`, trabaja en un
  `SAVEPOINT`; importación de indicadores, creación de jerarquías y
  materialización aceptan `commit=False`.

## 2026-10-04

- Las reparaciones del catálogo de unidades verifican el resultado dentro de la
  misma transacción, con el bloqueo del catálogo y antes del `COMMIT`: los
  conflictos restantes deben ser exactamente los previstos por el plan. Una
  reparación ya no se revierte porque queden otros conflictos sin relación;
  una discrepancia devuelve `409` sin persistir nada.
- Nuevas correcciones de factor (`/api/v1/admin/unit-catalog/factor-corrections`)
  para unidades heredadas como `kgCO2e` o `tCO2e` cuyo factor contradice la
  base `kg CO2e`: exigen un texto de reconocimiento exacto, una unidad base
  activa con factor 1 y la confirmación del catálogo de referencia incluido;
  desactivan la unidad errónea de forma auditada y reversible.
- Informe de impacto de solo lectura por unidad
  (`GET /api/v1/admin/unit-catalog/units/{unit_id}/impact`): recuentos de
  valores y revisiones posiblemente afectados e identificadores opacos.
- Migración `050`: diagnóstico saneado de errores `500` por `request_id`
  (tipos de excepción, ubicaciones de código, clasificación y campos
  permitidos de PostgreSQL; nunca mensajes, cuerpos, SQL ni parámetros), con
  retención de 14 días y 500 registros, legible por administradores en
  `/api/v1/admin/diagnostics/errors`. Las ramas genéricas `500` del router de
  valores registran el diagnóstico y dejan de escribir el mensaje de la
  excepción en el log.
- Las migraciones `049` y `050` restablecen `search_path` antes de crear sus
  tablas; una instalación desde cero en una sola transacción ya no intenta
  crearlas en `pg_catalog`.

## 2026-10-03

- Añadidos endpoints de administración (`/api/v1/admin`, admin con token
  bearer y `manage_system`) para validar e importar paquetes de contratos de
  cálculo en una única transacción idempotente con bloqueo por paquete, listar
  conflictos del catálogo de unidades con el mismo análisis que el conversor y
  reparar de forma previsualizada, firmada, auditada y reversible un duplicado
  como `m3` frente a `m³`.
- Migración `049`: tabla de solo inserción `admin_catalog_operations`, cuya
  revisión hace que cada proceso de la API recargue su catálogo de unidades en
  un segundo como máximo tras una reparación.
- El servicio de importación de contratos acepta los bytes del paquete y puede
  dejar la transacción al llamador; la CLI conserva su comportamiento.

## 2026-10-02

- Añadido `PUT /auth/users/{username}` para que un administrador con token
  bearer y `manage_users` cambie `email`, `full_name`, `company_id`, `role` o
  `is_active` de otro usuario existente, y `POST
  /auth/users/{username}/reset-password` para restablecer su contraseña con la
  política de la aplicación. Ambos rechazan claves API, otros roles, la
  autoedición y campos ajenos, limitan a 5 peticiones por minuto, no devuelven
  los valores enviados en errores de validación e invalidan las sesiones del
  usuario afectado. El `username` no es editable.
- La política de contraseñas del aprovisionamiento pasa a
  `src/auth/login_policy.py`; el script delega en ella sin cambiar su
  comportamiento.
- Un email ya usado por otra cuenta devuelve `409` también en `PUT /auth/me`.
- `DatabaseUserStore.update_user` ya no devuelve el registro anterior cuando la
  actualización no se persiste, y `/auth/refresh` rechaza el refresh token de un
  usuario desactivado también sin base de datos (`REQUIRE_DATABASE=false`).

## 2026-09-29

- Añadido `api/scripts/provision_application_user.py` para que Jenkins cree,
  verifique o rote explícitamente usuarios de aplicación en PostgreSQL. La
  contraseña solo entra por entrada estándar; los desajustes de perfil fallan
  sin elevar privilegios ni aplicar cambios parciales; una rotación avanza
  `auth_version`; y los despliegues ordinarios verifican sin rotar.
- Documentado el primer aprovisionamiento de `admin` y `analyst`, la custodia en
  Jenkins Credentials, la recuperación segura tras restaurar una base de datos
  y las comprobaciones funcionales del rol `analyst`, incluida la denegación de
  escritura con HTTP 403.
- La CI pública de la versión publicada aprobó compatibilidad con Python 3.11 y
  3.12, lint, seguridad y cobertura completa. La configuración de credenciales
  y la ejecución en producción continúan pendientes del responsable de Jenkins.

## 2026-09-28

- Incorporado el código completo de la API, migraciones, CLI, contratos,
  pruebas y herramientas de importación a la distribución pública, conservando
  las decisiones de cierre de entregables.
- El arranque local genera las cuatro claves necesarias para la API completa,
  y los avisos de terceros acompañan a los recursos Swagger UI y ReDoc.
- Aclarado que `make public-env` crea credenciales propias sin mostrarlas ni
  versionarlas. La comprobación local de arranque no acredita un despliegue de
  producción con paquetes externos del operador.
- Excluidos por defecto los archivos de instrucciones de agentes y las áreas
  locales de trabajo de futuras incorporaciones al repositorio público.
- Publicada la licencia `Sustainability Data Space Software Evaluation License
  v1.0` para el código SDS propio, junto con la guía de publicación, avisos de
  terceros y reporte responsable de vulnerabilidades. La licencia permite
  evaluación técnica no productiva y mantiene prohibidos el uso productivo,
  explotación comercial, SaaS/servicios gestionados, reventa y servicios a
  terceros sin acuerdo separado por escrito. No se presenta como licencia open
  source ni como cierre jurídico de la ayuda; la resolución individual sigue
  fuera del repositorio público.
- Retirada de la descripción OpenAPI una etiqueta anterior incompatible con la
  licencia de evaluación publicada.

### Current deliverable publication status (authoritative)

El estado vigente sigue siendo el cierre aprobado el 2026-09-18: E01–E13 y
R1–R23 están cerrados en `deliverables/evidence-public/dossier-closure-status-sds-v2026-09-18.md`.
Una observación técnica posterior no revoca ni sustituye esa decisión formal.

## 2026-09-18

- Publicada la versión pública inicial de SDS con perfil DB-first instalable,
  paquete sintético, gates R8/R10 de 1.000 filas y entregables E01–E13.
- Reconciliado el estado vigente de cierre del expediente: E01–E13 y R1–R23
  están cerrados por decisión de proyecto; se retiraron reservas históricas.
- Añadida una revisión actual de la superficie pública que separa incidencias de
  mantenimiento web posteriores del cierre formal del expediente.
