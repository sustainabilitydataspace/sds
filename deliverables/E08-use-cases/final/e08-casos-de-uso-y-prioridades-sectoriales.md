# Entregable E8 — Casos de uso y prioridades sectoriales

Tabla 1  Ficha del entregable E8

| Campo | Valor |
|---|---|
| Proyecto | Sustainability Data Spaces (SDS) |
| Entregable | E8 - Casos de uso y prioridades sectoriales |
| Paquete de trabajo | PT4 - Talleres con partes interesadas |
| Actividad principal | A4.3 - Recopilación de casos de uso prioritarios y retos sectoriales |
| Versión | V1.0 |
| Fecha | 2026-06-18 |
| Estado | Documento final |
| Responsable | Oficina del Programa SDS |
## 1. Resumen ejecutivo

Este entregable define un catálogo de casos de uso y una matriz de priorización para orientar el despliegue sectorial de SDS. El catálogo organiza los casos de uso según su capacidad para generar información de sostenibilidad comparable, trazable y reutilizable.

La priorización combina valor regulatorio, viabilidad técnica y urgencia sectorial. La base técnica se formula mediante conjuntos de datos, indicadores, relaciones, contratos de cálculo, reglas de unidades, catálogos, políticas, términos de producto y rutas de resolución de valores.

Cuando el documento menciona NEIS/ESRS se refiere a las Normas Europeas de Información sobre Sostenibilidad y a su denominación técnica en inglés, utilizada en modelos, catálogos y correspondencias.

La versión pública de E7 incorporada al repositorio documenta de forma consolidada los talleres, participantes, necesidades, barreras, decisiones y conclusiones. La matriz pública consolidada `e07-feedback-traceability-v1-0.csv` vincula los identificadores `F01`–`F10` con UC-01–UC-06 y con los ajustes A1–A10 de E9. E8 utiliza esa evidencia como contexto de priorización; los nombres, organizaciones, roles y el enlace público de la grabación externa se conservan como evidencia de veracidad y calidad, mientras que la grabación interna de acceso restringido permanece en el repositorio documental controlado. La muestra externa observada incluye empresas, academia y centros tecnológicos, pero no demuestra participación directa de reguladores, inversores o asociaciones sectoriales.

## 2. Base técnica de SDS

La base técnica de SDS combina capacidades semánticas, operativas y de gobernanza para pasar de datos aislados a productos de datos reutilizables bajo control del titular.

| Ámbito | Capacidad SDS | Uso en E8 |
|---|---|---|
| Preparación semántica | Catálogo de indicadores y requisitos de información. | Ancla los casos de uso a conceptos comunes y trazables. |
| Requisitos de información | Requisitos estructurados por marco, dominio y relación. | Conecta cada salida sectorial con una necesidad normativa. |
| Puntos fuente proyectados | Puntos de datos fuente vinculados a procedencia, unidad y periodo. | Explica entradas, controles y trazabilidad. |
| Valores ASG estructurados | Observaciones por entidad, periodo, concepto y unidad. | Soporta pilotos con datos comparables y auditables. |
| Contratos de cálculo | Reglas versionadas para agregaciones y transformaciones. | Separa dato fuente, cálculo y resultado. |
| Unidades | Catálogo de unidades y categorías de magnitud. | Evita mezclas de unidad y falsas comparaciones. |
| Relaciones entre marcos | Mapeos ESRS/GRI/GHG con relación explícita. | Evita equivalencias totales no evidenciadas. |

En NGSI-LD, SDS representa la interoperabilidad mediante entidades genéricas como `Dataset` e `Indicator`. Las especializaciones sectoriales se expresan mediante indicadores, relaciones, cálculos y políticas, de forma que energía, agua, residuos o biodiversidad compartan un modelo común reutilizable.

## 3. Metodología de priorización

La priorización combina tres criterios con escala de 1 a 5:

- **Impacto:** valor regulatorio, valor operativo y capacidad de reutilización.
- **Viabilidad:** disponibilidad de datos, madurez técnica y complejidad de integración.
- **Urgencia:** presión normativa, necesidad de auditoría y demanda esperable de las partes interesadas.

La puntuación total es la suma de los tres criterios, con un máximo de 15 puntos. La prioridad no sustituye la decisión de piloto: sirve para ordenar el esfuerzo inicial y hacer explícitas las dependencias técnicas y de gobernanza.

## 4. Matriz de priorización

| ID | Caso de uso | Sectores principales | Impacto | Viabilidad | Urgencia | Puntuación | Prioridad | Evidencia SDS principal |
|---|---|---|---:|---:|---:|---:|---:|---|
| UC-01 | Energía: consumo, mezcla energética e intensidad | Industria, energía, edificios, logística | 5 | 5 | 5 | 15 | 1 | Cálculos energéticos, unidades, GRI 302 y NEIS/ESRS E1. |
| UC-02 | Inventario de emisiones GEI e intensidad | Industria, logística, manufactura, comercio | 5 | 4 | 5 | 14 | 2 | GHG Protocol, GRI 305, NEIS/ESRS E1 y trazabilidad de factores. |
| UC-03 | Agua: extracciones, consumo, vertidos y estrés hídrico | Agroalimentario, industria, servicios urbanos | 5 | 4 | 5 | 14 | 3 | GRI 303, NEIS/ESRS E3, unidades hídricas y rutas de resolución. |
| UC-04 | Residuos, materiales y circularidad | Industria, residuos, construcción, química | 4 | 4 | 3 | 11 | 4 | GRI 306, GRI 301, NEIS/ESRS E5 y productos de datos bajo política. |
| UC-05 | Incorporación ASG de pymes para financiación sostenible | Entidades financieras, pymes multisectoriales | 4 | 4 | 3 | 11 | 5 | Perfil mínimo, catálogos DCAT-AP, términos de producto y consentimiento. |
| UC-06 | Biodiversidad y diligencia debida en cadenas de suministro | Agroalimentario, compras, distribución | 4 | 3 | 3 | 10 | 6 | GRI 304, NEIS/ESRS E4, procedencia y control de datos sensibles. |

## 5. Caso de uso UC-01 — Energía: consumo, mezcla energética e intensidad

**Sectores prioritarios:** industria, energía, edificios, logística y organizaciones con consumo energético material.

**Anclaje normativo:** NEIS/ESRS E1, GRI 302 y requisitos asociados a consumo total, fuentes energéticas, energía renovable, energía no renovable e intensidad energética.

**Entradas clave:**

- Consumos por fuente energética, instalación, periodo y unidad.
- Producción, actividad o denominador operativo para calcular intensidades.
- Contratos, garantías de origen y atributos de suministro cuando sean relevantes.
- Reglas de conversión y normalización de unidades.

**Salidas esperadas:**

- Consumo total y consumo por fuente.
- Mezcla energética y porcentaje renovable cuando exista evidencia.
- Intensidad energética por unidad de actividad, producto o volumen.
- Evidencia trazable para preparación de informes y auditoría.

**Encaje SDS actual:**

- Indicadores codificados frente a NEIS/ESRS y GRI.
- Contratos de cálculo para agregaciones energéticas.
- Catálogos DCAT-AP para publicar conjuntos de datos energéticos.
- Políticas EDC/ODRL para controlar propósito, acceso, retención y reutilización.

**Condiciones de piloto:**

- Confirmar que el denominador de intensidad está aprobado por el responsable del dato.
- Versionar factores, unidades y reglas de conversión.
- Separar datos medidos, estimados y derivados.

### Ejemplo práctico UC-01 — cálculo granular e interoperabilidad ESRS/GRI

