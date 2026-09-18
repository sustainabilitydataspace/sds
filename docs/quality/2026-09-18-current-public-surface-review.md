# Revisión actual de superficie pública — 2026-09-18

Esta nota registra únicamente comprobaciones ejecutadas el 2026-09-18 sobre la
superficie pública actual. No reutiliza matrices ni observaciones históricas.

## Repositorio público

- URL: `https://github.com/sustainabilitydataspace/sds`
- Rama publicada: `main`
- Commit comprobado: `8bdf4a78b452c6adc09206d0afd68f26da3ac3c3`
- API/gates locales del commit: `24 passed`; perfil DB-first arrancado desde
  volumen vacío; R8 procesó 1.000/1.000 transformaciones en 2,122 s; R10
  importó 1.000 filas en 1,414 s con persistencia y purga verificadas.

## Sitio web público

Comprobaciones HTTP/DOM actuales:

- Inicio, proyecto, documentación, noticias y eventos y páginas legales: HTTP 200.
- `/contacto/` redirige a `/contacto-2/`, que responde HTTP 200.
- El formulario de contacto está visible; no se envió ningún formulario ni se
  generó comunicación externa durante esta revisión.
- La portada muestra enlaces de recursos a `/blog/#articles`, `#news`, `#guides`,
  `#videos`, `#interviews` y `#events`; en la comprobación actual esos destinos
  responden HTTP 404 y el navegador muestra «Página no encontrada».
- El enlace visible de solicitud de demo `?page_id=4340` responde HTTP 404.

Estos 404 son incidencias de mantenimiento web observadas tras el cierre formal
actual. No se presentan como corregidos ni se usan para alterar el estado de
cierre de los requisitos del expediente.
