# E2 - Informe de interoperabilidad y correspondencias

Tabla 1  Ficha del entregable E2

| Campo | Valor |
|---|---|
| Proyecto | Sustainability Data Spaces (SDS) |
| Entregable | E2 - Informe de interoperabilidad y correspondencias |
| Paquete de trabajo | PT1 - Mapeo de Datos, Normativas y Estándares |
| Actividad principal | A1.3 - Mapeo de relaciones entre estándares para garantizar interoperabilidad |
| Versión | V1.0 |
| Fecha | 2026-06-18 |
| Estado | Documento final |
| Responsable | Oficina del Programa SDS |
## Resumen

E2 define la capa de interoperabilidad SDS para mapear coincidencias, diferencias y relaciones gobernadas entre estándares de presentación de información sobre sostenibilidad. Cumple los requisitos de aceptación del proyecto relativos al mapeo de correspondencias entre estándares y a las recomendaciones técnicas, y define el modelo activo de relaciones SDS para anclajes de NEIS (ESRS en inglés), GRI y Protocolo de GEI (GHG Protocol). [@EU_CSRD_2022_2464] [@EU_ESRS_2023_2772] [@GRI_STANDARDS_2025] [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD]

E2 es distinto de E1. E1 mide la cobertura de estándares oficiales; E2 mide la cobertura de relaciones revisadas entre estándares. La cobertura completa de NEIS, GRI y Protocolo de GEI en E1 no implica automáticamente interoperabilidad completa en E2. E2 solo cuenta relaciones que han sido revisadas, tipadas y evidenciadas.

El dossier cita CSRD, GRI, SASB, ISSB y TCFD como universo descriptivo del análisis. La superficie interoperable implementada hoy en SDS se limita a NEIS/ESRS, GRI y la extensión técnica del Protocolo de GEI. SASB, ISSB y TCFD se reconocen como marcos relevantes, pero no están instalados en el catálogo operativo y no disponen de relaciones activas en esta versión. Su incorporación futura deberá seguir el mismo proceso de fuente oficial, tipado de relaciones, evidencia, revisión y publicación controlada; esta delimitación evita presentar como implementada una cobertura que el código y los paquetes actuales no ofrecen.

| Marco citado en el dossier | Estado en la versión actual | Consecuencia operativa |
|---|---|---|
| CSRD / NEIS (ESRS) | Implementado como anclaje principal | Puede participar en relaciones revisadas, cálculos y resolución según contrato. |
| GRI | Implementado como anclaje principal | Puede participar en relaciones revisadas y rutas autorizadas. |
| Protocolo de GEI | Extensión técnica implementada | Cobertura adicional, separada del mínimo contractual. |
| SASB | Reconocido; no instalado | No genera mapeos, cálculos ni rutas activas. |
| ISSB | Reconocido; no instalado | No genera mapeos, cálculos ni rutas activas. |
| TCFD | Reconocido; no instalado | No genera mapeos, cálculos ni rutas activas. |

## Evidencia pública

La superficie de evidencia pública de E2 es:

- Estándares públicos externos: Directiva (UE) 2022/2464, relativa a la presentación de información sobre sostenibilidad por parte de las empresas, NEIS, Estándares GRI y estándares corporativos y de cadena de valor del Protocolo de GEI. [@EU_CSRD_2022_2464] [@EU_ESRS_2023_2772] [@GRI_STANDARDS_2025] [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD]
- Evidencia de entregable del proyecto: el artefacto público E02 y la fila E02 en `deliverables/deliverables-register.csv`, que registra la ruta canónica, la versión, la suma de comprobación, la clase de privacidad y la referencia de fuente saneada.
- Contexto entre entregables: el artefacto público E01 registra la línea base de estándares oficiales que distingue la cobertura de estándares de la interoperabilidad revisada.

Las instantáneas internas de verificación y los controles de validación de calidad siguen formando parte de la traza de evidencia de ingeniería. No se enumeran aquí como evidencia pública del entregable.

## Criterios de aceptación del proyecto