El ejemplo práctico muestra cómo SDS captura variables operativas primitivas una sola vez y expone salidas coherentes para NEIS/ESRS y GRI. La fuente de verdad no es un indicador final ESRS ni un indicador final GRI, sino el dato operativo normalizado por SDS con dimensiones, unidad, periodo, entidad y relación semántica.

**Tesis de lectura:** SDS carga variables operativas primitivas una vez. ESRS y GRI son vistas calculadas o resueltas sobre la misma evidencia.

**Átomo energético SDS:** valor MWh normalizado creado desde una cantidad física primitiva mediante cálculo directo. Las salidas ESRS y GRI son proyecciones, agregaciones o puentes de equivalencia sobre esos átomos.

| Relación | Significado en UC-01 | Ejemplo |
|---|---|---|
| `direct calculation` | SDS deriva un valor desde cantidad física y factor, o desde suma de componentes. | litros × MWh/litro = MWh |
| `narrower component` | Un componente calculado alimenta una divulgación más amplia, pero no es equivalente por sí solo. | `E1-5_11` → `GRI 302-1.a` |
| `equivalent bridge` | Un resultado calculado puede reutilizarse como valor objetivo tras conversión de unidad. | `E1-5_01` → `GRI 302-1.e` |

#### Ledger mínimo de variables primitivas

| Primitiva | Ejemplo | Unidad | Rol SDS | Uso ESRS | Uso GRI |
|---|---:|---|---|---|---|
| cantidad física | 3,050.505 | litros | `activity_quantity` | input al átomo `E1-5_11` | detalle de combustible |
| factor | 0.009900 | MWh/litro | `energy_conversion_factor` | soporte metodológico | soporte `GRI 302-1.f/g` |
| átomo MWh | 30.200 | MWh | `calculated_energy_mwh` | proyección SDS ESRS `E1-5_11` | proyección SDS GRI `302-1.a` |
| `fuel_type` | diesel | texto | `fuel_type` | familia productos petrolíferos | tipo no renovable |
| uso | `mobile_combustion` | texto | `combustion_use_case` | contexto de desagregación | contexto de uso |

#### Traza end-to-end: diesel móvil

| Etapa | Entrada / operación | Salida | Rol SDS | Proyección estándar |
|---|---|---:|---|---|
| cantidad primitiva | consumo diesel móvil | 3,050.505 litros | `activity_quantity` | evidencia fuente |
| factor primitivo | factor energético | 0.009900 MWh/litro | `energy_conversion_factor` | evidencia metodológica |
| `direct calculation` | 3,050.505 × 0.009900 | 30.200 MWh | átomo energético SDS | valor reutilizable |
| ESRS projection | familia productos petrolíferos | 30.200 MWh | proyección SDS ESRS | componente de `E1-5_11` |
| GRI projection | combustible no renovable | 30.200 MWh | proyección SDS GRI | `narrower component` de `GRI 302-1.a` |
| aggregate contribution | sumar en totales | incluido | input de cálculo | totales ESRS y GRI |

#### Segundo ejemplo granular: hulla

| Fuel | Cantidad | Factor | Cálculo directo | Átomo SDS | ESRS | GRI |
|---|---:|---:|---|---:|---|---|
| bituminous_coal / hulla | 3.344 t | 6.800 MWh/t | 3.344 × 6.800 | 22.741 MWh | SDS ESRS `E1-5_10` | `narrower component` de SDS GRI `302-1.a` |

#### Diagrama tabular por capas

| Capa primitiva SDS | Capa cálculo SDS | Proyección SDS ESRS | Relación | Proyección SDS GRI |
|---|---|---|---|---|
| `fuel_type` + cantidad + factor | átomo energético en MWh | `E1-5_10` / `E1-5_11` / `E1-5_12` | `narrower component` | `GRI 302-1.a` |
| conjunto total de átomos | cálculo de energía total | `E1-5_01` | `equivalent bridge` | `GRI 302-1.e` |

Lectura correcta: no se dice “ESRS calcula GRI”. SDS calcula desde átomos operativos y expone resultados coherentes como proyección ESRS, proyección GRI o puente equivalente.

#### Matriz puente y totales derivados

| Resultado SDS | SDS ESRS | Relación | SDS GRI | Explicación |
|---|---|---|---|---|
| energía carbón | `E1-5_10` | `narrower component` | `GRI 302-1.a` | combustible procedente de fuentes no renovables |
| energía productos petrolíferos | `E1-5_11` | `narrower component` | `GRI 302-1.a` | incluye gasolina, diesel, jet fuel, etc. |
| energía gas natural | `E1-5_12` | `narrower component` | `GRI 302-1.a` | detalle de combustible no renovable |
| otras fuentes fósiles | `E1-5_13` | `narrower component` | `GRI 302-1.a` | resto de familia fósil |
| energía fósil adquirida | `E1-5_14` | `narrower component` | `GRI 302-1.c` | energía adquirida, no combustión directa |
| energía total | `E1-5_01` | `equivalent bridge` | `GRI 302-1.e` | mismo total, convertido a GJ para vista GRI |

| Salida | Fórmula / puente | MWh | GJ | Estado |
|---|---|---:|---:|---|
| ESRS `E1-5_02` energía fósil | `E1-5_10 + E1-5_11 + E1-5_12 + E1-5_13 + E1-5_14` | 832.964 | 2,998.670 | `direct calculation` |
| GRI `302-1.a` combustible no renovable | `E1-5_10 + E1-5_11 + E1-5_12 + E1-5_13` | 700.291 | 2,521.048 | component aggregation |
| ESRS `E1-5_01` energía total | `E1-5_02 + E1-5_03 + E1-5_05` | 1,650.573 | 5,942.063 | `direct calculation` |
| GRI `302-1.e` energía total | `equivalent bridge` desde `E1-5_01` | 1,650.573 | 5,942.063 | `equivalent bridge` |

La vista GRI se muestra en GJ usando `1 MWh = 3.6 GJ`.

## 6. Caso de uso UC-02 — Inventario de emisiones GEI e intensidad

**Sectores prioritarios:** industria, logística, manufactura, comercio y organizaciones con cadenas de suministro relevantes.

**Anclaje normativo:** NEIS/ESRS E1, GRI 305 y GHG Protocol para alcances 1, 2 y 3.

**Entradas clave:**

- Datos de actividad: combustibles, electricidad, transporte, materiales y otras fuentes relevantes.
- Factores de emisión con fuente, versión, fecha y método.
- Categorías de alcance 3 cuando exista evidencia suficiente.
- Unidades normalizadas y relación entre dato fuente, cálculo y resultado.

**Salidas esperadas:**

- Emisiones de gases de efecto invernadero por alcance, categoría y periodo.
- Indicadores de intensidad por unidad de actividad.
- Trazabilidad de fuente, método, factor y responsable.
- Identificación de huecos de datos para mejorar la siguiente iteración.

**Encaje SDS actual:**

- Uso de indicadores y contratos de cálculo, evitando tratar factores de emisión como simples conversiones de unidad cuando el método requiere una regla de cálculo.
- Relaciones entre marcos NEIS/ESRS, GRI y GHG con tipo de relación explícito.
- Productos de datos con términos de uso, propósito permitido y obligaciones de auditoría.

**Condiciones de piloto:**

- No afirmar cobertura total de alcance 3 sin revisión de categorías y evidencia.
- Mantener separado el dato primario del resultado calculado.
- Registrar responsable, versión y fuente de cada factor utilizado.

### Ejemplo práctico UC-02 — cálculo GEI por alcance e interoperabilidad ESRS/GRI/GHG

SDS captura actividad, factor de emisión, método y alcance una sola vez y expone salidas coherentes para ESRS, GRI y GHG Protocol.

