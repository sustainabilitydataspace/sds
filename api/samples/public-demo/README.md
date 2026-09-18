# Paquete público de demostración SDS

Este paquete es autocontenido y sintético. Ha sido creado para que un clon limpio
pueda arrancar y ejecutar las comprobaciones E4/R8 y E4/R10 sin datos de
operadores ni catálogos de estándares de terceros.

Incluye tres métricas SDS propias:

- volumen de agua de muestra (`L` a `m3`);
- uso de energía de muestra (`kWh` a `MWh`);
- masa de emisiones de muestra (`kg CO2e` a `t CO2e`).

No es un catálogo ESRS, GRI, ISO, EFRAG, GHG Protocol ni una declaración de
conformidad con dichos estándares. Los paquetes autorizados de cada operador se
cargan por separado mediante los contratos de importación SDS.

Los gates generan sus 1.000 filas deterministas en tiempo de ejecución y las
purgan al finalizar. Los informes de esa ejecución se escriben en un directorio
de artefactos local, no sobre la evidencia histórica versionada.
