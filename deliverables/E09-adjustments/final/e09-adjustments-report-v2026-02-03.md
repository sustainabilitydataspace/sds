# Entregable E9 - Ajustes al marco de gobernanza y diseño técnico

Tabla 1  Ficha del entregable E9

| Campo | Valor |
|---|---|
| Proyecto | Sustainability Data Spaces (SDS) |
| Entregable | E9 - Ajustes al marco de gobernanza y diseño técnico |
| Paquete de trabajo | PT4 - Talleres con partes interesadas |
| Actividad principal | A4.4 - Ajuste del modelo de gobernanza y los requisitos técnicos |
| Versión | V1.0 |
| Fecha | 2026-06-18 |
| Estado | Documento final |
| Responsable | Oficina del Programa SDS |

## 1. Resumen ejecutivo

Este entregable recoge los ajustes al marco de gobernanza y al diseño técnico de SDS derivados del trabajo de contraste con partes interesadas y del análisis de los casos de uso prioritarios. Su función es convertir las necesidades detectadas en modificaciones concretas del modelo de operación, de las reglas de compartición y de la arquitectura técnica del espacio de datos.

Los ajustes se orientan a cuatro objetivos:

- Reforzar la soberanía del dato y el control de uso;
- Mejorar la comparabilidad entre marcos de sostenibilidad sin crear equivalencias falsas;
- Asegurar que los datos publicados tengan unidad, período, perímetro, método y procedencia suficientes;
- Separar la preparación de evidencia dentro de SDS de las decisiones externas de auditoría, financiación, cumplimiento o autorización.

El resultado es una propuesta integrada de cambios sobre gobernanza, modelo de datos, relaciones semánticas, políticas de publicación y despliegue por casos de uso. Con ello, E9 da respuesta a los requisitos R18 y R19: los ajustes quedan descritos con contexto, justificación, viabilidad de aplicación y alineación con los objetivos estratégicos del espacio de datos.

## 2. Alcance del entregable

El alcance de E9 comprende los ajustes necesarios para que SDS pueda operar como espacio de datos de sostenibilidad con trazabilidad, interoperabilidad y control de uso. Incluye:

- Ajustes al modelo de gobernanza;
- Ajustes al diseño técnico del dato y de las relaciones semánticas;
- Reglas para decidir qué información se conserva, se comparte o se publica;
- Criterios de aplicación por caso de uso;
- Riesgos y condiciones que deben gestionarse en los pilotos.

No forma parte de este entregable sustituir la decisión de auditores, financiadores, reguladores o responsables legales. SDS prepara datos, evidencia y trazabilidad; la decisión final corresponde al actor competente en cada proceso.

## 3. Fuentes de trabajo consideradas

Los ajustes se han definido a partir de los entregables y activos técnicos ya desarrollados en el proyecto.

| Fuente de trabajo | Papel en E9 |
|---|---|
| E6 - Modelo de gobernanza y política de datos | Base para políticas de acceso, control de uso, roles, auditoría, productos de datos y gestión de cambios. |
| E8 - Casos de uso y prioridades sectoriales | Identificación de necesidades por caso de uso, reglas de publicación, prioridades y dependencias de evidencia. |
| E3 - Modelo común y paquete semántico | Base para representar indicadores, datasets, relaciones, políticas y correspondencias entre marcos. |
| E4 - Prototipo API y transformación de datos | Base para operaciones de lectura, transformación, normalización, cálculo y exposición controlada. |
| E5 - Código técnico del modelo | Base para reglas implementables, controles de calidad, pruebas y criterios de mantenibilidad. |
| E7 - Talleres y matriz de feedback `F01`–`F10` | Insumo consolidado de empresas, academia, centros tecnológicos y equipo interno. La evidencia disponible no demuestra participación directa de reguladores, inversores o asociaciones sectoriales. |

## 4. Necesidades consolidadas

La revisión conjunta de los casos de uso y del marco de gobernanza permite agrupar las necesidades en ocho bloques.

