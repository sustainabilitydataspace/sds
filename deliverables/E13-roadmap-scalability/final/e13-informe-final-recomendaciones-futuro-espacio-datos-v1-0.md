---
title: "E13 - Informe final con recomendaciones para el futuro del espacio de datos"
subtitle: "Hoja de ruta, escalabilidad, sostenibilidad e interoperabilidad para SustainabilityDataSpace"
date: "2026-06-18"
lang: es-ES
reference-section-title: "Bibliografía y fuentes"
---

# E13 - Informe final con recomendaciones para el futuro del espacio de datos

Proyecto: Espacios de Datos de Sostenibilidad (SDS)\
Entregable: E13 - Informe final con recomendaciones para el futuro del espacio de datos\
Paquete de trabajo: PT6 - Hoja de Ruta y Escalabilidad\
Versión: V1.0 Fecha: 2026-06-18\
Estado: Documento final\
Responsable: Oficina del Programa SDS

## Resumen ejecutivo

Los espacios de datos nacen de una intuición sencilla: una economía moderna no solo necesita producir datos, sino hacerlos circular con confianza. Un dato aislado se parece a una gota de lluvia; puede observarse, medirse y acumularse, pero apenas transforma nada. En cambio, cuando muchas gotas se conectan en un cauce común, aparecen corriente, dirección y capacidad de riego. Ese cauce es lo que la estrategia europea denomina espacios comunes de datos: infraestructuras, reglas y acuerdos para que información de alto valor pueda ser encontrada, compartida y reutilizada sin que sus titulares pierdan control sobre ella.[^eu-data-strategy]

SustainabilityDataSpace (SDS) se sitúa en una zona especialmente relevante de ese mapa: la sostenibilidad empresarial. La sostenibilidad ya no es solo una narrativa anual en PDF. Con CSRD, ESRS, GRI, ISSB, XBRL y las taxonomías digitales, la información ambiental, social y de gobernanza (ASG) se está convirtiendo en una materia prima estructurada, comparable, trazable y cada vez más reutilizable. La pregunta que este entregable aborda es, por tanto, doble: cómo evolucionan los espacios de datos en Europa y qué debe hacer SDS para convertirse, desde su base/prototipo validado, en una pieza útil, interoperable y sostenible dentro de ese ecosistema.

El dossier oficial define el entregable E13 como un informe final con recomendaciones para el futuro del espacio de datos, incluyendo aprendizajes del proyecto, oportunidades de expansión, fortalecimiento de la interoperabilidad y pasos para garantizar la sostenibilidad a largo plazo. También exige cubrir escalabilidad, sostenibilidad, interoperabilidad y recomendaciones técnicas y legales, y que cada sección incluya análisis, conclusiones y recomendaciones prácticas.[^dossier-e13] Este documento responde a ese mandato con una mirada de cierre y, al mismo tiempo, de apertura: no declara que SDS sea ya una solución desplegada de adopción amplia, sino que lo presenta como una base/prototipo validado con una hoja de ruta concreta para escalar.

La conclusión principal es que SDS está bien orientado si conserva tres decisiones de fondo. Primero, debe estar **orientado a estándares**: la confianza del espacio nace de representar correctamente los marcos de información de sostenibilidad y no de inventar una taxonomía propietaria. Segundo, debe aplicar **gobernanza desde el diseño**: las políticas de acceso, uso, trazabilidad y responsabilidad no pueden aparecer al final, como barniz legal, sino estar integradas en cada producto de datos. Tercero, debe convertirse progresivamente en un **puente entre información de sostenibilidad y espacio de datos**: un sistema que ayuda a pasar de documentos de sostenibilidad a datos gobernados, interoperables y reutilizables.

La ruta europea confirma esta dirección. La estrategia europea de datos persigue un mercado único de datos, soberanía, disponibilidad para economía y sociedad, y control por parte de quienes generan los datos.[^eu-data-strategy] El Reglamento de Gobernanza de Datos (Data Governance Act) busca aumentar la confianza en la compartición, facilitar reutilización y apoyar espacios comunes en dominios estratégicos.[^dga] El Reglamento de Datos (Data Act) aclara quién puede usar datos y bajo qué condiciones, y establece requisitos de interoperabilidad para que los datos fluyan entre sectores y Estados miembros.[^data-act] En paralelo, DSSC, Gaia-X, IDSA, Eclipse Dataspace Components y Simpl están convirtiendo esa visión en bloques de gobernanza, confianza, conectores, catálogos, identidad, políticas y herramientas de implementación.[^dssc-edih][^dssc-blueprint][^gaiax-trust][^edc][^simpl]

El ecosistema de información de sostenibilidad se mueve en la misma dirección. EFRAG desarrolla taxonomías XBRL para digitalizar declaraciones ESRS; GRI ha lanzado una taxonomía de sostenibilidad en XBRL alineada con otros estándares; IFRS S1 e IFRS S2 establecen una línea base global para información financiera relacionada con sostenibilidad.[^efrag-xbrl][^gri-taxonomy][^ifrs-s1][^issb-s1-s2] La consecuencia estratégica es clara: el futuro no pertenece al informe estático, sino al dato verificable que puede viajar con contexto, trazabilidad, permiso y significado.

Para SDS, la recomendación es avanzar por cinco fases:

1. **Cierre y estabilización del núcleo SDS**: consolidar evidencia, artefactos, modelo semántico, API, gobernanza y criterios de aceptación.
2. **Preparación para interoperabilidad europea**: mapear SDS contra plan director del DSSC, Reglamento de Datos, Reglamento de Gobernanza de Datos, Gaia-X/IDSA/EDC/Simpl y taxonomías digitales.
3. **Pilotos sectoriales controlados**: ejecutar casos de uso limitados con actores tipo, datos de prueba o datos saneados, y métricas de valor.
4. **Federación y productos de datos**: convertir salidas SDS en productos de datos gobernados, catalogables, trazables y negociables mediante políticas.
5. **Operación sostenible y escalado**: establecer modelo operativo, soporte, ciclo de versionado, medición de impacto, sostenibilidad económica y aprendizaje continuo.

El futuro deseable para SDS no es ser una base de datos grande. Es ser una especie de tejido conectivo: una capa que permite que los datos de sostenibilidad respiren entre normas, organizaciones, auditores, reguladores, herramientas digitales y espacios de datos. Un tejido no se impone por volumen, sino por compatibilidad, resistencia y capacidad de integrarse sin romper lo que une.

## 1. Objeto, alcance y criterio de lectura

### 1.1 Objeto del informe

Este informe define una hoja de ruta para el futuro del espacio de datos SDS. Su función es servir como pieza de cierre del proyecto y como guía práctica para futuros participantes. Por ello combina cuatro planos:

| Plano | Pregunta que responde | Salida esperada |
|---|---|---|
| Estratégico | Hacia dónde se dirige el mercado europeo de datos y la información de sostenibilidad | Visión de futuro y posicionamiento SDS |
| Técnico | Qué capacidades necesita SDS para escalar sin perder interoperabilidad | Hoja de ruta técnica y semántica |
| Legal-gobernanza | Qué reglas y responsabilidades deben acompañar el intercambio | Recomendaciones legales, de gobernanza y de control |
| Operativo | Qué actores, fases, métricas y riesgos permiten llevarlo a práctica | Plan de acción por etapas |

El documento no sustituye a los entregables anteriores. Los toma como base: inventario, interoperabilidad, modelo común, prototipo/API, código técnico, gobernanza, talleres/casos de uso, ajustes, comunicación y difusión. E13 mira hacia delante: pregunta qué debe ocurrir después para que esos resultados no queden como artefactos aislados, sino como una plataforma capaz de evolucionar.

### 1.2 Alcance

El alcance de E13 incluye:

- Estrategias de escalabilidad técnica, organizativa y sectorial;
- Sostenibilidad operativa, económica y de conocimiento;
- Interoperabilidad con espacios de datos europeos, estándares ASG y taxonomías digitales;
- Recomendaciones técnicas, legales y estratégicas;
- Fases de despliegue, actores tipo, riesgos, métricas y pasos siguientes.