| Requisito | Forma de aceptación pública |
|---|---|
| `R3` Mapeo completo entre estándares | El informe mapea al menos el `75%` de los puntos clave de interoperabilidad identificados entre los estándares seleccionados y presenta los resultados en tablas o diagramas. |
| `R4` Recomendaciones técnicas | El informe proporciona recomendaciones técnicas claras y accionables para transformar y alinear datos entre estándares. |
| Detección de temas | El conjunto aceptado de métricas por tema mantiene al menos una referencia de marco para al menos el `90%` de las filas revisadas. |
| Sin equivalencia forzada | Las relaciones no equivalentes y ausentes permanecen explícitas para que las declaraciones públicas de interoperabilidad no se exageren. |

## Métricas aceptadas por tema

| Tema | Cobertura | Detección | Control de validación de cobertura | Control de validación de detección |
|---|---:|---:|---|---|
| Energía | `80 / 85` (94%) | `85 / 85` (100%) | PASS | PASS |
| GEI | `146 / 154` (95%) | `154 / 154` (100%) | PASS | PASS |
| Agua | `43 / 53` (81%) | `53 / 53` (100%) | PASS | PASS |

Estas métricas satisfacen el control de validación de cobertura de relaciones `>=75%` y el control de validación de detección `>=90%` para el conjunto de temas Energía, GEI y Agua en el alcance seleccionado de NEIS y GRI. [@EU_ESRS_2023_2772] [@GRI_STANDARDS_2025]

El CSV fuente contiene `1.501` filas. El proyecto seleccionó `292` para el alcance medido en este entregable (`85` Energía, `154` GEI y `53` Agua), equivalente al `19,45%` del conjunto fuente; las `1.209` filas restantes están etiquetadas como `other` y no forman parte del denominador del gate temático. El cierre vigente de proyecto, registrado el 2026-09-18, acepta ese alcance temático y el cumplimiento R3; la cifra se conserva como contexto metodológico, no como reserva abierta.

## Modelo de línea base de relaciones

El reporte E2 utiliza registros de relaciones revisadas, no recuentos brutos de indicadores. La línea base de relaciones contiene estos campos obligatorios:

| Campo | Significado |
|---|---|
| Estándar fuente e ID oficial | Anclaje fuente NEIS, GRI o Protocolo de GEI procedente de la línea base oficial |
| Estándar destino e ID oficial | Anclaje oficial destino que se compara |
| Tipo de relación de revisión | El vocabulario de análisis conserva `equivalent`, `partial`, `broader`, `narrower`, `component`, `transform`, `support_context`, `no_match` y estados pendientes. Solo `equivalent`, `partial`, `broader` y `narrower` pueden alimentar la superficie operacional general; los demás permanecen como evidencia de revisión o requieren una ruta específica autorizada. |
| Estado de cobertura | `accepted`, `rejected`, `pending_missing_standard`, `pending_evidence` o `pending_review` |
| Evidencia | Evidencia fuente, validación técnica o nota de revisión controlada |
| Estado de activación | solo informe, vista previa, importado, materializado o retirado |

Por tanto, los KPI de E2 son:

| KPI | Definición |
|---|---|
| Cobertura de revisión de candidatos | Candidatos de relación revisados / universo candidato de relaciones |
| Recuento de relaciones aceptadas | Mapeos aceptados por tipo de relación |
| Recuento de equivalencias exactas | Solo relaciones estrictas `equivalent` |
| Cobertura de transformaciones/componentes | Relaciones que requieren fórmulas, componentes, denominadores o reglas de soporte |
| Recuento explícito de no coincidencias | No mapeos revisados conservados para evitar falsas equivalencias |
| Recuento pendiente | Candidatos bloqueados por evidencia ausente, estándar ausente o revisión pendiente |

Las líneas base oficiales establecen NEIS, GRI y Protocolo de GEI como anclajes disponibles para la revisión de relaciones, pero por sí solas no crean relaciones aceptadas entre estándares. [@EU_ESRS_2023_2772] [@GRI_STANDARDS_2025] [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD]

## Capacidades de interoperabilidad

E2 proporciona estas capacidades de interoperabilidad gobernada de SDS:

- publicación de relaciones revisadas entre estándares;
- validación de que cada relación referencia estándares disponibles en el entorno SDS de destino;
- revisión controlada antes de que las relaciones pasen a estar activas en comportamiento de mapeo, cálculo, búsqueda o exportación;
- separación entre relaciones operacionales (`equivalent`, `partial`, `broader`, `narrower`) y vocabulario de revisión (`component`, `transform`, `support_context`, `no_match` y estados pendientes);
- tratamiento sin falsas equivalencias mediante no coincidencias explícitas y tipos de relación no equivalentes;
- anclajes de equivalencia exacta del Protocolo de GEI para determinadas relaciones de Alcance 1 y Alcance 2; y
- soporte de transformación con control de unidades y de cálculo para relaciones que no son equivalencias estrictas.