| ID | Necesidad | Implicación para SDS |
|---|---|---|
| N1 | Evitar que el dato base sea solo un indicador final de un marco concreto. | SDS debe conservar datos primitivos, dimensiones y reglas de transformación antes de proyectar resultados a NEIS/ESRS, GRI u otros marcos. |
| N2 | Permitir comparabilidad entre unidades y fuentes heterogéneas. | El modelo debe conservar unidad original, unidad normalizada, regla de conversión y condición de equivalencia. |
| N3 | Separar relación semántica de equivalencia automática. | Una correspondencia puede ser directa, parcial, condicionada, de componente o no publicable. |
| N4 | Proteger información sensible sin perder utilidad agregada. | La gobernanza debe distinguir evidencia interna, dato estructurado, paquete compartible y conclusión publicable. |
| N5 | Hacer visible la calidad de la evidencia. | Cada dato debe indicar procedencia, método, responsable, fecha, revisión y estado de calidad. |
| N6 | Controlar finalidad, destinatario y duración del uso. | La compartición debe estar mediada por políticas de uso, permisos y límites de retención. |
| N7 | Evitar decisiones automáticas fuera del mandato de SDS. | SDS puede calcular, preparar evidencia y señalar brechas, pero no debe emitir decisiones externas sin metodología y autorización. |
| N8 | Priorizar despliegues por madurez y valor. | Energía, emisiones y agua son la primera ola; circularidad, perfil ASG y biodiversidad requieren controles adicionales. |

## 5. Criterios de ajuste

Los ajustes de E9 se estructuran según cinco criterios.

### 5.1 Trazabilidad del dato

Todo dato usado para una conclusión debe poder reconstruirse desde su fuente, unidad, período, entidad, método y regla de transformación. Si esa reconstrucción no es posible, SDS puede conservar la información, pero no debe presentarla como conclusión publicable.

### 5.2 Interoperabilidad condicionada

La interoperabilidad no consiste en declarar equivalentes todos los indicadores parecidos. Consiste en indicar cuándo dos datos son comparables, bajo qué condiciones y con qué transformaciones. La relación puede ser directa, parcial, agregada, de componente o no utilizable para publicación.

### 5.3 Soberanía y control de uso

El titular o responsable autorizado debe mantener control sobre finalidad, destinatario, nivel de detalle, plazo de uso y posibilidad de reutilización. La arquitectura técnica debe reflejar estas condiciones en los productos de datos y en las políticas de acceso.

### 5.4 Publicación responsable

La publicación de resultados debe depender de controles de unidad, período, perímetro, método, procedencia, permiso y completitud. Un dato incompleto puede ser útil para diagnóstico interno, pero no necesariamente para una salida externa.

### 5.5 Despliegue por pilotos

Los ajustes deben ser viables. Por ello se proponen como reglas aplicables por oleadas de pilotos, empezando por los casos de uso con mayor madurez técnica y valor regulatorio.

## 6. Matriz de ajustes

| ID | Ajuste | Justificación | Aplicación prevista |
|---|---|---|---|
| A1 | Adoptar un registro de datos primitivos antes de publicar indicadores finales. | Los casos de energía, emisiones, agua, circularidad, perfil ASG y biodiversidad muestran que el dato final no basta para reconstruir vistas comparables. | Incluir valor base, unidad, período, entidad, perímetro y dimensiones relevantes antes de generar indicadores. |
| A2 | Conservar unidad original, unidad normalizada y regla de conversión. | La comparabilidad exige convertir solo magnitudes compatibles y dejar rastro de la regla aplicada. | Toda vista multiestándar debe declarar si la unidad coincide, se convierte o no es comparable. |
| A3 | Clasificar relaciones semánticas por tipo y condición. | No todas las correspondencias entre NEIS/ESRS, GRI o GHG Protocol son equivalencias completas. | Usar tipos de relación como cálculo directo, agregación, componente, equivalencia condicionada o no publicable. |
| A4 | Introducir puertas de publicación por suficiencia de evidencia. | SDS puede conservar datos incompletos, pero no debe publicar conclusiones si faltan dimensiones críticas, método, permiso o cobertura. | Cada producto de datos debe tener controles de publicación antes de exponerse a terceros. |
| A5 | Mantener estados de madurez y calidad del dato. | Los usuarios deben distinguir dato medido, declarado, calculado, pendiente, restringido o revisado. | Definir vocabulario común de estado para pilotos y productos de datos. |
| A6 | Separar evidencia, paquete compartible y decisión externa. | En financiación sostenible, auditoría o cumplimiento, SDS no debe sustituir la metodología aprobada por la entidad responsable. | SDS prepara evidencia y trazabilidad; la decisión final queda fuera del sistema salvo mandato explícito. |
| A7 | Aplicar políticas de uso por finalidad, destinatario, retención y sensibilidad. | El valor del espacio de datos depende de compartir sin perder control ni exponer información sensible. | Todo paquete compartible debe declarar política concreta antes de la compartición. |
| A8 | Priorizar pilotos por ola de despliegue. | Energía, emisiones y agua combinan mayor urgencia, madurez y valor normativo. Circularidad, perfil ASG y biodiversidad requieren controles adicionales. | Primera ola: UC-01, UC-02 y UC-03. Segunda ola: UC-04, UC-05 y UC-06. |
| A9 | Incorporar minimización de datos por defecto. | Algunos casos requieren detalle interno que no debe exponerse completo al destinatario final. | Publicar vistas agregadas cuando sean suficientes y conservar el detalle solo donde sea necesario y permitido. |
| A10 | Gestionar cambios de políticas, relaciones e indicadores mediante revisión explícita. | Nuevas relaciones semánticas, reglas de publicación o cambios de acceso pueden afectar a confianza, cumplimiento y auditoría. | Someter cambios relevantes a revisión de gobernanza antes de su uso operativo. |

