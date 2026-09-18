---

title: "E1 - Inventario de datos clave y variables brutas"

subtitle: "Espacios de Datos de Sostenibilidad (SDS)"

date: "2026-06-09"

lang: es-ES

reference-section-title: "Referencias"

---



# E1 - Inventario de datos clave y variables brutas

Tabla 1  Ficha del entregable E1

| Campo | Valor |
|---|---|
| Proyecto | Sustainability Data Spaces (SDS) |
| Entregable | E1 - Inventario de datos clave y variables brutas |
| Paquete de trabajo | PT1 - Mapeo de Datos, Normativas y Estándares |
| Actividad principal | A1.2 - Identificación de variables y datos brutos requeridos para informes de sostenibilidad |
| Versión | V1.0 |
| Fecha | 2026-06-18 |
| Estado | Documento final |
| Responsable | Oficina del Programa SDS |
## Resumen

E1 define el inventario SDS de puntos de datos de sostenibilidad, variables brutas, metadatos de medición y representación técnica necesarios para la presentación de información alineada con estándares. Cumple los requisitos de aceptación del proyecto relativos a cobertura normativa y categorización clara, y registra los metadatos aceptados de la línea base técnica SDS para NEIS (ESRS en inglés), Estándares GRI y cobertura adicional voluntaria del Protocolo de GEI (GHG Protocol) en esta versión. El Protocolo de GEI no formaba parte del alcance mínimo de aceptación de E1; se documenta aquí como cobertura técnica adicional. [@EU_CSRD_2022_2464] [@EU_ESRS_2023_2772] [@GRI_STANDARDS_2025] [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD]

E1 utiliza la fuente oficial de cada estándar como denominador y aplica un proceso controlado de granulación de indicadores y validación técnica. La evidencia SDS resultante registra atributos, unidades, preparación para fórmulas, dependencias de cálculo y trazabilidad como evidencia técnica a nivel de estándar. Es evidencia estructural, de catálogo y de preparación para cálculo; no es una declaración de valores operativos de una empresa.

La descripción del dossier menciona CSRD, GRI, SASB y otros estándares relevantes, mientras que sus criterios de aceptación exigen conexiones explícitas con al menos CSRD/NEIS y GRI. La implementación actual de SDS materializa NEIS/ESRS y GRI como línea base operativa y añade el Protocolo de GEI como extensión técnica. SASB, ISSB y TCFD no están instalados en el catálogo operativo ni se presentan como cobertura implementada en esta versión; quedan identificados como posibles extensiones sujetas a fuente oficial, modelado, revisión y aceptación antes de poder alimentar catálogos o relaciones activas.

## Evidencia pública

E1 utiliza dos capas de evidencia pública:

- Estándares públicos externos: NEIS, Estándares GRI y estándares corporativos y de cadena de valor del Protocolo de GEI. [@EU_ESRS_2023_2772] [@GRI_STANDARDS_2025] [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD]
- Evidencia de entregable del proyecto: el artefacto público E01. El control del artefacto se registra por separado en `deliverables/deliverables-register.csv`, que recoge la ruta canónica, la versión, la suma de comprobación, la clase de privacidad y la referencia de fuente saneada.

El entregable público declara el resultado técnico a nivel de estándar y registra solo las categorías de evidencia necesarias para la revisión pública.

## Criterios de aceptación del proyecto

| Requisito | Forma de aceptación pública |
|---|---|
| `R1` Cobertura normativa | El inventario cubre al menos el `90%` de las variables necesarias para los estándares seleccionados. |
| `R2` Categorización clara | Las variables se clasifican por área de sostenibilidad e incluyen metadatos de medición como unidad, fuente y frecuencia de recopilación. |
| Integridad de referencia al estándar | Las filas del inventario mantienen al menos una referencia a estándar para los estándares seleccionados. |
| Trazabilidad | La evidencia del inventario mantiene trazabilidad de fuente y revisión suficiente para la revisión de aceptación pública. |

## Inventario de variables clave

El inventario E1 registra `1,805` variables clave derivadas de los estándares seleccionados (NEIS/ESRS-CSRD y Estándares GRI). Cada variable se identifica con un identificador canónico, se clasifica por área de sostenibilidad e incluye su unidad de medida, fuente de origen (estándar oficial) y frecuencia de recopilación. La lista completa y legible por máquina se entrega como anexo de evidencia en `deliverables/E01-mapeo-datos-normativas/evidence/e01-inventario-variables-clave-v2026-06-12.csv`.

El anexo CSV usa valores controlados en inglés para campos de máquina; por ejemplo, la columna `frecuencia` usa `annual` para representar la frecuencia anual. En la narrativa y tablas de lectura humana de este entregable, ese mismo valor se presenta como `anual`.