## Capas publicadas de mapeo de correspondencias

| Capa pública | Papel |
|---|---|
| Correspondencia temática NEIS-GRI | Evidencia de relaciones aceptadas y clasificadas por tema para el alcance Energía, GEI y Agua |
| Capa de modelado granulado | Evidencia a nivel de relación con pistas de modelado aguas abajo sobre unidad, dimensión y modelado |
| Línea base de completitud | Evidencia aceptada de completitud para el control de validación de cobertura E2 |
| Línea base de detección | Evidencia aceptada de detección para el control de validación de detección E2 |
| Capa de equivalencia exacta del Protocolo de GEI | Evidencia adicional para anclajes revisados de equivalencia exacta en Alcance 1 y Alcance 2 |

## Ejemplos de mapeo

E2 trata de **correspondencia trazable**, no de equivalencia forzada. Estos ejemplos aptos para página capturan los patrones de relación que SDS soporta.

### Ejemplo 1 a 1

`E1-5_06` mapea limpiamente con `GRI 302-1.b`.

| Fuente | Relación | Destino |
|---|---|---|
| `E1-5_06` | mapea limpiamente con | `GRI 302-1.b` |

### Ejemplo 1 a n

`ESRS E3-5` mapea con varios anclajes de contenidos GRI en la línea base aceptada del tema Agua.

| Fuente | Relación | Destino |
|---|---|---|
| `ESRS E3-5` | mapea con | `GRI 303-3` |
| `ESRS E3-5` | mapea con | `GRI 303-4` |
| `ESRS E3-5` | mapea con | `GRI 303-5` |

### Ejemplo n a 1

Varios puntos de datos de energía NEIS reutilizan el mismo anclaje GRI `GRI 302-1`.

| Fuente | Relación | Destino |
|---|---|---|
| `E1-5_04` | reutiliza anclaje | `GRI 302-1` |
| `E1-5_09` | reutiliza anclaje | `GRI 302-1` |
| `E1-5_15` | reutiliza anclaje | `GRI 302-1` |

### Ejemplo 1 a ninguno

Los casos de brecha permanecen explícitos en lugar de forzarse en mapeos falsos. Ejemplo: `E3-5_01`.

| Fuente | Relación | Destino/estado |
|---|---|---|
| `E3-5_01` | sin mapeo aceptado | Sin contraparte mapeada aceptada en la línea base de relaciones |

### Ejemplo de transformación

Algunas relaciones no son equivalencias. Requieren fórmulas, política de denominador, política de unidad o lógica de componentes antes de poder utilizarse operativamente.

| Paso | Papel |
|---|---|
| Punto de datos fuente | Anclaje de entrada |
| Fórmula o contrato de transformación | Lógica de transformación, política de denominador, política de unidad o lógica de componentes |
| Punto de datos destino | Anclaje de salida |

## Capa de equivalencia exacta del Protocolo de GEI

E2 incluye una capa controlada de equivalencia exacta del Protocolo de GEI para correspondencias revisadas de Alcance 1 y Alcance 2. [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD]

Esta capa registra correspondencias exactas revisadas para:

- Emisiones totales de GEI de Alcance 1.
- Emisiones de GEI de Alcance 2 basadas en ubicación.
- Emisiones de GEI de Alcance 2 basadas en mercado.

Los candidatos de Alcance 3 y no equivalentes se excluyen deliberadamente de las declaraciones de equivalencia estricta porque esas relaciones requieren un juicio más amplio y no deben presentarse como equivalencias directas.

## Casos de uso prácticos de interoperabilidad

Los ejemplos siguientes conectan una necesidad empresarial con el estado real de la implementación. No convierten una similitud temática en equivalencia automática.

### E2-CU01 — Energía total para NEIS/ESRS y GRI

