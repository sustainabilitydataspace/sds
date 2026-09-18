# Criterios públicos de aceptación SDS

Esta distribución conserva dos clases de prueba: una demostración técnica
reproducible desde un clon limpio y evidencia documental de los entregables
E01–E13. Los estados de subvención se interpretan únicamente mediante
`deliverables/evidence-public/dossier-closure-status-sds-v2026-09-18.md`.

## Carril técnico reproducible

1. `make public-env` crea el entorno local ignorado sin imprimir secretos.
2. `make up` arranca PostgreSQL y la API DB-first con migraciones y recursos
   sintéticos propios.
3. `make smoke` exige `/healthz` y `/ready` saludables.
4. `make gate-e4-r8` genera 1.000 entradas sintéticas, transforma tres reglas
   físicas, comprueba valor/unidad/originales, persistencia y limpieza completa.
5. `make gate-e4-r10` importa 1.000 filas sintéticas estrictas, comprueba
   `esg_values`, `value_contexts`, `value_revisions`,
   `value_revision_events` y `current_value_pointers`, y exige limpieza completa
   y un tiempo estrictamente menor de 30 segundos.
6. `make test-public` y `make verify-public` validan el código fuente y la
   regresión pública; `make down` elimina el perfil local y sus volúmenes.

Los dos gates E4 son tripwires locales de regresión de tamaño fijo y proceso
único; no son una garantía de capacidad productiva, concurrencia ni volumen

## Entregables y trazabilidad

- `make deliverables-check` verifica el registro de E01–E13, los SHA-256 de sus
  artefactos canónicos, los índices y la higiene de la superficie pública.
- `make subsidy-closure-check` valida que la frontera entre publicación y cierre
  de subvención esté explícita y sea internamente consistente.
- E01–E13 se incluyen como justificación documental pública/saneada. La
  superficie de estado vigente registra R1–R23 y E01–E13 como cerrados por
  decisión de proyecto el 2026-09-18.

Los paquetes de estándares o de operadores con contenido sujeto a derechos se
cargan por un carril externo autorizado. La demostración incluida utiliza solo
recursos sintéticos SDS y no es un reemplazo de dichos paquetes.