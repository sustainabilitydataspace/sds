# Organización de anexos por entregable

Esta guía describe un procedimiento de organización documental. Las
asociaciones de la tabla sirven para localizar anexos junto a sus documentos
principales; no sustituyen el contenido aprobado ni atribuyen por sí mismas
cumplimiento técnico o administrativo. La guía no publica ni importa anexos
controlados y no acredita que hayan llegado a destinatarios remotos.

## Asociaciones y nombres impersonales

| Actividad | Entregable asociado | Carpeta orientativa | Contenido asociado |
|---|---|---|---|
| A1.4 | [E01](../deliverables/E01-mapeo-datos-normativas/) | `E01_Anexos` | Doble materialidad y evidencia de apoyo. |
| A2.2 | [E03](../deliverables/E03-modelo-ngsi-ld/) | `E03_Anexos` | Informe de pruebas, matriz y evidencia asociada. |
| A2.3 | [E04](../deliverables/E04-api-prototype/) | `E04_Anexos` | Guía y registro de verificación. |
| A6.2 | [E13](../deliverables/E13-roadmap-scalability/) | `E13_Anexos` | Escalabilidad territorial y recomendaciones. |

Nombrar carpetas y archivos por entregable, actividad y contenido. Mantener los
anexos junto a sus originales en la fuente controlada, con nombres estables e
impersonales, sin nombres de revisores o destinatarios. Un anexo conserva su
relación original aunque se organice bajo el entregable asociado; registrar
esa relación en el índice en lugar de inferirla del nombre de carpeta.

## Fuente controlada y Word manual

En Windows, trabajar primero sobre la fuente controlada local de OneDrive
cuando esté disponible. Si no lo está, utilizar la fuente controlada de
SharePoint, incluido el flujo desde Linux, conforme al acceso autorizado.
Disponibilidad local, permisos remotos y propagación cloud son comprobaciones
separadas.

El Word manual aprobado es la autoridad para su contenido y maquetación.
Antes de cambiarlo, identificar la versión y obtener aprobación deliberada
para las modificaciones exactas. Conservar el original y verificar el resultado,
incluida su presentación cuando corresponda. Una actualización de guías,
índices o estados no autoriza regenerarlo, actualizar sus campos o índice,
reformatearlo ni importar una versión externa a otra autoridad documental.

## Traslado con integridad e índice vigente

1. Delimitar los anexos y su evidencia asociada. Identificar documento original,
   entregable, actividad, versión y condición histórica. Excluir los documentos
   principales y los paquetes históricos que no formen parte del traslado.
2. Registrar antes del traslado rutas relativas, tamaño en bytes y SHA-256 de
   cada archivo. Comprobar si el manifiesto anterior coincide con la fuente:
   una discrepancia previa debe conservarse y explicarse, sin presentarlo como
   válido ni atribuirla automáticamente al traslado.
3. Trasladar los archivos asociados a la carpeta impersonal conservando sus
   bytes. No abrir y guardar Office, convertir formatos o normalizar finales
   de línea como parte de una operación de organización.
4. Verificar tamaños y hashes en el destino contra el registro previo. Comprobar
   que el conjunto previsto está completo, sin archivos faltantes o inesperados,
   y que los documentos principales excluidos permanecen sin cambios.
5. Mantener un índice vigente, por ejemplo `00_Indice_Anexos.csv`, con entregable,
   actividad, relación con el original, versión/fecha, rutas relativas de origen
   y destino, bytes y SHA-256. Verificar sus entradas contra los archivos
   actuales. Los hashes de fuentes controladas permanecen en su registro de
   custodia; no se incorporan a una guía pública genérica.
6. Conservar las copias históricas fechadas y sus manifiestos sin sobrescribirlos.
   La organización de una fuente actual no regenera automáticamente una
   colección archivística ni convierte un paquete histórico en espejo del
   código actual. Actualizar enlaces relativos de documentación y comprobarlos.

Citar evidencia interna mediante vínculos relativos verificables. Para fuentes
normativas o bibliográficas, seguir el
[sistema de citas de entregables](../deliverables/citation-system.md).
La relación entre una guía y una evidencia controlada no implica autorización
para publicar esa evidencia.

## Qué demuestra cada registro

| Registro | Qué puede demostrar | Comprobación distinta que requiere evidencia propia |
|---|---|---|
| Hashes e índice vigente | Integridad y organización del conjunto local delimitado. | Disponibilidad cloud, permisos remotos o recepción. |
| Matriz y registros históricos de pruebas | Casos y resultados documentados para su versión y alcance. | Nueva ejecución operativa, transformación o conversión de datos en el sistema actual. |
| Ejecución operativa registrada | Comportamiento observado en el entorno, versión y casos ejecutados. | Aceptación general, despliegue de conectores o confianza productiva fuera de ese alcance. |
| Registro de mensaje enviado | Comunicación efectivamente enviada y contenido comunicado. | Recepción del destinatario, apertura de enlaces o descarga de archivos. |
| Comprobación cloud | Propagación y acceso comprobados en el alcance indicado. | Recepción del mensaje o aceptación del contenido. |
| Acuse del destinatario | Recepción expresamente confirmada para el objeto indicado. | Conformidad técnica o aprobación administrativa. |
| Aceptación del propietario | Decisión del propietario con fecha y alcance explícitos. | Decisión ministerial o administrativa emitida por la autoridad competente. |
| Decisión ministerial o administrativa | Resolución de su autoridad, con su alcance y condiciones. | Extensiones a otros expedientes o actuaciones posteriores. |

## Correspondencia y límites del seguimiento

El mensaje efectivamente enviado y su cuerpo gobiernan lo comunicado frente a
un borrador anterior. Registrar el estado con su fecha y alcance, manteniendo
los borradores como historia. No inferir envío de un borrador, ni recepción de
un registro de envío, ni propagación cloud de un archivo local.

Una conformidad técnica posterior puede superar una observación anterior si
no existe reapertura expresa. Mantener una posible falta de recuperación del
original como laguna de archivo separada; no fabricar el documento ni reabrir
lo aceptado por ese solo motivo. Conservar el baseline de aceptación existente:
el seguimiento documental no lo borra ni establece una nueva decisión
administrativa. Los proyectos y expedientes independientes requieren su propia
evidencia.