| Elemento | Descripción |
|---|---|
| Entrada | Valores energéticos operativos por fuente, entidad, periodo y unidad. |
| Tratamiento | Normalización a MWh, ejecución del contrato de suma de componentes y resolución mediante un puente certificado cuando sus precondiciones se cumplen. |
| Salida | Resultado NEIS/ESRS `E1-5_02` y reutilización autorizada hacia GRI `302-1.e`. |
| Estado | **ejecutable y verificado** para el conjunto de datos de demostración y los contratos actuales. |
| Límite | Si faltan componentes, unidad compatible, periodo o puente autorizado, la ruta devuelve rechazo estructurado. |

### E2-CU02 — Emisiones GEI por alcance

| Elemento | Descripción |
|---|---|
| Entrada | Actividad, factor de emisión, potencial de calentamiento, alcance, método y perímetro. |
| Tratamiento | Cálculo a tCO2e y agregación por alcance; una correspondencia solo puede reutilizarse si método, perímetro y componentes son compatibles. |
| Salida | Anclajes NEIS/ESRS E1, GRI 305 y Protocolo de GEI según la relación revisada. |
| Estado | **diseño controlado; piloto pendiente** para demostrar el recorrido completo con observaciones sectoriales. |
| Límite | Alcance 3, exclusiones y diferencias metodológicas no se publican como equivalencia exacta sin evidencia específica. |

### E2-CU03 — Agua: extracción, vertido y consumo

| Elemento | Descripción |
|---|---|
| Entrada | Lecturas de extracción y vertido, fuente/destino, cuenca, unidad, entidad y periodo. |
| Tratamiento | Conversión compatible de unidades, cálculo `consumo = extracción - vertido` cuando el contrato lo permite y conservación de dimensiones de estrés hídrico. |
| Salida | Anclajes NEIS/ESRS E3 y GRI 303 con relación y condiciones explícitas. |
| Estado | **diseño controlado; piloto pendiente**; la preparación y los mapeos no equivalen a un piloto hídrico ejecutado. |
| Límite | Si una salida necesita un desglose que el total fuente no conserva, SDS debe emitir rechazo estructurado o solicitar componentes; no debe fabricar la dimensión. |

## Recomendaciones técnicas

1. Verificar que ambos estándares estén instalados antes de importar o materializar una relación; los estándares ausentes deben permanecer en `pending_missing_standard`.
2. Limitar la materialización general a `equivalent`, `partial`, `broader` y `narrower`; conservar componentes, transformaciones, contexto de soporte y no coincidencias en la capa de revisión salvo que exista una ruta específica autorizada.
3. Exigir unidad, periodo, perímetro, método y dimensiones compatibles antes de reutilizar un valor entre marcos.
4. Asociar toda transformación a un contrato versionado con componentes, fórmula, política de unidad, autoridad de ejecución y pruebas de rechazo.
5. Mantener fallo cerrado: ante evidencia insuficiente, estándar ausente o relación pendiente, devolver rechazo estructurado en lugar de producir una equivalencia aproximada.
6. Asignar al custodio semántico la aprobación de relaciones, al responsable del dato la suficiencia de los componentes y al operador técnico la ejecución y observabilidad de la ruta.
7. Convertir E2-CU02 y E2-CU03 en pilotos reproducibles antes de describirlos como capacidades operativas verificadas.

## Alcance técnico adicional

El requisito mínimo del proyecto E2 es un informe de interoperabilidad NEIS/GRI con al menos un `75%` de cobertura sobre puntos clave de interoperabilidad identificados, más recomendaciones técnicas. E2 cubre ese requisito y documenta el siguiente alcance técnico adicional:

| Elemento de alcance adicional | Relación con el requisito mínimo del proyecto |
|---|---|
| Protocolo de GEI añadido al alcance de interoperabilidad | El requisito mínimo de E2 nombra la Directiva (UE) 2022/2464, las NEIS y GRI. La evidencia de equivalencia exacta del Protocolo de GEI y las líneas base GEI añaden cobertura de marco. [@EU_CSRD_2022_2464] [@EU_ESRS_2023_2772] [@GRI_STANDARDS_2025] [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD] |
| Taxonomía de relaciones | SDS distingue `equivalent`, `broader`, `narrower`, `component`, `transform`, `support_context`, `no_match` y estados pendientes en lugar de aplanar toda relación a un mapeo simple. |
| Regla de no falsa equivalencia | Las no coincidencias revisadas y las relaciones no equivalentes se conservan como evidencia para que los sistemas aguas abajo no sobredeclaren interoperabilidad. |
| Validación controlada de relaciones | La evidencia de relaciones se valida antes de que pueda convertirse en comportamiento activo de mapeo. |
| Validación de estándares instalados | La evidencia de relaciones se comprueba contra los estándares disponibles en el entorno SDS de destino; las filas de estándares ausentes permanecen pendientes y no pueden alimentar mapeos en vivo. |
| Publicación operativa de mapeos | Los grupos de relaciones revisadas pueden publicarse en el comportamiento de mapeo del producto. |
| Transformaciones con control de cálculo | Las relaciones de transformación/componente pueden conectarse a fórmulas, denominadores, metadatos de unidad y estado. |
| Soporte de relaciones con unidades normalizadas | Las etiquetas de unidad se normalizan al catálogo de unidades SDS para NEIS, GRI y Protocolo de GEI, lo que reduce ambigüedad en las comprobaciones de relaciones y transformaciones. |

