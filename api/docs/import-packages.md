# Paquetes SDS

## Paquete público incluido

`api/samples/public-demo/` contiene un recurso autocontenido y redistribuible
para arrancar el repositorio: tres indicadores SDS sintéticos, un catálogo
mínimo de unidades y una semilla de moneda de demostración. No representa un
catálogo ESG oficial ni incluye texto, etiquetas o descripciones de estándares
de terceros.

Los comandos `make gate-e4-r8` y `make gate-e4-r10` generan sus propias 1.000
filas deterministas, las importan mediante el carril normal y las purgan. Los
recibos se guardan bajo `artifacts/`.

## Paquetes de operador

Los paquetes externos pueden incluir registros de indicadores, valores y
contratos de cálculo cuando el operador tenga derechos para emplearlos:

```text
sds-package/
├── manifest.json
├── MANIFEST.sha256
├── sds_dataset_register.csv
├── sds_values.csv                 # opcional
└── sds_calculation_contract.json  # opcional
```

Valide sin escribir antes de importar:

```bash
python scripts/import_sds_package.py \
  --package-dir <ruta-al-paquete> \
  --db-url "$DATABASE_URL" \
  --dry-run
```

Los mapeos canónicos se importan como paquetes separados y se revisan antes de
materializarse. Los códigos oficiales que un paquete autorizado aporte se usan
literalmente; SDS no convierte esa disponibilidad en una licencia para
redistribuir texto, etiquetas o descripciones del estándar.
