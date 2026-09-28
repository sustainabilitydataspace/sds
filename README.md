# Sustainability Data Space

Sustainability Data Space (SDS) es una API FastAPI DB-first para importar,
gobernar, transformar y consultar datos ESG mediante paquetes trazables.

Este repositorio contiene el código completo de la API SDS, sus migraciones,
contratos, pruebas, scripts de importación y la superficie pública de
entregables E01–E13. Incluye también datos sintéticos para ensayar la aplicación.
Los paquetes operativos y las credenciales de cada instalación los aporta el
operador; no se incluyen datos de producción ni claves de otras instalaciones.

## Alcance de uso previsto

SDS se publica como código fuente disponible para evaluación. No es software de
código abierto en sentido OSI/FSF porque la licencia restringe el uso
productivo y comercial.

La licencia aplicable al código SDS propio es `LICENSE`. En resumen, permite
ver, descargar, bifurcar en GitHub, instalar y ejecutar SDS en entornos no
productivos para evaluación técnica, revisión de seguridad, auditoría, pruebas y
demostraciones. No permite usar SDS en la actividad ordinaria propia, ponerlo en
producción, prestar servicios a terceros, ofrecerlo como SaaS o servicio
gestionado, venderlo, relicenciarlo ni redistribuirlo fuera de los forks de
evaluación permitidos en GitHub sin acuerdo separado y por escrito con el
titular de los derechos.

Las instrucciones de instalación de este repositorio son instrucciones de
evaluación. Que el código arranque localmente no concede derechos de
explotación productiva. Las licencias de componentes de terceros, los derechos
sobre paquetes de estándares, los datos de operador, los productos de datos y
los entregables formales conservan sus términos propios y no quedan ampliados
por la licencia SDS. Consulte `docs/third-party-notices.md` para los avisos de
terceros y `docs/license-and-publication.md` para la explicación pública de la
licencia y de la publicación.

La publicación de esta fuente sirve para revisión técnica, verificabilidad y
difusión del proyecto. Las fuentes oficiales públicas de la convocatoria PYSED
no fijan una licencia concreta del código; sí contemplan la difusión amplia de
resultados y la posible colaboración en difusión. La resolución global de
concesión remite además a condiciones particulares de cada beneficiario, por lo
que esta publicación no debe leerse como cierre jurídico de subvención sin
revisar la resolución individual correspondiente.

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
una instalación de producción con todos los paquetes operativos. El ejemplo
instalado es suficiente para una demostración técnica. Los paquetes operativos
o de estándares que un operador quiera procesar se importan por los contratos
SDS y deben contar con sus derechos de uso aplicables. Los códigos oficiales se
preservan literalmente cuando un paquete autorizado los aporta; esta
distribución no reclama certificación, aprobación o licencia de GRI ni de
ninguna otra entidad por sí misma.

Consulte `docs/public-profile.md`, `api/docs/import-packages.md` y
`docs/quality/acceptance_gates.md`. Para uso, licencia, avisos de terceros y
seguridad, consulte `LICENSE`, `docs/license-and-publication.md`,
`docs/third-party-notices.md` y `SECURITY.md`.