## Mapeo con el control de validación de aceptación

| Punto de aceptación | Evidencia pública del proyecto |
|---|---|
| Completitud temática `>=75%` | Artefacto público E02 y fila E02 en `deliverables/deliverables-register.csv` |
| Detección temática `>=90%` | Artefacto público E02 y fila E02 en `deliverables/deliverables-register.csv` |
| Publicación del mapeo de correspondencias | Artefacto público E02 y fila E02 en `deliverables/deliverables-register.csv` |
| Capa de modelado enriquecida | Artefacto público E02 y fila E02 en `deliverables/deliverables-register.csv` |
| Continuidad de línea base entre estándares | Artefacto público E02 y fila E02 en `deliverables/deliverables-register.csv` |
| Evidencia de equivalencia exacta del Protocolo de GEI | Artefacto público E02 y fila E02 en `deliverables/deliverables-register.csv` |
| Superficie de publicación de mapeos | La evidencia de relaciones revisadas está disponible para el comportamiento activo de mapeo del producto |
| Guardarraíl de validación de mapeos | La evidencia de relaciones se valida antes de la publicación |
| Regla de no autoinstalación | Las filas de estándares ausentes permanecen pendientes y no pueden convertirse en mapeos en vivo |

## Estado de validación

E2 valida los umbrales de completitud y detección del proyecto a partir de la fuente revisada de mapeo de correspondencias, y comprueba que la evidencia resumida permanezca sincronizada. La validación de relaciones permanece separada de la cobertura de estándares oficiales de E1 porque E2 mide interoperabilidad revisada, no cobertura bruta de indicadores.

## Referencias

- Parlamento Europeo y Consejo. Directiva (UE) 2022/2464, de 14 de diciembre de 2022, por la que se modifican el Reglamento (UE) n.º 537/2014, la Directiva 2004/109/CE, la Directiva 2006/43/CE y la Directiva 2013/34/UE por lo que respecta a la presentación de información sobre sostenibilidad por parte de las empresas. Diario Oficial de la Unión Europea, 2022. https://eur-lex.europa.eu/eli/dir/2022/2464/oj. [@EU_CSRD_2022_2464]
- Comisión Europea. Reglamento Delegado (UE) 2023/2772 de la Comisión, de 31 de julio de 2023, por el que se completa la Directiva 2013/34/UE en lo relativo a las normas de presentación de información sobre sostenibilidad. Diario Oficial de la Unión Europea, 2023. https://eur-lex.europa.eu/eli/reg_del/2023/2772/oj. [@EU_ESRS_2023_2772]
- Global Reporting Initiative. Estándares GRI: conjunto completo de Estándares GRI. Ámsterdam: Global Reporting Initiative, 2025. https://www.globalreporting.org/standards/gri-standards-download-center/gri-standards/. [@GRI_STANDARDS_2025]
- Greenhouse Gas Protocol. El Protocolo de Gases de Efecto Invernadero: estándar corporativo de contabilidad y reporte, edición revisada. World Resources Institute and World Business Council for Sustainable Development. https://ghgprotocol.org/corporate-standard. [@GHG_PROTOCOL_CORPORATE_STANDARD]
- Greenhouse Gas Protocol. Estándar corporativo de contabilidad y reporte de la cadena de valor (Alcance 3). World Resources Institute and World Business Council for Sustainable Development, 2011. https://ghgprotocol.org/corporate-value-chain-scope-3-standard. [@GHG_PROTOCOL_SCOPE3_STANDARD]