### 6.1 Trazabilidad de feedback, casos y ajustes

| Feedback E7 | Síntesis | Casos E8 | Ajustes E9 |
|---|---|---|---|
| F01 | Fragmentación de estándares y definiciones. | UC-01, UC-02, UC-03 | A3, A4 |
| F02 | Duplicidad de reporting y necesidad de datos primitivos. | UC-01, UC-02, UC-03 | A1, A2 |
| F03 | Soberanía, acceso y compartición segura. | UC-05, UC-06 | A7, A9 |
| F04 | Trazabilidad, calidad y fiabilidad antes de reutilizar. | UC-01–UC-06 | A4, A5 |
| F05 | Arquitectura federada y evolución controlada. | UC-01–UC-06 | A10 |
| F06 | Priorización multisectorial por madurez. | UC-01–UC-06 | A8 |
| F07 | Resolver entre marcos solo con relación o contrato compatible. | UC-01, UC-02, UC-03 | A3, A4 |
| F08 | Distinguir evidencia, calidad y conclusión publicable. | UC-01–UC-06 | A4, A5 |
| F09 | Control de usos por terceros e IA. | UC-05, UC-06 | A6, A7 |
| F10 | Transferencia de conocimiento y despliegue progresivo. | UC-01–UC-06 | A8, A10 |

La matriz detallada y saneada se conserva en `deliverables/E07-workshops/evidence/e07-feedback-traceability-v1-0.csv`. Los identificadores representan observaciones consolidadas del acta, no citas textuales ni atribuciones individuales. De esta forma R18 se justifica con trazabilidad verificable sin inventar participantes o feedback no registrado.

## 7. Ajustes al marco de gobernanza

### 7.1 Niveles de tratamiento de la información

SDS debe diferenciar cuatro niveles de tratamiento para evitar que todo dato cargado se trate como dato publicable.

| Nivel | Descripción | Uso permitido |
|---|---|---|
| Evidencia interna | Dato, documento o soporte conservado para trazabilidad. | Revisión, auditoría interna, control de calidad y preparación de paquetes. |
| Dato estructurado | Valor normalizado con unidad, período, entidad, método y procedencia. | Cálculo, comparación, detección de brechas y preparación de salidas. |
| Paquete compartible | Conjunto de datos y evidencias sujeto a política de finalidad, destinatario y retención. | Intercambio con terceros autorizados. |
| Conclusión publicable | Resultado que supera controles de unidad, perímetro, método, permiso y completitud. | Informe, API autorizada o paquete de cumplimiento. |

Este ajuste permite conservar información útil sin convertirla automáticamente en una conclusión externa. También reduce el riesgo de publicar datos sensibles o conclusiones que excedan la evidencia disponible.

### 7.2 Derechos de decisión

El marco de gobernanza debe dejar claro quién puede tomar cada decisión.

| Decisión | Responsable operativo | Criterio de decisión |
|---|---|---|
| Autorizar un nuevo producto de datos | Responsable del dominio con apoyo de gobernanza SDS | Finalidad, destinatario, sensibilidad, utilidad y evidencia mínima. |
| Publicar una correspondencia entre marcos | Responsable semántico con revisión de gobernanza | Relación probada, unidad compatible, perímetro definido y condiciones documentadas. |
| Compartir un paquete con un tercero | Titular del dato o delegado autorizado | Permiso, finalidad, destinatario, plazo de uso y nivel de detalle. |
| Emitir una conclusión de cumplimiento | Responsable legal, auditor o entidad competente | Evidencia completa y metodología aprobada. |
| Resolver una brecha de evidencia | Responsable del dato y responsable de calidad | Corrección, fuente alternativa, restricción o no publicación. |

