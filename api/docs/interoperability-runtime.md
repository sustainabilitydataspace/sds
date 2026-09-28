# Runtime Interoperability Guide

> **Última actualización:** 2026-06-29

Guía de uso de la resolución runtime de interoperabilidad SDS. Esta guía cubre
el uso manual desde Swagger y el mismo flujo por API.

La funcionalidad funciona sobre el runtime activo: catálogo semántico, valores
operativos, mappings materializados, contratos de cálculo y catálogo de
unidades.

---

## Qué resuelve

`POST /api/v1/values/resolve` intenta responder una petición de valor
`concept/entity/period` usando evidencia real del SDS:

1. valor directo ya almacenado para el concepto solicitado,
2. contrato de cálculo ejecutable para el concepto solicitado,
3. mapping exacto/equivalente hacia otro concepto con valor o cálculo,
4. conversión de unidad soportada dentro de la misma dimensión física.

Si ninguna ruta es válida, devuelve una negativa estructurada. No inventa
valores.

Estados principales:

| `status` | Significado |
|---|---|
| `resolved` | SDS resolvió el valor y devuelve `value`, `unit`, `method` y traza opcional. |
| `not_found` | No existe valor, cálculo ni mapping candidato para la petición. |
| `not_enough_evidence` | Existe una relación o ruta, pero falta el valor fuente o el cálculo ejecutable. |
| `not_transformable` | Hay mapping, pero no es exacto/equivalente y no se permite transferencia numérica. |
| `ambiguous_mapping` | Hay más de una ruta equivalente posible y SDS no elige arbitrariamente. |
| `unsupported_conversion` | La conversión solicitada no pertenece a la misma dimensión física o no está soportada. |

Métodos principales:

| `method` | Ruta usada |
|---|---|
| `direct_stored` | Valor directo en `/values`. |
| `calculation` | Resultado de `/calculate`. |
| `equivalent_mapping` | Transferencia por mapping exacto/equivalente. |
| `converted` | Conversión de unidad sobre un valor directo o calculado. |
| `mapping_then_conversion` | Mapping equivalente seguido de conversión de unidad. |
| `calculation_then_mapping` | Cálculo fuente seguido de mapping equivalente. |

---

## Readiness antes de usar Swagger

Ejecuta primero:

```http
GET /api/v1/interoperability/readiness
```

Este endpoint no escribe datos. Comprueba que el runtime tiene capacidades
operativas clave cargadas:

- catálogos semánticos,
- `L -> m3`,
- `kWh -> MWh`,
- rechazo correcto de `GJ -> t CO2e` como conversión directa,
- mapping `csrd:E3-4_05 -> gri:303-5.c`,
- contrato ejecutable para `urn:sds:disclosure:csrd:e3-5`.

`status=ready` significa que esos checks concretos pueden ejecutarse. No
significa que todos los indicadores del catálogo tengan valores, mappings y
contratos suficientes.

---

## Uso en Swagger

1. Abre `/docs`.
2. Haz login en `POST /auth/login`.
3. Copia el `access_token` y úsalo en **Authorize**.
4. Ejecuta `GET /api/v1/interoperability/readiness`.
5. Crea o confirma el perimetro organizativo en `POST /api/v1/hierarchies`
   antes de importar valores para una entidad nueva como `nh_es_valencia_plant`.
6. Empieza por el código estándar que conoce el usuario:
   `urn:sds:disclosure:csrd:e3-5`, `csrd:E3-4_05`, `gri:303-5.c`, etc.
7. Si es un cálculo, ejecuta
   `GET /api/v1/calculate/dependencies/{concept}`. SDS devuelve las variables
   internas requeridas. El usuario no tiene que conocer `syg:*` de antemano.
8. Si es un cruce ESRS/GRI/GHG, ejecuta
   `GET /api/v1/mappings/from/{standard}/{code}` para ver las equivalencias
   disponibles.
9. Solo si faltan valores, crea los inputs descubiertos por SDS en
   `POST /api/v1/values`.
10. Ejecuta `POST /api/v1/calculate` o `POST /api/v1/values/resolve`.