## Doble materialidad y origen del dato

El inventario distingue la procedencia de la definición respecto del origen del valor operativo. Las definiciones proceden de estándares externos controlados (`origen_definicion=external-standard`), mientras que el dato que una organización aporte para una variable tendrá un origen específico de esa entidad (`origen_dato_operativo=entity-specific`) y deberá conservar procedencia, responsable, periodo y método.

La doble materialidad no puede asignarse de forma universal desde un catálogo técnico: depende de impactos, riesgos, oportunidades, actividades y contexto de cada entidad. Por ello, el anexo marca cada variable como `entity-assessment-required`. Este estado identifica variables candidatas para la evaluación de materialidad de impacto y materialidad financiera, pero no predetermina la materialidad de una empresa ni sustituye su proceso documentado de evaluación.

El anexo legible por máquina incorpora ahora `definicion`, `origen_definicion`, `origen_dato_operativo` y `evaluacion_doble_materialidad` para las `1,805` variables. Así se preserva la exigencia contractual de definición y origen sin inventar una clasificación empresarial que no pertenece a E1.

### Clasificación por área de sostenibilidad

| Área de sostenibilidad | NEIS/ESRS | GRI | Total |
|---|---:|---:|---:|
| Ambiental | `501` | `260` | `761` |
| Social | `383` | `167` | `550` |
| Gobernanza | `84` | `234` | `318` |
| Transversal/General | `157` | `19` | `176` |
| **Total** | **`1,125`** | **`680`** | **`1,805`** |

Cada fila del inventario incluye unidad de medida, fuente de origen y frecuencia de recopilación (`anual` para el ejercicio de reporte CSRD/GRI), con lo que el inventario satisface la categorización por área (ambiental, social, gobernanza) y los metadatos de medición requeridos por variable.

### Muestra representativa del inventario

| Variable | Área | Estándar | Código | Unidad | Frecuencia |
|---|---|---|---|---|---|
| Residuos totales generados | Ambiental | ESRS/CSRD | E5-5_07 | toneladas | anual |
| Emisiones a la atmósfera por contaminante | Ambiental | ESRS/CSRD | E2-4_02 | toneladas | anual |
| Porcentaje de trabajadores no asalariados remunerados por debajo del salario digno | Social | ESRS/CSRD | S1-10_04 | porcentaje | anual |
| Indicadores de formación y desarrollo de competencias (por género) | Social | ESRS/CSRD | S1-13_01 | porcentaje | anual |
| Número de incidentes confirmados de corrupción o soborno | Gobernanza | ESRS/CSRD | G1-4_04 | recuento | anual |
| Porcentaje de funciones en riesgo cubiertas por formación | Gobernanza | ESRS/CSRD | G1-3_07 | porcentaje | anual |

El recuento de `1,805` variables es el registro canónico SDS con metadatos completos y constituye la lista de variables clave revisable del inventario E1. No debe confundirse con las `5,021` definiciones canónicas granuladas (que expanden subindicadores y códigos GRI ampliados), ni con las `87,547` coordenadas de valor expandidas por dimensión del anexo de cálculo (un equivalente de campos de formulario plano para dimensionamiento, no un recuento de variables).

## Línea base técnica de estándares oficiales

Esta tabla resume la línea base técnica cualificada por fecha para E1 `V2026-06-05`. `make e1-gate` comprueba la presencia del artefacto canónico español, el anexo de cálculo y el inventario CSV; valida las columnas y sumas del libro mayor agrupado frente a los totales esperados. La suma de comprobación, la ruta canónica y la clasificación de privacidad se validan por separado mediante `make deliverables-check`. NEIS y GRI son el alcance mínimo de aceptación de E1; el Protocolo de GEI es cobertura técnica adicional voluntaria.

| Estándar | Denominador normativo | Cobertura de granulación controlada | Definiciones canónicas de indicadores SDS | Definiciones listas para cálculo |
|---|---:|---:|---:|---:|
| NEIS [@EU_ESRS_2023_2772] | `1,200` filas oficiales de inventario / `99` alcances | `1,200 / 1,200` filas, `99 / 99` alcances | `1,242` definiciones | `1,249` nodos |
| GRI [@GRI_STANDARDS_2025] | `3,964` filas de la fuente técnica controlada de GRI / `573` alcances | `3,964 / 3,964` filas, `573 / 573` alcances | `3,666` definiciones | `4,252` nodos |
| Protocolo de GEI - cobertura técnica adicional voluntaria [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD] | `18` alcances oficiales | `18 / 18` alcances | `113` definiciones | `317` nodos |