El informe utiliza actores tipo, no responsables nominales. Cuando se habla de "operador del espacio de datos", "órgano de gobernanza", "proveedor de datos", "consumidor de datos", "integrador tecnológico" o "auditor/verificador", se hace referencia a funciones que podrían desempeñarse por distintos participantes en una implantación futura.

### 1.3 Criterio de evidencia

El informe se apoya en tres capas de evidencia:

| Capa | Uso en este informe | Límite |
|---|---|---|
| Dossier oficial SDS | Define mandato de E13, PT6, A6.1-A6.3 y R23 | No se copia ni se incorpora como fuente pública bruta |
| Entregables y artefactos SDS | Demuestran la base/prototipo validado que sirve como punto de partida | No convierten automáticamente el prototipo en adopción amplia |
| Fuentes externas oficiales/técnicas | Sitúa SDS en el contexto europeo y de información de sostenibilidad | Se citan como dirección de mercado, regulación o referencia técnica |

La regla de redacción es deliberada: cuando un hecho procede de una fuente, se cita; cuando una frase es recomendación, se formula como recomendación; cuando una proyección mira al futuro, se presenta como escenario razonado, no como certeza.

## 2. Punto de partida: de la obligación de informar al derecho a reutilizar datos

### 2.1 Análisis

Durante años, gran parte de la información de sostenibilidad se comportó como una fotografía anual. La organización recogía datos, preparaba un informe, lo publicaba y lo archivaba. La fotografía podía ser bella o incómoda, pero casi siempre era difícil de reutilizar: los datos quedaban atrapados en formatos, tablas, narrativas y criterios no siempre comparables.

El nuevo ciclo europeo cambia esa lógica. CSRD y ESRS empujan a las empresas a reportar con mayor estructura, trazabilidad y doble materialidad. XBRL y las taxonomías digitales convierten los requisitos de divulgación en datos etiquetados. El Reglamento de Gobernanza de Datos y el Reglamento de Datos empujan hacia confianza, acceso, uso justo e interoperabilidad. Los espacios de datos aportan el marco para que esos datos no vivan en silos, sino en ecosistemas gobernados.

La sostenibilidad se convierte así en un caso de uso privilegiado para los espacios de datos. Sus datos son:

- **Multiactor**: nacen en empresas, cadenas de suministro, auditores, plataformas, administraciones y organismos de estandarización;
- **Multinorma**: deben leerse desde ESRS, GRI, ISSB, taxonomías sectoriales y marcos internos;
- **Multifinalidad**: sirven para cumplimiento, financiación, gestión de riesgos, cadena de suministro, supervisión, evaluación comparativa e innovación;
- **Sensibles**: pueden revelar estrategia, exposición a riesgos, relaciones comerciales o desempeño operativo;
- **Comparables solo si se gobiernan**: sin definición, unidad, periodo, metodología y trazabilidad, el dato pierde valor.

El reto no es solo "tener datos ASG". El reto es que esos datos puedan pasar de una organización a otra como una muestra científica bien etiquetada: con origen, método, fecha, unidad, restricciones de uso y significado. Sin esa etiqueta, el dato viaja como una botella sin mensaje; con ella, puede convertirse en evidencia.

### 2.2 Conclusión

SDS debe posicionarse en la transición entre informe y dato. No basta con ayudar a estructurar informes; debe preparar datos que puedan ser reutilizados dentro de un espacio de datos, manteniendo control, semántica, trazabilidad y gobernanza.

### 2.3 Recomendaciones prácticas

| Recomendación | Actor tipo | Resultado esperado |
|---|---|---|
| Mantener la distinción entre dato operativo, indicador, requisito de divulgación y producto de datos | Custodio semántico / operador SDS | Evitar que una métrica de información se confunda con una observación operativa |
| Definir metadatos mínimos para reutilización | Órgano de gobernanza / integrador tecnológico | Cada dato publicable incluye unidad, periodo, fuente, calidad, versión y restricciones |
| Conectar el modelo de información con políticas de uso | Responsable de cumplimiento / operador SDS | Los datos no solo son legibles, también son compartibles bajo condiciones |

## 3. La ruta oficial europea: del mercado único de datos a los espacios comunes

### 3.1 Análisis

La estrategia europea de datos persigue un mercado único de datos que combine competitividad, soberanía y control por parte de quienes generan la información.[^eu-data-strategy] Esta visión no propone un depósito central único. Propone un continente de sistemas capaces de interoperar: datos que se encuentran, se entienden y se comparten bajo reglas.

El Reglamento de Gobernanza de Datos introduce mecanismos para aumentar la confianza en la compartición y apoyar la creación de espacios comunes de datos en sectores estratégicos como salud, energía, agricultura, movilidad, finanzas, administración pública y competencias.[^dga] Su mensaje para SDS es directo: la gobernanza no es un anexo, es infraestructura.

El Reglamento de Datos refuerza el otro lado de la ecuación: acceso, uso, condiciones justas, derechos sobre datos generados por productos conectados, competencia en nube e interoperabilidad.[^data-act] Para un espacio de datos de sostenibilidad, esto implica que la escalabilidad no puede basarse en integraciones a medida. Debe apoyarse en condiciones claras, interfaces estandarizadas, metadatos y capacidad de fluir entre sectores.

El Centro de Apoyo a los Espacios de Datos (DSSC) traduce esa visión en guías, modelo conceptual, bloques de construcción y selección de estándares. El DSSC no describe solo tecnología; incluye dimensiones de negocio, gobernanza, legal, operación, funcionalidad y técnica.[^dssc-edih] Su plan director organiza capacidades empresariales/organizativas y técnicas, desde modelos de negocio y gobernanza hasta interoperabilidad de datos, soberanía, confianza, trazabilidad y catálogos.[^dssc-blueprint]

Simpl, por su parte, es una señal de madurez de la ruta europea: una plataforma de código abierto de capa intermedia para facilitar acceso e interoperabilidad entre espacios de datos, con componentes como Simpl-Open, Simpl-Labs y Simpl-Live.[^simpl] Cuando una política pública empieza a producir componentes tecnológicos comunes, deja de ser solo una declaración de rumbo y se convierte en una arquitectura de despliegue.

### 3.2 Lectura para SDS

| Elemento de la ruta europea | Implicación para SDS | Recomendación SDS |
|---|---|---|
| Mercado único de datos | SDS debe evitar encierro propietario y favorecer portabilidad | Publicar contratos, esquemas y perfiles de interoperabilidad estables |
| Soberanía y control | Los proveedores deben controlar quién usa qué datos y para qué | Usar políticas de acceso/uso expresables y auditables |
| Espacios comunes sectoriales | SDS debe poder federarse o dialogar con otros espacios | Alinear catálogo, identidad, semántica y gobernanza con patrones DSSC/Gaia-X/IDSA |
| Interoperabilidad legal y técnica | No basta con interfaces API; hacen falta reglas, metadatos y trazabilidad | Mantener un reglamento operativo SDS y perfiles de datos documentados |
| Componentes tecnológicos comunes europeos | Simpl/EDC reducen la necesidad de construir todo desde cero | Evaluar compatibilidad técnica antes de escalar a pilotos federados |

### 3.3 Conclusión

La ruta oficial europea valida el enfoque de SDS, siempre que SDS no se limite a ser una API de sostenibilidad. La evolución correcta es convertirse en un nodo preparado para espacios de datos: catalogable, gobernable, interoperable y capaz de expresar condiciones de uso.

### 3.4 Recomendaciones prácticas

1. Crear una matriz de alineación SDS-DSSC que cubra negocio, gobernanza, legal, operación, funcionalidad y tecnología.
2. Formalizar una plantilla mínima de producto de datos SDS con campos NGSI-LD/DCAT, términos de producto y políticas E6; dejar para piloto procedencia, versión, responsables, calidad, reutilización y evidencia de intercambio.
3. Preparar una prueba técnica de compatibilidad con conectores o capa intermedia de referencia, sin convertirla aún en promesa de producción.
4. Mantener una decisión arquitectónica explícita: SDS no debe competir con el espacio común europeo, sino servir como capa especializada para datos de sostenibilidad dentro de él.

