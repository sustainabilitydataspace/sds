#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_SOURCE="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd -- "$(dirname -- "$SCRIPT_SOURCE")" && pwd -P)"
SECURE_IO_HELPER="$SCRIPT_DIR/secure_operational_io.py"

SERVICE_NAME="${SERVICE_NAME:-sds-api.service}"
CURRENT_SYMLINK="${CURRENT_SYMLINK:-/opt/sds-api/current}"
BACKUP_DIR="${BACKUP_DIR:-/opt/sds-api/backups}"
ENV_FILE="${ENV_FILE:-/etc/sds-api/sds-api.env}"
DB_NAME="${DB_NAME:-sds_api}"
# Host-specific values are operator-supplied via env, not hardcoded in this public
# script. SERVICE_USER is optional (chown is skipped when empty); the smoke URLs
# infer the systemd ExecStart port before falling back to the documented API port
# and can be overridden per deployment.
SERVICE_USER="${SERVICE_USER:-}"
HEALTH_PORT="${HEALTH_PORT:-}"
HEALTH_URL="${HEALTH_URL:-}"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10
MAX_PYTHON_MINOR=12

TARBALL=""
TARBALL_SHA256=""
RELEASE=""
COMMIT=""
PYTHON_BIN="${PYTHON_BIN:-}"
STRIP_COMPONENTS=1
RUN_BOOTSTRAP=0
RUN_SEMANTIC_PROJECTOR=1
RESTART_SERVICE=1
CREATE_DB_BACKUP=1

usage() {
  cat <<'EOF'
Usage:
  deploy_native_release.sh --tarball /tmp/sds-api-<commit>.tar \
    --tarball-sha256 <sha256> \
    --release /opt/sds-api/releases/<release-name> --commit <short-sha> [options]

Options:
  --python <path>              Explicit Python interpreter for venv creation.
  --tarball-sha256 <sha256>    Required independently supplied TAR SHA-256.
  --env-file <path>            Runtime env file. Defaults to /etc/sds-api/sds-api.env.
  --service-name <name>        systemd service. Defaults to sds-api.service.
  --current-symlink <path>     Current release symlink. Defaults to /opt/sds-api/current.
  --backup-dir <path>          Backup/report directory. Defaults to /opt/sds-api/backups.
  --db-name <name>             PostgreSQL database. Defaults to sds_api.
  --strip-components <n>       tar strip count. Defaults to 1 for git archive api/.
  --bootstrap-semantic-model   Run semantic bootstrap repair before projector gates.
  --skip-semantic-projector    Skip semantic projector dry-run/apply/final-gate.
  --skip-db-backup             Skip pg_dump backup.
  --skip-restart               Prepare and gate release without switching/restarting.
  -h, --help                   Show this help.

The script refuses to install dependencies until it has selected and validated
a Python 3.10-3.12 interpreter. Resolver order: explicit --python/PYTHON_BIN,
systemd service ExecStart, current release venv, then python3.12/3.11/3.10.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tarball)
      TARBALL="${2:?missing value for --tarball}"
      shift 2
      ;;
    --tarball-sha256)
      TARBALL_SHA256="${2:?missing value for --tarball-sha256}"
      shift 2
      ;;
    --release)
      RELEASE="${2:?missing value for --release}"
      shift 2
      ;;
    --commit)
      COMMIT="${2:?missing value for --commit}"
      shift 2
      ;;
    --python)
      PYTHON_BIN="${2:?missing value for --python}"
      shift 2
      ;;
    --env-file)
      ENV_FILE="${2:?missing value for --env-file}"
      shift 2
      ;;
    --service-name)
      SERVICE_NAME="${2:?missing value for --service-name}"
      shift 2
      ;;
    --current-symlink)
      CURRENT_SYMLINK="${2:?missing value for --current-symlink}"
      shift 2
      ;;
    --backup-dir)
      BACKUP_DIR="${2:?missing value for --backup-dir}"
      shift 2
      ;;
    --db-name)
      DB_NAME="${2:?missing value for --db-name}"
      shift 2
      ;;
    --strip-components)
      STRIP_COMPONENTS="${2:?missing value for --strip-components}"
      shift 2
      ;;
    --bootstrap-semantic-model)
      RUN_BOOTSTRAP=1
      shift
      ;;
    --skip-semantic-projector)
      RUN_SEMANTIC_PROJECTOR=0
      shift
      ;;
    --skip-db-backup)
      CREATE_DB_BACKUP=0
      shift
      ;;
    --skip-restart)
      RESTART_SERVICE=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