### 7.3 Minimización y sensibilidad

Los casos de perfil ASG, financiación sostenible, biodiversidad y diligencia debida muestran que puede existir información útil pero sensible: proveedor, lote, ubicación, metodología financiera, evidencia territorial, condiciones comerciales o datos de pyme.

El ajuste de gobernanza consiste en aplicar minimización por defecto:

- Usar vistas agregadas cuando el destinatario no necesita el detalle;
- Ocultar identificadores nominales si no son imprescindibles;
- Conservar el detalle como evidencia interna cuando sea necesario;
- Registrar finalidad, permiso y plazo de uso;
- Bloquear conclusiones si la política de uso no permite compartir el dato base.

### 7.4 Gestión de cambios

Toda modificación relevante en políticas, relaciones semánticas, productos de datos o criterios de publicación debe tener revisión explícita. El objetivo no es ralentizar el uso del espacio de datos, sino evitar que un cambio técnico cree una consecuencia de cumplimiento, acceso o confianza no revisada.

## 8. Ajustes al diseño técnico

### 8.1 Modelo de dato operativo

El diseño técnico debe tratar los resultados finales como salidas derivadas, no como única fuente de verdad. Un dato SDS completo debe poder conservar:

- Valor y unidad original;
- Unidad normalizada cuando exista conversión aprobada;
- Período, entidad, perímetro y ubicación agregada cuando aplique;
- Método de medición, estimación o cálculo;
- Procedencia y responsable;
- Calidad y estado de evidencia;
- Relación con indicadores o divulgaciones;
- Política de acceso y uso;
- Estado de publicación.

### 8.2 Relaciones entre marcos

Las relaciones técnicas entre marcos deben distinguir, como mínimo, los siguientes tipos.

| Tipo de relación | Uso | Riesgo si no se distingue |
|---|---|---|
| Cálculo directo | Derivar un resultado desde datos primitivos y fórmula aprobada. | Confundir dato calculado con dato declarado. |
| Agregación | Sumar o filtrar por alcance, fuente, cuenca, material, categoría o período. | Publicar un total sin saber qué incluye. |
| Componente | Usar un dato como parte de un indicador mayor. | Presentar una parte como si fuera el total. |
| Equivalencia condicionada | Reutilizar un resultado solo si unidad, método, perímetro y dimensiones son compatibles. | Crear equivalencias falsas entre marcos. |
| No publicable | Conservar evidencia interna sin emitir salida externa. | Exponer datos sensibles o conclusiones no demostradas. |

### 8.3 Conversión y normalización

SDS no debe exigir que todos los datos entren ya en la misma unidad, fuente o período. El sistema debe ayudar a reconciliar cuando sea técnicamente correcto.

La regla de ajuste es:

- Si la unidad coincide, se conserva;
- Si la magnitud es compatible, se convierte con una regla trazable;
- Si el período difiere, se reconcilia solo con criterio aprobado;
- Si la fuente, método o perímetro no son compatibles, la relación queda parcial o no publicable.

Este enfoque evita dos errores: rechazar datos válidos solo por llegar en una unidad diferente y, al mismo tiempo, publicar equivalencias que no están suficientemente justificadas.

### 8.4 Evidencia y calidad

El modelo técnico debe permitir que cada dato incorpore estado de evidencia. Como mínimo:

- Completo;
- Parcial;
- Pendiente de revisión;
- Restringido por política de uso;
- Corregido o sustituido;
- No publicable.

Estos estados deben ser legibles para el usuario y utilizables por las reglas de publicación.

## 9. Aplicación por caso de uso

| Caso de uso | Ajuste principal | Control de publicación |
|---|---|---|
| UC-01 Energía | Cálculo desde actividad o energía primitiva y conversión de unidades. | Publicar solo si unidad, período, fuente y regla de conversión son trazables o reconciliables. |
| UC-02 Emisiones GEI | Mantener alcance, método, factor, gases y categorías. | No publicar un alcance completo si faltan categorías relevantes o exclusiones. |
| UC-03 Agua | Separar fuente, destino, cuenca, estrés hídrico, método y calidad. | No reconstruir desgloses GRI si solo existe un total agregado insuficiente. |
| UC-04 Residuos y materiales | Conservar composición, peligrosidad, tratamiento, destino y origen. | No tratar subproductos o materiales secundarios como reciclado si no son equivalentes. |
| UC-05 Perfil ASG pyme | Preparar paquete de evidencia con consentimiento, brechas y calidad de dato. | No emitir puntuación ASG automática sin metodología aprobada. |
| UC-06 Biodiversidad y diligencia debida | Conservar procedencia, cobertura, granularidad, permiso de uso y contexto territorial. | No afirmar cumplimiento legal ni suficiencia territorial automática. |