## 4. Lo que se está construyendo en paralelo: convergencia técnica y gobernanza viva

### 4.1 Análisis

Mientras la regulación marca el cauce, el ecosistema técnico construye puentes. Gaia-X, IDSA, EDC, DSSC y Simpl no son piezas idénticas; son capas de una misma conversación europea sobre confianza, soberanía, interoperabilidad y operación.

Gaia-X aporta un marco de confianza que define una línea base para pertenecer al ecosistema, con gobernanza común, interoperabilidad y control por parte de los usuarios. Su marco de confianza se apoya en credenciales verificables, datos enlazados y reglas computables de cumplimiento.[^gaiax-trust] Para SDS, esta orientación confirma que las declaraciones de identidad, conformidad, servicio y recurso deben ser legibles por máquina, verificables y versionadas.

IDSA y Eclipse Dataspace Components empujan la implementación de conectores y protocolos. EDC define un marco técnico con componentes como conector, catálogo federado, centro de identidad, servicio de registro y cuadro de mando, y se apoya en especificaciones de Gaia-X e IDSA para interoperabilidad por diseño.[^edc] Desde la perspectiva de SDS, el conector no es solo un canal técnico: es el punto donde se negocian políticas, se publican ofertas y se mantiene control sobre datos compartidos.

DSSC aporta el lenguaje común. Su plan director identifica como piezas esenciales los reglamentos operativos, los marcos de gobernanza, los modelos de negocio, los aspectos legales, la interoperabilidad de datos, la procedencia, la trazabilidad, la soberanía de los datos y la confianza, junto con los catálogos y los servicios de valor.[^dssc-blueprint] Esa estructura ayuda a evitar un error habitual: pensar que un espacio de datos se construye instalando programas informáticos. La tecnología es necesaria, pero el espacio aparece cuando hay reglas, roles, incentivos, contratos, semántica y operaciones repetibles.

Simpl apunta a la reducción de fricción. Como capa intermedia abierta, modular y segura para espacios de datos europeos, ofrece una base común que puede ayudar a experimentar, probar interoperabilidad y desplegar instancias en espacios sectoriales.[^simpl] En términos prácticos, esto significa que SDS debe prepararse para convivir con componentes comunes, no encapsularse en una pila aislada.

### 4.2 Comparación entre ruta oficial e implementación paralela

| Dimensión | Ruta oficial UE | Implementación paralela | Lectura para SDS |
|---|---|---|---|
| Gobernanza | Confianza, reglas, intermediación, control | reglamentos operativos del DSSC, marco de confianza de Gaia-X | SDS debe tomar el modelo de gobernanza E6 ya definido como línea base de piloto y validarlo con evidencia operativa |
| Interoperabilidad | Requisito para mercado único y espacios comunes | EDC, protocolo de espacio de datos (Dataspace Protocol), Simpl, catálogos | SDS debe probar intercambio con patrones de conector |
| Semántica | Datos reutilizables entre sectores | Modelos, datos enlazados, JSON-LD, vocabularios | SDS ya parte bien si conserva NGSI-LD/JSON-LD/SHACL |
| Soberanía | Control del titular y condiciones claras | Políticas de uso, credenciales, control de cumplimiento | SDS debe unir políticas de gobernanza con entorno de ejecución |
| Escalado | Espacios comunes sectoriales y transfronterizos | Componentes de código abierto, laboratorios, comunidades | SDS debe escalar por pilotos controlados y compatibilidad |

### 4.3 Conclusión

La alineación no depende de adoptar una sola marca tecnológica. Depende de respetar los principios comunes: identidad verificable, catalogación, semántica compartida, políticas, trazabilidad, interoperabilidad de conectores y gobernanza transparente. SDS está alineado cuando sus artefactos se pueden traducir a esos principios.

### 4.4 Recomendaciones prácticas

| Recomendación | Actor tipo | Prioridad | Evidencia de aceptación |
|---|---|---:|---|
| Elaborar una matriz de alineación de SDS con espacios de datos frente a DSSC/Gaia-X/EDC/Simpl | Custodio semántico / integrador tecnológico | Alta | Matriz publicada y revisada por gobernanza |
| Completar un perfil mínimo de conector SDS sobre los artefactos EDC, catálogo y políticas ya existentes | Operador SDS / integrador tecnológico | Alta | Perfil con catálogo, identidad, política y transferencia validado en prueba |
| Separar "API SDS" de "participación en espacio de datos" | Órgano de gobernanza | Alta | Documento de arquitectura que define límites |
| Evaluar Simpl-Labs o entorno equivalente para pruebas | Integrador tecnológico | Media | Informe de compatibilidad con brechas y decisiones |
| Mantener vocabulario común de actores y roles | Órgano de gobernanza | Media | Reglamento operativo SDS con roles tipo y responsabilidades |

## 5. El ecosistema de información de sostenibilidad: de taxonomías a datos vivos

### 5.1 Análisis

La información de sostenibilidad está entrando en una fase de digitalización profunda. CSRD y ESRS amplían el alcance y la estructura de la información corporativa. EFRAG desarrolla la taxonomía XBRL de ESRS para digitalizar declaraciones de sostenibilidad, y esa taxonomía se conecta con el proceso de etiquetado bajo ESEF.[^efrag-xbrl] GRI ha lanzado una taxonomía de sostenibilidad basada en XBRL, pensada para hacer los informes más comparables, recuperables y analizables, alineándose con ESRS e ISSB.[^gri-taxonomy] IFRS S1 exige información sobre riesgos y oportunidades de sostenibilidad útiles para usuarios de informes financieros, incluyendo gobernanza, estrategia, procesos de gestión de riesgos y métricas/objetivos.[^ifrs-s1]

Visto desde SDS, esto significa que la sostenibilidad se convierte en un mapa de datos relacionados. Cada requisito de divulgación ocupa un lugar dentro de una taxonomía, tiene una relevancia determinada por su materialidad, se conecta con otros requisitos e indicadores, y debe conservar su trazabilidad: fuente, fecha, método, unidad y evidencia asociada. Un informe tradicional presenta esos elementos como contenido cerrado; un espacio de datos debe mantenerlos estructurados, verificables y reutilizables para que puedan alimentar otros informes, auditorías, análisis y decisiones.

La interoperabilidad entre ESRS, GRI, ISSB y XBRL no debe entenderse como equivalencia simple. Muchas veces no hay "la misma pregunta con otro nombre", sino preguntas parecidas con ámbitos, unidades, materialidad, límites organizativos y objetivos distintos. Por eso SDS debe evitar la tentación de traducir todo automáticamente. La buena interoperabilidad no aplana diferencias: las hace explícitas.

### 5.2 Implicaciones para SDS

| Tendencia de información de sostenibilidad | Riesgo si SDS no se adapta | Oportunidad para SDS |
|---|---|---|
| ESRS/XBRL e informes digitales | Quedar como capa documental no integrada con taxonomías | Generar datos preparados para tagging, validación y reutilización |
| GRI digital y alineación con otras taxonomías | Duplicación de mapeos y pérdida de trazabilidad | Mantener relaciones revisadas entre estándares |
| ISSB como línea base global | Quedar demasiado centrado en un único marco europeo | Diseñar extensibilidad controlada para nuevos marcos |
| Doble materialidad y materialidad financiera | Confundir impactos, riesgos y métricas operativas | Separar capas de impacto, riesgo, divulgación de información y valor operativo |
| Aseguramiento y auditoría | Datos sin procedencia no aceptables | Incluir trazabilidad, versión, fuente y políticas de uso desde el origen |

### 5.3 Conclusión

SDS puede ocupar una posición valiosa si se convierte en una capa de interoperabilidad entre información digital de sostenibilidad y espacios de datos. No tiene que reemplazar taxonomías oficiales; debe ayudar a operar con ellas, conectar sus conceptos, preservar diferencias y preparar productos de datos reutilizables.

