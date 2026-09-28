#!/usr/bin/env python3
"""
Transforma datos del Atomizer al formato E1 Dataset Register.

Versión: 1.2
Changelog:
  - v1.2: Canonicaliza URNs SDS para códigos GRI/ESRS con espacios y puntuación
  - v1.1: Añadido manejo de BOM, validación de duplicados, logging
  - v1.0: Versión inicial

Uso:
    python scripts/prepare_atomizer_for_import.py
    python scripts/prepare_atomizer_for_import.py --verbose
"""
import argparse
import csv
import sys
import hashlib
from pathlib import Path
from datetime import datetime
from collections import Counter

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
API_DIR = SCRIPT_DIR.parent / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from indicator_identifiers import build_indicator_identifier
from esg_classification import classify_esg_dimension
from src.services.indicator_metadata_overrides import apply_indicator_metadata_overrides

# Rutas por defecto
DEFAULT_FRAMEWORK_PATH = Path("data/atomizer/framework_datapoints.csv")
DEFAULT_ATOMIZED_PATH = Path("data/atomizer/atomized_variables.csv")
DEFAULT_OUTPUT_PATH = Path("data/processed/e1_dataset_register.csv")
LOG_PATH = Path("data/processed/prepare_log.txt")


def write_console_line(message: str) -> None:
    """Write a console line without failing on narrow Windows encodings."""
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        print(message)
    except UnicodeEncodeError:
        safe_message = message.encode(encoding, errors="replace").decode(encoding, errors="replace")
        stream.write(safe_message + "\n")
        stream.flush()


def log_message(msg, verbose=True):
    """Log a mensaje a archivo y consola."""
    timestamp = datetime.now().isoformat()
    line = f"[{timestamp}] {msg}\n"

    # Escribir a log siempre
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(line)

    # Mostrar en consola si verbose
    if verbose:
        write_console_line(msg)


def read_csv_with_encoding(path):
    """Lee CSV manejando UTF-8 con o sin BOM."""
    encodings = ['utf-8-sig', 'utf-8', 'latin-1']

    for encoding in encodings:
        try:
            with open(path, 'r', encoding=encoding) as f:
                content = f.read()
                # Detectar si hay contenido
                if content.strip():
                    from io import StringIO
                    reader = csv.DictReader(StringIO(content))
                    rows = list(reader)
                    log_message(f"✓ Leído {path} con encoding {encoding} ({len(rows)} filas)")
                    return rows
        except UnicodeDecodeError:
            continue

    raise ValueError(f"No se pudo leer {path} con ningún encoding conocido")


def validate_no_duplicates(rows, key_field='identifier'):
    """Valida que no haya duplicados en el campo clave."""
    identifiers = [row.get(key_field, '').strip() for row in rows if row.get(key_field)]
    counter = Counter(identifiers)
    duplicates = {k: v for k, v in counter.items() if v > 1}

    if duplicates:
        log_message(f"⚠️ ADVERTENCIA: Duplicados encontrados en {key_field}:", True)
        for dup, count in list(duplicates.items())[:5]:  # Mostrar primeros 5
            log_message(f"   - {dup}: aparece {count} veces", True)
        if len(duplicates) > 5:
            log_message(f"   ... y {len(duplicates) - 5} más", True)
        return False

    return True