## 10. Relación con R18 y R19

| Requisito | Criterio | Respuesta de E9 |
|---|---|---|
| R18 - Justificación de los ajustes | Las modificaciones deben presentarse con contexto y motivo. | La matriz A1-A10 vincula cada ajuste con la necesidad que lo origina y con su aplicación prevista. |
| R18 - Viabilidad | Los ajustes deben ser aplicables al diseño y operación de SDS. | Los ajustes se formulan como reglas de dato, políticas de gobernanza, controles de publicación y criterios de piloto. |
| R19 - Consistencia estratégica | Los ajustes deben ser coherentes con los objetivos generales del espacio de datos. | Las propuestas refuerzan interoperabilidad, soberanía del dato, trazabilidad, reutilización y control de uso. |
| R19 - Coherencia del marco | El ajuste técnico no debe contradecir el modelo de gobernanza. | El diseño técnico queda subordinado a finalidad, permisos, minimización, calidad de evidencia y decisión responsable. |

## 11. Riesgos y controles pendientes

| Riesgo | Motivo | Control propuesto |
|---|---|---|
| Sobreafirmación de capacidades | Algunos casos de uso necesitan reglas específicas de piloto antes de publicarse como función automática. | Mantenerlos como criterios de diseño hasta que exista piloto validado. |
| Equivalencias falsas entre marcos | Dos indicadores pueden parecer similares pero depender de unidades, métodos o perímetros distintos. | Exigir tipo de relación y condición de equivalencia antes de publicar. |
| Datos sensibles | Algunos productos pueden contener información comercial, territorial o personal. | Aplicar minimización, permisos, políticas de uso y vistas agregadas. |
| Decisión de terceros | Financiación, auditoría y cumplimiento pueden depender de metodologías externas. | SDS entrega evidencia y trazabilidad; la decisión queda en la entidad competente. |
| Cambios normativos o técnicos | Las relaciones semánticas y criterios de publicación pueden cambiar. | Revisar políticas, relaciones y productos de datos mediante gestión de cambios. |

## 12. Plan de aplicación

| Horizonte | Acción | Salida esperada |
|---|---|---|
| Inmediato | Usar UC-01, UC-02 y UC-03 como primera ola de pilotos. | Pilotos con datos primitivos, conversiones, relaciones semánticas y reglas de publicación claras. |
| Inmediato | Definir vocabulario común de estado de evidencia. | Estados compartidos para completo, parcial, pendiente, restringido, revisado y no publicable. |
| Corto plazo | Convertir reglas de publicación en criterios de aceptación por piloto. | Cada piloto sabrá qué puede publicar y qué debe bloquear. |
| Corto plazo | Asociar cada producto de datos a política de finalidad, destinatario y retención. | Paquetes compartibles sin pérdida de soberanía del dato. |
| Medio plazo | Revisar UC-04, UC-05 y UC-06 como segunda ola. | Tratamiento específico para sensibilidad comercial, consentimiento y evidencia territorial. |
| Medio plazo | Revisar cambios de relaciones e indicadores mediante gobernanza. | Evolución controlada del modelo semántico y de las salidas publicables. |

## 13. Conclusiones

Los ajustes propuestos orientan SDS hacia un modelo más robusto de compartición de datos de sostenibilidad. El espacio de datos no debe limitarse a almacenar indicadores finales; debe conservar datos primitivos, reglas de conversión, condiciones semánticas, políticas de uso y estados de evidencia.

La gobernanza debe decidir qué se conserva, qué se comparte y qué se publica. El diseño técnico debe hacer posible esa decisión mediante datos trazables, relaciones condicionadas y controles de publicación. Así se evita tanto la pérdida de utilidad por exceso de restricción como la publicación de conclusiones no suficientemente justificadas.

Con este ajuste, SDS queda alineado con su propósito: facilitar datos de sostenibilidad comparables y reutilizables, preservando soberanía, trazabilidad, calidad de evidencia y control de uso.