La multifinalidad del dato de sostenibilidad tiene una consecuencia práctica que va más allá del cumplimiento: cuando el dato está bien estructurado, trazado y gobernado, puede alimentar decisiones de gestión sin reconstruir manualmente cada evidencia. Para SDS, esto significa que los productos de datos no deben servir solo para preparar informes o responder auditorías, sino también para aportar señales útiles sobre riesgos, oportunidades, costes y valor operativo. Esta orientación debe pesar en la selección de pilotos.

### 5.4 Recomendaciones prácticas

1. Mantener NEIS (ESRS) y GRI como núcleo formal inicial, incorporando el Protocolo de GEI como cobertura técnica adicional ya trabajada y dejando ISSB y taxonomías XBRL como extensión controlada.
2. Aplicar y mantener la política operacional de relaciones de mapeo: equivalente, parcial, más amplia o más estrecha. `component`, `transform`, `support_context`, `no_match` y estados pendientes forman parte del vocabulario de revisión o de rutas específicas y no deben presentarse como relaciones generalmente consultables. Los identificadores técnicos deben seguir normalizados para evitar falsas equivalencias.
3. Evitar afirmaciones de cobertura total entre marcos salvo que existan revisiones explícitas y evidenciadas.
4. Extender la preparación runtime existente para información digital de sostenibilidad, de modo que la disponibilidad de catálogos, mapeos, contratos de cálculo, unidades, trazabilidad y autoridad de ejecución pueda leerse también como preparación para etiquetado, auditoría y reutilización.
5. Vincular cada recomendación de información de sostenibilidad a un control de gobernanza: quién aprueba la relación, quién la versiona y quién responde si cambia el estándar fuente.

## 6. Posición actual de SDS: una base/prototipo validado

### 6.1 Análisis

SDS parte de una base técnica y documental consistente. Los entregables anteriores han construido las piezas que normalmente aparecen dispersas en un proyecto de datos: inventario de variables, interoperabilidad entre estándares, modelo común NGSI-LD/JSON-LD, prototipo API, código técnico, gobernanza, casos de uso, ajustes, comunicación y evidencia de publicación.

El valor de esa base no está en cada pieza por separado, sino en su encadenamiento. Un inventario sin modelo común es una lista. Un modelo sin API es un plano. Una API sin gobernanza es una puerta sin cerradura. Una gobernanza sin datos ejecutables es una constitución sin ciudad. SDS empieza a ser útil cuando esas piezas se alinean: dato, significado, transformación, acceso, política y evidencia.

La arquitectura pública del proyecto describe SDS como una capa API y de evidencia técnica para catalogar indicadores ASG, importar valores operativos, ejecutar cálculos, gestionar mapeos entre estándares y publicar contratos/controles de gobernanza de datos.[^repo-readme] Los entregables recientes documentan inventario, interoperabilidad, modelo semántico, API/prototipo, código técnico y gobernanza como superficie revisable.[^sds-e1][^sds-e2][^sds-e3][^sds-e4][^sds-e5][^sds-e6]

Sin embargo, una base/prototipo validado no equivale a un ecosistema a escala. Para pasar de prototipo a espacio de datos operativo hacen falta decisiones adicionales: pilotos, actores, contratos, soporte, ciclos de versionado, seguridad operacional, modelo económico, catalogación, conectores y medición de valor.

### 6.2 Fortalezas

| Fortaleza | Por qué importa | Riesgo asociado si no se conserva |
|---|---|---|
| Base orientada a estándares | Facilita alineación con información de sostenibilidad y evita taxonomía aislada | Deriva propietaria y baja comparabilidad |
| Modelo semántico y validación | Permite datos legibles por máquina y controlables | Datos correctos en apariencia pero ambiguos |
| API y entorno de ejecución técnico | Da superficie ejecutable al modelo | Entregables documentales sin operación |
| Gobernanza E6 | Introduce políticas, roles, auditoría y facultades de decisión | Intercambio sin confianza ni responsabilidad |
| Separación entre evidencia y declaraciones no respaldadas | Protege credibilidad del proyecto | Declaraciones demasiado amplias o no verificables |

### 6.3 Brechas normales antes de escalar

| Brecha | Descripción | Tratamiento recomendado |
|---|---|---|
| Validación con actores externos | El prototipo necesita pilotos controlados con usuarios/roles reales o representativos | Fase 3 de pilotos sectoriales |
| Compatibilidad con conectores | API SDS no equivale automáticamente a conector de espacio de datos | Perfil de conector y prueba técnica |
| Modelo operativo | Falta rutina de soporte, versionado, incidentes y responsabilidades permanentes | Modelo operativo y gestión del servicio |
| Modelo de sostenibilidad económica | El escalado requiere costes, beneficios e incentivos | Lienzo de modelo de negocio de espacio de datos |
| Hoja de ruta legal detallada | Las recomendaciones legales necesitan concreción por caso de uso | Revisión legal por piloto |

### 6.4 Conclusión

SDS no debe presentarse como una ciudad terminada, sino como una infraestructura urbana ya trazada: calles principales, normas de circulación, puntos de acceso y planos técnicos. La siguiente tarea no es redibujar el mapa, sino empezar a poblarlo con pilotos gobernados y conexiones interoperables.

### 6.5 Recomendaciones prácticas

1. Mantener la etiqueta de "base/prototipo validado" hasta que existan pilotos operativos con evidencia.
2. Convertir los entregables en una hoja de ruta de producto: qué parte es núcleo, qué parte es opción, qué parte requiere piloto.
3. Crear un tablero de madurez SDS con niveles: documental, técnico, piloto, federado, operativo.
4. Vincular cada nivel a pruebas verificables: esquemas, API, políticas, conectores, usuarios, soporte, disponibilidad y auditoría.

## 7. Hoja de ruta propuesta

### 7.1 Principio de diseño

La hoja de ruta SDS debe crecer como un árbol, no como una torre. Una torre añade pisos sobre un único centro de gravedad; si el cimiento falla, todo cae. Un árbol distribuye crecimiento: raíces semánticas, tronco de gobernanza, ramas de casos de uso y hojas de productos de datos. La escalabilidad sostenible se parece más al árbol: modular, adaptable y resistente.

### 7.2 Fases de despliegue

| Fase | Objetivo | Actores tipo | Salidas | Criterio de avance |
|---|---|---|---|---|
| 1. Estabilización del núcleo | Consolidar entregables, modelo, API, gobernanza y evidencia | Operador SDS, custodio semántico, responsable de calidad | Artefactos versionados, índices, pruebas/controles, reglamento operativo inicial | Evidencia reproducible y sin declaraciones no respaldadas |
| 2. Alineación europea | Mapear SDS contra DSSC, Reglamento de Datos, Reglamento de Gobernanza de Datos, Gaia-X/IDSA/EDC/Simpl y información digital de sostenibilidad | Custodio semántico, responsable legal, integrador | Matriz de alineación, brechas, decisiones arquitectónicas | Brechas clasificadas por criticidad |
| 3. Pilotos sectoriales controlados | Probar valor en casos limitados con datos de prueba/saneados | Proveedor de datos, consumidor, auditor, operador | Caso de uso, producto de datos, políticas, medición | Piloto completado con métricas y lecciones |
| 4. Federación técnica | Preparar catalogación, identidad, políticas y conector | Integrador tecnológico, operador, órgano de gobernanza | Perfil de conector, catálogo, políticas de uso, pruebas de intercambio | Transferencia o simulación federada validada |
| 5. Operación sostenible | Convertir SDS en servicio repetible | Operador, soporte, gobernanza, comunidad sectorial | Modelo operativo, soporte, versionado, financiación, mejora continua | Servicio con acuerdo de nivel de servicio interno y ciclo de gobierno |

### 7.3 Hoja de ruta temporal orientativo

El calendario debe adaptarse a la disponibilidad de actores y pilotos, pero puede estructurarse en tres horizontes:

| Horizonte | Enfoque | Resultado esperado |
|---|---|---|
| Corto plazo: 0-6 meses | Cierre de versión 1.0, matriz de alineación, selección de pilotos, preparación de perfiles de producto de datos | SDS listo para piloto gobernado |
| Medio plazo: 6-18 meses | Ejecución de pilotos, compatibilidad con patrones de conector, validación de taxonomías digitales, soporte operativo inicial | SDS probado en contexto sectorial |
| Largo plazo: 18-36 meses | Federación, catálogo, productos de datos reutilizables, comunidad, modelo económico y mejora continua | SDS como componente de ecosistema |

### 7.4 Controles de decisión

| Control | Pregunta | Evidencia requerida |
|---|---|---|
| Control A - Preparación | Puede SDS explicar qué datos ofrece, bajo qué política y con qué significado? | Perfil de producto de datos + reglamento operativo |
| Control B - Piloto | Puede un actor usar SDS para un caso concreto sin asistencia artesanal? | Piloto documentado + soporte mínimo |
| Control C - Interoperabilidad | ¿Puede SDS intercambiar o exponer información según patrones de espacio de datos? | Prueba con conector/capa intermedia o simulación equivalente |
| Control D - Escalabilidad | ¿Puede repetirse el proceso en otro sector o caso de uso? | Método replicable + métrica de coste/esfuerzo |
| Control E - Sostenibilidad | Tiene SDS responsables, costes, beneficios y ciclo de versionado? | Modelo operativo y económico aprobado |

### 7.5 Conclusión

El camino correcto no es saltar directamente a "gran plataforma europea". Es avanzar por umbrales verificables. Cada fase debe producir una prueba de madurez, no solo una promesa.

## 8. Escalabilidad

### 8.1 Escalabilidad técnica

SDS debe escalar sin perder trazabilidad. En sostenibilidad, más datos no siempre significan más valor; a veces solo significan más ruido. La escalabilidad técnica debe medirse por la capacidad de incorporar nuevos estándares, indicadores, unidades, actores y productos de datos manteniendo validación, versionado y políticas.

| Dimensión técnica | Estado deseado | Recomendación |
|---|---|---|
| Modelos y taxonomías | Extensibles sin romper contratos existentes | Versionar vocabularios y mantener compatibilidad |
| API y entorno de ejecución | orientada a base de datos, trazable y con controles de permisos | Separar perfiles piloto, prueba y operación |
| Datos y cálculos | Importables con validación y linaje | Mantener manifiestos, checksums y trazas |
| Mapeos entre estándares | Revisados, tipados, no automáticos por defecto | Separar borrador, revisado y publicado |
| Conector/espacio de datos | Intercambio federado bajo políticas | Probar perfil de conector antes de adoptar tecnología definitiva |

### 8.2 Escalabilidad organizativa

La organización debe crecer antes que el volumen. Un espacio de datos pequeño con reglas claras escala mejor que una plataforma grande con responsabilidades difusas.

| Capacidad organizativa | Actor tipo responsable | Recomendación |
|---|---|---|
| Decisión de cambios semánticos | Custodio semántico | Comité ligero de revisión de estándares y mapeos |
| Políticas y contratos | Órgano de gobernanza / responsable legal | Reglamento operativo versionado y plantillas de términos |
| Operación | Operador SDS | Manual operativo de despliegue, soporte, incidentes y versionado |
| Calidad | Auditor/verificador | Controls reproducibles y evidencias de control |
| Comunidad | Comunidad sectorial | Canal de comentarios y priorización de casos |

### 8.3 Escalabilidad sectorial y geográfica

SDS debe escalar por adyacencia, no por salto brusco. La expansión debe comenzar por sectores donde:

- Exista presión regulatoria o de cadena de valor sobre datos de sostenibilidad;
- Los indicadores tengan suficiente estabilidad;
- El intercambio de datos genere valor claro para más de un actor;
- La sensibilidad de los datos pueda gobernarse con políticas;
- Haya capacidad técnica para integrarse con API, catálogos o conectores.

| Tipo de sector candidato | Potencial de valor | Riesgo principal | Enfoque recomendado |
|---|---|---|---|
| Sectores intensivos en energía/emisiones | Alto por datos GEI y energía | Calidad y granularidad de datos | Piloto de indicadores prioritarios |
| Cadenas de suministro reguladas | Alto por trazabilidad y comparabilidad | Confidencialidad comercial | Políticas de uso y agregación |
| Organizaciones con obligaciones CSRD | Alto por obligación estructurada | Sobrecarga de cumplimiento | Reutilización de datos de información de sostenibilidad |
| Ecosistemas con datos públicos/privados mixtos | Medio-alto | Gobernanza de acceso | Producto de datos con restricciones claras |

### 8.4 Conclusión

La escalabilidad de SDS debe entenderse como capacidad de replicar confianza. La tecnología permite volumen; la gobernanza permite continuidad; la semántica permite comparabilidad.

### 8.5 Recomendaciones prácticas

1. Definir un modelo de madurez SDS de cinco niveles.
2. Seleccionar pilotos por valor, viabilidad y riesgo de datos.
3. Consolidar la guía operativa de incorporación de nuevos estándares sobre los flujos existentes de validación, importación, materialización y controles de estándares instalados.
4. Mantener tablas de compatibilidad de versiones para API, taxonomías, mapeos y políticas.
5. Medir coste de incorporación de un nuevo caso de uso como métrica de escalabilidad real.

## 9. Sostenibilidad del ecosistema SDS

### 9.1 Análisis

La sostenibilidad de un espacio de datos tiene tres capas: técnica, económica e institucional. La técnica pregunta si el sistema puede mantenerse. La económica pregunta si alguien tiene incentivos para sostenerlo. La institucional pregunta si las reglas siguen siendo legítimas cuando cambian los actores.

SDS debe evitar depender de heroísmo técnico. Un prototipo puede sobrevivir por la energía de un equipo pequeño; un espacio de datos necesita procesos que funcionen aunque cambien personas, versiones, normas y prioridades.

### 9.2 Modelo de sostenibilidad

| Capa | Riesgo si falta | Recomendación |
|---|---|---|
| Técnica | Deuda, incompatibilidad, obsolescencia | Versionado, pruebas, documentación, contratos estables |
| Económica | Falta de mantenimiento o adopción | Modelo de valor para proveedores, consumidores y operadores |
| Gobernanza | Conflictos, baja confianza, decisiones opacas | Reglamento operativo, órgano de decisión, revisión periódica |
| Conocimiento | Dependencia de expertos concretos | Documentación, formación, trazabilidad y incorporación |
| Comunidad | Aislamiento del proyecto | Participación en ecosistemas espacios de datos e información de sostenibilidad |

### 9.3 Opciones de modelo operativo

| Modelo | Ventaja | Límite | Uso recomendado |
|---|---|---|---|
| Operador único | Rapidez y coherencia | Riesgo de dependencia central | Fases iniciales y pilotos |
| Gobernanza federada | Mayor legitimidad | Mayor coordinación | Fase de escalado multisector |
| Comunidad técnica abierta | Innovación y transparencia | Requiere curación y soporte | Componentes, perfiles y ejemplos |
| Servicio gestionado | Continuidad operativa | Coste recurrente | Cuando haya demanda estable |

### 9.4 Conclusión

La sostenibilidad de SDS no vendrá de "mantener la tecnología funcionando" solamente. Vendrá de mantener viva la relación entre reglas, valor y confianza. Un espacio de datos muere cuando nadie confía en él, aunque sus servidores respondan.

### 9.5 Recomendaciones prácticas

1. Definir costes mínimos de operación: infraestructura, mantenimiento, soporte, revisión semántica, gobernanza y seguridad.
2. Separar funciones permanentes de funciones de proyecto: operador, custodio semántico, soporte, órgano de gobernanza.
3. Crear ciclos de revisión: trimestral para pilotos, semestral para hoja de ruta, anual para reglamento operativo y modelo económico.
4. Documentar registros de decisión para cambios de taxonomía, políticas, API y mapeos.
5. Medir valor no solo por número de conjuntos de datos, sino por reutilizaciones verificadas y reducción de fricción de preparación de información.

