# Perfil público de SDS

La distribución pública SDS tiene dos carriles deliberadamente separados:

1. Un perfil de demostración autocontenido: PostgreSQL, código, migraciones,
   ontología mínima SDS, paquete sintético, ejemplos y gates E4/R8–R10.
2. Un perfil de operador: paquetes externos de indicadores, mapeos, unidades,
   divisas y semántica que el operador tenga derecho a usar.

El primer carril puede instalarse con claves generadas localmente por
`make public-env` y no depende de cuentas ni contraseñas de la organización.
Para procesar paquetes propios en el segundo, cada operador aporta su
infraestructura, configuración y derechos de uso; la demostración sintética
no prueba por sí sola ese despliegue operativo.

El perfil de demostración no contiene valores operativos, datos personales,
secretos, mapeos completos de estándares ni descripciones o etiquetas de
catálogos de terceros. Sus tres métricas son identificadores SDS propios bajo
`urn:sds:sample:*`.

Los entregables públicos E01–E13 y el registro de trazabilidad justifican el
alcance del proyecto. El estado vigente de 2026-09-18 registra el cierre de
R1–R23 y E01–E13 por decisión de proyecto. Las incidencias posteriores de
mantenimiento web se documentan separadamente y no reabren ese cierre.

Los identificadores oficiales que aparezcan en documentos o paquetes
autorizados se conservan literalmente. Un derecho o certificación de otro
producto no cubre automáticamente SDS, y el repositorio no presenta SDS como
herramienta certificada por GRI sin autorización específica y escrita.
