# Sustainability Data Space

Sustainability Data Space (SDS) es una API FastAPI DB-first para importar,
gobernar, transformar y consultar datos ESG mediante paquetes trazables.

Este repositorio es una distribución pública limpia: conserva el código, la
superficie de entregables E01–E13, la trazabilidad de subvención y un perfil de
demostración sintético. No contiene secretos, datos operativos, historiales
privados ni catálogos textuales de estándares de terceros.

## Alcance de uso previsto

La descarga, instalación y ejecución de pruebas con SDS están previstas solo
para evaluación técnica en un entorno de pruebas. Estas instrucciones no
constituyen permiso para usar SDS en la actividad ordinaria propia, ponerlo en
producción, prestar servicios a terceros, venderlo ni redistribuirlo. Para
cualquiera de esos usos hace falta un acuerdo separado y por escrito con el
titular de los derechos. Las licencias de componentes de terceros se rigen por
sus propios textos. Las condiciones de GitHub permiten ver y bifurcar el
repositorio dentro de la plataforma; esto no concede explotación productiva.
Las condiciones definitivas de evaluación y publicación
del código SDS están pendientes de revisión de titularidad, condiciones
particulares de la ayuda y asesoramiento jurídico. Esta nota no es una licencia
definitiva.

## Arranque de evaluación desde un clon limpio

Requisitos: Git, un motor Compose compatible con Docker Compose y CPython 3.10
para los tests/gates de host. En sistemas con Podman se puede sustituir el
motor mediante `COMPOSE="podman compose"`.

```bash
git clone https://github.com/sustainabilitydataspace/sds.git
cd sds
make public-env
make up
make smoke
make gate-e4-r8
make gate-e4-r10
```

`make public-env` genera `api/.env` con secretos locales que no se muestran ni
se versionan. No hacen falta credenciales del equipo que desarrolló SDS:
cada instalación utiliza las suyas. No subas `api/.env` al repositorio ni
reutilices sus claves en otro entorno. El perfil incluido carga tres métricas
SDS propias y sintéticas:
volumen de agua, uso de energía y masa de emisiones. Los gates generan y purgan
1.000 filas por ejecución. R8 exige transformaciones correctas para `L → m3`,
`kWh → MWh` y `kgCO2e → tCO2e`; R10 exige importación, cinco superficies de
persistencia, limpieza completa y un tiempo estricto menor que 30 segundos.
Los informes se escriben bajo `artifacts/` y no sustituyen la evidencia
histórica versionada.

Para apagar y eliminar el perfil local:

```bash
make down
```

## Verificación de código y evidencia

```bash
make test-public
make deliverables-check
make subsidy-closure-check
```

`make deliverables-check` valida registro, rutas canónicas, SHA-256, índices y
higiene de la superficie de entregables. `make subsidy-closure-check` verifica
el cierre vigente de E01–E13 y R1–R23 definido en la superficie de estado
fechada el 2026-09-18.

## Paquetes de operador

El arranque anterior verifica el perfil público de demostración; no autoriza
el uso productivo ni acredita por sí solo una instalación de producción con
todos los paquetes operativos.
El ejemplo instalado es suficiente para una demostración técnica. Los paquetes
operativos o de estándares que un operador quiera procesar se importan por los
contratos SDS y deben contar con sus derechos de uso aplicables. Los códigos
oficiales se preservan literalmente cuando un paquete autorizado los aporta;
esta distribución no reclama certificación, aprobación o licencia de GRI ni de
ninguna otra entidad por sí misma.

Consulte `docs/public-profile.md`, `api/docs/import-packages.md` y
`docs/quality/acceptance_gates.md`.