**Tesis de lectura:** SDS calcula emisiones desde actividad operativa, factor metodológico y dimensiones. ESRS E1-6, GRI 305 y GHG Protocol son vistas o puentes cuando la relación está validada.

**Definición operativa:** átomo GEI SDS = valor tCO2e calculado desde cantidad física o energética y factor de emisión. El factor es evidencia metodológica con fuente, versión, gases incluidos, GWP, consolidación, categoría cuando aplica y calidad del dato.

| Relación | Significado en UC-02 | Ejemplo |
|---|---|---|
| cálculo directo | SDS deriva tCO2e desde actividad y factor de emisión. | litros x kg CO2e/litro / 1000 |
| agregación por alcance | SDS suma átomos por alcance, método y perímetro. | diesel + gas = alcance 1 |
| puente equivalente | Un resultado se reutiliza cuando la equivalencia exacta está revisada. | alcance 1 -> E1-6_07 -> GRI 305-1.a |
| proyección por categoría | Una categoría se proyecta solo con evidencia específica. | alcance 3 -> E1-6_11 / GRI 305-3.a |
| control de completitud | SDS bloquea el total si faltan categorías, exclusiones o método. | alcance 3 completo / parcial |

La granularidad fina se conserva en SDS: actividad, unidad, factor de emisión, gases incluidos, GWP, `alcance_gei`, `source_type`, país, `business_unit`, entidad y periodo.

| Primitiva | Ejemplo | Unidad | Rol SDS | Uso ESRS | Uso GRI/GHG |
|---|---:|---|---|---|---|
| cantidad física | 3,050.505 | litros | `activity_quantity` | input alcance 1 | actividad combustión móvil |
| factor emisión | 2.680 | kg CO2e/litro | `emission_factor` | metodología E1-6_15 | GRI 305-1.e / GHG |
| átomo GEI | 8.175 | tCO2e | `calculated_ghg_tco2e` | componente E1-6_07 | componente GRI 305-1.a |
| alcance | alcance 1 | texto | `alcance_gei` | desagregación E1-6 | límite GHG Protocol |
| consolidación | operational control | texto | `consolidation_approach` | perímetro E1-6 | GRI 305-1.f |

| Etapa | Entrada / operación | Salida | Rol SDS | Proyección estándar |
|---|---|---|---|---|
| actividad primitiva | consumo diesel móvil | 3,050.505 litros | `activity_quantity` | evidencia fuente |
| factor primitivo | factor de emisión | 2.680 kg CO2e/litro | `emission_factor` | evidencia metodológica |
| cálculo directo | 3,050.505 x 2.680 / 1000 | 8.175 tCO2e | átomo GEI SDS | valor reutilizable |
| clasificación de alcance | combustión móvil propia | alcance 1 | `alcance_gei=alcance_1` | límite GHG Protocol |
| proyección ESRS | emisiones brutas alcance 1 | 8.175 tCO2e | proyección SDS ESRS | componente de E1-6_07 |
| proyección GRI | emisiones directas brutas | 8.175 tCO2e | proyección SDS GRI | componente de GRI 305-1.a |

Alcance 2 exige conservar el método. La misma electricidad comprada puede producir una vista location-based y otra market-based sin duplicar la actividad fuente.

| Fuente | Cantidad | Factor | Cálculo directo | Átomo SDS | ESRS | GRI/GHG |
|---|---:|---:|---|---:|---|---|
| electricidad location-based | 420.000 MWh | 0.180 tCO2e/MWh | 420.000 x 0.180 | 75.600 tCO2e | E1-6_09 | GRI 305-2.a / GHG alcance 2 LB |
| electricidad market-based | 420.000 MWh | 0.095 tCO2e/MWh | 420.000 x 0.095 | 39.900 tCO2e | E1-6_10 | GRI 305-2.b / GHG alcance 2 MB |

| Capa primitiva SDS | Capa cálculo SDS | SDS ESRS | Relación | SDS GRI/GHG |
|---|---|---|---|---|
| actividad + factor + GWP | átomo GEI tCO2e | E1-6_07 | puente equivalente | GRI 305-1.a / GHG alcance 1 |
| electricidad + factor LB | átomo alcance 2 LB | E1-6_09 | puente equivalente | GRI 305-2.a / GHG alcance 2 LB |
| electricidad + factor MB | átomo alcance 2 MB | E1-6_10 | puente equivalente | GRI 305-2.b / GHG alcance 2 MB |
| actividad alcance 3 + categoría | átomo alcance 3 | E1-6_11 | proyección por categoría | GRI 305-3.a con revisión |
| denominador operativo | ratio intensidad | cálculo SDS | puente de cálculo | GRI 305-4.a |

No se dice "GRI calcula ESRS" ni "GHG Protocol es el dato base". SDS conserva actividad, factor, método y alcance; después expone cada estándar con relación explícita.

Alcance 3 es la parte más compleja porque cada categoría puede tener actividad, factor, propietario del dato, método de estimación, frontera y calidad distintos. SDS no debe tratarlo como un total opaco.

| Categoría | Actividad primitiva | Factor | Cálculo | tCO2e | Control semántico |
|---|---|---|---|---:|---|
| Cat. 1 bienes y servicios comprados | 2,400 kg acero | 1.850 kg CO2e/kg | 2,400 x 1.850 / 1000 | 4.440 | proveedor, versión factor, material, unidad |
| Cat. 4 transporte aguas arriba | 1,250 t-km | 0.145 kg CO2e/t-km | 1,250 x 0.145 / 1000 | 0.181 | ruta, modo transporte, transportista |
| Cat. 6 viajes de negocio | 8,600 pasajero-km | 0.158 kg CO2e/pkm | 8,600 x 0.158 / 1000 | 1.359 | modo, fecha, fuente factor |
| Cat. 7 desplazamiento empleados | 42,000 pasajero-km | 0.120 kg CO2e/pkm | 42,000 x 0.120 / 1000 | 5.040 | encuesta, extrapolación, calidad dato |
| Subtotal ejemplo alcance 3 | categorías 1+4+6+7 | - | 4.440 + 0.181 + 1.359 + 5.040 | 11.020 | parcial hasta cerrar frontera |

| Dimensión SDS | Por qué importa | Ejemplo |
|---|---|---|
| `categoria_ghg_protocol` | evita mezclar naturalezas distintas en una única cifra | cat_1, cat_4, cat_6, cat_7 |
| `data_owner` / proveedor | permite trazabilidad y revisión de dato externo | supplier_id, transportista |
| `metodo_estimacion` | distingue dato medido, estimado, spend-based o activity-based | activity_based |
| `factor_source_version` | evita cambios silenciosos de factor o GWP | factor_v2024 |
| `estado_frontera` | marca incluido, excluido, no aplicable o pendiente | incluido / pendiente_revision |
| `data_quality_score` | controla incertidumbre antes de publicar un total | A/B/C o 1-5 |

**Regla de publicación:** E1-6_11 y GRI 305-3.a pueden mostrar el total de alcance 3 solo cuando SDS sabe qué categorías están incluidas, cuáles se excluyen, por qué, y qué calidad tiene cada cálculo.

| Resultado SDS | SDS ESRS | Relación | SDS GRI/GHG | Explicación |
|---|---|---|---|---|
| alcance 1 bruto | E1-6_07 | puente equivalente | GRI 305-1.a / GHG alcance 1 | combustión propia y emisiones directas |
| alcance 2 LB | E1-6_09 | puente equivalente | GRI 305-2.a / GHG alcance 2 LB | electricidad con método location-based |
| alcance 2 MB | E1-6_10 | puente equivalente | GRI 305-2.b / GHG alcance 2 MB | electricidad con método market-based |
| alcance 3 por categoría | E1-6_11 | proyección por categoría | GRI 305-3.a | requiere categorías, exclusiones y evidencia |
| total LB | E1-6_12 | cálculo directo | vista SDS | alcance 1 + alcance 2 LB + alcance 3 validado |
| total MB | E1-6_13 | cálculo directo | vista SDS | alcance 1 + alcance 2 MB + alcance 3 validado |

