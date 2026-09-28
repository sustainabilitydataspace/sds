#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PROFILE="${1:-minimal}" # minimal

API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:8090}"

ADMIN_USER="${ADMIN_USER:-admin}"

dotenv_value() {
  local key="$1"
  local line
  if [ ! -f "${ROOT_DIR}/.env" ]; then
    return 1
  fi
  line="$(grep -E "^[[:space:]]*${key}=" "${ROOT_DIR}/.env" | tail -n 1 || true)"
  if [ -z "$line" ]; then
    return 1
  fi
  local value="${line#*=}"
  value="${value%$'\r'}"
  if [[ "$value" == \"*\" && "$value" == *\" ]]; then
    value="${value:1:${#value}-2}"
  fi
  printf "%s" "$value"
}

ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(dotenv_value BOOTSTRAP_ADMIN_PASSWORD || true)}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin123}"

CLEANUP="${CLEANUP:-0}" # set to 1 to run make down at the end
NO_START="${NO_START:-0}" # set to 1 to reuse an already started Compose stack

log() {
  printf "[smoke] %s\n" "$*"
}

die() {
  printf "[smoke][ERROR] %s\n" "$*" >&2
  exit 1
}

strip_cr() {
  printf "%s" "${1%$'\r'}"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

wait_for_url() {
  local url="$1"
  local name="$2"
  local max_seconds="${3:-90}"

  log "Waiting for ${name} (${url}) ..."
  local deadline=$((SECONDS + max_seconds))
  while true; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "${name} is up"
      return 0
    fi
    if [ $SECONDS -ge $deadline ]; then
      die "Timeout waiting for ${name} (${url})"
    fi
    sleep 2
  done
}

main() {
  require_cmd curl
  require_cmd python3
  require_cmd make

  if [ ! -f "${ROOT_DIR}/.env" ]; then
    die "Missing ${ROOT_DIR}/.env (run: cd api && cp .env.example .env)"
  fi

  cd "$ROOT_DIR"

  case "$PROFILE" in
    minimal)
      if [ "$NO_START" = "1" ]; then
        log "Reusing already started stack (minimal)"
      else
        log "Starting stack (minimal)"
        make up
      fi
      ;;
    *) die "Unknown profile: ${PROFILE} (use: minimal)" ;;
  esac

  wait_for_url "${API_BASE_URL}/healthz" "API"

  log "Checking /docs and /redoc render non-blank"
  docs_html="$(curl -fsS "${API_BASE_URL}/docs")"
  printf "%s" "$docs_html" | grep -qi "swagger" || die "/docs did not include the API docs shell"
  redoc_html="$(curl -fsS "${API_BASE_URL}/redoc")"
  printf "%s" "$redoc_html" | grep -qi "redoc" || die "/redoc did not look like ReDoc"

  log "Login as ${ADMIN_USER}"
  token="$(
    curl -fsS -X POST "${API_BASE_URL}/auth/login" \
      -H "Content-Type: application/json" \
      -d "{\"username\":\"${ADMIN_USER}\",\"password\":\"${ADMIN_PASSWORD}\"}" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin).get("access_token",""))'
  )"
  token="$(strip_cr "$token")"
  [ -n "$token" ] || die "Login failed (no access_token). If you changed the bootstrap password, set ADMIN_PASSWORD=..."

  authz_header="Authorization: Bearer $token"

  log "Check authenticated API readiness"
  curl -fsS "${API_BASE_URL}/ready" -H "$authz_header" >/dev/null

  log "Check semantic concept catalogue is populated"
  indicator_json="$(curl -fsS "${API_BASE_URL}/api/v1/indicators?limit=1" -H "$authz_header")"
  indicator_total="$(
    printf "%s" "$indicator_json" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin).get("total", 0))'
  )"
  indicator_total="$(strip_cr "$indicator_total")"
  concept_json="$(curl -fsS "${API_BASE_URL}/api/v1/concepts?limit=50" -H "$authz_header")"
  semantic_total="$(
    printf "%s" "$concept_json" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin).get("total", 0))'
  )"
  semantic_total="$(strip_cr "$semantic_total")"
  min_semantic_concepts="${MIN_SEMANTIC_CONCEPTS:-8}"
  [ "$semantic_total" -ge "$min_semantic_concepts" ] \
    || die "/api/v1/concepts returned total=${semantic_total}; expected at least ${min_semantic_concepts}. Run the semantic backfill/refresh before accepting the stack."
  [ "$semantic_total" -ge "$indicator_total" ] \
    || die "/api/v1/concepts returned total=${semantic_total}; expected at least active indicator total=${indicator_total}. Run the semantic catalog projector before accepting the stack."

  log "Pick a compatible concept from /api/v1/concepts (offline-safe)"
  readarray -t concept_pick < <(
    printf "%s" "$concept_json" \
      | python3 -c 'import json,sys
d=json.load(sys.stdin)
mapping={
    "sds:CubicMeter":"L",
    "sds:Tonne":"t",
    "sds:Kilogram":"kg",
    "sds:Liter":"L",
    "sds:KilowattHour":"kWh",
}
for item in d.get("items") or []:
    uri=item.get("uri","")
    if not uri:
        continue
    unit=item.get("unit")
    resolved=mapping.get(unit, unit)
    if not unit or (resolved and ":" not in resolved):
        print(uri)
        print(resolved or "L")
        break'
  )
  concept_uri="$(strip_cr "${concept_pick[0]:-}")"
  concept_unit="$(strip_cr "${concept_pick[1]:-}")"
  [ -n "$concept_uri" ] || die "No concept returned by /api/v1/concepts"
  [ -n "$concept_unit" ] || die "No compatible concept unit returned by /api/v1/concepts"

  entity_id="smoke_facility"

  log "Create + activate a minimal hierarchy so entity exists (${entity_id})"
  hierarchy_id="$(
    curl -fsS -X POST "${API_BASE_URL}/api/v1/hierarchies" \
      -H "Content-Type: application/json" \
      -H "$authz_header" \
      -d "{
        \"company_id\":\"smoke_company\",
        \"hierarchy_type\":\"organizational\",
        \"name\":\"smoke\",
        \"description\":\"smoke config\",
        \"levels\":[
          {\"id\":\"root\",\"name\":\"Root\",\"parent\":null,\"level\":0},
          {\"id\":\"${entity_id}\",\"name\":\"Smoke Facility\",\"parent\":\"root\",\"level\":1}
        ],
        \"active\":true
      }" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))'
  )"
  hierarchy_id="$(strip_cr "$hierarchy_id")"
  [ -n "$hierarchy_id" ] || die "Failed to create hierarchy config"

  curl -fsS -X POST "${API_BASE_URL}/api/v1/hierarchies/${hierarchy_id}/activate" -H "$authz_header" >/dev/null

  smoke_identity_suffix="$(python3 -c 'import secrets; print(secrets.token_hex(15))')"
  smoke_username="smoke_data_manager_${smoke_identity_suffix}"
  smoke_email="smoke-${smoke_identity_suffix}@example.com"
  smoke_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

  log "Create a disposable tenant-bound data manager"
  curl -fsS -X POST "${API_BASE_URL}/auth/users" \
    -H "Content-Type: application/json" \
    -H "$authz_header" \
    -d "{
      \"username\":\"${smoke_username}\",
      \"password\":\"${smoke_password}\",
      \"email\":\"${smoke_email}\",
      \"full_name\":\"Disposable smoke data manager\",
      \"company_id\":\"smoke_company\",
      \"role\":\"data_manager\"
    }" >/dev/null

  data_manager_token="$(
    curl -fsS -X POST "${API_BASE_URL}/auth/login" \
      -H "Content-Type: application/json" \
      -d "{\"username\":\"${smoke_username}\",\"password\":\"${smoke_password}\"}" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin).get("access_token",""))'
  )"
  data_manager_token="$(strip_cr "$data_manager_token")"
  [ -n "$data_manager_token" ] || die "Disposable data-manager login failed"
  data_manager_authz_header="Authorization: Bearer ${data_manager_token}"

  log "Create a value (concept=${concept_uri}, unit=${concept_unit})"
  value_id="$(
    curl -fsS -X POST "${API_BASE_URL}/api/v1/values" \
      -H "Content-Type: application/json" \
      -H "$data_manager_authz_header" \
      -d "{
        \"concept\":\"${concept_uri}\",
        \"entity\":\"${entity_id}\",
        \"period\":\"2024-01-15\",
        \"value\":123,
        \"unit\":\"${concept_unit}\"
      }" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))'
  )"
  value_id="$(strip_cr "$value_id")"
  [ -n "$value_id" ] || die "Failed to create value"

  log "List values (must include created id)"
  curl -fsS "${API_BASE_URL}/api/v1/values?entity=${entity_id}&limit=50" -H "$data_manager_authz_header" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); ids=[v.get('id') for v in (d.get('items') or [])]; assert '${value_id}' in ids, ids; print('ok')"

  log "Check calculate dependencies endpoint"
  curl -fsS "${API_BASE_URL}/api/v1/calculate/dependencies/csrd:E3_5" -H "$data_manager_authz_header" >/dev/null

  log "Bulk import smoke values"
  import_committed="$(
    curl -fsS -X POST "${API_BASE_URL}/api/v1/values/import" \
      -H "Content-Type: application/json" \
      -H "$data_manager_authz_header" \
      -d "{
        \"items\":[
          {
            \"concept\":\"${concept_uri}\",
            \"entity\":\"${entity_id}\",
            \"period\":\"2024-01-16\",
            \"period_start\":\"2024-01-16\",
            \"period_end\":\"2024-01-16\",
            \"external_key\":\"smoke:${hierarchy_id}:bulk-1\",
            \"value\":124,
            \"value_type\":\"numeric\",
            \"unit\":\"${concept_unit}\",
            \"metadata\":{\"source\":\"smoke_stack\"}
          },
          {
            \"concept\":\"${concept_uri}\",
            \"entity\":\"${entity_id}\",
            \"period\":\"2024-01-17\",
            \"period_start\":\"2024-01-17\",
            \"period_end\":\"2024-01-17\",
            \"external_key\":\"smoke:${hierarchy_id}:bulk-2\",
            \"value\":125,
            \"value_type\":\"numeric\",
            \"unit\":\"${concept_unit}\",
            \"metadata\":{\"source\":\"smoke_stack\"}
          }
        ]
      }" \
      | python3 -c 'import json,sys; print("true" if json.load(sys.stdin).get("committed") else "false")'
  )"
  import_committed="$(strip_cr "$import_committed")"
  [ "$import_committed" = "true" ] || die "Smoke value import did not commit"

  log "Smoke test OK (profile=${PROFILE})"

  if [ "$CLEANUP" = "1" ]; then
    log "Cleanup requested (make down)"
    make down
  fi
}

main "$@"