Las columnas `Denominador normativo`, `Cobertura de granulación controlada`, `Definiciones canónicas de indicadores SDS` y `Definiciones listas para cálculo` son metadatos técnicos aceptados del espacio de datos para representación y computación. No añaden ni redefinen las obligaciones oficiales de reporte incluidas en el denominador normativo.

Los recuentos de definiciones no son un recuento de campos operativos de captura expandidos por dimensión. En una implementación de estilo Sygris, una definición canónica de indicador puede crear varios registros de valor rellenables cuando la superficie de reporte la expande por entidad, centro, actividad, período de reporte, perímetro de consolidación, alcance/categoría GEI, escenario u otras dimensiones. Esa expansión de instancias de valor operativo es evidencia de implementación/entorno de ejecución, no parte del recuento estructural del inventario E1.

### Interpretación de coordenadas de valor expandidas por dimensión

Para auditoría y dimensionamiento de implementación, SDS también registra una interpretación expandida por dimensión de la misma superficie técnica. En esta vista, cada punto de datos canónico se expande por las dimensiones finitas definidas por el estándar correspondiente o por el contrato de cálculo SDS, mientras que se excluyen el período de reporte, el perímetro de entidad, el centro, la ubicación y otras repeticiones específicas de empresa. Las dimensiones tipadas o abiertas se tratan como dimensiones del entorno de ejecución gobernadas, no como una lista fija de indicadores precalculada.

Con esta interpretación, E1 representa aproximadamente `87,547` coordenadas de valor reportables de dimensión cerrada:

| Superficie de estándar | Coordenadas de valor reportables de dimensión cerrada | Base |
|---|---:|---|
| NEIS | `82,858` | Tablas dimensionales finitas de NEIS Set 1 XBRL expandidas; repeticiones de período y perímetro excluidas. |
| GRI | `4,399` | Filas del registro público SDS expandidas por dimensiones cerradas en el paquete derivado controlado de SDS para GRI. |
| Protocolo de GEI - cobertura técnica adicional voluntaria | `290` | Filas del registro público SDS expandidas por dimensiones cerradas en la extensión técnica voluntaria del Protocolo de GEI. |
| **Total** | **`87,547`** | Coordenadas de valor reportables de dimensión cerrada. |

Incluyendo los nodos internos de cálculo y contratos de soporte de SDS, la capacidad técnica más amplia representada por el mismo método es de aproximadamente `88,397` coordenadas de valor.

Esta cifra es un equivalente de campos de formulario plano, no una obligación de que todas las empresas reporten todos los valores ni una obligación de crear `87,547` indicadores maestros separados. El reporte real se reduce por materialidad, aplicabilidad, introducciones progresivas, contenidos alternativos, actividades no disponibles y contexto de empresa. SDS conserva los puntos de datos canónicos una sola vez y representa la granularidad de reporte requerida mediante dimensiones, miembros, controles de validación, fórmulas y coordenadas de valor.

El método de cálculo, las fórmulas, las variables, la aritmética agrupada y la reconciliación se registran en `deliverables/E01-mapeo-datos-normativas/evidence/dimension-expanded-value-coordinate-annex-v2026-06-05.md` y en su libro mayor de cálculo asociado `deliverables/E01-mapeo-datos-normativas/evidence/dimension-expanded-value-coordinate-calculation-v2026-06-05.csv`.

## KPI de preparación técnica

| Capa KPI | Estado SDS |
|---|---|
| Cobertura de línea base oficial | NEIS `100%` y GRI `100%` para el alcance mínimo de aceptación de E1; Protocolo de GEI `100%` para el alcance técnico adicional voluntario |
| Validación estructural y de preparación para cálculo | La evidencia de NEIS, GRI y Protocolo de GEI supera la validación estructural y de preparación para cálculo |
| Identificadores SDS duplicados | `0` en los conjuntos de evidencia de NEIS, GRI y Protocolo de GEI |
| Filas numéricas sin metadatos de unidad | `0` en los conjuntos de evidencia de NEIS, GRI y Protocolo de GEI |
| Etiquetas de unidad fuera del catálogo de unidades SDS | `0` en los conjuntos de evidencia de NEIS, GRI y Protocolo de GEI |
| Valores operativos | No incluidos; E1 cubre evidencia estructural, de catálogo y de cálculo, no evidencia de valores de empresa |
| Coordenadas de valor expandidas por dimensión | Total reportable de dimensión cerrada: `87,547`; capacidad técnica más amplia: `88,397`; repeticiones de período, perímetro, ubicación y datos maestros de empresa excluidas |

## Forma del inventario

La evidencia del inventario SDS utiliza tres capas:

1. Fuente oficial del estándar: las filas oficiales de inventario NEIS y las filas de la fuente técnica controlada de GRI definen el alcance mínimo de aceptación de E1; los alcances oficiales del Protocolo de GEI definen la cobertura técnica adicional voluntaria incluida por SDS. [@EU_ESRS_2023_2772] [@GRI_STANDARDS_2025] [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD]
2. Granulación y validación de indicadores: el proceso técnico define definiciones canónicas de puntos de datos publicables, reglas de validación, ejes dimensionales, fórmulas, reglas de soporte y degradaciones.
3. Representación técnica SDS: el registro normalizado de indicadores y los metadatos listos para cálculo definen la superficie orientada al sistema.

Esta separación evita una métrica de cobertura autorreferencial. El proceso de granulación puede descomponer una fila oficial en varios indicadores SDS o degradar contenedores no reportables; esos cambios mejoran la fidelidad técnica, pero no redefinen el denominador oficial.

## Limites de evidencia

E1 separa tres propósitos de evidencia:

| Propósito de evidencia | Papel en E1 |
|---|---|
| Evidencia de aceptación del proyecto | Demuestra que E1 satisface los requisitos de aceptación `R1` y `R2` mediante el artefacto público actual, los resúmenes de cobertura de estándares oficiales, el anexo de cálculo, el libro mayor agrupado y el control de validación de E1. |
| Evidencia operativa de servicio/catálogo | Da soporte al uso del entorno de ejecución y de catálogo de SDS, pero no redefine el denominador de los estándares oficiales. |
| Línea base técnica granulada | Registra los metadatos aceptados estructurales, de catálogo, de unidades y de preparación para cálculo de NEIS, GRI y cobertura adicional voluntaria del Protocolo de GEI resumidos en este entregable. |

Cualquier cambio en la línea base técnica de E1 requiere un artefacto canónico actualizado, suma de comprobación, revisión de privacidad y actualización de `deliverables/deliverables-register.csv`.

## Alcance técnico adicional

El requisito mínimo del proyecto es un inventario alineado con estándares con al menos un `90%` de cobertura sobre variables seleccionadas de la Directiva (UE) 2022/2464, relativa a la presentación de información sobre sostenibilidad por parte de las empresas, las NEIS y los Estándares GRI. El Protocolo de GEI no formaba parte de ese alcance mínimo de E1. E1 cubre el requisito mínimo y documenta por separado el siguiente alcance técnico adicional:

| Elemento de alcance adicional | Relación con el requisito mínimo del proyecto |
|---|---|
| Denominador completo de estándares oficiales | E1 distingue las filas oficiales NEIS y las filas de la fuente técnica controlada de GRI para el alcance mínimo de aceptación, más los alcances del Protocolo de GEI para el alcance técnico adicional voluntario. |
| Cobertura completa de indicadores granulares | Cada fila/alcance de la línea base oficial dentro del alcance técnico se vincula con evidencia fuente validada, no solo con una fila seleccionada del inventario. |
| Protocolo de GEI como tercer marco voluntario | El requisito mínimo de E1 nombra solo la Directiva (UE) 2022/2464, las NEIS y GRI. Los alcances oficiales del Protocolo de GEI se documentan como cobertura técnica adicional voluntaria y no se utilizan para satisfacer el umbral mínimo de aceptación `R1` / `R2`. [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD] |
| Metadatos listos para cálculo | E1 incorpora nodos de cálculo, estado, fórmulas, referencias a componentes, reglas de validación, ejes dimensionales y reglas de soporte a nivel de definición. |
| Normalización del catálogo de unidades | E1 valida todas las etiquetas de unidad contra el catálogo de unidades SDS y registra `0` etiquetas de unidad desconocidas para NEIS, GRI y Protocolo de GEI. |
| Preparación de evidencia técnica | E1 valida la evidencia mediante reglas de validación estructural y de preparación para cálculo. |
| Límite explícito de importación de valores | E1 establece que la línea base es evidencia estructural, de catálogo y de cálculo. Esto evita sobredeclarar importaciones operativas de valores de empresa. |

## Mapeo con el control de validación de aceptación