| Salida | Fórmula / puente | tCO2e | Estado |
|---|---|---:|---|
| alcance 1 bruto | diesel 8.175 + gas natural 3.878 | 12.054 | cálculo directo + puente equivalente |
| alcance 2 LB | 420.000 x 0.180 | 75.600 | cálculo directo + puente equivalente |
| alcance 2 MB | 420.000 x 0.095 | 39.900 | cálculo directo + puente equivalente |
| alcance 3 ejemplo parcial | cat. 1 + 4 + 6 + 7 | 11.020 | proyección por categoría con control de completitud |
| Total GEI LB | 12.054 + 75.600 + 11.020 | 98.674 | cálculo directo; publicable solo si alcance 3 está cerrado |
| Total GEI MB | 12.054 + 39.900 + 11.020 | 62.974 | cálculo directo; publicable solo si alcance 3 está cerrado |
| Intensidad 305-4.a | (alcance 1 + alcance 2 LB) / 10,000 t producto | 0.008765 | puente de cálculo |

Nota: alcance 3 se conserva como subledger categorizado; el subtotal del ejemplo no se presenta como cobertura total hasta revisar todas las categorías aplicables, exclusiones y controles de calidad.

## 7. Caso de uso UC-03 — Agua: extracciones, consumo, vertidos y estrés hídrico

**Sectores prioritarios:** agroalimentario, industria, servicios urbanos, energía y organizaciones con dependencia hídrica.

**Anclaje normativo:** NEIS/ESRS E3 y GRI 303.

**Entradas clave:**

- Extracciones por fuente: red, aguas superficiales, aguas subterráneas u otras fuentes.
- Consumo neto, reutilización y vertidos por destino.
- Ubicación, cuenca o zona de estrés hídrico cuando sea necesario.
- Unidad, periodo, método de medición y evidencia de origen.

**Salidas esperadas:**

- Extracción, consumo y vertido por periodo y unidad.
- Indicadores de exposición a estrés hídrico.
- Comparabilidad entre centros cuando las unidades y métodos estén normalizados.
- Evidencia preparada para revisión interna y auditoría.

**Encaje SDS actual:**

- Indicadores hídricos conectados con GRI 303 y NEIS/ESRS E3.
- Normalización de unidades hídricas y reglas de cálculo.
- Catálogos para distinguir conjuntos de datos públicos de referencia y datos operativos privados.
- Políticas de acceso para información geográfica o industrial sensible.

**Condiciones de piloto:**

- Acordar la granularidad geográfica antes de compartir datos.
- Evitar publicar ubicaciones sensibles cuando baste una agregación.
- Documentar método de medición, estimación o consolidación.

### Ejemplo práctico UC-03 — cálculo hídrico e interoperabilidad ESRS/GRI

SDS captura observaciones hídricas primitivas una sola vez y expone salidas coherentes para ESRS E3-4 y GRI 303. La fuente de verdad no es el total ESRS ni el total GRI, sino el ledger SDS con volumen, fuente, destino, cuenca, estrés hídrico, riesgo hídrico, calidad del agua, método, calidad de evidencia, unidad de negocio, país, entidad y periodo.

**Tesis de lectura:** SDS calcula y agrega desde variables operativas primitivas. ESRS y GRI son vistas reguladas sobre la misma evidencia, con conversión de unidad y controles semánticos.

**Átomo hídrico SDS:** volumen normalizado en `m3` asociado a un evento o medición de extracción, vertido, consumo, reutilización o almacenamiento. Para la vista GRI, SDS convierte a megalitros con `1 ML = 1,000 m3`.

| Relación | Significado en UC-03 | Ejemplo |
|---|---|---|
| cálculo directo | SDS deriva un resultado desde lecturas y regla de cálculo. | consumo = extracción - vertido |
| agregación dimensional | SDS suma átomos por fuente, destino, cuenca, unidad de negocio o país. | extracciones por `water_source` |
| puente equivalente condicionado | Un total puede reutilizarse si unidad, perímetro, método y dimensión son compatibles o están reconciliados con regla trazable. | `E3-4_01` -> `GRI 303-5.a` tras m3 a ML |
| proyección por componente | Un desglose GRI necesita dimensiones que el total ESRS no conserva por sí solo. | `water_source=groundwater` -> `GRI 303-3.a.ii` |
| soporte/contexto | Un valor explica la gestión hídrica pero no es un target exacto. | `E3-4_03` reciclada/reutilizada -> contexto GRI 303-5 |
| control de estrés hídrico | SDS separa zonas con estrés antes de publicar subtotales. | `water_stress_status=stressed` |

#### Ledger mínimo de variables primitivas

| Primitiva SDS | Ejemplo | Unidad | Rol SDS | Uso ESRS | Uso GRI |
|---|---:|---|---|---|---|
| lectura extracción | 12,500 | m3 | `withdrawal_volume_m3` | componente `E3-4_11` | componente `GRI 303-3.a` |
| fuente de agua | groundwater | texto | `water_source` | desagregación interna | desglose `GRI 303-3.a.ii` |
| lectura vertido | 9,800 | m3 | `discharge_volume_m3` | componente `E3-4_12` | componente `GRI 303-4.a` |
| destino del vertido | surface_water | texto | `water_destination` | desagregación interna | desglose `GRI 303-4.a.i` |
| cuenca | basin_a | texto | `basin_id` | control geográfico | control de estrés |
| estrés hídrico | stressed | texto | `water_stress_status` | filtro de `E3-4_02` | `GRI 303-5.b` / `303-3.b` / `303-4.c` |
| riesgo hídrico | high | texto | `water_risk_class` | condiciona `E3-4_02` | contexto de riesgo; no sustituye al estrés |
| calidad del agua | freshwater | texto | `tds_class` | evidencia técnica | `GRI 303-3.c` / `303-4.b` |
| método | metered / balance | texto | `measurement_method` | `E3-4_06` | `GRI 303-5.d` |
| calidad de evidencia | measured | texto | `evidence_quality` | fiabilidad del dato | medido, estimado o modelado |
| unidad de negocio | nordhaven_water_ops | texto | `business_unit` | desagregación por entidad | desglose organizativo |
| país | ES | texto | `country` | contexto geográfico | contexto GRI |

#### Traza por centro operativo

| Business unit | País | Centro | Cuenca | Estrés | Riesgo | Método | Calidad evidencia | Extracción por fuente | Vertido por destino | Reutilizada | Almacenamiento inicial | Almacenamiento final |
|---|---|---|---|---|---|---|---|---:|---:|---:|---:|---:|
| nordhaven_water_ops | ES | Planta A | basin_a | stressed | high | metered | measured | groundwater 12,500 + third_party 4,000 = 16,500 m3 | surface_water 9,800 m3 | 1,600 m3 | 1,900 m3 | 2,150 m3 |
| nordhaven_water_ops | ES | Planta B | basin_b | not_stressed | low | balance | estimated | surface_water 8,200 + third_party 5,500 = 13,700 m3 | third_party_treatment 7,100 m3 | 900 m3 | 600 m3 | 520 m3 |

#### Cálculo paso a paso

