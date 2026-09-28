#!/usr/bin/env python3
"""
Convierte el E1 dataset register CSV a formato JSON para uso offline.

El archivo JSON resultante se usa como fallback cuando no hay PostgreSQL disponible
(REQUIRE_DATABASE=false).

Uso:
    python scripts/csv_to_json_indicators.py \
        data/processed/e1_dataset_register.csv \
        api/src/data/indicators.json
"""
import csv
import json
import sys
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
API_DIR = SCRIPT_DIR.parent / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from indicator_identifiers import canonicalize_indicator_identifier, is_valid_indicator_identifier
from src.services.indicator_metadata_overrides import apply_indicator_metadata_overrides


def write_console_line(message: str) -> None:
    """Write output without failing on Windows code pages."""
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        print(message)
    except UnicodeEncodeError:
        safe_message = message.encode(encoding, errors="replace").decode(encoding, errors="replace")
        stream.write(safe_message + "\n")
        stream.flush()


def csv_to_json_indicators(csv_path: Path, json_path: Path):
    """Convierte CSV de indicadores a JSON para fallback offline."""

    if not csv_path.exists():
        print(f"❌ Error: No se encontró {csv_path}")
        print("   Ejecuta primero: python scripts/prepare_atomizer_for_import.py")
        sys.exit(1)

    indicators = []

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            canonical_identifier = canonicalize_indicator_identifier(row.get('identifier', ''))
            if not is_valid_indicator_identifier(canonical_identifier):
                raise ValueError(f"Invalid indicator identifier after normalization: {row.get('identifier', '')}")
            # Mapear campos CSV a estructura JSON
            indicator = {
                "id": canonical_identifier,
                "identifier": canonical_identifier,
                "title": row.get('title', ''),
                "indicator_name": row.get('indicator') or row.get('title', ''),
                "description": row.get('description', ''),
                "dimension": row.get('dimension', ''),
                "unit_name": row.get('unitName', ''),
                "unit_type": row.get('unitType', ''),
                "periodicity": row.get('periodicity', 'annual'),
                "period_type": row.get('periodType', 'fiscal_year'),
                "source_ref": row.get('sourceRef', ''),
                "code_esrs": row.get('codeESRS', ''),
                "code_gri": row.get('codeGRI', ''),
                "code_gri_expanded": row.get('codeGRI_expanded', ''),
                "evidence_path": row.get('evidencePath', ''),
                "source_row": int(row.get('sourceRow')) if row.get('sourceRow') else None,
                "owner": row.get('owner', 'system'),
                "access_rights": row.get('accessRights', 'Internal'),
                "validation_method": row.get('validationMethod', 'automated'),
                "double_materiality": row.get('doubleMateriality', ''),
                "value_type": row.get('valueType', 'numeric'),
            }
            apply_indicator_metadata_overrides(indicator)
            indicators.append(indicator)

    # Estructura del JSON
    output = {
        "indicators": indicators,
        "source": "atomizer-conversion",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "count": len(indicators)
    }

    # Escribir JSON
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    write_console_line(f"✓ Convertidos {len(indicators)} indicadores a JSON")
    write_console_line(f"  CSV fuente: {csv_path}")
    write_console_line(f"  JSON destino: {json_path}")


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(f"Uso: {sys.argv[0]} <input.csv> <output.json>")
        print("")
        print("Ejemplos:")
        print(f"  # Conversión estándar:")
        print(f"  {sys.argv[0]} data/processed/e1_dataset_register.csv api/src/data/indicators.json")
        sys.exit(1)

    csv_to_json_indicators(
        Path(sys.argv[1]),
        Path(sys.argv[2])
    )