| Punto de aceptación | Superficie de evidencia pública |
|---|---|
| Cobertura normativa `>=90%` | La evidencia de cobertura aceptada cubre NEIS `1,200 / 1,200` filas oficiales de inventario y `99 / 99` alcances, más GRI `3,964 / 3,964` filas de la fuente técnica controlada y `573 / 573` alcances para el alcance mínimo de aceptación de E1. La cobertura del Protocolo de GEI es de `18 / 18` alcances técnicos voluntarios y no se utiliza para satisfacer el umbral mínimo. |
| Cobertura de dominio visible por grupo ESG | El inventario canónico SDS clasifica `1,242` definiciones NEIS, `3,666` definiciones GRI y `113` definiciones voluntarias del Protocolo de GEI por familia de estándar y área de sostenibilidad. |
| Sin variables huérfanas | La granulación controlada reconcilia los denominadores oficiales seleccionados con definiciones SDS validadas, con `0` identificadores SDS duplicados en los conjuntos de evidencia de NEIS, GRI y Protocolo de GEI. |
| Trazabilidad de backfill para filas a nivel de sección | La cobertura E1 actual y la trazabilidad de backfill vinculan filas/alcances oficiales con referencias fuente, trazabilidad de revisor y definiciones canónicas SDS. |
| Trazabilidad de revisor hasta el cierre de aceptación | El control de validación de E1 comprueba la presencia del artefacto canónico, el inventario CSV y el anexo; valida la estructura y los totales del libro mayor. La suma de comprobación y el registro se comprueban con `make deliverables-check`. |
| Línea base de curación entre estándares | NEIS, GRI y Protocolo de GEI voluntario se representan como `5,021` definiciones canónicas SDS y `5,818` nodos listos para cálculo en la línea base técnica. |
| Trazabilidad del registro de servicio | La superficie de registro orientada al servicio es la capa canónica de definiciones: NEIS `1,242`, GRI `3,666` y Protocolo de GEI voluntario `113` definiciones. |
| Línea base de indicadores granulares | La evidencia de coordenadas de valor reportables expandidas por dimensión suma `87,547`: NEIS `82,858`, GRI `4,399` y Protocolo de GEI voluntario `290`. |
| Preparación de evidencia técnica | La validación estructural y de preparación para cálculo fue superada; los nodos listos para cálculo suman NEIS `1,249`, GRI `4,252` y Protocolo de GEI voluntario `317`. |
| Preparación del catálogo de unidades | Filas numéricas sin metadatos de unidad: `0`; etiquetas de unidad fuera del catálogo de unidades SDS: `0` en los conjuntos de evidencia de NEIS, GRI y Protocolo de GEI voluntario. |
| Expansión de captura operativa | Los valores operativos de empresa se excluyen de los recuentos de E1. El equivalente plano a nivel de estándares es de `87,547` coordenadas de valor reportables y `88,397` coordenadas técnicas más amplias, antes de repeticiones de período, perímetro, centro, ubicación y datos maestros de empresa. |

## Estado de validación

E1 valida la estructura del inventario, la categorización y los totales del libro mayor para el alcance mínimo NEIS/GRI. Los porcentajes de cobertura normativa proceden de la evidencia técnica resumida en el entregable y no se recalculan dentro de `make e1-gate`; su promoción pública depende además de `make deliverables-check`. La línea base técnica de estándares oficiales es un resumen de metadatos cualificado por fecha para evidencia estructural, de catálogo y de preparación para cálculo de NEIS, GRI y cobertura adicional voluntaria del Protocolo de GEI. Sus recuentos de indicadores y nodos son recuentos a nivel de definición, no recuentos de instancias operativas de valor expandidas por dimensión.



# Anexo - Cálculo de coordenadas de valor expandidas por dimensión

Proyecto: Espacios de Datos de Sostenibilidad (SDS)
Entregable: E1 - Inventario de datos clave y variables brutas
Versión del anexo: V2026-06-05
Fecha: 2026-06-05
Estado: anexo de evidencia de cálculo

## 1. Propósito

Este anexo explica como SDS convierte puntos de datos canónicos de sostenibilidad y
nodos de contratos de cálculo en un equivalente plano de coordenadas de valor.
El cálculo se utiliza para explicación de auditoría, dimensionamiento de
implementación y comparación con sistemas que crean un campo rellenable por cada
celda de matriz.

Este anexo no redefine el denominador oficial de estándares utilizado para la
aceptación de E1. El alcance formal de aceptación de E1 sigue siendo la línea
base aceptada de cobertura NEIS y GRI. El Protocolo de GEI se incluye aquí como cobertura técnica adicional voluntaria y no se utiliza para satisfacer el umbral mínimo de aceptación de E1.

## 2. Archivos de evidencia

Este anexo se apoya en el libro mayor de cálculo legible por máquina:

- `dimension-expanded-value-coordinate-calculation-v2026-06-05.csv`

El CSV es la columna vertebral de auditoría. Contiene las filas de cálculo
agrupadas utilizadas para reproducir los totales principales, incluidos
estándar, versión de fuente, grupo, recuento de nodos base, dimensiones,
recuentos de miembros, fórmula, coordenadas reportables, coordenadas técnicas,
exclusiones y localizadores de fuente.