Si una importación devuelve `Unknown entity`, SDS está indicando que la entidad
no existe en ninguna jerarquía activa para la compañía del usuario. Crea el
perímetro con `POST /api/v1/hierarchies` o cambia el payload a una entidad ya
registrada.

Swagger incluye ejemplos editables para:

- resolución de agua ESRS a GRI:
  `csrd:E3-4_05 -> gri:303-5.c`,
- resolución calculada de GRI 302-1.e con puente certificado:
  `resolve_gri_302_1e_from_certified_bridge`,
- conversión de energía:
  `urn:sds:reg:esrs:e1_5_12`, `MWh -> MWh`,
- negativa cerrada de energía a emisiones:
  `GJ -> t CO2e`.

---

## Ejemplos

### Empezar desde el código ESRS/GRI

El flujo normal es que el usuario conozca un código de reporting, no una
variable interna de Sygris. Ese código puede ser un disclosure/concepto
calculable o un datapoint granular.

En este ejemplo, `urn:sds:disclosure:csrd:e3-5` es el concepto calculable que SDS usa para ESRS
E3-5. No es un datapoint granular IG3. Datapoints granulares de E3-5 serían
por ejemplo `E3-5_01`, `E3-5_02`, etc.

Para consultar un cálculo ESRS:

```http
GET /api/v1/calculate/dependencies/urn:sds:disclosure:csrd:e3-5
```

Respuesta abreviada:

```json
{
  "concept": "urn:sds:disclosure:csrd:e3-5",
  "formula": "e3_5_01",
  "required_variables": [
    "urn:sds:reg:esrs:e3_5_01"
  ],
  "unit": "Currency"
}
```

A partir de esa respuesta, el usuario verifica si existen valores para esas
variables y, si no existen, los crea. La guía no asume que el usuario las
conoce antes de preguntar a SDS.

### Escenario A: la base ya tiene valores

1. Ejecuta `GET /api/v1/calculate/dependencies/urn:sds:disclosure:csrd:e3-5`.
2. SDS devuelve `required_variables`.
3. Consulta valores existentes con `GET /api/v1/values`, filtrando por cada
   variable devuelta, entidad y periodo.
4. Si existen, ejecuta directamente `POST /api/v1/calculate`:

```json
{
  "concept": "urn:sds:disclosure:csrd:e3-5",
  "entity": "nh_group",
  "period": "2024",
  "granularity": "annual",
  "include_trace": true
}
```

La traza muestra qué variables usó SDS y qué valores tomó de la base.

### Escenario B: la base no tiene valores

1. Ejecuta `GET /api/v1/calculate/dependencies/urn:sds:disclosure:csrd:e3-5`.
2. Usa solo las variables que SDS devuelve en `required_variables`.
3. Crea un valor por cada variable requerida en `POST /api/v1/values`.
4. Ejecuta `POST /api/v1/calculate` con `concept = "urn:sds:disclosure:csrd:e3-5"`.
5. Si quieres verificar equivalencia ESRS/GRI, ejecuta después
   `POST /api/v1/values/resolve` o consulta
   `GET /api/v1/mappings/from/ESRS/E3-4_05?limit=10`.

El usuario sigue trabajando con el código estándar. Los datapoints técnicos
aparecen porque SDS los ha devuelto como inputs necesarios, no porque el usuario
los conozca previamente.

Para descubrir equivalencias ESRS/GRI:

```http
GET /api/v1/mappings/from/ESRS/E3-4_05?limit=10
```

Para buscar por familia de código destino en el índice canónico:

```http
GET /api/v1/mappings/search?source_standard=ESRS&target_standard=GRI&target_code=GRI%20305&limit=10
```

`target_code` filtra por prefijo del código canónico almacenado, por eso el
ejemplo usa `GRI 305` en lugar de `30`.

Respuesta abreviada:

```json
{
  "source_standard": "ESRS",
  "source_code": "E3-4_05",
  "target_standard": "GRI",
  "target_code": "GRI 303-5.c",
  "relationship_type": "equivalent"
}
```

### Mapping equivalente ESRS -> GRI

