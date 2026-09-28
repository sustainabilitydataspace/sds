#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SECURE_IO_HELPER="$SCRIPT_DIR/secure_operational_io.py"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

if [ ! -f "$SECURE_IO_HELPER" ]; then
  echo "Secure operational I/O helper not found: $SECURE_IO_HELPER" >&2
  exit 2
fi

usage() {
  cat >&2 <<'USAGE'
Usage: restore.sh [--yes] [--project-name NAME] [--env-file FILE] [--compose-file FILE]
                  [--manifest FILE] [--require-manifest] [--skip-verify]
                  [--allow-unverified-restore] [--allow-target-mismatch]
                  [--timeout-seconds SECONDS] <backup.sql>
USAGE
}

yes=0
backup_file=""
PROJECT_NAME="${PROJECT_NAME:-}"
ENV_FILE="${ENV_FILE:-.env}"
COMPOSE_FILE="${COMPOSE_FILE:-compose.yml}"
manifest_file=""
require_manifest=0
skip_verify=0
allow_unverified=0
allow_target_mismatch=0
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-0}"

# Uses docker compose through compose_args so callers can target disposable projects.

while [ "$#" -gt 0 ]; do
  case "$1" in
    --yes|-y)
      yes=1
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
    --manifest)
      manifest_file="${2:?--manifest requires a value}"
      shift
      ;;
    --require-manifest)
      require_manifest=1
      ;;
    --allow-unverified-restore)
      allow_unverified=1
      ;;
    --allow-target-mismatch)
      allow_target_mismatch=1
      ;;
    --skip-verify)
      skip_verify=1
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
      if [ -n "$backup_file" ]; then
        usage
        exit 2
      fi
      backup_file="$1"
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

if [ "$backup_file" = "" ]; then
  usage
  exit 2
fi

if [ ! -f "$backup_file" ]; then
  echo "Backup file not found: $backup_file" >&2
  exit 2
fi

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

if [ -z "$manifest_file" ] && [ -f "${backup_file}.manifest.json" ]; then
  manifest_file="${backup_file}.manifest.json"
fi

if [ -n "$manifest_file" ]; then
  if [ ! -f "$manifest_file" ]; then
    echo "Manifest file not found: $manifest_file" >&2
    exit 2
  fi
  if ! manifest_receipt="$(python "$SECURE_IO_HELPER" read-backup-manifest --path "$manifest_file")"; then
    echo "Manifest validation failed: $manifest_file" >&2
    exit 2
  fi
  IFS=$'\t' read -r expected_sha256 manifest_size manifest_db manifest_user extra_manifest <<<"$manifest_receipt"
  if [ -z "$expected_sha256" ] || [ -z "$manifest_size" ] \
    || [ -z "$manifest_db" ] || [ -z "$manifest_user" ] \
    || [ -n "${extra_manifest:-}" ]; then
    echo "Manifest receipt is incomplete: $manifest_file" >&2
    exit 2
  fi
  echo "Manifest contract OK: $manifest_file"

  # F13 M1: validate the manifest's recorded target against the live target container so
  # a valid backup is never silently restored into the wrong database/user.
  if ! target_info="$(docker_with_timeout "${compose_args[@]}" exec -T postgres sh -lc 'printf "%s\t%s" "$POSTGRES_DB" "$POSTGRES_USER"' 2>/dev/null)"; then
    echo "Unable to verify the live PostgreSQL restore target." >&2
    exit 2
  fi
  case "$target_info" in
    *$'\t'*) ;;
    *)
      echo "Live PostgreSQL target response is malformed." >&2
      exit 2
      ;;
  esac
  target_db="${target_info%%$'\t'*}"
  target_user="${target_info#*$'\t'}"
  if [ -z "$target_db" ] || [ -z "$target_user" ]; then
    echo "Live PostgreSQL target response is incomplete." >&2
    exit 2
  fi
  if [ -n "$manifest_db" ] && [ -n "$target_db" ] && \
     { [ "$manifest_db" != "$target_db" ] || [ "$manifest_user" != "$target_user" ]; }; then
    echo "Restore target mismatch:" >&2
    echo "  manifest target: db=$manifest_db user=$manifest_user" >&2
    echo "  live target:     db=$target_db user=$target_user" >&2
    if [ "$allow_target_mismatch" != "1" ]; then
      echo "Refusing restore into a different database/user. Pass --allow-target-mismatch to override." >&2
      exit 2
    fi
    echo "Proceeding despite target mismatch (--allow-target-mismatch)." >&2
  fi