## 3. Entradas fuente

| Superficie | Entrada fuente | Papel en el cálculo |
|---|---|---|
| NEIS | Taxonomía XBRL de NEIS Set 1 de EFRAG, paquete de publicación fechado el 2024-08-30 | Aporta tablas dimensionales finitas, códigos de rol, partidas, ejes y miembros. |
| GRI | Paquete derivado controlado de SDS del registro GRI cerrado generado el 2026-05-22 | Aporta nodos de registro público y dimensiones de contrato de cálculo. |
| Protocolo de GEI | Paquete derivado controlado de SDS para Protocolo de GEI cerrado generado el 2026-05-22 | Aporta nodos de registro público voluntario y dimensiones de contrato de cálculo. |

El cálculo NEIS utiliza las linkbases de definición de la taxonomía. Los
cálculos GRI y GEI utilizan el contrato de cálculo SDS porque el registro SDS es
la superficie pública de indicadores y el contrato de cálculo es la superficie
semántica del entorno de ejecución para dimensiones, fórmulas, controles de validación y nodos de soporte.

## 4. Límite de recuento

El recuento es un equivalente plano de coordenadas de valor a nivel de
estándares. Expande matrices finitas de estándar, pero no multiplica por período
de reporte ni por repeticiones específicas de empresa.

Incluido:

- puntos de datos reportables y valores calculados reportables;
- dimensiones cerradas finitas con listas controladas de miembros;
- nodos de contrato de cálculo SDS cuando se informa del recuento más amplio de
  capacidad técnica;
- salidas calculadas o agregadas cuando SDS las almacena o resuelve como
  coordenadas de valor.

Excluido del titular reportable:

- período de reporte;
- perímetro de entidad;
- centro, ubicación, instalación, activo, proveedor, producto, ruta y otras
  repeticiones de datos maestros de empresa;
- dimensiones tipadas/abiertas más allá de un marcador de posición del entorno de ejecución gobernado;
- representaciones técnicas duplicadas;
- contenedores no reportables.

Para NEIS, el eje actual/reexpresión-anterior `ReportingScopeAxis` se excluye
del titular porque es un eje técnico de reexpresión, no un denominador normal de
captura separado para este ejercicio de dimensionamiento.

## 5. Diccionario de variables

| Variable | Significado |
|---|---|
| `base_nodes` | Recuento de partidas reportables, filas del registro público o nodos de contrato de cálculo en el grupo. |
| `closed_dimension_axis` | Dimensión finita con lista controlada de miembros. |
| `member_count(axis)` | Numero de miembros permitidos contabilizados para una dimensión cerrada del grupo. |
| `closed_multiplier` | Producto de todos los recuentos de miembros de dimensión cerrada del grupo. |
| `typed_open_axis` | Dimensión específica de empresa o abierta, contabilizada como `1` marcador de posición a nivel de estándares. |
| `reportable_coordinates` | Recuento plano de coordenadas de valor para la superficie pública/reportable. |
| `technical_coordinates` | Recuento plano de coordenadas de valor para la superficie más amplia del contrato de cálculo SDS. |
| `excluded_repetitions` | Repeticiones de período, perímetro, ubicación y datos maestros de empresa excluidas deliberadamente del recuento a nivel de estándares. |

## 6. Formulas

Para un nodo o grupo de partidas:

```text
closed_multiplier = product(member_count(axis) for each closed_dimension_axis)
```

```text
expanded_coordinates(group) = base_nodes * closed_multiplier
```

Para dimensiones tipadas/abiertas:

```text
member_count(typed_open_axis) = 1
```

Para un estándar:

```text
standard_reportable_total = sum(reportable_coordinates(group))
```

Para el titular E1 reportable expandido por dimensión:

```text
reportable_total = ESRS_reportable + GRI_reportable + GHG_reportable
```

```text
reportable_total = 82,858 + 4,399 + 290 = 87,547
```

Para el recuento más amplio de capacidad técnica SDS:

```text
technical_total = ESRS_technical + GRI_contract + GHG_contract
```

```text
technical_total = 82,858 + 4,985 + 554 = 88,397
```

## 7. Regla de cálculo NEIS

Para NEIS, cada rol de divulgación XBRL se contabiliza como:

```text
ESRS_role_coordinates = non_abstract_line_items * product(finite_axis_member_counts)
```

El titular NEIS utiliza los siguientes controles:

