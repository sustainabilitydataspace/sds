# Sustainability Data Space

Sustainability Data Space (SDS) es una API FastAPI DB-first para importar,
gobernar, transformar y consultar datos ESG mediante paquetes trazables.

Este repositorio contiene el código completo de la API SDS, sus migraciones,
contratos, pruebas, scripts de importación y la superficie pública de
entregables E01–E13. Incluye también datos sintéticos para ensayar la aplicación.
Los paquetes operativos y las credenciales de cada instalación los aporta el
operador; no se incluyen datos de producción ni claves de otras instalaciones.

## Alcance de uso previsto

La descarga, instalación y ejecución de pruebas con SDS están previstas solo
para evaluación técnica en un entorno de pruebas. Estas instrucciones no
constituyen permiso para usar SDS en la actividad ordinaria propia, ponerlo en
producción, prestar servicios a terceros, venderlo ni redistribuirlo. Para
cualquiera de esos usos hace falta un acuerdo separado y por escrito con el
titular de los derechos. Las licencias
de componentes de terceros se rigen por sus propios textos; esta nota no los
restringe ni amplía. Las condiciones definitivas de evaluación y publicación
del código de SDS están pendientes de revisión de titularidad, condiciones
particulares de la ayuda y asesoramiento jurídico. No se presenta esta nota
como una licencia definitiva.

## Arranque de evaluación desde un clon limpio

Requisitos: Git y un motor Compose compatible con Docker Compose. La API
admite Python 3.10-3.12. La serialización `sds-canonical-json-v1` requiere
CPython 3.10 con Unicode `13.0.0`; con otra versión falla cerrada. Las pruebas
de serialización canónica deben ejecutarse con Python 3.10. En sistemas con
Podman se puede usar
`make up COMPOSE="podman compose"` y el mismo ajuste al apagar.

```bash
git clone https://github.com/sustainabilitydataspace/sds.git
cd sds
make public-env
make up
make smoke
```

`make public-env` genera en `api/.env` (ignorado por Git) cuatro claves propias:
base de datos, sesión JWT, firma de exportaciones y cuenta administradora
inicial. No las muestra en pantalla. Si ya existe ese archivo, el comando
se niega a reemplazarlo. Cada persona que instale SDS utiliza sus propias
credenciales; no subas `api/.env` al repositorio ni reutilices esas claves
en otra instalación. La API escucha solo en la máquina local por defecto.
Tras el arranque, `/healthz` es público y `/ready` requiere autenticación.
Consulta `api/README.md` para la instalación sin contenedores, las migraciones
y el paquete sintético de ejemplo.

Para detener los contenedores sin borrar la base de datos local:

```bash
make down
```

## Verificación de código y evidencia

```bash
make api-ci-local
make deliverables-check
make subsidy-closure-check
```

`make deliverables-check` valida registro, rutas canónicas y comprobaciones
de privacidad de los entregables. `make subsidy-closure-check` comprueba el
estado formal fechado el 2026-09-18 sin confundirlo con observaciones posteriores.

## Runtime Interoperability

La API incluye resoluciones entre estándares, cálculo y conversiones
respaldadas por datos en PostgreSQL. Las decisiones se cierran cuando faltan
paquetes o evidencia; importar un paquete no concede por sí solo derechos sobre
su contenido. Consulta `api/docs/interoperability-runtime.md`.

## Paquetes de operador

El arranque anterior ejecuta el producto completo con una base de datos nueva
para evaluación técnica; no autoriza el uso productivo ni acredita por sí solo
una instalación de producción.
Los paquetes
operativos o de estándares que un operador quiera procesar se importan por los
contratos SDS y deben contar con sus derechos de uso aplicables. Los códigos
oficiales se preservan literalmente cuando un paquete autorizado los aporta;
esta distribución no reclama certificación, aprobación o licencia de GRI ni de
ninguna otra entidad por sí misma.

Consulte `docs/public-profile.md`, `api/docs/import-packages.md` y
`docs/quality/acceptance_gates.md`.