```json
{
  "source_concept": "csrd:E3-4_05",
  "target_concept": "gri:303-5.c",
  "entity": "nh_group",
  "period": "2024",
  "granularity": "annual",
  "target_unit": "m3",
  "include_trace": true
}
```

Resultado esperado cuando existe valor fuente y mapping equivalente:

```json
{
  "status": "resolved",
  "method": "equivalent_mapping",
  "target_concept": "gri:303-5.c",
  "source_concept": "csrd:E3-4_05",
  "unit": "m3"
}
```

### Cálculo de indicador

```json
{
  "concept": "urn:sds:disclosure:csrd:e3-5",
  "entity": "nh_group",
  "period": "2024",
  "granularity": "annual",
  "include_trace": true
}
```

Si el contrato o la fórmula semántica está cargada y existen inputs, la traza
incluye fórmula, variables, contrato y valores fuente.

### Energía física

```json
{
  "target_concept": "urn:sds:reg:esrs:e1_5_12",
  "entity": "nh_group",
  "period": "2024",
  "granularity": "annual",
  "target_unit": "MWh",
  "include_trace": true
}
```

El valor Nordhaven 2024 se resuelve directamente como `390.604 MWh` porque el
concepto, entidad y periodo existen en la base local.

### GRI 302-1.e desde componentes ESRS autorizados

GRI 302-1.e pide consumo total de energía dentro de la organización. Un
datapoint ESRS más estrecho, por ejemplo un componente de ESRS E1-5, no puede
responder por sí solo al total GRI. SDS solo puede calcular el valor GRI desde
componentes ESRS cuando existe un contrato de cálculo con puente certificado que
declare exactamente qué componentes entran en el total y qué relación de mapeo
está autorizada.

En Swagger usa el ejemplo
`resolve_gri_302_1e_from_certified_bridge` en
`POST /api/v1/values/resolve`:

```json
{
  "target_concept": "urn:sds:reg:gri:gri_302_1_e_total_energy_consumption_within_organization",
  "entity": "nh_group",
  "period": "2024",
  "granularity": "annual",
  "include_trace": true
}
```

Si la ruta está autorizada, la respuesta mantiene `status = "resolved"` y
`method = "calculation"`, e incluye:

```json
{
  "execution_authority": "certified_bridge",
  "bridge_id": "bridge-gri-302-1e-total-energy"
}
```

`execution_authority` explica por qué SDS puede ejecutar la ruta. `bridge_id`
identifica el contrato que autoriza usar componentes más estrechos para devolver
el objeto de reporting GRI solicitado. Si falta ese contrato, SDS debe responder
con negativa estructurada, no usar el mapping como fórmula.

### Energía a emisiones

```json
{
  "target_concept": "urn:sds:reg:esrs:e1_5_12",
  "entity": "nh_group",
  "period": "2024",
  "granularity": "annual",
  "target_unit": "t CO2e",
  "include_trace": true
}
```

Esta petición debe devolver `unsupported_conversion` si no existe un contrato
de cálculo explícito con factor de emisión. `GJ -> t CO2e` no es una conversión
de unidad.

---

## Requisitos para cualquier indicador

La resolución no está limitada a los ejemplos anteriores. Para otros
indicadores, SDS necesita una ruta real:

- que el usuario pueda localizar el código estándar en catálogo o mapping,
- un valor operativo para `target_concept/entity/period`,
- o un contrato/fórmula ejecutable con inputs cargados,
- o un mapping exacto/equivalente hacia un concepto con valor o cálculo,
- o un puente certificado cuando una ruta no equivalente, como
  `narrower`, alimenta explícitamente un cálculo,
- y, si se solicita `target_unit`, una conversión compatible.

Si un indicador existe solo en catálogo pero no tiene valores, contrato, fórmula
o mapping operativo, SDS debe fallar cerrado.

---

## Permisos

`POST /api/v1/values/resolve` requiere permisos de:

- lectura de valores,
- ejecución de cálculos,
- conversión de unidades,
- lectura de mappings.

`GET /api/v1/interoperability/readiness` requiere lectura suficiente de las
superficies anteriores y consulta semántica.