| Salida SDS | Fórmula | m3 | ML | Relación |
|---|---|---:|---:|---|
| extracciones totales | 16,500 + 13,700 | 30,200 | 30.200 | agregación dimensional |
| vertidos totales | 9,800 + 7,100 | 16,900 | 16.900 | agregación dimensional |
| consumo total por balance | 30,200 - 16,900 | 13,300 | 13.300 | cálculo directo |
| consumo en zona con estrés | 16,500 - 9,800 | 6,700 | 6.700 | cálculo directo + filtro `water_stress_status=stressed` |
| agua reutilizada/reciclada | 1,600 + 900 | 2,500 | 2.500 | agregación de soporte |
| cambio de almacenamiento | (2,150 - 1,900) + (520 - 600) | 170 | 0.170 | cálculo directo |

#### Matriz puente ESRS / GRI

| Resultado SDS | SDS ESRS | Relación | SDS GRI | Condición semántica |
|---|---|---|---|---|
| extracciones totales | `E3-4_11` | puente condicionado + conversión unidad | `GRI 303-3.a` | requiere `water_source` para el desglose GRI |
| extracciones en estrés hídrico | vista filtrada SDS | componente | `GRI 303-3.b` | requiere `water_stress_status=stressed` |
| vertidos totales | `E3-4_12` | puente condicionado + conversión unidad | `GRI 303-4.a` | requiere `water_destination` para el desglose GRI |
| vertidos en estrés hídrico | vista filtrada SDS | componente | `GRI 303-4.c` | requiere cuenca y estrés hídrico |
| consumo total | `E3-4_01` | puente equivalente condicionado | `GRI 303-5.a` | unidad convertible, perímetro y método compatibles o reconciliados |
| consumo en zonas de riesgo/estrés | `E3-4_02` | puente condicionado | `GRI 303-5.b` | solo exacto si `water_risk_class` se alinea con `water_stress_status` |
| reciclada y reutilizada | `E3-4_03` | soporte/contexto | `GRI 303-5` / metodología | no declarar target exacto sin mapeo atomizado |
| cambio de almacenamiento | `E3-4_05` | puente equivalente condicionado | `GRI 303-5.c` | aplica si el almacenamiento es material |

#### Lectura correcta

No se dice "ESRS calcula GRI" ni "GRI calcula ESRS". SDS conserva las variables primitivas y las dimensiones que ambos estándares necesitan. Si solo se carga un total agregado ESRS, SDS puede reportar ese total, pero no puede reconstruir los desgloses GRI por fuente, destino, estrés hídrico o calidad del agua. Las dimensiones `business_unit`, `country`, `measurement_method` y `evidence_quality` permiten separar perímetro, geografía, método y fiabilidad sin mezclar dato medido con estimación. La interoperabilidad completa aparece cuando Nordhaven carga el dato operativo con máxima granularidad desde el inicio.

**Regla de publicación:** los totales GRI 303 se publican solo si SDS puede demostrar unidad fuente, unidad normalizada, regla de conversión, perímetro, método y dimensiones requeridas. Cuando falta una dimensión crítica, SDS mantiene el valor como dato interno o soporte metodológico, no como puente equivalente.

## 8. Caso de uso UC-04 — Residuos, materiales y circularidad

**Sectores prioritarios:** industria, residuos, construcción, química, fabricación y distribución.

**Anclaje normativo:** NEIS/ESRS E5, GRI 306 y GRI 301.

**Entradas clave:**

- Residuos por tipo, origen, tratamiento y destino.
- Materiales utilizados, recuperados o valorizados.
- Subproductos y materiales secundarios con especificación mínima.
- Condiciones de calidad, cantidad, ubicación y periodo.

**Salidas esperadas:**

- Indicadores de generación, tratamiento, recuperación y valorización.
- Tasas de circularidad y residuos evitados cuando exista método aprobado.
- Productos de datos para compartir oferta o demanda de materiales secundarios bajo condiciones.
- Evidencia de cumplimiento y mejora operativa.

**Encaje SDS actual:**

- Catálogos DCAT-AP para describir conjuntos de datos y productos de datos.
- Políticas EDC/ODRL para limitar acceso, propósito y uso posterior.
- Relaciones entre indicadores NEIS/ESRS y GRI con equivalencia o parcialidad explícita.

**Condiciones de piloto:**

- No compartir composición, precio o ubicación detallada sin política aprobada.
- Definir responsables de calidad del dato y de actualización.
- Diferenciar claramente residuo, subproducto y material recuperado.

### Ejemplo práctico UC-04 — registro de materiales, residuos y circularidad NEIS/ESRS/GRI

SDS captura una vez los movimientos operativos de materiales y residuos y, desde esa evidencia, calcula los indicadores necesarios para NEIS/ESRS E5, GRI 301 y GRI 306. La fuente de verdad no es un indicador final de un marco concreto, sino el registro SDS con cantidad, unidad, tipo de material, origen, peligrosidad, tratamiento, ubicación agregada, método de medición, calidad de evidencia, entidad, país y periodo.

**Tesis de lectura:** SDS permite pasar de una lista operativa de materiales y residuos a vistas reguladas coherentes. La correspondencia entre NEIS/ESRS y GRI no siempre es total: algunas relaciones son parciales, más amplias o condicionadas por dimensiones obligatorias.

**Átomo SDS de circularidad:** cantidad normalizada en toneladas asociada a un movimiento de material o residuo. Puede representar material primario, material secundario, material reciclado, subproducto, residuo peligroso, residuo no peligroso, residuo valorizado o residuo destinado a eliminación.

| Relación | Significado en UC-04 | Ejemplo |
|---|---|---|
| cálculo directo | SDS deriva un total o porcentaje desde cantidades medidas. | material secundario / material total |
| agregación por dimensión | SDS suma por material, peligrosidad, tratamiento o país. | residuos no peligrosos reciclados |
| correspondencia condicionada | El valor se puede reutilizar si la magnitud es convertible a una unidad común y el perímetro y el método son equivalentes o están reconciliados con regla aprobada. | material total usado NEIS/GRI |
| correspondencia parcial | El valor cubre parte del requisito, pero no todo. | residuo total sin composición completa |
| correspondencia más amplia | Un dato NEIS/ESRS contiene varios desgloses GRI posibles. | residuos desviados por tratamiento |

#### Registro mínimo de variables primitivas

| Primitiva SDS | Ejemplo | Unidad | Rol SDS | Uso NEIS/ESRS | Uso GRI |
|---|---:|---|---|---|---|
| material total usado | 1.120 | t | material usado en producción y embalaje | `E5-4_02` | `GRI 301-1.a` |
| material secundario o reciclado | 120 | t | material no virgen incorporado | `E5-4_04` | base de `GRI 301-2.a` |
| residuo generado | 94 | t | residuo total del periodo | `E5-5_07` | componente de `GRI 306-3.a` |
| residuo desviado de eliminación | 66 | t | reciclaje, reutilización u otra valorización | `E5-5_08` | `GRI 306-4` |
| residuo destinado a eliminación | 28 | t | vertedero o incineración según tratamiento | `E5-5_09` | `GRI 306-5` |
| residuo no reciclado | 34 | t | parte no reciclada del residuo generado | `E5-5_10` | contexto de circularidad |
| clase de peligrosidad | no peligroso | texto | separa tratamiento y obligación | desglose E5 | desglose GRI 306 |
| método de medición | pesada en báscula | texto | fiabilidad de cantidad | metodología | contexto GRI |

#### Movimiento de materiales