- se excluyen los roles auxiliares y de enumeración;
- las partidas no abstractas se cuentan como nodos base;
- los ejes finitos se expanden por sus recuentos de miembros;
- los ejes tipados se cuentan como `1`;
- se excluye `ReportingScopeAxis`;
- se excluyen repeticiones de período, perímetro, entidad, ubicación y datos
  maestros de empresa;
- determinados ejes de objetivos climáticos E1 y trayectorias de emisiones
  cuentan miembros explícitos de línea base/hito/objetivo sin duplicar el
  miembro actual/raíz abstracto ya representado por la coordenada base de
  divulgación.

El último control se aplica a los roles de taxonomía NEIS `301042` a `301048` y
`301064` a `301066`. Evita el doble recuento de la coordenada de estado actual en
tablas de objetivos E1 y trayectorias de emisiones.

### Reconciliación NEIS

| Área NEIS | Coordenadas reportables |
|---|---:|
| ESRS 2 | 711 |
| E1 | 33,433 |
| E2 | 44,929 |
| E3 | 411 |
| E4 | 490 |
| E5 | 430 |
| S1 | 1,185 |
| S2 | 259 |
| S3 | 263 |
| S4 | 252 |
| G1 | 491 |
| Otras | 4 |
| **Total NEIS** | **82,858** |

### Ejemplos de cálculo NEIS

Tabla de contaminantes:

```text
coordinates = line_items * current/baseline/target_members * range_members * pollutant_members
coordinates = 3 * 3 * 3 * 92 = 2,484
```

Tabla de sustancias preocupantes:

```text
coordinates = line_items * substance_flow_members * hazard_category_members * hazard_class_members * current/baseline/target_members
coordinates = 12 * 6 * 9 * 18 * 3 = 34,992
```

Tabla de trayectoria de objetivos E1:

```text
coordinates = line_items * decarbonisation_lever_members * ghg_category_members * baseline/milestone/target_members
coordinates = 24 * 9 * 9 * 8 = 15,552
```

La aritmética agrupada completa de NEIS está en el libro mayor CSV. Cada fila
CSV registra los roles de taxonomía pertinentes, nodos base, dimensiones,
recuentos de miembros, fórmula y subtotal.

## 8. Regla de cálculo GRI

Para GRI, el titular reportable expande los nodos del registro público SDS por
las dimensiones cerradas declaradas en el contrato de cálculo controlado de GRI:

```text
GRI_reportable_coordinates(group) = public_register_nodes * product(closed_dimension_member_counts)
```

Para el recuento más amplio de capacidad técnica, la misma regla se aplica a
todos los nodos de contrato de cálculo GRI:

```text
GRI_technical_coordinates(group) = contract_nodes * product(closed_dimension_member_counts)
```

Las dimensiones tipadas/abiertas se mantienen como un marcador de posición del entorno de ejecución
gobernado:

```text
member_count(typed_open_dimension) = 1
```

### Reconciliación GRI

| Superficie GRI | Coordenadas |
|---|---:|
| Coordenadas reportables del registro público | 4,399 |
| Coordenadas técnicas del contrato de cálculo completo | 4,985 |
| Filas del registro público | 3,666 |
| Nodos de contrato de cálculo | 4,252 |

### Ejemplos de cálculo GRI

Grupo de recuento de empleados GRI 2-7:

```text
coordinates = nodes * gender_members * employee_count_method_members * employee_count_period_basis_members
coordinates = 1 * 4 * 3 * 3 = 36
```

Extracción de agua GRI por fuente y estado de estrés hídrico:

```text
coordinates = nodes * water_source_members * water_stress_area_members
coordinates = 1 * 5 * 2 = 10
```

Grupo de categorías de Alcance 3 GRI:

```text
coordinates = nodes * scope3_category_members
coordinates = 1 * 16 = 16
```

La aritmética agrupada completa de GRI está en el libro mayor CSV. Las filas
reportables y las filas del contrato técnico se separan para que el titular
público y la capacidad técnica más amplia puedan comprobarse de forma
independiente.

## 9. Regla de cálculo del Protocolo de GEI

El Protocolo de GEI se incluye como cobertura técnica adicional voluntaria. No
forma parte del umbral mínimo de aceptación de E1.

Para el titular reportable del Protocolo de GEI:

```text
GHG_reportable_coordinates(group) = public_register_nodes * product(closed_dimension_member_counts)
```

Para el recuento más amplio de capacidad técnica:

```text
GHG_technical_coordinates(group) = contract_nodes * product(closed_dimension_member_counts)
```

Las dimensiones tipadas/abiertas, como fuente de factor, proveedor, fuente de
actividad o identificadores de fuente específicos de empresa, se cuentan como
un marcador de posición del entorno de ejecución gobernado a nivel de estándares.

### Reconciliación del Protocolo de GEI