## 10. Interoperabilidad: técnica, semántica, legal y operativa

### 10.1 Análisis

Interoperar no es simplemente conectar dos sistemas. Dos personas pueden hablar por teléfono y no entenderse; dos interfaces API pueden intercambiar JSON y no compartir significado. La interoperabilidad de SDS debe operar en cuatro niveles:

| Nivel | Pregunta | Ejemplo SDS |
|---|---|---|
| Técnico | ¿Podemos intercambiar datos? | API, NGSI-LD, JSON-LD, conectores, catálogos |
| Semántico | ¿Entendemos lo mismo por cada dato? | Indicadores, unidades, requisitos de divulgación, mapeos |
| Legal/gobernanza | ¿Podemos usar el dato bajo reglas claras? | Políticas, contratos, finalidad, retención |
| Operativo | ¿Podemos repetir el proceso con soporte y control? | Incorporación, manuales operativos, auditoría, incidentes |

El error común es invertir solo en el primer nivel. SDS debe evitarlo. En sostenibilidad, una unidad mal interpretada, un periodo ambiguo o una equivalencia forzada pueden tener más impacto que un error de transporte.

### 10.2 Interoperabilidad con espacios de datos

Para conectarse al ecosistema de espacio de datos, SDS debe preparar:

- Catálogos de productos de datos;
- Metadatos de procedencia, calidad y versión;
- Políticas de acceso y uso;
- Identidad y roles de participantes;
- Perfiles de intercambio;
- Trazabilidad de negociación, acceso y uso.

### 10.3 Interoperabilidad con información de sostenibilidad

Para conectarse al ecosistema de sostenibilidad, SDS debe preparar:

- Correspondencias controladas entre ESRS, GRI, ISSB y otros marcos;
- Distinción entre requisito de divulgación, punto de dato, indicador, valor y evidencia;
- Gestión de unidades y periodos;
- Trazabilidad de fuente normativa;
- Compatibilidad conceptual con taxonomías XBRL;
- Control de cambios cuando evoluciona el estándar.

### 10.4 Recomendaciones técnicas

| Recomendación | Beneficio | Evidencia esperada |
|---|---|---|
| Mantener JSON-LD/NGSI-LD como capa de publicación semántica | Facilita datos enlazados e interoperabilidad | Contextos versionados y validados |
| Mantener publicados y versionados los esquemas y restricciones de validación ya existentes | Reduce ambigüedad en integraciones | JSON Schema/SHACL con pruebas |
| Versionar mapeos y relaciones | Evita equivalencias opacas | Registro de relaciones revisadas |
| Consolidar perfiles de producto de datos sobre NGSI-LD/DCAT, términos de producto y políticas | Conecta API con espacio de datos | Plantilla de producto de datos + política |
| Preparar perfil de conector | Reduce salto hacia federación | Prueba con EDC/Simpl o entorno equivalente |

### 10.5 Recomendaciones legales y de gobernanza

| Recomendación | Actor tipo | Resultado |
|---|---|---|
| Aplicar y versionar las finalidades permitidas ya registradas | Responsable legal / órgano de gobernanza | Uso del dato vinculado a finalidad |
| Mantener condiciones de conservación vinculadas a políticas y evidencias | Responsable de cumplimiento | Menor riesgo de uso indefinido |
| Crear modelo de consentimiento/legitimación cuando aplique | Responsable legal | Base clara para datos personales o sensibles |
| Aplicar y completar reglas de agregación y anonimización donde el piloto lo exija | Auditor/verificador | Menor exposición de datos sensibles |
| Versionar el reglamento operativo SDS | Órgano de gobernanza | Cambios trazables y revisables |

### 10.6 Conclusión

La interoperabilidad real es una cadena. Si un eslabón falla, el dato viaja pero no sirve, sirve pero no se puede usar, o se puede usar pero no se puede defender. SDS debe diseñar la cadena completa.

## 11. Recomendaciones accionables para SDS

### 11.1 Recomendaciones estratégicas

| Código | Recomendación | Actor tipo | Horizonte | Indicador de éxito |
|---|---|---|---|---|
| E13-R01 | Posicionar SDS como capa especializada de datos de sostenibilidad para espacios de datos europeos | Órgano de gobernanza | Corto | Mensaje aprobado y consistente en docs |
| E13-R02 | Priorizar casos de uso donde el dato estructurado reduzca fricción de información de sostenibilidad o auditoría | Comunidad sectorial / operador | Corto | Lista priorizada de pilotos |
| E13-R03 | Mantener una política contra declaraciones no respaldadas para cobertura, interoperabilidad y preparación | Auditor/verificador | Permanente | Afirmaciones trazadas a evidencia |
| E13-R04 | Construir alianzas por roles, no por dependencia única | Órgano de gobernanza | Medio | Ecosistema de actores tipo cubierto |

### 11.2 Recomendaciones técnicas

| Código | Recomendación | Actor tipo | Horizonte | Indicador de éxito |
|---|---|---|---|---|
| E13-R05 | Consolidar perfil SDS de producto de datos | Custodio semántico | Corto | Plantilla con campos mínimos, políticas y trazabilidad |
| E13-R06 | Preparar una matriz de compatibilidad con DSSC/Gaia-X/EDC/Simpl | Integrador tecnológico | Corto | Brechas clasificadas |
| E13-R07 | Mantener mapeos revisados separados de candidatos | Custodio semántico | Permanente | Estados de relación visibles |
| E13-R08 | Crear prueba de catalogación federada | Integrador tecnológico | Medio | Producto SDS visible en catálogo de prueba |
| E13-R09 | Completar compatibilidad conceptual con XBRL | Custodio semántico | Medio | Mapa de requisito de divulgación/punto de dato/taxonomía |

### 11.3 Recomendaciones legales y de gobernanza

| Código | Recomendación | Actor tipo | Horizonte | Indicador de éxito |
|---|---|---|---|---|
| E13-R10 | Aprobar versión operativa del reglamento SDS | Órgano de gobernanza | Corto | Versión 1 aprobada |
| E13-R11 | Operacionalizar la asociación de cada producto de datos a finalidad y política | Responsable legal / operador | Corto | Políticas vinculadas a productos |
| E13-R12 | Mantener y extender el proceso de cambios normativos | Custodio semántico / legal | Medio | acuerdo de nivel de servicio para actualización de estándares |
| E13-R13 | Crear registro de decisiones de gobernanza | Órgano de gobernanza | Permanente | Decisiones trazables |
| E13-R14 | Preparar criterios de aseguramiento | Auditor/verificador | Medio | Lista de comprobación de evidencia y trazabilidad |

### 11.4 Recomendaciones operativas

| Código | Recomendación | Actor tipo | Horizonte | Indicador de éxito |
|---|---|---|---|---|
| E13-R15 | Crear guía operativa de piloto SDS | Operador | Corto | Guía reutilizable |
| E13-R16 | Definir soporte mínimo para participantes | Operador / integrador | Medio | Canal, tiempos y preguntas frecuentes |
| E13-R17 | Medir coste de incorporación de nuevo caso | Operador | Medio | Horas/esfuerzo por piloto |
| E13-R18 | Establecer ciclo de versiones | Operador / calidad | Permanente | Versiones con registro de cambios |
| E13-R19 | Mantener formación para actores tipo | Comunidad sectorial | Medio | Materiales y sesiones |

### 11.5 Recomendaciones de sostenibilidad económica

| Código | Recomendación | Actor tipo | Horizonte | Indicador de éxito |
|---|---|---|---|---|
| E13-R20 | Definir propuesta de valor por actor | Órgano de gobernanza | Corto | Mapa de valor por actor, incluyendo fricción reducida, señales de riesgo/oportunidad, coste de uso y valor protegido o creado en al menos un caso piloto |
| E13-R21 | Separar costes de plataforma, gobernanza y soporte | Operador | Corto | Modelo de costes mínimo |
| E13-R22 | Explorar servicios de valor sobre datos gobernados | Operador / comunidad | Medio | Lista de servicios priorizados |
| E13-R23 | Medir reutilización efectiva | Auditor/verificador | Permanente | Número de usos verificables por producto |

