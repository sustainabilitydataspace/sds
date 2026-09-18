# Convergencia europea de SustainabilityDataSpace

Esta nota reúne las respuestas documentales sobre la convergencia de SustainabilityDataSpace (SDS) con el ecosistema europeo de espacios de datos. Distingue la arquitectura, los estándares, los artefactos técnicos y la hoja de ruta del proyecto.

## 1. Interacciones realizadas con otros proyectos europeos

SustainabilityDataSpace ha desarrollado una línea de convergencia técnica con el ecosistema europeo de espacios de datos mediante el análisis e incorporación de sus principales marcos de referencia: Data Spaces Support Centre (DSSC), Gaia-X, International Data Spaces Association (IDSA), Dataspace Protocol, Eclipse Dataspace Components y Simpl.

Esta convergencia se ha trasladado al diseño del proyecto mediante la definición de requisitos de interoperabilidad, identidad, gobierno del dato, trazabilidad, soberanía digital y federación. La actividad A6.3 de la memoria técnica orienta expresamente esta estrategia de integración con Gaia-X y otros espacios de datos, y sus resultados se han concretado en una arquitectura abierta, modular y preparada para el intercambio gobernado de productos de datos de sostenibilidad.

El proyecto aporta una base técnica especializada para que los datos ASG puedan participar progresivamente en ecosistemas europeos interoperables, manteniendo el control de uso, la trazabilidad y la reutilización del dato.

## 2. Utilización de software, estándares o infraestructuras de la Unión Europea (p. ej., SIMPL, IPCEI CIS)

SustainabilityDataSpace ha incorporado estándares y componentes tecnológicos alineados con la estrategia europea de espacios de datos.

El modelo común del proyecto utiliza NGSI-LD de ETSI como base de representación e intercambio de información, complementado con JSON-LD, SHACL, JSON Schema y OWL. Esta combinación permite publicar, validar y extender datos de sostenibilidad en formatos legibles por máquina e interoperables.

En el plano de intercambio gobernado, se ha generado un [paquete técnico para Eclipse Dataspace Components](../configs/edc/README.md) que incluye 1.805 definiciones de activos, 8 definiciones de políticas de uso y 1.805 definiciones de contrato. El paquete aplica un modelo de políticas *deny-by-default*, con restricciones explícitas de finalidad, región y conservación.

El diseño de gobierno, identidad y trazabilidad se apoya asimismo en los principios de Gaia-X, IDSA, DSSC y Dataspace Protocol. Simpl se incorpora como referencia de interoperabilidad europea y como vía de evolución para futuras pruebas técnicas, pilotos y federación de productos de datos.

## 3. Otros documentos y justificantes vinculados a la convergencia europea

La convergencia europea del proyecto se acredita mediante un conjunto coherente de documentos técnicos, de gobernanza y de hoja de ruta.

La memoria técnica de solicitud, especialmente la actividad A6.3, define la estrategia de integración con Gaia-X y otros espacios de datos. El [entregable E3](../deliverables/E03-modelo-ngsi-ld/final/e03-modelo-comun-ngsi-ld-paquete-semantico-v2026-06-09.md), Modelo común NGSI-LD y paquete semántico, documenta el modelo interoperable, los contextos JSON-LD, las restricciones SHACL, los contratos JSON Schema y la validación reproducible del paquete semántico.

El [entregable E6](../deliverables/E06-governance/final/e06-gobernanza-politica-datos-v2026-06-09.md), Gobernanza y política de datos, desarrolla las políticas de uso, los términos de los productos de datos, la trazabilidad, los roles de gobierno y el modelo de confianza. El paquete EDC incorpora activos, políticas y definiciones de contrato preparados para el intercambio de productos de datos bajo reglas explícitas y verificables.

El [entregable E13](../deliverables/E13-roadmap-scalability/final/e13-informe-final-recomendaciones-futuro-espacio-datos-v1-0.md), como hoja de ruta candidata en revisión, estructura la escalabilidad e interoperabilidad con DSSC, Gaia-X, IDSA, EDC, Simpl, el Reglamento de Gobernanza de Datos y el Reglamento de Datos.

En conjunto, estos documentos demuestran que la convergencia europea se ha incorporado al núcleo técnico, semántico y de gobernanza de SustainabilityDataSpace.

## 4. Otros documentos: certificados de validación por organismos internacionales, certificados de conexión con espacios comunes europeos, certificación de arquitecturas o estándares

SustainabilityDataSpace ha establecido una base de validación técnica y de gobernanza orientada a estándares internacionales y europeos.

El proyecto cuenta con evidencia reproducible de validación del modelo NGSI-LD, los contextos JSON-LD, las restricciones SHACL, los contratos JSON Schema, la proyección ontológica y la representación semántica de los datos de sostenibilidad. Asimismo, incorpora políticas de uso expresables mediante ODRL, trazabilidad de eventos, modelos de identidad descentralizada basados en DID y credenciales verificables, así como un paquete de configuración para conectores EDC.

Esta base técnica se articula con referencias consolidadas del ecosistema europeo e internacional: ETSI NGSI-LD, DSSC Blueprint, Gaia-X Trust Framework, IDSA Dataspace Protocol, Eclipse Dataspace Protocol, W3C ODRL, W3C Verifiable Credentials, W3C DID y el marco europeo eIDAS.

El resultado es una arquitectura de datos de sostenibilidad trazable, gobernable e interoperable, preparada para procesos de validación de conectores, pruebas de intercambio y evolución hacia ecosistemas federados.