require_value() {
  local value="$1"
  local name="$2"
  if [ -z "$value" ]; then
    echo "Missing required argument: $name" >&2
    usage >&2
    exit 2
  fi
}

python_label() {
  "$1" - <<'PY'
import sys
print(".".join(str(part) for part in sys.version_info[:3]))
PY
}

validate_python() {
  local python="$1"
  local version
  if [ ! -x "$python" ]; then
    echo "Refusing deployment: selected Python is not executable: $python" >&2
    return 1
  fi
  if ! version="$(python_label "$python" 2>/dev/null)"; then
    echo "Refusing deployment: selected Python cannot report its version: $python" >&2
    return 1
  fi
  if ! "$python" - <<PY
import sys
raise SystemExit(
    0
    if (
        sys.version_info.major == $MIN_PYTHON_MAJOR
        and $MIN_PYTHON_MINOR <= sys.version_info.minor <= $MAX_PYTHON_MINOR
    )
    else 1
)
PY
  then
    echo "Refusing deployment: selected Python $python is version $version; SDS API requires Python 3.10-3.12." >&2
    return 1
  fi
}

service_exec_start() {
  local line
  while IFS= read -r line; do
    case "$line" in
      ExecStart=*)
        printf '%s\n' "${line#ExecStart=}"
        return 0
        ;;
    esac
  done < <(systemctl cat "$SERVICE_NAME" 2>/dev/null || true)
}

service_exec_python() {
  local exec_start
  exec_start="$(service_exec_start)"
  if [ -z "$exec_start" ]; then
    return 0
  fi
  set -- $exec_start
  local candidate="${1:-}"
  candidate="${candidate#-}"
  candidate="${candidate#+}"
  candidate="${candidate#!}"
  candidate="${candidate#@}"
  case "$candidate" in
    *python*) printf '%s\n' "$candidate" ;;
  esac
}

service_exec_port() {
  local exec_start port
  exec_start="$(service_exec_start)"
  if [ -z "$exec_start" ]; then
    return 0
  fi
  set -- $exec_start
  while [ "$#" -gt 0 ]; do
    port=""
    case "$1" in
      --port=*)
        port="${1#--port=}"
        ;;
      --port)
        shift || true
        port="${1:-}"
        ;;
    esac
    if [ -n "$port" ]; then
      case "$port" in
        *[!0-9]*)
          return 0
          ;;
        *)
          printf '%s\n' "$port"
          return 0
          ;;
      esac
    fi
    shift || true
  done
}

candidate_python_bins() {
  if [ -n "$PYTHON_BIN" ]; then
    printf '%s\n' "$PYTHON_BIN"
  fi
  service_exec_python
  printf '%s\n' "$CURRENT_SYMLINK/.venv/bin/python"
  for name in python3.12 python3.11 python3.10; do
    command -v "$name" 2>/dev/null || true
  done
}

resolve_python() {
  local candidate
  while IFS= read -r candidate; do
    if [ -z "$candidate" ]; then
      continue
    fi
    if validate_python "$candidate" >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done < <(candidate_python_bins | awk 'NF && !seen[$0]++')

  echo "No usable Python >= ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR} found." >&2
  echo "Checked explicit PYTHON_BIN/--python, service ExecStart, current release venv, python3.12, python3.11, and python3.10." >&2
  return 1
}