| Superficie del Protocolo de GEI | Coordenadas |
|---|---:|
| Coordenadas reportables del registro público | 290 |
| Coordenadas técnicas del contrato de cálculo completo | 554 |
| Filas del registro público | 113 |
| Nodos de contrato de cálculo | 317 |

### Ejemplos de cálculo del Protocolo de GEI

Grupo de categorías de Alcance 3:

```text
coordinates = nodes * scope3_category_members
coordinates = 1 * 16 = 16
```

Emisiones requeridas de gases por alcance:

```text
coordinates = nodes * ghg_scope_members * ghg_gas_members
coordinates = 1 * 2 * 7 = 14
```

Grupo de método de Alcance 2:

```text
coordinates = nodes * scope2_calculation_method_members
coordinates = 1 * 2 = 2
```

La aritmética agrupada completa del Protocolo de GEI está en el libro mayor CSV.

## 10. Tratamiento de dimensiones abiertas

Las dimensiones abiertas o tipadas no se aplanan en un recuento universal de
indicadores a nivel de estándares. Algunos ejemplos son:

- `site_id`;
- `supplier_id`;
- `product_id`;
- `route_id`;
- `asset_id`;
- `emission_factor_id`;
- `facility_id`;
- `country` o `region` cuando el estándar no proporciona una lista cerrada
  finita en la superficie de cálculo.

Para el recuento de auditoría:

```text
member_count(open_dimension) = 1
```

Para el reporte operativo:

```text
operational_value_rows = standards_coordinate * actual_company_members
```

Ejemplo:

```text
Valor de categoría de Alcance 3 por supplier_id
```

El recuento a nivel de estándares mantiene `supplier_id = 1` como marcador de posición.
Una empresa con 200 proveedores relevantes instanciaría filas operativas a
nivel de proveedor solo cuando ese punto de datos sea aplicable.

Esto mantiene el recuento a nivel de estándares acotado y auditable, al tiempo
que conserva toda la granularidad del entorno de ejecución.

## 11. Resultado principal

| Superficie de estándar | Coordenadas reportables de dimensión cerrada | Coordenadas técnicas más amplias | Notas |
|---|---:|---:|---|
| NEIS | 82,858 | 82,858 | Dimensiones finitas de NEIS Set 1 XBRL expandidas; repeticiones de período/perímetro excluidas. |
| GRI | 4,399 | 4,985 | Titular del registro público más capacidad completa del contrato de cálculo. |
| Protocolo de GEI | 290 | 554 | Cobertura técnica adicional voluntaria; titular del registro público más capacidad completa del contrato de cálculo. |
| **Total** | **87,547** | **88,397** | Repeticiones de período, perímetro, ubicación y datos maestros de empresa excluidas. |

## 12. Interpretación

El total de `87,547` coordenadas reportables es un equivalente de campos de
formulario plano. Aproxima cuantas posiciones de valor individuales necesitaría
un cuestionario plano o una implementación de indicador por celda para
representar la misma cobertura finita de estándares.

No es una declaración de que todas las empresas informantes deban presentar los
`87,547` valores. El reporte real se reduce por materialidad, aplicabilidad,
introducciones progresivas, contenidos alternativos, actividades no disponibles
y contexto de empresa.

Tampoco es una obligación de crear `87,547` indicadores maestros separados. SDS
almacena los puntos de datos canónicos una sola vez y aplica dimensiones, miembros,
controles de validación, fórmulas y coordenadas de valor del entorno de ejecución. Esto evita la explosión de
indicadores a la vez que preserva auditabilidad y granularidad completa de
reporte.

## 13. Procedimiento de verificación

Para verificar los totales del anexo:

1. Abrir `dimension-expanded-value-coordinate-calculation-v2026-06-05.csv`.
2. Sumar `reportable_coordinates` donde `standard = ESRS`.
3. Sumar `reportable_coordinates` donde `standard = GRI`.
4. Sumar `reportable_coordinates` donde `standard = GHG Protocol`.
5. Confirmar:

```text
ESRS = 82,858
GRI = 4,399
GHG Protocol = 290
reportable_total = 87,547
```

6. Sumar las coordenadas del contrato técnico para GRI y GHG Protocol y
   combinarlas con el total ESRS:

```text
ESRS_technical = 82,858
GRI_technical = 4,985
GHG_technical = 554
technical_total = 88,397
```

El CSV se agrupa intencionadamente por firma de cálculo en lugar de expandirse
a una fila por celda de valor final. Esto mantiene legible el archivo de
auditoría y, aun asi, hace reproducibles cada multiplicación y subtotal.



## Referencias



::: {#refs}

:::
