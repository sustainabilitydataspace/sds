# Configuración pública de SDS

## Perfil de demostración incluido

Desde la raíz del repositorio, `make public-env` crea `api/.env` con una
contraseña de PostgreSQL y una clave JWT aleatorias. El fichero tiene permisos
locales restringidos, está ignorado por Git y los secretos nunca se imprimen.
Después ejecute `make up` y `make smoke`.

El perfil usa por defecto los recursos sintéticos incluidos bajo
`api/samples/public-demo/` y la pareja de ontologías mínima
`api/ontologies/core_tbox.owl` + `api/ontologies/generated_projection.owl`.
No requiere que el revisor aporte un paquete privado.

## Variables relevantes

`api/.env.example` contiene solamente nombres de variables y valores no
secretos. Los valores generados localmente incluyen:

- `POSTGRES_PASSWORD` y `JWT_SECRET_KEY`;
- `DATABASE_URL` para ejecutar los gates desde el host;
- `REQUIRE_DATABASE=true`;
- `SEED_REFERENCE_DATA_ON_STARTUP=true`;
- `VALUE_REVISION_PRIMARY_READ_PATH=revision`.

## Paquetes de operador

Un operador puede sustituir el perfil sintético usando rutas autorizadas:

```bash
export REFERENCE_INDICATORS_PATH='<ruta-a-indicadores.json>'
export REFERENCE_MAPPINGS_PATH='<ruta-a-mapeos.json>'
export UNITS_JSON_PATH='<ruta-a-unidades.json>'
export CURRENCIES_SEED_PATH='<ruta-a-divisas.json>'
export SEMANTIC_BUNDLE_PATH='<directorio-con-core_tbox-y-generated_projection>'
```

Esas rutas deben quedar fuera del repositorio público cuando contengan datos
operativos o material sujeto a derechos. El runtime DB-first falla cerrado si
la base de datos o el catálogo de unidades requerido no están disponibles.