| Material | Cantidad | Origen | Uso SDS |
|---|---:|---|---|
| acero primario | 820 t | material virgen | material total usado |
| plástico virgen | 180 t | material virgen | material total usado |
| cartón reciclado | 75 t | material reciclado | material secundario |
| granza plástica reciclada | 45 t | material reciclado | material secundario |
| total materiales | 1.120 t | mixto | base `E5-4_02` / `GRI 301-1.a` |
| total secundario o reciclado | 120 t | reciclado | base `E5-4_04` / `E5-4_05` |

SDS calcula el porcentaje de material secundario o reciclado:

`120 / 1.120 x 100 = 10,714 %`

Ese porcentaje puede alimentar `E5-4_05`. Para `GRI 301-2.a`, SDS debe comprobar que el material incluido corresponde realmente a material reciclado de entrada usado para fabricar productos o embalajes. Si la cifra NEIS/ESRS incluye también subproductos, componentes reutilizados u otras categorías secundarias no equivalentes, la relación con GRI es parcial.

#### Movimiento de residuos

| Flujo de residuo | Peligrosidad | Tratamiento | Cantidad | Clasificación SDS |
|---|---|---|---:|---|
| chatarra metálica | no peligroso | reciclaje | 42 t | desviado de eliminación |
| cartón de embalaje | no peligroso | reciclaje | 18 t | desviado de eliminación |
| residuo con disolvente | peligroso | otra valorización | 6 t | desviado de eliminación |
| residuo industrial mixto | no peligroso | vertedero | 24 t | destinado a eliminación |
| lodo contaminado | peligroso | incineración con recuperación energética | 4 t | destinado a eliminación |

#### Cálculo paso a paso

| Salida SDS | Fórmula | Toneladas | Relación |
|---|---|---:|---|
| residuo total generado | 42 + 18 + 6 + 24 + 4 | 94 | cálculo directo |
| residuo desviado de eliminación | 42 + 18 + 6 | 66 | agregación por tratamiento |
| residuo destinado a eliminación | 24 + 4 | 28 | agregación por tratamiento |
| residuo reciclado | 42 + 18 | 60 | componente de valorización |
| residuo no reciclado | 94 - 60 | 34 | cálculo directo |
| porcentaje no reciclado | 34 / 94 x 100 | 36,170 % | cálculo directo |

#### Matriz de correspondencia NEIS/ESRS y GRI

| Resultado SDS | NEIS/ESRS | Relación | GRI | Condición semántica |
|---|---|---|---|---|
| material total usado | `E5-4_02` | correspondencia condicionada | `GRI 301-1.a` | unidad convertible, periodo compatible o agregado con regla trazable, y perímetro equivalente o reconciliado |
| porcentaje de material secundario | `E5-4_05` | parcial | `GRI 301-2.a` | GRI se centra en material reciclado de entrada |
| residuo total generado | `E5-5_07` | parcial | `GRI 306-3.a` | GRI exige composición y contexto |
| residuo desviado de eliminación | `E5-5_08` | más amplia | `GRI 306-4.b/c` | separar peligroso/no peligroso y tipo de recuperación |
| residuo destinado a eliminación | `E5-5_09` | más amplia | `GRI 306-5.b/c` | separar peligroso/no peligroso y tipo de eliminación |
| residuo no reciclado | `E5-5_10` | cálculo SDS | vista de circularidad | no sustituye por sí solo a GRI 306 |

#### Lectura correcta

No se dice "NEIS calcula GRI" ni "GRI calcula NEIS". SDS conserva los movimientos operativos y las dimensiones necesarias para que cada marco pueda consultarse con su propio criterio. Un total agregado de residuos puede servir para `E5-5_07`, pero no basta para reconstruir `GRI 306-3.a` si faltan composición, peligrosidad, tratamiento o contexto de recopilación.

#### Regla de publicación

SDS puede publicar un total de materiales o residuos cuando conoce entidad, periodo, unidad fuente, unidad normalizada, regla de conversión, método y responsable del dato. Para publicar una vista GRI 306 completa necesita además composición del residuo, clase de peligrosidad, tratamiento, ubicación agregada y si el tratamiento ocurre dentro o fuera de la organización. Si falta una dimensión crítica, SDS mantiene el valor como evidencia interna o correspondencia parcial, no como equivalencia completa.

La utilidad práctica de UC-04 está en que una organización puede registrar residuos y materiales una sola vez, calcular circularidad y publicar información comparable sin exponer composición sensible, ubicación exacta, precio o condiciones comerciales de materiales secundarios.

## 9. Caso de uso UC-05 — Incorporación ASG de pymes para financiación sostenible

**Sectores prioritarios:** entidades financieras, pymes multisectoriales, asesores y plataformas de acompañamiento.

**Anclaje normativo:** perfil mínimo de sostenibilidad, NEIS/ESRS cuando aplique, GRI por dominio y requisitos de diligencia de entidades financiadoras.

**Entradas clave:**

- Evidencias básicas de energía, agua, residuos, emisiones, gobernanza y actividad.
- Metadatos de calidad: fuente, fecha, responsable, cobertura y método.
- Consentimiento y propósito de uso para compartir información con terceros.
- Estado de completitud y revisión de cada indicador.

**Salidas esperadas:**

- Perfil ASG mínimo reutilizable y trazable.
- Paquete de evidencias para evaluación, financiación o acompañamiento.
- Señal de madurez del dato y de brechas pendientes.
- Producto de datos controlado por la pyme y reutilizable bajo condiciones.

**Encaje SDS actual:**

- Catálogos para descubrimiento de productos de datos.
- Términos de producto y políticas de propósito, retención y obligación.
- Indicadores y relaciones para construir un perfil progresivo sin exigir madurez completa desde el primer ciclo.

**Condiciones de piloto:**

- Evitar puntuaciones automáticas sin metodología aprobada.
- Mantener el control del titular sobre propósito y destinatario.
- Separar evidencia documental, dato estructurado y resultado calculado.

### Ejemplo práctico UC-05 — perfil ASG mínimo para financiación sostenible

SDS permite que una pyme reúna evidencias ambientales, sociales y de gobernanza en un perfil mínimo, trazable y compartible con una entidad financiadora. El objetivo no es emitir una puntuación automática, sino preparar un paquete de datos verificable, con consentimiento, calidad conocida y brechas explícitas.

**Tesis de lectura:** SDS convierte evidencias dispersas en un perfil ASG reutilizable. La entidad financiadora recibe datos comparables; la pyme conserva el control sobre qué se comparte, con quién, para qué finalidad y durante cuánto tiempo.

**Átomo SDS de perfil ASG:** dato o evidencia con valor, unidad, periodo, entidad, procedencia, responsable, método, calidad, estado de revisión y permiso de uso. Puede ser un dato medido, un dato calculado, una evidencia documental o una señal de completitud.

| Relación | Significado en UC-05 | Ejemplo |
|---|---|---|
| dato declarado | La pyme aporta un valor sin cálculo adicional. | política ambiental aprobada |
| dato calculado | SDS deriva un resultado desde valores primitivos y regla aprobada. | emisiones estimadas desde electricidad y combustible |
| evidencia documental | Archivo o certificado que respalda el dato estructurado. | factura eléctrica, certificado de gestor de residuos |
| señal de completitud | SDS marca si el dato está completo, parcial, pendiente o restringido. | residuos completos, emisiones parciales |
| correspondencia por dominio | SDS relaciona el dato con NEIS/ESRS y GRI solo cuando el dominio y las condiciones lo permiten. | energía -> E1 / GRI 302 |
| control de consentimiento | SDS limita el uso del paquete al propósito autorizado. | financiación sostenible, entidad concreta, periodo concreto |

#### Registro mínimo de variables primitivas