## 12. Riesgos y mitigaciones

### 12.1 Matriz de riesgos

| Riesgo | Impacto | Probabilidad | Mitigación |
|---|---:|---:|---|
| Sobredeclarar madurez de SDS | Alto | Media | Mantener lenguaje de base/prototipo validado hasta evidencia de pilotos |
| Forzar equivalencias entre estándares | Alto | Media | Usar tipologías de relación y revisión semántica |
| Falta de adopción por actores | Alto | Media | Pilotos con valor claro y bajo coste de entrada |
| Complejidad legal no resuelta | Alto | Media | Reglamento operativo, revisión legal por caso y políticas explícitas |
| Dependencia de pila técnica única | Medio | Media | Arquitectura modular y pruebas con componentes comunes |
| Deriva de estándares ASG | Medio | Alta | Proceso de actualización y versionado |
| Datos de baja calidad | Alto | Media | Validación, procedencia, calidad y rechazo controlado |
| Falta de sostenibilidad económica | Alto | Media | Modelo de costes y propuesta de valor por actor |
| Fragmentación semántica sectorial | Medio | Media | Vocabularios base + extensiones gobernadas |
| Riesgos de privacidad/confidencialidad | Alto | Media | Políticas, minimización, agregación y control de acceso |

### 12.2 Riesgos de comunicación

E13 debe evitar dos extremos. El primero es la modestia excesiva: presentar SDS como un simple documento técnico cuando ya integra capas de modelo, API, gobernanza y evidencia. El segundo es la exageración: presentarlo como infraestructura europea consolidada sin pilotos federados suficientes. El tono correcto es el de una base validada que sabe lo que es y sabe lo que le falta.

### 12.3 Conclusión

El riesgo principal de SDS no es técnico; es perder precisión narrativa. Si SDS explica con rigor su madurez, su rol y sus límites, gana credibilidad. Si promete más de lo que puede demostrar, pierde la confianza que justamente quiere habilitar.

## 13. Métricas de éxito

### 13.1 Métricas de madurez

| Dimensión | Métrica | Umbral inicial recomendado |
|---|---|---|
| Evidencia | Porcentaje de afirmaciones con fuente o control | 100% en documentos públicos |
| Semántica | Relaciones revisadas frente a candidatas | Aumentar revisadas sin eliminar diferencias |
| Interoperabilidad | Productos de datos con perfil completo | 1-3 en pilotos iniciales |
| Gobernanza | Políticas vinculadas a productos | 100% de productos piloto |
| Operación | Tiempo de incorporación de caso de uso | Medido y reducido por iteración |
| Calidad | Incidencias de validación por lote | Tendencia descendente |
| Reutilización | Usos verificados de un producto de datos | Al menos 1 por piloto |
| Sostenibilidad | Coste operativo mínimo documentado | Cubierto antes de fase federada |

### 13.2 Métricas de alineación europea

| Area | Indicador |
|---|---|
| DSSC | Bloques de negocio/gobernanza/legal/técnicos mapeados |
| Reglamento de Datos / Reglamento de Gobernanza de Datos | Políticas de acceso, uso, interoperabilidad y confianza identificadas |
| Gaia-X/IDSA/EDC | Componentes o conceptos equivalentes identificados |
| Simpl | Compatibilidad evaluada o decisión justificada |
| Información digital de sostenibilidad | Mapa ESRS/GRI/ISSB/XBRL con brechas y decisiones |

### 13.3 Métricas de impacto

Las métricas de impacto deben evitar inflarse con actividad. No basta contar reuniones, conjuntos de datos o puntos de acceso. El impacto real aparece cuando un actor consigue hacer algo que antes era difícil:

- Preparar un dato de sostenibilidad una vez y reutilizarlo en varios contextos;
- Explicar la procedencia de un valor sin reconstruir manualmente su historia;
- Responder a una necesidad de auditoría o cadena de suministro con menos fricción;
- Comparar marcos de información de sostenibilidad sin borrar sus diferencias;
- Compartir datos bajo reglas comprensibles y verificables;
- Acceder a una visión de riesgos u oportunidades de sostenibilidad útil para la toma de decisiones sin agregación manual desde múltiples fuentes desconectadas.

## 14. Modelo de actores tipo

| Actor tipo | Responsabilidad principal | Necesidad dentro de SDS |
|---|---|---|
| Operador del espacio de datos | Mantener servicio, soporte, catálogo y operación | Manual operativo, acuerdo de nivel de servicio, versionado, gestión de incidentes |
| Órgano de gobernanza | Aprobar reglas, prioridades y cambios | Reglamento operativo, registros de decisión, calendario de revisión |
| Proveedor de datos | Publicar datos o productos de datos | Plantillas, validación, políticas, comentarios |
| Consumidor de datos | Usar datos bajo condiciones | Catálogo, términos claros, interfaces API/documentación |
| Custodio semántico | Mantener modelos, estándares y relaciones | Proceso de mapeo, versionado, QA semántico |
| Responsable de cumplimiento | Revisar finalidades, contratos y riesgos | Matriz legal, políticas y evidencias |
| Integrador tecnológico | Conectar SDS con sistemas y espacio de datos | interfaces API, perfiles de conector, guías técnicas |
| Auditor/verificador | Revisar evidencia, trazabilidad y controles | Registros, manifiestos, controles, lista de comprobación de aseguramiento |
| Comunidad sectorial | Priorizar casos y validar utilidad | Foro, hoja de ruta y pilotos |

## 15. Escenarios de futuro

### 15.1 Escenario conservador: SDS como herramienta de preparación de datos

SDS se usa para estructurar inventarios, mapeos, API y evidencia de sostenibilidad dentro de organizaciones o proyectos. No se federa plenamente, pero reduce fricción de preparación de información y mejora trazabilidad.

**Valor:** bajo riesgo, adopción gradual.\
**Límite:** menor impacto ecosistémico.\
**Condición de éxito:** documentación, soporte y compatibilidad con información digital de sostenibilidad.

### 15.2 Escenario intermedio: SDS como nodo especializado en pilotos de espacio de datos

SDS participa en pilotos sectoriales con productos de datos de sostenibilidad, políticas y catalogación. Se prueban conectores o capa intermedia, sin prometer aún operación masiva.

**Valor:** aprendizaje real y validación externa.\
**Límite:** requiere coordinación de actores.\
**Condición de éxito:** pilotos con métricas, gobernanza y productos de datos claros.

### 15.3 Escenario ambicioso: SDS como capa de referencia para sostenibilidad en espacios de datos

SDS se convierte en una capa interoperable de referencia para datos ASG, conectada con información digital de sostenibilidad, taxonomías, productos de datos y espacios de datos.

**Valor:** alto impacto y posicionamiento europeo.\
**Límite:** alta exigencia de gobernanza, soporte, sostenibilidad económica y alineación normativa.\
**Condición de éxito:** ecosistema de adopción, modelo económico y validación federada.

### 15.4 Escenario recomendado

El escenario recomendado es el intermedio con opción de evolución ambiciosa. Primero, pilotos gobernados; después, federación. Primero, evidencia; después, escala. Primero, confianza; después, mercado.

## 16. Índice aprobado de cobertura R23

R23 exige cubrir el 100% de los temas establecidos en el Índice aprobado, con análisis, conclusiones y recomendaciones prácticas. La siguiente matriz muestra la cobertura de este informe:

