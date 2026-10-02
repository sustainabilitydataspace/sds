# Changelog

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
