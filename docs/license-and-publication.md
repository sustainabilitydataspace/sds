# Licencia y publicación del código SDS

Este documento explica cómo se publica el código de Sustainability Data Space
(SDS) y qué permisos concede la publicación del repositorio.

No sustituye al texto de licencia. En caso de diferencia, prevalece `../LICENSE`.

## Resumen ejecutivo

SDS se publica como código fuente disponible para evaluación. No se publica como
software de código abierto en sentido OSI/FSF, porque la licencia restringe el
uso productivo y comercial.

Puede descargarse, instalarse y ejecutarse para revisar, probar, auditar y
validar técnicamente el producto en entornos no productivos. No puede usarse en
producción, para beneficio operativo propio, como SaaS, como servicio gestionado,
para prestar servicios a terceros, para reventa ni para redistribución comercial
sin un acuerdo separado y por escrito con Cambridge Business Initiatives S.L. o
con el titular de derechos que corresponda.

La licencia del código SDS propio está en `../LICENSE`.

## Qué permite

Dentro de un entorno no productivo, la licencia permite:

- consultar el código y la documentación;
- clonar o bifurcar el repositorio en GitHub;
- instalar SDS con los datos sintéticos incluidos o con otros datos que la
  persona evaluadora tenga derecho a usar;
- crear copias, entornos, compilaciones, contenedores y bases de datos locales
  necesarios para la evaluación;
- ejecutar pruebas, demos, revisiones de seguridad, auditorías técnicas,
  benchmarks y pruebas de compatibilidad;
- modificar una copia o un fork de GitHub únicamente para evaluación;
- publicar resultados de evaluación no confidenciales, sin divulgar
  vulnerabilidades antes de un reporte responsable y sin sugerir certificación,
  aprobación ni autorización productiva;
- comunicar incidencias o hallazgos no sensibles por GitHub Issues u otro canal
  acordado.

## Qué no permite

Salvo acuerdo separado y por escrito, la licencia no permite:

- usar SDS en procesos ordinarios de negocio;
- usar SDS con clientes, usuarios finales o datos operativos reales como parte de
  un servicio;
- prestar servicios a terceros apoyándose en SDS;
- ofrecer SDS como SaaS, servicio alojado, servicio gestionado, outsourcing o
  bureau service;
- vender, alquilar, relicenciar o redistribuir SDS como producto;
- publicar paquetes, imágenes de contenedor, mirrors, versiones modificadas o
  integraciones comerciales fuera de los forks de evaluación permitidos en
  GitHub;
- retirar avisos de derechos, financiación o licencias de terceros;
- presentar SDS como certificado, aprobado o licenciado por una entidad pública,
  organismo de estándares o proveedor de datos sin autorización específica.

## Código SDS, entregables, terceros y datos son capas distintas

La licencia SDS cubre el código propio y materiales de software distribuidos en
este repositorio, salvo los elementos que tengan licencia o términos propios.

No cubre ni amplía derechos sobre:

- componentes de terceros y sus avisos de licencia;
- estándares, vocabularios, taxonomías o documentación de terceros;
- paquetes de operador, datos de producción, datos personales o datos de cliente;
- productos de datos publicados mediante SDS;
- marcas, nombres comerciales, logos o dominios.

Los entregables formales bajo `../deliverables/` son evidencia pública y
material de difusión del proyecto. Pueden leerse, descargarse, citarse y
compartirse sin cambios, con atribución y avisos intactos, para evaluación,
auditoría, difusión pública o verificación de la ayuda. Esa posibilidad no
concede derechos productivos sobre el software SDS ni autoriza modificar,
revender o reempaquetar comercialmente dichos entregables.

Cada operador debe contar con derechos suficientes para los paquetes, estándares
y datos que use. La demo sintética incluida en el repositorio sirve para
evaluación técnica y no prueba por sí sola un despliegue productivo autorizado.

Consulte también `third-party-notices.md`.

## Relación con GitHub

GitHub permite técnicamente ver y bifurcar un repositorio público dentro de la
plataforma. Esa posibilidad técnica no equivale a una licencia de explotación
productiva.

Un fork público de evaluación en GitHub debe conservar la licencia y los avisos,
marcarse como fork o versión modificada y no presentarse como producto, servicio,
SaaS, despliegue productivo ni oferta comercial.

## Relación con la ayuda PYSED

La revisión de fuentes públicas encontró tres puntos relevantes:

1. La Orden TDF/1229/2024 obliga a cumplir las bases, la convocatoria, la
   resolución de concesión, instrucciones específicas y obligaciones de
   información, comunicación y publicidad.
2. La misma Orden contempla una bonificación de intensidad de ayuda cuando los
   resultados se difunden ampliamente por conferencias, publicaciones, servicios
   de compartición de datos con al menos un área de libre acceso o programas
   informáticos gratuitos o de fuente abierta.
3. La convocatoria permite que el órgano de seguimiento solicite colaboración del
   beneficiario en labores de difusión de los resultados del programa.

Las fuentes públicas revisadas no imponen una licencia concreta para el código
SDS, como MIT, Apache, GPL u otra. Tampoco convierten automáticamente una
publicación con restricciones de uso en software de código abierto.

La resolución global de concesión indica que las ayudas quedan sometidas a los
condicionantes particulares de la resolución individual de cada beneficiario.
Esa resolución individual no se encuentra incorporada a este repositorio público
ni ha sido revisada en este documento. Por tanto, esta página no certifica cierre
jurídico de la subvención ni sustituye la revisión de la resolución individual.

## Por qué no se llama open source

La licencia SDS impide el uso productivo y comercial sin acuerdo separado. Esa
restricción es incompatible con las definiciones habituales de software de código
abierto o software libre. Por eso la documentación usa "código fuente disponible
para evaluación" o "source-available evaluation release", no "open source".

## Productos de datos y políticas ilustrativas

Algunas políticas de datos, como `docs/policies/data_product_terms.md`, modelan
cómo podría describirse un producto de datos SDS bajo contratos de conector. Esas
políticas no son una licencia general del repositorio ni conceden derechos sobre
el código SDS, sobre estándares de terceros ni sobre entregables formales salvo
que un producto de datos concreto se publique expresamente bajo esos términos por
quien tenga derechos suficientes para hacerlo.

## Solicitud de uso productivo o comercial

Quien quiera usar SDS en producción, prestar servicios con SDS, alojarlo para
terceros, integrarlo en una oferta comercial o redistribuirlo fuera del marco de
evaluación debe obtener antes un acuerdo separado y por escrito.

Para solicitarlo, abra una GitHub Issue sin información confidencial o contacte
por un canal privado con Cambridge Business Initiatives S.L. en
info@sustainabilitydataspace.com. No incluya secretos, datos de cliente,
credenciales, vulnerabilidades ni condiciones contractuales confidenciales en una
Issue pública.

## Seguridad

No reporte vulnerabilidades por GitHub Issues públicas. Siga `../SECURITY.md` y
`security-reporting.md`.

## Fuentes oficiales consultadas

- Orden TDF/1229/2024, de 4 de noviembre:
  https://www.boe.es/eli/es/o/2024/11/04/tdf1229
- Convocatoria BDNS 796655:
  https://www.infosubvenciones.es/bdnstrans/GE/es/convocatoria/796655/document/1177567
- Resolución global de concesión PYSED:
  https://sede.mineco.gob.es/es/SedePublications/PYSED_ResolucionGlobal.pdf_firmado.pdf