elif [ "$require_manifest" = "1" ]; then
  echo "Manifest required but not found. Expected sidecar: ${backup_file}.manifest.json" >&2
  exit 2
else
  # F13 M2: never perform an unverified DESTRUCTIVE restore in automation. A
  # non-interactive (--yes) run without a manifest checksum must opt into the risk
  # explicitly via --allow-unverified-restore (break-glass).
  if [ "$yes" = "1" ] && [ "$allow_unverified" != "1" ]; then
    echo "Refusing unverified non-interactive restore: no manifest checksum for $backup_file." >&2
    echo "Provide a sidecar manifest, or pass --allow-unverified-restore for break-glass use." >&2
    exit 2
  fi
  echo "Warning: restoring without manifest checksum validation." >&2
fi

if [ "$yes" != "1" ]; then
  if [ ! -t 0 ]; then
    echo "Refusing non-interactive restore without --yes." >&2
    exit 2
  fi
  echo "This will restore into the active compose PostgreSQL database and may drop existing objects." >&2
  printf "Type RESTORE to continue: " >&2
  read -r confirmation
  if [ "$confirmation" != "RESTORE" ]; then
    echo "Restore cancelled." >&2
    exit 2
  fi
fi

if ! api_ps="$(docker_with_timeout "${compose_args[@]}" ps api --format json 2>/dev/null)"; then
  echo "Unable to determine whether the compose api service is stopped." >&2
  exit 2
fi
if printf "%s" "$api_ps" | python "$SECURE_IO_HELPER" compose-service-stopped --service api; then
  :
else
  service_state_status=$?
  if [ "$service_state_status" -eq 3 ]; then
    echo "Refusing restore while the compose api service is running." >&2
    echo "Stop it first with docker compose ... stop api, then restart it after a verified restore." >&2
  else
    echo "Unable to verify the compose api service state safely." >&2
  fi
  exit 2
fi

echo "Restoring from: $backup_file"
restore_sha256="${expected_sha256:-$(sha256_for "$backup_file" | tr '[:upper:]' '[:lower:]')}"
stream_args=(stream-file --path "$backup_file" --sha256 "$restore_sha256")
if [ -n "${manifest_size:-}" ]; then
  stream_args+=(--size "$manifest_size")
fi
python "$SECURE_IO_HELPER" "${stream_args[@]}" \
  | docker_with_timeout "${compose_args[@]}" exec -T postgres sh -lc 'psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" --set ON_ERROR_STOP=on --single-transaction' >/dev/null
echo "Restore OK"

if [ "$skip_verify" != "1" ]; then
  verify_sql='
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM alembic_version) THEN
    RAISE EXCEPTION '"'"'missing alembic_version row'"'"';
  END IF;
END $$;
SELECT '"'"'alembic_version='"'"' || version_num FROM alembic_version LIMIT 1;
SELECT '"'"'indicators='"'"' || COUNT(*) FROM indicators;
SELECT '"'"'units='"'"' || COUNT(*) FROM units;
SELECT '"'"'concepts='"'"' || COUNT(*) FROM concepts;
SELECT '"'"'user_accounts='"'"' || COUNT(*) FROM user_accounts;
'
  printf "%s\n" "$verify_sql" | docker_with_timeout "${compose_args[@]}" exec -T postgres sh -lc 'psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" --set ON_ERROR_STOP=on -At'
  echo "Post-restore verification OK"
else
  echo "Post-restore verification skipped by --skip-verify"
fi