| Dato SDS | Ejemplo | Unidad o estado | Procedencia | Uso para la entidad financiadora |
|---|---:|---|---|---|
| electricidad adquirida | 125 | MWh | factura eléctrica | energía y cálculo de emisiones |
| combustible usado | 8.500 | litros gasóleo | registro de compras | cálculo de emisiones directas |
| agua consumida | 1.850 | m3 | factura y contador | indicador hídrico básico |
| residuo generado | 12,4 | t | gestor autorizado | indicador de residuos |
| residuo peligroso | 0,8 | t | certificado de gestor | señal de riesgo operativo |
| política ambiental | sí | revisada | documento interno | evidencia de gobernanza ambiental |
| responsable del dato | dirección operativa | identificado | declaración de la pyme | trazabilidad y contacto de revisión |
| consentimiento de uso | aprobado | vigente | autorización firmada | permiso para compartir el paquete |

#### Cálculo básico y normalización

SDS no rechaza un dato porque la unidad de origen no sea la unidad requerida por otra vista. Conserva la unidad original, aplica una conversión aprobada cuando la magnitud es compatible y registra la regla usada.

| Salida SDS | Fórmula | Resultado | Estado |
|---|---|---:|---|
| energía en GJ para vista GRI | 125 MWh x 3,6 | 450 GJ | unidad convertida con regla trazable |
| emisiones por electricidad | 125 MWh x 0,180 tCO2e/MWh | 22,500 tCO2e | cálculo con factor identificado |
| emisiones por gasóleo | 8.500 l x 2,680 kgCO2e/l / 1000 | 22,780 tCO2e | cálculo con factor identificado |
| emisiones operativas estimadas | 22,500 + 22,780 | 45,280 tCO2e | parcial si faltan otras fuentes materiales |
| intensidad por producción | 45,280 / 2.400 t producto | 0,0189 tCO2e/t | requiere denominador revisado |
| porcentaje de residuo peligroso | 0,8 / 12,4 x 100 | 6,452 % | cálculo directo |

#### Perfil compartible con entidad financiadora

| Bloque del perfil | Evidencia compartida | Estado SDS | Lectura correcta |
|---|---|---|---|
| energía | electricidad adquirida y conversión a GJ | completo revisado | comparable si periodo, perímetro y tipo de energía son compatibles |
| emisiones | cálculo desde electricidad y combustible | parcial | no equivale a huella completa si faltan fuentes relevantes |
| agua | consumo medido del periodo | completo declarado | suficiente para señal básica; no sustituye desgloses hídricos avanzados |
| residuos | total y residuo peligroso | completo revisado | útil para riesgo operativo; GRI 306 completo exige más desglose |
| gobernanza | política ambiental y responsable del dato | revisado | evidencia documental, no puntuación automática |
| consentimiento | permiso firmado de uso | vigente | uso limitado a finalidad, destinatario y periodo autorizados |
| brechas | fuentes de emisión pendientes | pendiente | se muestra como brecha, no se oculta en una nota agregada |

#### Matriz de correspondencia NEIS/ESRS y GRI

| Dato del perfil | Dominio NEIS/ESRS | Relación | Referencia GRI | Condición SDS |
|---|---|---|---|---|
| consumo energético | E1 energía | condicionada | GRI 302 | unidad convertible a una unidad común; periodo compatible o agregable; perímetro y tipo de energía equivalentes o reconciliados; procedencia y método trazados |
| emisiones calculadas | E1 clima | condicionada | GRI 305 | factor, método, gases incluidos, periodo y perímetro deben estar documentados |
| agua consumida | E3 agua | parcial | GRI 303 | unidad normalizada y periodo trazado; los desgloses por fuente, destino o cuenca requieren dimensiones adicionales |
| residuos generados | E5 recursos y circularidad | parcial | GRI 306 | total publicable; vista GRI completa exige composición, peligrosidad, tratamiento y contexto |
| política ambiental | información general y gobernanza | soporte | GRI 2 | evidencia de gestión; no sustituye indicadores ambientales cuantitativos |
| consentimiento de uso | gobernanza SDS del intercambio | control interno | no aplica como indicador GRI | habilita el intercambio; no es evidencia normativa por sí solo |

#### Estados de madurez del dato

| Estado SDS | Qué significa | Uso permitido |
|---|---|---|
| completo revisado | dato estructurado, evidencia y responsable comprobados | puede incluirse en el paquete compartible |
| completo declarado | dato aportado por la pyme con procedencia conocida | puede compartirse con advertencia de revisión pendiente |
| parcial | falta una fuente, dimensión o método relevante | sirve para diagnóstico y plan de mejora |
| pendiente | no hay dato suficiente | no se usa para cálculo ni comparación |
| restringido | existe evidencia, pero el permiso limita su uso | solo se comparte bajo la política autorizada |

#### Regla de publicación

SDS no aprueba ni rechaza la financiación. Ordena la evidencia, calcula cuando hay regla aprobada, convierte unidades cuando la magnitud es compatible, marca brechas y conserva consentimiento. La decisión de financiación pertenece a la entidad financiadora y a su metodología aprobada.

No se debe publicar una puntuación ASG automática si no existe metodología aprobada, trazable y comunicada a la pyme. El resultado publicable es un paquete de evidencia: qué existe, qué calidad tiene, qué falta, quién lo revisó y bajo qué permiso puede utilizarse.

## 10. Caso de uso UC-06 — Biodiversidad y diligencia debida en cadenas de suministro

**Sectores prioritarios:** agroalimentario, compras, distribución, materias primas y organizaciones con exposición territorial.

**Anclaje normativo:** NEIS/ESRS E4, GRI 304 y requisitos de diligencia debida aplicables a cadenas de suministro, incluida la normativa europea sobre productos libres de deforestación cuando corresponda.

**Entradas clave:**

- Ubicación o zona de origen con nivel de detalle proporcional al riesgo.
- Evidencia de uso del suelo, áreas protegidas o cambios relevantes.
- Lotes, proveedores y cadena de custodia.
- Reglas de minimización y retención para datos sensibles.

**Salidas esperadas:**

- Evidencia de diligencia debida por lote, zona o proveedor.
- Señales de riesgo territorial y necesidad de revisión.
- Registro de procedencia y método de comprobación.
- Información agregada reutilizable sin exponer datos sensibles innecesarios.

**Encaje SDS actual:**

- Políticas de acceso y minimización para información sensible.
- Catálogos que separen datos públicos de referencia y datos privados.
- Relaciones entre indicadores y requisitos normativos con revisión explícita.

**Condiciones de piloto:**

- Definir el nivel de granularidad geográfica antes del intercambio.
- Evitar trazabilidad nominal cuando baste evidencia agregada.
- Acordar responsabilidades de actualización si cambian fuentes o normativa.

### Ejemplo práctico UC-06 — evidencia territorial y diligencia debida por lote

El ejemplo práctico cierra UC-06 con la capacidad que SDS tiene ahora: conservar procedencia, dimensiones, evidencia, permiso de uso, política de acceso y relación normativa. La clasificación granular de admisibilidad se presenta como regla de piloto, no como un motor específico de biodiversidad ya implementado.

**Tesis de lectura:** SDS no certifica por sí solo que un lote esté libre de impacto sobre biodiversidad ni sustituye una evaluación legal. SDS ordena la evidencia disponible, muestra cobertura y brechas, conserva la política de uso y evita publicar conclusiones cuando la evidencia no alcanza el umbral definido.

**Átomo SDS de diligencia debida territorial:** registro de lote, proveedor o zona con cantidad, periodo, procedencia, nivel geográfico, tipo de evidencia, fecha de la fuente, método, permiso de uso, responsable de revisión y estado de admisibilidad del piloto.