def calculate_checksum(path):
    """Calcula SHA-256 del archivo para trazabilidad."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_from_atomizer(
    framework_path: Path = DEFAULT_FRAMEWORK_PATH,
    atomized_path: Path = DEFAULT_ATOMIZED_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    verbose: bool = True
):
    """Convierte CSVs del Atomizer a formato E1 dataset register."""

    log_message("=== Iniciando transformación Atomizer → E1 ===", verbose)

    # Validar archivos fuente
    if not framework_path.exists():
        log_message(f"❌ Error: No se encontró {framework_path}", True)
        log_message(
            "   Usa --framework/--atomized con rutas al paquete del repo Atomizer, "
            "o copia esos archivos al cache local git-ignored data/atomizer/.",
            True,
        )
        sys.exit(1)

    # Log de checksums para trazabilidad
    fw_checksum = calculate_checksum(framework_path)
    log_message(f"📄 Framework checksum: {fw_checksum}", verbose)

    # Cargar framework datapoints
    try:
        fw_rows = read_csv_with_encoding(framework_path)
    except Exception as e:
        log_message(f"❌ Error leyendo framework: {e}", True)
        sys.exit(1)

    frameworks = {}
    for row in fw_rows:
        code = row.get('DatapointCode', '').strip()
        if code:
            frameworks[code] = {
                'framework': row.get('FrameworkID', ''),
                'label': row.get('Label', ''),
                'description': row.get('Description', ''),
                'datatype': row.get('DataType', ''),
                'unit': row.get('DefaultUnit', ''),
            }

    log_message(f"✓ Cargados {len(frameworks)} frameworks únicos", verbose)

    # Cargar variables atomizadas (opcional)
    atomized = {}
    if atomized_path.exists():
        atom_checksum = calculate_checksum(atomized_path)
        log_message(f"📄 Atomized checksum: {atom_checksum}", verbose)

        try:
            atom_rows = read_csv_with_encoding(atomized_path)
            for row in atom_rows:
                code = row.get('DatapointCode', '').strip()
                atom_id = row.get('AtomizedID', '').strip()
                if code and atom_id:
                    if code not in atomized:
                        atomized[code] = []
                    atomized[code].append({
                        'id': atom_id,
                        'label': row.get('Label', ''),
                        'unit': row.get('UnitName', ''),
                        'unit_type': row.get('UnitType', ''),
                        'is_activity': row.get('IsActivityData', ''),
                        'is_calculated': row.get('IsCalculated', ''),
                        'calc_logic': row.get('CalculationLogic', ''),
                    })
            log_message(f"✓ Cargadas {len(atomized)} entradas atomizadas", verbose)
        except Exception as e:
            log_message(f"⚠️ Advertencia leyendo atomized variables: {e}", verbose)

    # Generar registros E1
    fieldnames = [
        'identifier', 'title', 'indicator', 'description', 'dimension',
        'unitName', 'unitType', 'periodicity', 'periodType', 'sourceRef',
        'codeESRS', 'codeGRI', 'codeGRI_expanded', 'evidencePath',
        'sourceRow', 'owner', 'accessRights', 'validationMethod',
        'doubleMateriality', 'valueType'
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_rows = []
    unclassified_codes = []
    for index, (code, fw) in enumerate(frameworks.items(), start=1):
        # Dimensión ESG: clasificador determinista por framework + código de
        # norma (serie/prefijo), NO por el Topic de texto libre. El Topic de GRI
        # son códigos ("GRI 403"), no temas, así que el antiguo heurístico de
        # palabras clave con default 'E' etiquetaba mal las 680 filas GRI y
        # producía cero 'G'. Ver scripts/esg_classification.py y
        # workspace/consensus/20260612-e1-dimension-fault-attribution.
        dimension = classify_esg_dimension(fw.get('framework', ''), code)
        if dimension is None:
            unclassified_codes.append(f"{fw.get('framework', '')}:{code}")
            dimension = ''

        # Determinar códigos ESRS/GRI
        framework = fw.get('framework', '').upper()
        code_esrs = code if framework == 'ESRS' else ''
        code_gri = code if framework == 'GRI' else ''

        # Crear URN identifier
        identifier = build_indicator_identifier(framework, code, fallback=f"item_{index}")

        # Obtener unidad de variables atomizadas si existe
        unit_name = fw.get('unit', '')
        unit_type = ''
        if code in atomized and atomized[code]:
            first_atom = atomized[code][0]
            if not unit_name and first_atom.get('unit'):
                unit_name = first_atom['unit']
            if first_atom.get('unit_type'):
                unit_type = first_atom['unit_type']

        row = {
            'identifier': identifier,
            'title': fw['label'] or f"{fw['framework']} {code}",
            'indicator': fw['label'],
            'description': fw['description'],
            'dimension': dimension,
            'unitName': unit_name,
            'unitType': unit_type,
            'periodicity': 'annual',
            'periodType': 'fiscal_year',
            'sourceRef': f"{fw['framework']} Official" if fw['framework'] else 'Unknown',
            'codeESRS': code_esrs,
            'codeGRI': code_gri,
            'codeGRI_expanded': '',
            'evidencePath': '',
            'sourceRow': '',
            'owner': 'system',
            'accessRights': 'Internal',
            'validationMethod': 'automated',
            'doubleMateriality': '',
            'valueType': fw['datatype'].lower() if fw['datatype'] else 'numeric',
        }
        apply_indicator_metadata_overrides(row)
        output_rows.append(row)

    # Fallar ruidosamente si algún código no es clasificable (en lugar del
    # antiguo default silencioso 'E', que ocultaba las fugas de clasificación).
    if unclassified_codes:
        log_message(
            f"❌ {len(unclassified_codes)} código(s) sin dimensión ESG "
            f"clasificable: {', '.join(unclassified_codes[:10])}"
            + (" ..." if len(unclassified_codes) > 10 else ""),
            True,
        )
        sys.exit(1)

    # Validar duplicados antes de escribir
    if not validate_no_duplicates(output_rows, 'identifier'):
        log_message("⚠️ Se encontraron duplicados. Revisar datos fuente.", True)
        # Continuar pero advertir

    # Escribir CSV
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    # Escribir metadata
    meta_path = output_path.with_suffix('.meta.json')
    import json
    meta = {
        "generated_at": datetime.now().isoformat(),
        "version": "1.2",
        "source_frameworks_checksum": fw_checksum,
        "count": len(output_rows),
        "dimensions": dict(Counter(r['dimension'] for r in output_rows)),
        "frameworks": dict(Counter(r['sourceRef'] for r in output_rows))
    }
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)

    log_message(f"✓ Generados {len(output_rows)} registros en {output_path}", True)
    log_message(f"📊 Por dimensión: {meta['dimensions']}", verbose)
    log_message(f"📝 Metadata guardada en {meta_path}", verbose)

    return len(output_rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Transforma datos del Atomizer a formato E1 Dataset Register'
    )
    parser.add_argument(
        '--framework',
        type=Path,
        default=DEFAULT_FRAMEWORK_PATH,
        help='Ruta a framework_datapoints.csv exportado por Atomizer'
    )
    parser.add_argument(
        '--atomized',
        type=Path,
        default=DEFAULT_ATOMIZED_PATH,
        help='Ruta a atomized_variables.csv exportado por Atomizer'
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help='Ruta de salida'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        default=True,
        help='Mostrar mensajes detallados'
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Solo mostrar errores'
    )

    args = parser.parse_args()

    verbose = not args.quiet

    try:
        count = prepare_from_atomizer(
            args.framework,
            args.atomized,
            args.output,
            verbose
        )
        sys.exit(0)
    except Exception as e:
        log_message(f"❌ Error fatal: {e}", True)
        import traceback
        log_message(traceback.format_exc(), False)
        sys.exit(1)
