# Avisos de terceros y materiales no cubiertos por la licencia SDS

Este documento identifica las principales superficies donde aparecen materiales
de terceros o términos separados en la distribución pública SDS.

La licencia `../LICENSE` se aplica únicamente a los materiales originales de
software SDS que Cambridge Business Initiatives S.L. posee o controla. No
sustituye ni amplía licencias o derechos de terceros.

## Activos estáticos de documentación API

SDS incluye copias locales de Swagger UI y ReDoc para que `/docs` y `/redoc`
funcionen sin CDN. Sus avisos se conservan en:

- `../api/src/static/swagger-ui/LICENSE`
- `../api/src/static/swagger-ui/NOTICE`
- `../api/src/static/swagger-ui/swagger-ui-bundle.js.LICENSE.txt`
- `../api/src/static/swagger-ui/swagger-ui-standalone-preset.js.LICENSE.txt`
- `../api/src/static/redoc/LICENSE`
- `../api/src/static/redoc/redoc.standalone.js.LICENSE.txt`
- `../api/src/static/redoc/756674defce81e90acea.worker.js.LICENSE.txt`

Esos textos controlan los derechos sobre dichos activos.

## Dependencias de Python

Las dependencias instaladas desde índices de paquetes son software de sus
respectivos autores. Sus licencias no se modifican por la licencia SDS.

El inventario de licencias de dependencias se genera durante la revisión de
seguridad/desarrollo con:

```bash
make -C api security-scan
```

El repositorio contiene herramientas asociadas a esa revisión, incluido
`../api/scripts/export_dependency_license_inventory.py`. El resultado de cada
instalación debe revisarse contra el entorno exacto que se vaya a evaluar u
operar.

## Estándares, vocabularios y proveedores

Las referencias a ESRS/EFRAG, GRI, GHG Protocol, ISSB, FIWARE, NGSI-LD, DCAT,
ODRL u otros estándares, vocabularios, códigos o identificadores se conservan
para interoperabilidad y trazabilidad. Esa conservación no concede certificación,
aprobación, marca, sublicencia ni derechos de explotación sobre materiales de
esas entidades.

Quien importe paquetes de estándares, mapeos o datos debe contar con derechos de
uso suficientes para esos materiales.

El paquete de demostración `api/demo/a23` reproduce literalmente los nombres y
descripciones de los datapoints ESRS E1-5 y E1-6 publicados por EFRAG; la fuente
se indica en `api/demo/a23/NOTICE.md`. Esos textos no quedan cubiertos por la
licencia SDS. Las referencias a GRI de ese paquete usan solo códigos.

## Paquetes de operador y productos de datos

La licencia SDS no licencia paquetes de operador, datos de producción, datos
personales, secretos empresariales, evidencias privadas, productos de datos ni
ficheros de cliente.

Los términos de producto de datos bajo `policies/`, incluido
`policies/data_product_terms.md`, son plantillas o registros de política para
productos concretos. No son una licencia general del repositorio, del código SDS,
de los entregables formales ni de estándares de terceros.

## Entregables formales

Los entregables bajo `../deliverables/` son evidencia pública y materiales de
difusión del proyecto. Pueden leerse, descargarse, citarse y compartirse sin
cambios, con atribución y avisos intactos, para evaluación, auditoría, difusión
pública o verificación de la ayuda. Cualquier material de tercero incorporado o
referenciado dentro de esos entregables conserva sus propios términos.

## Regla de conflicto

Si una licencia o término de un tercero entra en conflicto con la licencia SDS,
la licencia o término de ese tercero prevalece para el material de tercero.