resolve_smoke_urls() {
  if [ -z "$HEALTH_PORT" ]; then
    HEALTH_PORT="$(service_exec_port || true)"
  fi
  HEALTH_PORT="${HEALTH_PORT:-8090}"
  HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:${HEALTH_PORT}/healthz}"
}

verify_service_ready() {
  local health_file="${1:-$HEALTH_FILE}"
  curl -fsS "$HEALTH_URL" >"$health_file" 2>/dev/null \
    && systemctl is-active --quiet "$SERVICE_NAME"
}

refuse_deployment() {
  echo "Refusing deployment: $1" >&2
  return 1
}

verify_active_target_unchanged() {
  local observed_target
  if [ ! -L "$CURRENT_SYMLINK" ]; then
    refuse_deployment "active release link changed or is no longer a symlink: $CURRENT_SYMLINK"
    return 1
  fi
  observed_target="$(readlink -f -- "$CURRENT_SYMLINK" 2>/dev/null || true)"
  if [ -z "$observed_target" ] || [ ! -d "$observed_target" ] \
    || [ "$observed_target" != "$ACTIVE_TARGET" ]; then
    refuse_deployment "active release link changed during deployment: $CURRENT_SYMLINK"
    return 1
  fi
}

swap_current_link() {
  local expected_target="$1"
  local observed_target
  if ! ln -sfnT -- "$expected_target" "$CURRENT_SYMLINK"; then
    echo "Current release link swap failed: $CURRENT_SYMLINK -> $expected_target" >&2
    return 1
  fi
  observed_target="$(readlink -f -- "$CURRENT_SYMLINK" 2>/dev/null || true)"
  if [ "$observed_target" != "$expected_target" ]; then
    echo "Current release link verification failed: expected $expected_target; observed ${observed_target:-unresolved}" >&2
    return 1
  fi
}

verify_fresh_destination_unchanged() {
  local observed_parent
  if [ -e "$RELEASE" ] || [ -L "$RELEASE" ]; then
    refuse_deployment "release destination is no longer fresh: $RELEASE"
    return 1
  fi
  observed_parent="$(readlink -f -- "$RELEASE_PARENT" 2>/dev/null || true)"
  if [ -z "$observed_parent" ] || [ ! -d "$observed_parent" ] \
    || [ "$observed_parent" != "$RELEASE_PARENT" ]; then
    refuse_deployment "release container changed or is unsafe: $RELEASE_PARENT"
    return 1
  fi
}