| Tema R23/E13 | Secciones que lo cubren | Evidencia de cobertura |
|---|---|---|
| Escalabilidad | 7, 8, 13, 15 | Fases, horizontes, métricas y modelo de madurez |
| Sostenibilidad | 9, 11, 13, 15 | Modelo técnico/económico/institucional y recomendaciones |
| Interoperabilidad | 3, 4, 5, 10 | Ruta UE, iniciativas paralelas, información de sostenibilidad y niveles de interoperabilidad |
| Recomendaciones técnicas | 10.4, 11.2 | Perfil de producto consolidado, conector, esquemas, mapeos |
| Recomendaciones legales | 10.5, 11.3 | Finalidades versionadas, conservación, reglamento operativo, cambios normativos |
| Recomendaciones estratégicas | 11.1, 15 | Posicionamiento y escenarios |
| Oportunidades de expansión | 8.3, 15 | Sectores candidatos y escenarios |
| Adaptación a nuevos desafíos | 12, 13 | Riesgos, mitigaciones y métricas |
| Guía para futuros participantes | 7, 14 | Fases, actores tipo y responsabilidades |

## 17. Conclusiones finales

SDS aparece al final del proyecto como una base/prototipo validado con una virtud principal: no trata la sostenibilidad como una historia aislada, sino como un sistema de datos. Esa virtud importa porque Europa está moviéndose precisamente hacia ahí: datos que circulan con soberanía, reglas, interoperabilidad y confianza.

La visión de futuro no es una nube abstracta donde todos los datos se mezclan. Cada organización conserva una parte de la evidencia de sostenibilidad: emisiones, energía, agua, personas, gobernanza, riesgos e impactos. Los espacios de datos permiten que esa evidencia mantenga su origen, sus permisos y su trazabilidad y, aun así, pueda compararse, combinarse y reutilizarse. SDS debe aspirar a ser una de las capas que hacen ese intercambio más preciso y defendible.

La recomendación de cierre es avanzar con disciplina:

1. cerrar y revisar la versión 1.0 de E13;
2. convertir el prototipo en pilotos gobernados;
3. mapear SDS contra la infraestructura europea de espacio de datos;
4. preparar productos de datos de sostenibilidad reutilizables;
5. medir valor, no solo actividad;
6. mantener la honestidad evidencial que protege la confianza del proyecto.

Si SDS conserva esa disciplina, su aportación puede ser concreta y necesaria: ayudar a que los datos de sostenibilidad dejen de ser una colección de informes y se conviertan en una infraestructura compartida de conocimiento responsable.

## Bibliografía y fuentes

Esta sección consolida las fuentes utilizadas en el informe. Las notas a pie mantienen la referencia puntual de cada afirmación o bloque de análisis.

- **Fuentes del proyecto SDS**: memoria técnica oficial del proyecto SustainabilityDataSpace; `README.md` público del repositorio SDS; entregables E1 a E6 en sus versiones DOCX finales: `e01-inventario-datos-clave-variables-brutas-v2026-06-09.docx`, `e02-informe-interoperabilidad-correspondencias-v2026-06-09.docx`, `e03-modelo-comun-ngsi-ld-paquete-semantico-v2026-06-09.docx`, `e04-prototipo-api-transformacion-datos-v2026-06-09.docx`, `e05-codigo-tecnico-modelo-v2026-06-09.docx` y `e06-gobernanza-politica-datos-v2026-06-09.docx`.
- **Estrategia y normativa europea de datos**: Comisión Europea, *A European strategy for data*; Comisión Europea, *European Data Governance Act*; Comisión Europea, *Data Act explained*.
- **Espacios de datos e infraestructura europea**: European Digital Innovation Hubs Network, *Data Spaces Support Centre*; Data Spaces Support Centre, *Introduction - Key Concepts of Data Spaces - Blueprint v2.0*; Gaia-X, *Gaia-X Trust Framework - 22.10 Release*; Eclipse Foundation, *Eclipse Dataspace Components (EDC)*; Comisión Europea, *Simpl: Cloud-to-edge federations empowering EU data spaces*.
- **Información digital de sostenibilidad**: EFRAG, *Digital Reporting with XBRL* y *ESRS XBRL Taxonomy*; GRI, *A digital leap forward for sustainability reporting*; IFRS Foundation, *IFRS S1 General Requirements for Disclosure of Sustainability-related Financial Information*; IFRS Foundation, *ISSB issues inaugural global sustainability disclosure standards*.

[^dossier-e13]: Memoria técnica oficial del proyecto SustainabilityDataSpace, PT6 "Hoja de Ruta y Escalabilidad", actividades A6.1-A6.3, entregable E13 y requisito R23. Fuente externa/local controlada; no incorporada como artefacto público bruto.

[^eu-data-strategy]: European Commission, "A European strategy for data", Shaping Europe's digital future, https://digital-strategy.ec.europa.eu/en/policies/strategy-data.

[^dga]: European Commission, "European Data Governance Act", Shaping Europe's digital future, https://digital-strategy.ec.europa.eu/en/policies/data-governance-act.

[^data-act]: European Commission, "Data Act explained", Shaping Europe's digital future, https://digital-strategy.ec.europa.eu/en/factpages/data-act-explained.

[^dssc-edih]: European Digital Innovation Hubs Network, "Data Spaces Support Centre", https://european-digital-innovation-hubs.ec.europa.eu/knowledge-hub/digitisation-projects-and-initiatives/data-spaces-support-centre.

[^dssc-blueprint]: Data Spaces Support Centre, "Introduction - Key Concepts of Data Spaces - Blueprint v2.0", https://dataspacessupportcentre.atlassian.net/wiki/spaces/BVE2/pages/1071251613/Introduction%2B-%2BKey%2BConcepts%2Bof%2BData%2BSpaces.

[^gaiax-trust]: Gaia-X, "Gaia-X Trust Framework - 22.10 Release", https://docs.gaia-x.eu/policy-rules-committee/trust-framework/22.10/.

[^edc]: Eclipse Foundation, "Eclipse Dataspace Components (EDC)", https://projects.eclipse.org/projects/technology.edc.

[^simpl]: European Commission, "Simpl: Cloud-to-edge federations empowering EU data spaces", https://digital-strategy.ec.europa.eu/en/policies/simpl.

[^efrag-xbrl]: EFRAG, "Digital Reporting with XBRL", https://www.efrag.org/en/sustainability-reporting/esrs-workstreams/digital-reporting-with-xbrl; EFRAG, "ESRS XBRL Taxonomy", https://www.efrag.org/en/projects/esrs-xbrl-taxonomy/concluded.

[^gri-taxonomy]: GRI, "A digital leap forward for sustainability reporting", 19 June 2025, https://www.globalreporting.org/news/news-center/a-digital-leap-forward-for-sustainability-reporting/.

[^ifrs-s1]: IFRS Foundation, "IFRS S1 General Requirements for Disclosure of Sustainability-related Financial Information", https://www.ifrs.org/issued-standards/ifrs-sustainability-standards-navigator/ifrs-s1-general-requirements/.

[^issb-s1-s2]: IFRS Foundation, "ISSB issues inaugural global sustainability disclosure standards", https://www.ifrs.org/news-and-events/news/2023/06/issb-issues-ifrs-s1-ifrs-s2/.

[^repo-readme]: SDS repository public README, current product/evidence description, `README.md`.

[^sds-e1]: SDS E1, "Inventario de datos clave y variables brutas", e01-inventario-datos-clave-variables-brutas-v2026-06-09.docx.

[^sds-e2]: SDS E2, "Informe de interoperabilidad y correspondencias", e02-informe-interoperabilidad-correspondencias-v2026-06-09.docx.

[^sds-e3]: SDS E3, "Modelo común NGSI-LD y paquete semántico", e03-modelo-comun-ngsi-ld-paquete-semantico-v2026-06-09.docx.

[^sds-e4]: SDS E4, "Prototipo API y transformación de datos", e04-prototipo-api-transformacion-datos-v2026-06-09.docx.

[^sds-e5]: SDS E5, "Código técnico del modelo", e05-codigo-tecnico-modelo-v2026-06-09.docx.

[^sds-e6]: SDS E6, "Gobernanza y política de datos", e06-gobernanza-politica-datos-v2026-06-09.docx.
