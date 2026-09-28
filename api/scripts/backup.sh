#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SECURE_IO_HELPER="$SCRIPT_DIR/secure_operational_io.py"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

if [ ! -f "$SECURE_IO_HELPER" ]; then
  echo "Secure operational I/O helper not found: $SECURE_IO_HELPER" >&2
  exit 1
fi

OUT_DIR="${OUT_DIR:-$ROOT/backups}"
PROJECT_NAME="${PROJECT_NAME:-}"
ENV_FILE="${ENV_FILE:-.env}"
COMPOSE_FILE="${COMPOSE_FILE:-compose.yml}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-0}"

# Uses docker compose through compose_args so callers can target disposable projects.
usage() {
  cat >&2 <<'USAGE'
Usage: backup.sh [--out-dir DIR] [--project-name NAME] [--env-file FILE] [--compose-file FILE]
                 [--timeout-seconds SECONDS]

Creates a PostgreSQL SQL dump and a sidecar JSON manifest containing size,
SHA-256, compose target metadata, and database/user metadata.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --out-dir)
      OUT_DIR="${2:?--out-dir requires a value}"
      shift
      ;;
    --project-name)
      PROJECT_NAME="${2:?--project-name requires a value}"
      shift
      ;;
    --env-file)
      ENV_FILE="${2:?--env-file requires a value}"
      shift
      ;;
    --compose-file)
      COMPOSE_FILE="${2:?--compose-file requires a value}"
      shift
      ;;
    --timeout-seconds)
      TIMEOUT_SECONDS="${2:?--timeout-seconds requires a value}"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
  shift
done

case "$TIMEOUT_SECONDS" in
  ''|*[!0-9]*)
    echo "--timeout-seconds must be a non-negative integer." >&2
    exit 2
    ;;
esac

mkdir -p "$OUT_DIR"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
out="${OUT_DIR}/postgres_${ts}.sql"
manifest="${out}.manifest.json"

compose_args=(compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
if [ -n "$PROJECT_NAME" ]; then
  compose_args+=(-p "$PROJECT_NAME")
fi

docker_with_timeout() {
  if [ "$TIMEOUT_SECONDS" -gt 0 ]; then
    if ! command -v timeout >/dev/null 2>&1; then
      echo "--timeout-seconds requires the platform 'timeout' command." >&2
      return 2
    fi
    timeout "$TIMEOUT_SECONDS" docker "$@"
  else
    docker "$@"
  fi
}

sha256_for() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print toupper($1)}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print toupper($1)}'
  else
    python - "$1" <<'PY'
import hashlib
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
digest = hashlib.sha256()
with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
print(digest.hexdigest().upper())
PY
  fi
}

backup_completed=0
cleanup_incomplete_backup() {
  if [ "$backup_completed" != "1" ]; then
    rm -f "$out" "$manifest"
  fi
}
trap cleanup_incomplete_backup EXIT

echo "Writing backup: $out"
if ! capture_receipt="$(
  docker_with_timeout "${compose_args[@]}" exec -T postgres sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' \
    | python "$SECURE_IO_HELPER" capture-file --path "$out"
)"; then
  echo "Backup failed. Incomplete SQL output removed." >&2
  exit 1
fi

db_info="$(docker_with_timeout "${compose_args[@]}" exec -T postgres sh -lc 'printf "%s\t%s" "$POSTGRES_DB" "$POSTGRES_USER"')"
case "$db_info" in
  *$'\t'*) ;;
  *)
    echo "PostgreSQL target response is malformed." >&2
    exit 1
    ;;
esac
postgres_db="${db_info%%$'\t'*}"
postgres_user="${db_info#*$'\t'}"
if [ -z "$postgres_db" ] || [ -z "$postgres_user" ]; then
  echo "PostgreSQL target response is incomplete." >&2
  exit 1
fi
IFS=$'\t' read -r sha256 size_bytes extra_receipt <<<"$capture_receipt"
if [ -z "$sha256" ] || [ -z "$size_bytes" ] || [ -n "${extra_receipt:-}" ]; then
  echo "Secure backup capture returned an invalid receipt." >&2
  exit 1
fi
compose_project="${PROJECT_NAME:-sds-api}"

python "$SECURE_IO_HELPER" write-backup-manifest \
  --path "$manifest" \
  --created-at-utc "$ts" \
  --backup-path "$out" \
  --size-bytes "$size_bytes" \
  --sha256 "$sha256" \
  --compose-project "$compose_project" \
  --env-file "$ENV_FILE" \
  --compose-file "$COMPOSE_FILE" \
  --postgres-db "$postgres_db" \
  --postgres-user "$postgres_user"
backup_completed=1
trap - EXIT

echo "Backup OK: $out"
echo "Manifest OK: $manifest"
