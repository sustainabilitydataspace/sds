# E4 - Prototipo API y transformación de datos

Tabla 1  Ficha del entregable E4

| Campo | Valor |
|---|---|
| Proyecto | Sustainability Data Spaces (SDS) |
| Entregable | E4 - Prototipo API y transformación de datos |
| Paquete de trabajo | PT2 - Modelado de Datos e Interoperabilidad Técnica |
| Actividad principal | A2.3 - Creación de esquemas y pipelines conceptuales para transformar datos brutos en reportes normativos interoperables |
| Versión | V1.0 |
| Fecha | 2026-06-18 |
| Estado | Documento final |
| Responsable | Oficina del Programa SDS |
## Resumen

E4 demuestra que un paquete controlado de granularización de indicadores de normas puede validarse, transformarse en el registro estandarizado SDS y exponerse mediante el prototipo de API de SDS. El entregable conecta la línea base de inventario de E1 y el modelo común de E3 con una superficie de servicio ejecutable para acceso a catálogo, consulta semántica, mapeos, valores, cálculos, conversión de unidades y operaciones de importación controlada. [@ETSI_NGSI_LD_CIM_009] [@W3C_JSON_LD_1_1] [@JSON_SCHEMA_2020_12]

El prototipo no es una maqueta simulada. El control de validación por defecto del repo ejecuta el libro mayor de cálculo ampliado por dimensiones actual, sigue aceptando un paquete de registro explícito cuando se proporciona, escribe evidencia de control de validación reproducible y valida que la fuente seleccionada pueda procesarse dentro del umbral de rendimiento de E4.

## Alcance de entrega

E4 cubre las siguientes capacidades de prototipo:

- Ingesta register-first desde un paquete estandarizado de indicadores de sostenibilidad.
- Validación de campos de registro requeridos, recuentos de filas, sumas de comprobación, dimensiones y tipos de valor.
- Transformación hacia la forma de registro canónico SDS utilizada por E1 y E3.
- Publicación API de indicadores, conceptos, mapeos, valores, cálculos, conversión de unidades y funciones de datos de referencia.
- Operaciones controladas de importación y vista previa para datos de indicadores y valores.
- Generación de evidencia para tiempos, tasa de éxito, cobertura de familias y reproducibilidad.
- Acceso con conciencia de gobernanza a funciones API protegidas mediante la capa de políticas y autenticación definida en E6.

El prototipo soporta productos de datos alineados con normas para NEIS (ESRS en inglés), Estándares GRI y Protocolo de GEI (GHG Protocol) mediante el mismo contrato de registro/API SDS. Las obligaciones de reporte siguen estando definidas por las normas fuente; E4 demuestra la capa técnica SDS de transformación y publicación API. [@EU_ESRS_2023_2772] [@GRI_STANDARDS_2025] [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD]

## Superficie del prototipo

| Área del prototipo | Qué demuestra E4 | Trazabilidad pública |
|---|---|---|
| Ingesta de paquetes | Un paquete de normas puede suministrarse como registro estructurado y validarse antes de su uso. | `scripts/e4_gate.py`; fila E04 en `deliverables/deliverables-register.csv` |
| Transformación del registro | El paquete seleccionado se normaliza hacia la forma de registro SDS utilizada por E1/E3. | `data/extracted/analysis/e4_performance_report.txt`; `data/extracted/analysis/e4_performance_summary.csv` |
| API de indicadores | Los indicadores pueden listarse, buscarse, exportarse, validarse, importarse de forma asíncrona y trazarse mediante superficies de manifiesto, historial, cambios y diff. | Rutas API de indicadores y evidencia de código técnico E5 |
| API semántica | Los conceptos, equivalencias, mapeos y vistas taxonómicas pueden consultarse utilizando el modelo semántico SDS. | Paquete de modelo E3 y rutas API de ontología/mapeo |
| API de valores y cálculo | Los valores pueden importarse, consultarse, exportarse y utilizarse por puntos de conexión de cálculo con trazabilidad. | Rutas API de valores/cálculo y evidencia de código técnico E5 |
| API de unidades y datos de referencia | La conversión de unidades y los controles de vista previa/importación de FX/datos de referencia están disponibles mediante la capa de servicio. | Evidencia de código técnico E5 y controles de gobernanza E6 |
| Controles de gobernanza | Las operaciones protegidas utilizan autenticación, comprobaciones de permisos, metadatos de políticas y límites orientados a auditoría. | Entregable de gobernanza E6 |

## Control de validación de cierre E4 requerido