| Relación | Significado en UC-06 | Ejemplo |
|---|---|---|
| evidencia directa | Documento o dato ligado al lote, parcela o zona concreta. | certificado de origen del lote |
| evidencia agregada | Evidencia válida para una zona o cooperativa, pero no para cada parcela individual. | zona de acopio revisada |
| cobertura parcial | Parte del lote tiene evidencia suficiente y parte no. | 100 t revisables sobre 120 t |
| uso restringido | La evidencia existe, pero la política limita detalle, destinatario o finalidad. | coordenadas no publicables |
| revisión requerida | SDS conserva la señal, pero no permite publicar conclusión cerrada. | fuente territorial contradictoria |

#### Registro mínimo de variables primitivas

| Variable SDS | Ejemplo | Unidad o estado | Rol SDS | Uso NEIS/ESRS E4 | Uso GRI 304 |
|---|---:|---|---|---|---|
| lote comprado | L-2024-017 | texto | identificador operativo | vínculo con cadena de valor | contexto de proveedor |
| cantidad del lote | 120 | t | volumen sujeto a diligencia debida | alcance de evaluación | base de cobertura |
| origen con parcela documentada | 70 | t | procedencia de mayor granularidad | ubicación sensible si aplica | soporte de sitio o zona |
| origen agregado de cooperativa | 30 | t | procedencia intermedia | evidencia de zona | contexto de área |
| origen sin evidencia suficiente | 20 | t | brecha del lote | limitación de cobertura | brecha de reporte |
| tipo de evidencia | documento de origen | texto | procedencia documental | soporte metodológico | fuente de comprobación |
| nivel geográfico | parcela / zona / país | texto | dimensión territorial | proximidad o sensibilidad | ubicación o área |
| permiso de publicación | agregado solamente | estado | política de uso | minimización de datos | no publicar coordenadas |
| revisión | pendiente / revisado | estado | control de admisibilidad piloto | trazabilidad de juicio | trazabilidad de fuente |

#### Cálculo de cobertura del lote

| Salida SDS | Fórmula | Resultado | Lectura correcta |
|---|---|---:|---|
| cobertura con parcela documentada | 70 / 120 x 100 | 58,333 % | evidencia granular, pero no cubre todo el lote |
| cobertura con evidencia revisable | (70 + 30) / 120 x 100 | 83,333 % | incluye parcela y zona agregada |
| brecha sin evidencia suficiente | 20 / 120 x 100 | 16,667 % | no debe ocultarse ni reasignarse |
| conclusión de lote completo | no aplicable | bloqueada | falta evidencia para el 100 % |

El lote no se publica como completamente cubierto. SDS permite explicar que el 83,333 % tiene evidencia revisable bajo el criterio del piloto, que el 58,333 % tiene granularidad de parcela y que el 16,667 % queda como brecha. La diferencia entre cobertura revisable y cobertura de parcela evita transformar una evidencia agregada en una afirmación más fuerte de lo que permite.

#### Regla piloto de admisibilidad

| Estado piloto | Qué exige | Resultado en el ejemplo | Uso permitido |
|---|---|---|---|
| válida | fuente identificada, fecha vigente, alcance compatible, permiso suficiente y responsable asignado | no aplica al lote completo | publicar solo si cubre el alcance declarado |
| parcial | evidencia suficiente para una parte del alcance | 100 t revisables | publicar con porcentaje y brecha |
| insuficiente | falta fuente, fecha, alcance, responsable o trazabilidad mínima | 20 t sin soporte suficiente | no usar para conclusión positiva |
| restringida | evidencia existente con limitación de confidencialidad o finalidad | coordenadas y proveedor nominal | usar vista agregada o bajo política aprobada |
| contradictoria | dos fuentes relevantes no permiten una lectura única | señal territorial en revisión | bloquear conclusión y elevar revisión |

Esta regla piloto es deliberadamente explícita. SDS ya puede conservar evidencia, dimensiones, procedencia, hashes, metadatos y políticas; lo que no debe afirmarse es que ya exista un validador automático UC-06 que resuelva por sí solo la suficiencia territorial, la contradicción entre fuentes o el cumplimiento legal.

#### Correspondencia NEIS/ESRS y GRI

| Evidencia SDS | Dominio NEIS/ESRS | Relación | Referencia GRI | Condición SDS |
|---|---|---|---|---|
| ubicación o zona de origen | E4 biodiversidad y ecosistemas | soporte condicionado | GRI 304 | nivel geográfico proporcional al riesgo y política de publicación definida |
| evidencia de uso del suelo | E4 impactos y dependencias | soporte | GRI 304 | fuente fechada, método conocido y alcance territorial compatible |
| cadena de custodia del lote | diligencia debida en cadena de valor | evidencia operativa | soporte contextual GRI | vínculo entre lote, proveedor o zona y cantidad comprada |
| señal de área sensible | E4 riesgo territorial | revisión requerida | GRI 304 | no publicar conclusión si falta comprobación o hay contradicción |
| restricción de datos sensibles | gobernanza del intercambio | control interno SDS | no aplica como indicador GRI | limita detalle, destinatario, finalidad y retención |

#### Regla de publicación

SDS puede publicar una vista agregada del lote cuando la política permite compartirla y el porcentaje de cobertura está declarado. No debe publicar coordenadas, proveedor nominal o conclusión de cumplimiento si el permiso no lo permite o si la evidencia no cubre el alcance completo.

La salida correcta no es "lote conforme" ni "lote no conforme" por defecto. La salida correcta es un paquete de evidencia: cobertura, brechas, fuente, fecha, método, responsable, política aplicable y decisión de revisión. La conclusión final corresponde al responsable de diligencia debida, auditoría o cumplimiento de la organización.

## 11. Alineación con el marco técnico SDS

| Componente SDS | Papel en E8 | Criterio de uso |
|---|---|---|
| Indicadores y requisitos de información | Conectan cada caso de uso con NEIS/ESRS, GRI y GHG cuando aplica. | Usar relaciones explícitas; no asumir equivalencia total sin evidencia. |
| NGSI-LD | Representa conjuntos de datos e indicadores de forma interoperable. | Citar solo entidades instanciadas o términos técnicos verificados. |
| DCAT-AP | Permite descubrir y describir conjuntos de datos y productos de datos. | Separar metadatos públicos de datos sujetos a control. |
| EDC/ODRL | Aplica políticas de propósito, acceso, obligación, retención y reutilización. | Asociar cada intercambio a una política aprobada. |
| Contratos de cálculo | Versionan reglas, agregaciones, componentes y condiciones de ejecución. | Distinguir dato fuente, cálculo y resultado. |
| Unidades y normalización | Hacen comparables energía, agua, emisiones, residuos y otros dominios. | Documentar unidad fuente, unidad destino y método. |
| Trazabilidad | Conserva fuente, fecha, método, evidencia y responsable. | No publicar resultados sin vínculo al origen y a la versión del método. |

## 12. Próximos pasos

1. Convertir UC-01, UC-02 y UC-03 en pilotos técnicos prioritarios porque combinan alta urgencia, mayor madurez local y valor normativo directo.
2. Formalizar para cada piloto una plantilla mínima de producto de datos con metadatos, responsable, versión, términos de uso, política aplicable, calidad esperada y evidencia de intercambio.
3. Mantener UC-04, UC-05 y UC-06 como segunda ola de despliegue, con especial atención a sensibilidad comercial, datos geográficos y consentimiento.
4. Registrar para cada caso de uso qué relaciones entre marcos son equivalentes, parciales, más amplias o más estrechas, evitando afirmaciones de cobertura total.
5. Mantener sincronizada la evidencia pública de E7 con E8 y E9, preservando en almacenamiento controlado cualquier grabación o material que no deba publicarse.
