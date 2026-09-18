# Sustainability Data Space

Sustainability Data Space (SDS) es una API FastAPI DB-first para importar,
gobernar, transformar y consultar datos ESG mediante paquetes trazables.

Este repositorio es una distribución pública limpia: conserva el código, la
superficie de entregables E01–E13, la trazabilidad de subvención y un perfil de
demostración sintético. No contiene secretos, datos operativos, historiales
privados ni catálogos textuales de estándares de terceros.

## Arranque reproducible desde un clon limpio

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
se versionan. El perfil incluido carga tres métricas SDS propias y sintéticas:
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
higiene de la superficie de entregables. `make subsidy-closure-check` no
certifica un cierre completo de subvención: mantiene explícitos los residuales
R3/E02, R20/E10 y R21/E11 descritos en la matriz de trazabilidad.

## Paquetes de operador

El ejemplo instalado es suficiente para una demostración técnica. Los paquetes
operativos o de estándares que un operador quiera procesar se importan por los
contratos SDS y deben contar con sus derechos de uso aplicables. Los códigos
oficiales se preservan literalmente cuando un paquete autorizado los aporta;
esta distribución no reclama certificación, aprobación o licencia de GRI ni de
ninguna otra entidad por sí misma.

Consulte `docs/public-profile.md`, `api/docs/import-packages.md` y
`docs/quality/acceptance_gates.md`.