El control de validación de cierre del proyecto para E4 requiere distinguir la evidencia estructural de la transformación operativa:

| Elemento requerido | Estado verificable actual |
|---|---|
| Transformar al menos el 95% de los datos brutos de prueba seleccionados | La matriz R8 ejecutada contiene 1.000 filas con entrada bruta, salida esperada, salida observada, preservación del valor/unidad original y estado por fila. Transformó correctamente 1.000 / 1.000 registros: `100%`, por encima del `95%`. |
| Procesar al menos 1.000 registros en menos de 30 segundos | El gate `make service-value-import-performance` importó 1.000 valores CSV por el flujo normal en `4,675 s`, verificó cinco superficies de persistencia y eliminó las 1.000 filas de cada superficie. R10 queda cerrado por esta ejecución PostgreSQL reproducible. |
| Registrar errores y estado del control de validación | Tanto el control estructural como el gate operativo escriben informes legibles por máquina, incluidos estados `PASS`, `FAIL` o `not_evaluated` y causas explícitas. |
| Documentar al menos tres conjuntos de reglas | La matriz ejecuta tres reglas reales: agua `L → m³` (334 filas), energía `kWh → MWh` (333) y emisiones `kg CO2e → t CO2e` (333), sobre conceptos canónicos ESRS/GRI. |
| Preservar la reproducibilidad | Los gates usan entradas deterministas, umbrales explícitos, sumas de comprobación o prefijos de ejecución, recuentos de salida y limpieza verificable. |

La salida de los controles utiliza etiquetas de fuente saneadas en lugar de rutas locales absolutas, de modo que la evidencia pública pueda revisarse sin exponer la estructura del entorno local.

El libro mayor dimensional no transforma observaciones brutas. `make e4-gate` lee el libro mayor, suma coordenadas y comprueba cobertura estructural por familia; es útil para dimensionamiento y reproducibilidad, pero no constituye una prueba de transformación de datos operativos. En consecuencia, R10 no queda demostrado por `make e4-gate`.

La prueba técnicamente adecuada para R10 es `make service-value-import-performance`: genera 1.000 valores sintéticos bajo el contrato CSV estricto, ejecuta el importador normal, comprueba las tablas de valores, contextos, revisiones, eventos y punteros actuales, mide el tiempo total y elimina sus propias filas. La ejecución PostgreSQL registrada importó `1.000 / 1.000` valores en `4,675092417 s`, por debajo del umbral de `30 s`, sin fallos. R10 queda cerrado por la ejecución PostgreSQL; el resultado es un tripwire local de regresión y no una garantía de concurrencia, SLA productivo o rendimiento general a gran volumen.

La prueba específica de R8 es `make service-raw-value-transform-matrix`. El gate genera el denominador antes de ejecutar, importa por el flujo PostgreSQL normal, consulta los valores persistidos y compara cada fila con su resultado esperado. R8 queda cerrado por la matriz ejecutada: 1.000 éxitos, 0 errores y tasa `100%`. El gate preservó `original_value` y `original_unit`, marcó `conversion_applied=true`, verificó las cinco superficies de persistencia y eliminó todas sus filas.

## Evidencia estructural del libro mayor dimensional

La evidencia siguiente corresponde únicamente al control estructural y de dimensionamiento:

| Campo de evidencia | Resultado |
|---|---:|
| Resultado del control estructural | PASS |
| Modo de fuente | Libro mayor de dimensiones |
| Grupos del libro mayor | 512 |
| Coordenadas reportables | 87,547 |
| Coordenadas contabilizadas | 87,547 |
| Segundos transcurridos | Registrado en el resumen actual del control estructural |
| Coordenadas ambientales/carbono | 80,711 |
| Coordenadas sociales | 2,126 |
| Coordenadas de gobernanza | 3,973 |
| Coordenadas transversales | 737 |

Archivos de evidencia:

- `data/extracted/analysis/e4_performance_report.txt`
- `data/extracted/analysis/e4_performance_summary.csv`
- `data/extracted/analysis/e4_raw_transform_matrix.csv` — matriz R8 de 1.000 filas, bruto → esperado → observado → éxito/error.
- `data/extracted/analysis/e4_raw_transform_report.json` — resumen R8, `passed=true`, `1000 / 1000`, tasa `1.0`.
- `deliverables/E04-api-prototype/evidence/e4-r10-value-import-performance-report-v1-0.json` — evidencia pública reproducible del gate operativo de 1.000 valores; `passed=true`, 1.000 importados, `4,675092417 s`, persistencia y limpieza completa.

