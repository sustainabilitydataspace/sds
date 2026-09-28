# Perfil público de SDS

La distribución pública SDS contiene el código completo de la API, sus
migraciones, pruebas, contratos y herramientas de importación. Se publica bajo
una licencia de código fuente disponible para evaluación, no como software de
código abierto. Véanse `../LICENSE` y `license-and-publication.md` antes de
instalar. Las instrucciones de instalación describen una evaluación técnica; no
conceden explotación propia, uso productivo, SaaS, servicio gestionado, reventa
ni prestación de servicios a terceros. Distingue dos tipos de datos para quienes
la evalúen:

1. Un paquete de demostración sintético incluido para probar la API completa
   sobre PostgreSQL sin datos operativos de terceros.
2. Paquetes de operador externos para indicadores, valores y mapeos que cada
   operador tenga derecho a usar.

El primer carril puede instalarse con claves generadas localmente por
`make public-env` y no depende de cuentas ni contraseñas de la organización.
Ninguna parte del código requiere las contraseñas del equipo que desarrolló
SDS. Para procesar paquetes propios en el segundo, cada operador necesitaría
un acuerdo separado y por escrito sobre el uso de SDS, además de
infraestructura, configuración y derechos de uso de esos paquetes; la
demostración sintética no autoriza ni prueba por sí sola ese despliegue
operativo.

El paquete sintético `api/demo/` no contiene valores operativos, datos
personales ni secretos. UC-01 a UC-03 prueban observaciones y conversiones;
UC-04 a UC-06 muestran contratos de almacenamiento y metadatos, sin afirmar
cálculos o certificaciones no ejecutados. Los paquetes operativos no se
distribuyen con el repositorio.

Los entregables públicos E01–E13 y el registro de trazabilidad justifican el
alcance del proyecto. El estado vigente de 2026-09-18 registra el cierre de
R1–R23 y E01–E13 por decisión de proyecto. Las incidencias posteriores de
mantenimiento web se documentan separadamente y no reabren ese cierre.

Los identificadores oficiales que aparezcan en documentos o paquetes
autorizados se conservan literalmente. Un derecho o certificación de otro
producto no cubre automáticamente SDS, y el repositorio no presenta SDS como
herramienta certificada por GRI sin autorización específica y escrita. Las
licencias y avisos de terceros se explican en `third-party-notices.md`, y las
vulnerabilidades deben reportarse según `security-reporting.md`, no mediante
Issues públicas.