preflight_release_paths() {
  local current_parent current_name requested_parent release_name
  local resolved_current_parent resolved_release resolved_release_parent

  case "$CURRENT_SYMLINK" in
    /*) ;;
    *)
      refuse_deployment "active release link must be an absolute path: $CURRENT_SYMLINK"
      return 1
      ;;
  esac
  case "$RELEASE" in
    /*) ;;
    *)
      refuse_deployment "release destination must be an absolute path: $RELEASE"
      return 1
      ;;
  esac

  current_parent="$(dirname -- "$CURRENT_SYMLINK")"
  current_name="$(basename -- "$CURRENT_SYMLINK")"
  resolved_current_parent="$(readlink -f -- "$current_parent" 2>/dev/null || true)"
  if [ -z "$resolved_current_parent" ] || [ ! -d "$resolved_current_parent" ]; then
    refuse_deployment "active release link has an unsafe parent: $current_parent"
    return 1
  fi
  CURRENT_SYMLINK="$resolved_current_parent/$current_name"
  CURRENT_PARENT="$resolved_current_parent"

  if [ ! -L "$CURRENT_SYMLINK" ]; then
    refuse_deployment "active release link is missing, broken, or not a symlink: $CURRENT_SYMLINK"
    return 1
  fi
  ACTIVE_TARGET="$(readlink -f -- "$CURRENT_SYMLINK" 2>/dev/null || true)"
  if [ -z "$ACTIVE_TARGET" ] || [ ! -d "$ACTIVE_TARGET" ]; then
    refuse_deployment "active release link is broken or unsafe: $CURRENT_SYMLINK"
    return 1
  fi
  ACTIVE_PARENT="$(dirname -- "$ACTIVE_TARGET")"

  if [ -e "$RELEASE" ] || [ -L "$RELEASE" ]; then
    resolved_release="$(readlink -f -- "$RELEASE" 2>/dev/null || true)"
    if [ -n "$resolved_release" ] && [ "$resolved_release" = "$ACTIVE_TARGET" ]; then
      refuse_deployment "release destination resolves to the active release: $RELEASE"
    else
      refuse_deployment "release destination already exists: $RELEASE"
    fi
    return 1
  fi

  requested_parent="$(dirname -- "$RELEASE")"
  release_name="$(basename -- "$RELEASE")"
  resolved_release_parent="$(readlink -f -- "$requested_parent" 2>/dev/null || true)"
  if [ -z "$resolved_release_parent" ] || [ ! -d "$resolved_release_parent" ]; then
    refuse_deployment "release destination has an unsafe container: $requested_parent"
    return 1
  fi
  if [ "$resolved_release_parent" != "$ACTIVE_PARENT" ]; then
    refuse_deployment "release destination is outside the active release container: $requested_parent"
    return 1
  fi

  RELEASE_PARENT="$resolved_release_parent"
  RELEASE="$RELEASE_PARENT/$release_name"
  if [ "$RELEASE" = "$ACTIVE_TARGET" ]; then
    refuse_deployment "release destination resolves to the active release: $RELEASE"
    return 1
  fi
}

require_value "$TARBALL" "--tarball"
require_value "$TARBALL_SHA256" "--tarball-sha256"
require_value "$RELEASE" "--release"
require_value "$COMMIT" "--commit"
if [ "$RUN_BOOTSTRAP" -eq 1 ]; then
  refuse_deployment "semantic bootstrap is a separate maintenance operation and cannot run during release preparation"
  exit 1
fi

if [ ! -f "$SECURE_IO_HELPER" ]; then
  echo "Secure operational I/O helper not found: $SECURE_IO_HELPER" >&2
  exit 1
fi

if [ ! -f "$TARBALL" ]; then
  echo "Tarball not found: $TARBALL" >&2
  exit 1
fi

preflight_release_paths || exit 1

# Lock the already-existing current-link parent without creating a lock file.
# Deployments using the same current link therefore serialize before mutation.
exec 9<"$CURRENT_PARENT"
if ! flock -n 9; then
  refuse_deployment "another deployment is already operating on $CURRENT_SYMLINK"
  exit 1
fi
verify_active_target_unchanged || exit 1
verify_fresh_destination_unchanged || exit 1

PYTHON_BIN="$(resolve_python)"
validate_python "$PYTHON_BIN"
HEALTH_FILE="$(mktemp "${TMPDIR:-/tmp}/sds-api-health.XXXXXX")"
trap 'rm -f -- "$HEALTH_FILE"' EXIT
resolve_smoke_urls
echo "Using Python: $PYTHON_BIN ($(python_label "$PYTHON_BIN"))"
echo "Smoke health URL: $HEALTH_URL"

mkdir -p "$BACKUP_DIR"
verify_active_target_unchanged
verify_fresh_destination_unchanged
"$PYTHON_BIN" "$SECURE_IO_HELPER" extract-tar \
  --tarball "$TARBALL" \
  --sha256 "$TARBALL_SHA256" \
  --destination "$RELEASE" \
  --strip-components "$STRIP_COMPONENTS" >/dev/null
printf '%s\n' "$COMMIT" > "$RELEASE/.deployment_commit"

if [ ! -f "$RELEASE/requirements.lock" ] || [ ! -d "$RELEASE/wheelhouse" ]; then
  refuse_deployment "release must contain requirements.lock and an offline wheelhouse"
  exit 1
fi
"$PYTHON_BIN" -m venv "$RELEASE/.venv"
"$RELEASE/.venv/bin/python" -m pip install \
  --no-index --find-links "$RELEASE/wheelhouse" --require-hashes \
  -r "$RELEASE/requirements.lock" >/dev/null

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
if [ "$CREATE_DB_BACKUP" -eq 1 ]; then
  DB_BACKUP="$BACKUP_DIR/pre-native-release-${COMMIT}-${STAMP}.dump"
  sudo -u postgres pg_dump -Fc --dbname="$DB_NAME" \
    | "$PYTHON_BIN" "$SECURE_IO_HELPER" capture-file --path "$DB_BACKUP" >/dev/null
  echo "DB backup: $DB_BACKUP"
fi

ENV_DIGEST="$("$PYTHON_BIN" "$SECURE_IO_HELPER" env-digest --env-file "$ENV_FILE")"
runtime_env_exec() {
  "$PYTHON_BIN" "$SECURE_IO_HELPER" env-exec \
    --env-file "$ENV_FILE" --sha256 "$ENV_DIGEST" -- \
    env PYTHONPATH="$RELEASE" "$@"
}
cd "$RELEASE"

runtime_env_exec .venv/bin/python -m alembic current --check-heads

if [ "$RUN_SEMANTIC_PROJECTOR" -eq 1 ]; then
  GATE_REPORT="$BACKUP_DIR/semantic-projector-${COMMIT}-gate.json"
  runtime_env_exec .venv/bin/python scripts/gate_semantic_catalog_projection.py --report "$GATE_REPORT"
  echo "Semantic gate report: $GATE_REPORT"
fi

if [ -n "$SERVICE_USER" ] && id "$SERVICE_USER" >/dev/null 2>&1; then
  chown -R "$SERVICE_USER:$SERVICE_USER" "$RELEASE"
fi

if [ "$RESTART_SERVICE" -eq 1 ]; then
  PREVIOUS_TARGET="$ACTIVE_TARGET"

  rollback_release() {
    local deployed_target
    echo "Deployment smoke failed for $RELEASE; rolling back." >&2
    if [ -n "$PREVIOUS_TARGET" ] && [ -d "$PREVIOUS_TARGET" ] \
      && [ "$PREVIOUS_TARGET" != "$RELEASE" ]; then
      deployed_target="$(readlink -f -- "$CURRENT_SYMLINK" 2>/dev/null || true)"
      if [ "$deployed_target" != "$RELEASE" ]; then
        echo "Current release changed after the swap; refusing to overwrite it during rollback." >&2
        return 1
      fi
      if ! swap_current_link "$PREVIOUS_TARGET"; then
        echo "Rollback link swap failed; refusing to restart an unverified release." >&2
        return 1
      fi
      if systemctl restart "$SERVICE_NAME" \
        && verify_service_ready "$HEALTH_FILE"; then
        echo "Restored previous release verified healthy: $PREVIOUS_TARGET" >&2
        return 0
      fi
      echo "Previous release restore failed health/readiness checks: $PREVIOUS_TARGET" >&2
      return 1
    else
      echo "No prior healthy release to restore; manual intervention required." >&2
      return 1
    fi
  }

  verify_active_target_unchanged
  swap_current_link "$RELEASE" || exit 1
  if ! systemctl restart "$SERVICE_NAME"; then
    rollback_release
    exit 1
  fi

  healthy=0
  for _ in $(seq 1 60); do
    if curl -fsS "$HEALTH_URL" >"$HEALTH_FILE" 2>/dev/null; then
      healthy=1
      break
    fi
    sleep 3
  done

  if [ "$healthy" -ne 1 ] || ! verify_service_ready; then
    rollback_release || true
    exit 1
  fi
fi

if [ "$RESTART_SERVICE" -eq 0 ]; then
  verify_active_target_unchanged
fi

echo "Release prepared: $RELEASE"
echo "Deployment commit: $(cat "$RELEASE/.deployment_commit")"