## Capacidades SDS adicionales más allá del alcance mínimo de E4

El control de validación de cierre original de E4 requiere una transformación de prototipo con evidencia de tiempos, registro de errores y al menos tres familias de reglas. SDS incluye las siguientes capacidades adicionales más allá de ese alcance mínimo:

| Capacidad adicional | Por qué excede el control de validación mínimo de E4 |
|---|---|
| Contrato de paquete register-first | E4 puede procesar directamente un paquete estandarizado en lugar de depender solo de entradas de compatibilidad. |
| Operaciones de catálogo API | El prototipo expone operaciones de listado/búsqueda/exportación/importación/estado en lugar de limitarse a escribir un CSV transformado. |
| Superficies de manifiesto, historial, cambios y diff | La API soporta la revisión operativa del estado de los conjuntos de datos, no solo evidencia de transformación puntual. |
| Consulta semántica y mapeos | El prototipo conecta las filas transformadas con conceptos, equivalencias y metadatos de relaciones revisados. |
| Valores y cálculos | La API incluye puntos de conexión operativos de importación/consulta/exportación de valores y de cálculo más allá del requisito de solo transformación. |
| Conversión de unidades y datos de referencia | El prototipo incluye controles deterministas de unidades y FX/datos de referencia que no son requeridos por el control de validación básico de filas/tiempo de E4. |
| Aplicación de gobernanza | Las funciones API protegidas están vinculadas a autenticación, permisos y controles de políticas de E6. |

Estas adiciones forman parte de la superficie técnica de producto SDS. Deben tratarse como valor del proyecto más allá de la evidencia mínima de cierre de E4, no como obligaciones adicionales impuestas por la definición original de E4.

## Comandos de validación

La evidencia de E4 se valida con:

- `make e4-gate`
- `make service-raw-value-transform-matrix` — ejecuta la matriz R8 contra PostgreSQL.
- `make service-value-import-performance` — requiere PostgreSQL y es la comprobación aplicable a R10.
- `api/.venv/Scripts/python.exe -m pytest api/tests/test_e4_gate.py -q`
- `make deliverables-check`

Cuando un revisor quiere validar un paquete controlado específico de normas, el mismo control de validación acepta `SDS_E4_REGISTER_PATH` como ruta del paquete y mantiene saneada la salida de evidencia.

## Conclusión de cumplimiento

E4 se entrega como una API funcional y un prototipo de transformación con contratos de importación, validación, tipado, conversión y exposición. La evidencia PostgreSQL actual cierra R8 y R10 sin utilizar el libro mayor dimensional como proxy: la matriz R8 obtuvo `1000 / 1000` transformaciones correctas (`100%`) y R10 importó 1.000 valores en `4,675092417 s`. Ambos gates verificaron persistencia y limpieza; sus resultados están limitados a fixtures locales, deterministas y de un solo proceso.

## Referencias

- European Telecommunications Standards Institute. ETSI GS CIM 009, Context Information Management (CIM); NGSI-LD API. https://cim.etsi.org/NGSI-LD/official/front-page.html. [@ETSI_NGSI_LD_CIM_009]
- Sporny, Manu, Dave Longley, Gregg Kellogg, Markus Lanthaler, Pierre-Antoine Champin, and Niklas Lindstrom. JSON-LD 1.1. W3C Recommendation, July 16, 2020. https://www.w3.org/TR/json-ld11/. [@W3C_JSON_LD_1_1]
- JSON Schema. JSON Schema Draft 2020-12. https://json-schema.org/draft/2020-12. [@JSON_SCHEMA_2020_12]
- European Commission. Commission Delegated Regulation (EU) 2023/2772, European Sustainability Reporting Standards. Official Journal of the European Union, December 22, 2023. [@EU_ESRS_2023_2772]
- Global Reporting Initiative. Consolidated Set of the GRI Standards. https://www.globalreporting.org/standards/. [@GRI_STANDARDS_2025]
- Greenhouse Gas Protocol. The Greenhouse Gas Protocol: A Corporate Accounting and Reporting Standard, revised edition. World Resources Institute and World Business Council for Sustainable Development. https://ghgprotocol.org/corporate-standard. [@GHG_PROTOCOL_CORPORATE_STANDARD]
- Greenhouse Gas Protocol. Corporate Value Chain (Scope 3) Accounting and Reporting Standard. World Resources Institute and World Business Council for Sustainable Development, 2011. https://ghgprotocol.org/corporate-value-chain-scope-3-standard. [@GHG_PROTOCOL_SCOPE3_STANDARD]
