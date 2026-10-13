#!/usr/bin/env bash
# tools/bootstrap.sh — bootstrap via venv + pip (Linux/macOS)
set -euo pipefail

BOOTSTRAP_TOOL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tools/adaosd_service_adapter.sh
source "${BOOTSTRAP_TOOL_DIR}/adaosd_service_adapter.sh"

CLIENT_SUBMODULE_PATH="src/adaos/integrations/adaos-client"
BACKEND_SUBMODULE_PATH="src/adaos/integrations/adaos-backend"
INFRA_SUBMODULE_PATH="src/adaos/integrations/infra-inimatic"
RASA_SUBMODULE_PATH="src/adaos/integrations/rasa-port"

VENV_DIR=".venv"
VENV_ACTIVATE=".venv/bin/activate"
MIN_PYTHON="3.11.9"
MAX_PYTHON_EXCLUSIVE="3.12.0"

JOIN_CODE=""
ROLE=""
INSTALL_SERVICE="auto" # auto|always|never
SERVE_HOST="127.0.0.1"
SERVE_PORT="8777"
CONTROL_PORT="8777"
ROOT_URL="https://api.inimatic.com"
REV="main"
ZONE_ID=""
LANGUAGE="${ADAOS_LANG:-en}"
NO_VOICE="0"
DEV_MODE="0"
BUILD_VENDORED_Y_PY="0"
PYTHON_ARG=""
NODE_NAME=""
NO_CORE_UPDATE="0"
WORKSPACE_REGISTRY_REPO="${ADAOS_WORKSPACE_REGISTRY_REPO:-}"
SUPERVISOR_IMPLEMENTATION="${ADAOS_SUPERVISOR_IMPLEMENTATION:-${ADAOS_RUNTIME_ENGINE:-}}"
SIDECAR_IMPLEMENTATION="${ADAOS_SIDECAR_IMPLEMENTATION:-${ADAOS_REALTIME_IMPLEMENTATION:-}}"
UPDATE_IMPLEMENTATION="${ADAOS_CORE_UPDATE_IMPLEMENTATION:-}"
ADAOSD_BINARY="${ADAOSD_BINARY:-}"
ADAOSD_SERVICE_CONFIG="${ADAOSD_SERVICE_CONFIG:-}"
ADAOSD_APPROVAL_SCOPE="${ADAOSD_APPROVAL_SCOPE:-m4c_canary}"
ADAOSD_APPROVAL_ID="${ADAOSD_APPROVAL_ID:-}"
ADAOSD_APPROVAL_STAND="${ADAOSD_APPROVAL_STAND:-}"

log()  { printf '\033[36m[*] %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m[+] %s\033[0m\n' "$*"; }
warn() { printf '\033[33m[!] %s\033[0m\n' "$*"; }
fail() { printf '\033[31m[x] %s\033[0m\n' "$*"; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

resolve_symlink_path() {
  local target="$1"
  [[ -n "${target:-}" ]] || return 1

  while [[ -L "$target" ]]; do
    local dir link
    dir="$(cd -P "$(dirname "$target")" >/dev/null 2>&1 && pwd)" || return 1
    link="$(readlink "$target")" || return 1
    if [[ "$link" == /* ]]; then
      target="$link"
    else
      target="$dir/$link"
    fi
  done

  if [[ "$target" == /* ]]; then
    printf '%s\n' "$target"
  else
    local dir
    dir="$(cd -P "$(dirname "$target")" >/dev/null 2>&1 && pwd)" || return 1
    printf '%s/%s\n' "$dir" "$(basename "$target")"
  fi
}

normalize_python_candidate() {
  local candidate="$1"
  local resolved=""
  if resolved="$(resolve_symlink_path "$candidate" 2>/dev/null)" && [[ -x "$resolved" ]]; then
    printf '%s\n' "$resolved"
  else
    printf '%s\n' "$candidate"
  fi
}

effective_root_url() {
  local root_url="$1"
  local zone_id="${2:-}"
  local normalized_zone
  normalized_zone="$(printf '%s' "${zone_id:-}" | tr '[:upper:]' '[:lower:]')"
  if [[ ! "$normalized_zone" =~ ^[a-z]{2}$ ]]; then
    normalized_zone=""
  fi
  case "$root_url" in
    ""|"https://api.inimatic.com"|"http://api.inimatic.com")
      if [[ "$normalized_zone" == "ru" ]]; then
        printf '%s' "https://${normalized_zone}.api.inimatic.com"
        return 0
      fi
      ;;
  esac
  printf '%s' "$root_url"
}

write_env_var() {
  local key="$1"
  local value="$2"
  local env_file="${3:-.env}"
  [[ -n "${key:-}" ]] || return 0
  touch "$env_file"
  local tmp_file
  tmp_file="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { updated = 0 }
    $0 ~ ("^" key "=") {
      print key "=" value
      updated = 1
      next
    }
    { print }
    END {
      if (!updated) {
        print key "=" value
      }
    }
  ' "$env_file" > "$tmp_file"
  mv "$tmp_file" "$env_file"
}

remove_env_var() {
  local key="$1"
  local env_file="${2:-.env}"
  [[ -f "$env_file" && -n "${key:-}" ]] || return 0
  local tmp_file
  tmp_file="$(mktemp)"
  awk -v key="$key" '$0 !~ ("^" key "=") { print }' "$env_file" > "$tmp_file"
  mv "$tmp_file" "$env_file"
}

ORIG_ARGS=("$@")

show_qr_if_available() {
  local text="$1"
  [[ -z "${text:-}" ]] && return 0
  have qrencode || return 0
  echo
  echo "     (QR)"
  qrencode -t ANSIUTF8 "$text" 2>/dev/null || true
  echo
}

detect_venv_activate() {
  if [[ -f "${VENV_DIR}/bin/activate" ]]; then
    VENV_ACTIVATE="${VENV_DIR}/bin/activate"
    return 0
  fi
  if [[ -f "${VENV_DIR}/Scripts/activate" ]]; then
    # Not expected on Linux/macOS, but helps on Git-Bash style envs.
    VENV_ACTIVATE="${VENV_DIR}/Scripts/activate"
    return 0
  fi
  return 1
}

venv_is_usable() {
  [[ -d "${VENV_DIR}" ]] || return 1
  detect_venv_activate || return 1
  return 0
}

http_get() {
  local url="$1"
  local header="${2:-}"
  if have curl; then
    if [[ -n "$header" ]]; then
      curl -fsS -H "$header" "$url"
    else
      curl -fsS "$url"
    fi
    return $?
  fi
  if have wget; then
    if [[ -n "$header" ]]; then
      wget -qO- --header="$header" "$url"
    else
      wget -qO- "$url"
    fi
    return $?
  fi
  return 1
}

read_env_value_from_file() {
  local path="$1"
  local key="$2"
  [[ -f "$path" ]] || return 0
  sed -n "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//p" "$path" \
    | head -n 1 \
    | tr -d '\r' \
    | tr -d '"' \
    | tr -d "'" \
    | xargs \
    || true
}

read_env_type_from_file() {
  read_env_value_from_file "$1" "ENV_TYPE"
}

resolve_adaos_base_dir() {
  if [[ -n "${ADAOS_BASE_DIR:-}" ]]; then
    printf '%s' "${ADAOS_BASE_DIR}"
    return 0
  fi
  local env_type="${ENV_TYPE:-}"
  if [[ -z "${env_type:-}" ]]; then
    env_type="$(read_env_type_from_file ".env" || true)"
  fi
  env_type="${env_type:-prod}"
  if [[ "$env_type" == "dev" ]]; then
    printf '%s' "$PWD/.adaos"
    return 0
  fi
  printf '%s' "$HOME/.adaos"
}

fallback_to_uv() {
  local reason="$1"
  warn "$reason"
  warn "Falling back to uv-based bootstrap (no root, no system Python required)..."
  # Some installers/extractors may drop the executable bit (or mount with `noexec`),
  # so invoke explicitly via bash instead of executing the file directly.
  exec bash "./tools/bootstrap_uv.sh" "${ORIG_ARGS[@]}"
}

ensure_required_submodules() {
  if ! have git; then
    warn "git not found; required submodule ${RASA_SUBMODULE_PATH} cannot be initialized automatically."
    return 0
  fi
  if [[ ! -d ".git" && ! -f ".git" ]]; then
    if [[ ! -f "${RASA_SUBMODULE_PATH}/pyproject.toml" ]]; then
      warn "Repository is not a git checkout; required submodule ${RASA_SUBMODULE_PATH} is unavailable in archive mode."
    fi
    return 0
  fi
  [[ -f "${RASA_SUBMODULE_PATH}/pyproject.toml" ]] && return 0
  log "Initializing required submodule: ${RASA_SUBMODULE_PATH}"
  git submodule sync -- "${RASA_SUBMODULE_PATH}" || { warn "git submodule sync failed for ${RASA_SUBMODULE_PATH}"; return 0; }
  git submodule update --init --recursive "${RASA_SUBMODULE_PATH}" || { warn "git submodule update failed for ${RASA_SUBMODULE_PATH}"; return 0; }
  if [[ -f "${RASA_SUBMODULE_PATH}/.git" && ! -f "${RASA_SUBMODULE_PATH}/pyproject.toml" ]]; then
    warn "${RASA_SUBMODULE_PATH} worktree is incomplete; restoring from HEAD..."
    git -C "${RASA_SUBMODULE_PATH}" restore --source=HEAD --worktree .
    git -C "${RASA_SUBMODULE_PATH}" restore --source=HEAD --staged .
  fi
}

show_optional_modules_note() {
  local missing=()
  [[ -f "${CLIENT_SUBMODULE_PATH}/package.json" ]] || missing+=("${CLIENT_SUBMODULE_PATH}")
  [[ -f "${BACKEND_SUBMODULE_PATH}/package.json" ]] || missing+=("${BACKEND_SUBMODULE_PATH}")
  [[ -f "${INFRA_SUBMODULE_PATH}/README.md" ]] || missing+=("${INFRA_SUBMODULE_PATH}")

  echo
  echo "Optional private modules:"
  echo "  Client:  ${CLIENT_SUBMODULE_PATH}"
  echo "  Backend: ${BACKEND_SUBMODULE_PATH}"
  echo "  Infra:   ${INFRA_SUBMODULE_PATH}"
  if (( ${#missing[@]} > 0 )); then
    echo "  Missing locally. Initialize only if you need them:"
    echo "    git submodule update --init --recursive ${missing[*]}"
  fi
}

print_bootstrap_config() {
  echo
  echo "Bootstrap config:"
  echo "  repo_root:      $PWD"
  echo "  env_file:       $PWD/.env"
  echo "  env_type:       ${ENV_TYPE:-}"
  echo "  adaos_base_dir: ${ADAOS_BASE_DIR:-}"
  echo "  dev_mode:       ${DEV_MODE:-0}"
  echo "  node_name:      ${NODE_NAME:-}"
  echo "  supervisor:     ${SUPERVISOR_IMPLEMENTATION:-python}"
  echo "  sidecar:        ${SIDECAR_IMPLEMENTATION:-python}"
  echo "  core update:    ${UPDATE_IMPLEMENTATION:-python}"
  if [[ "${SUPERVISOR_IMPLEMENTATION:-python}" == "go" || "${SIDECAR_IMPLEMENTATION:-python}" == "go" ]]; then
    echo "  adaosd_binary:  ${ADAOSD_BINARY:-}"
  fi
  if [[ "${SUPERVISOR_IMPLEMENTATION:-python}" == "go" ]]; then
    echo "  service_config: ${ADAOSD_SERVICE_CONFIG:-auto}"
  fi
  echo "  core_update:    $( [[ "${NO_CORE_UPDATE:-0}" == "1" ]] && printf '%s' "disabled" || printf '%s' "enabled" )"
  echo "  workspace_registry_repo: ${WORKSPACE_REGISTRY_REPO:-}"
  echo
}

set_node_name() {
  local py="$1"
  local node_name="$2"
  [[ -n "${node_name:-}" ]] || return 0
  log "Setting node name: ${node_name}"
  "$py" - "$node_name" <<'PY'
import sys

from adaos.services.node_config import set_node_names

name = str(sys.argv[1] or "").strip()
if not name:
    raise SystemExit(0)
conf = set_node_names([name])
names = list(getattr(conf, "node_names", []) or [])
print("node_names=" + ",".join(names))
PY
}

set_core_update_enabled() {
  local py="$1"
  local enabled="$2"
  log "Setting core update enabled: ${enabled}"
  "$py" - "$enabled" <<'PY'
import sys

from adaos.services.node_config import set_core_update_enabled

token = str(sys.argv[1] or "").strip().lower()
enabled = token in {"1", "true", "yes", "on"}
conf = set_core_update_enabled(enabled)
print("core_update_enabled=" + str(bool(getattr(conf, "core_update_enabled", True))).lower())
PY
}

wait_for_autostart_activation() {
  local timeout_sec="${1:-45}"
  local deadline=$(( $(date +%s) + timeout_sec ))
  local as_json=""
  local as_rc=0
  local active=""
  local listening=""

  while [[ $(date +%s) -lt $deadline ]]; do
    set +e
    as_json="$(python -m adaos autostart status --json 2>/dev/null)"
    as_rc=$?
    set -e
    if [[ $as_rc -eq 0 && -n "${as_json:-}" ]]; then
      active="$(
        python -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); v=d.get("active"); print("" if v is None else str(bool(v)).lower())' <<<"$as_json" 2>/dev/null || true
      )"
      listening="$(
        python -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); v=d.get("listening"); print("" if v is None else str(bool(v)).lower())' <<<"$as_json" 2>/dev/null || true
      )"
      if [[ "${active:-}" == "true" && "${listening:-}" != "false" ]]; then
        return 0
      fi
    fi
    sleep 2
  done
  return 1
}

print_autostart_diagnostics() {
  local as_json=""
  local as_rc=0
  local scope=""
  local service_name="adaos.service"
  local systemctl_args=()

  echo
  echo "Autostart diagnostics:"
  set +e
  as_json="$(python -m adaos autostart status --json 2>/dev/null)"
  as_rc=$?
  set -e
  if [[ $as_rc -eq 0 && -n "${as_json:-}" ]]; then
    echo "$as_json"
    scope="$(
      python -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); print((d.get("scope") or "").strip())' <<<"$as_json" 2>/dev/null || true
    )"
  else
    echo "  autostart status json: unavailable"
  fi

  if have systemctl; then
    if [[ "${scope:-}" == "user" ]]; then
      systemctl_args=(--user)
    else
      systemctl_args=()
    fi
    echo
    echo "systemctl ${systemctl_args[*]} status ${service_name}:"
    set +e
    systemctl "${systemctl_args[@]}" --no-pager -l status "${service_name}" 2>&1 || true
    set -e
    if have journalctl; then
      echo
      echo "journalctl ${systemctl_args[*]} -u ${service_name} (last 40 lines):"
      set +e
      journalctl "${systemctl_args[@]}" -u "${service_name}" -n 40 --no-pager 2>&1 || true
      set -e
    fi
  elif have schtasks; then
    echo
    echo "schtasks /Query /TN AdaOS /V /FO LIST:"
    set +e
    schtasks /Query /TN "AdaOS" /V /FO LIST 2>&1 || true
    set -e
  elif have launchctl; then
    echo
    echo "launchctl print gui/$(id -u 2>/dev/null || echo '?')/com.adaos.autostart:"
    set +e
    launchctl print "gui/$(id -u 2>/dev/null || echo 0)/com.adaos.autostart" 2>&1 || true
    set -e
  fi
  echo
}

print_next_steps() {
  local serve_host="$1"
  local serve_port="$2"
  local role="$3"
  local deep_link="$4"
  local connected_to_hub="$5"
  local tg_pair_code="${6:-}"
  local owner_url="${7:-}"
  local owner_code="${8:-}"

  echo
  ok "Bootstrap completed."
  echo
  echo "Next steps:"
  if [[ -n "${deep_link:-}" ]]; then
    echo "  1) Telegram: open and confirm pairing:"
    echo "     ${deep_link}"
    if [[ -n "${tg_pair_code:-}" ]]; then
      echo "     pair_code: ${tg_pair_code}"
    fi
    show_qr_if_available "${deep_link}"
  else
    echo "  1) Telegram pairing:"
    echo "     python -m adaos dev telegram"
  fi
  echo "  2) Owner browser:"
  if [[ -n "${owner_url:-}" && -n "${owner_code:-}" ]]; then
    echo "     Open: ${owner_url}"
    echo "     user_code: ${owner_code}"
    show_qr_if_available "${owner_url}"
  else
    echo "     python -m adaos dev root login"
    echo "     Then open https://inimatic.com/?mode=registration and enter the code."
  fi
  echo "  3) Start/stop/restart AdaOS API:"
  echo "     Start (foreground): python -m adaos api serve --host ${serve_host} --port ${serve_port}"
  echo "     Stop:              python -m adaos api stop"
  echo "     Restart:           python -m adaos api restart"
  echo "  4) Web UI:"
  echo "     Open https://inimatic.web.app/ or https://inimatic.com/ and connect to your local node (ports 8777/8778)."
  if [[ "${role:-}" == "member" ]]; then
    echo "  5) Member → hub connectivity:"
    echo "     connected_to_hub=${connected_to_hub:-unknown}"
    echo "     Details: python -m adaos node status"
  fi
  echo
  echo "Docs:"
  echo "  https://inimatic.github.io/adaos/"
  if ! have qrencode; then
    echo
    echo "Tip: install 'qrencode' to show QR codes in terminal."
  fi
}

configure_rasa_nlu() {
  if [[ "${NO_VOICE:-0}" == "1" ]]; then
    export ADAOS_NLU_RASA=0
    write_env_var "ADAOS_NLU_RASA" "0" ".env"
    log "Rasa NLU service disabled by --no_voice"
    return 0
  fi
  log "Rasa NLU will be prepared as an optional AdaOS service-skill"
}

py_is_supported_python() {
  local bin="$1"
  local min_ver="$2"
  local max_ver="$3"
  "$bin" - "$min_ver" "$max_ver" <<'PY' >/dev/null 2>&1
import sys
min_ver = tuple(int(x) for x in sys.argv[1].split("."))
max_ver = tuple(int(x) for x in sys.argv[2].split("."))
cur = sys.version_info[:3]
raise SystemExit(0 if min_ver <= cur < max_ver else 1)
PY
}

choose_python_311() {
  local cands=()
  if [[ -n "${ADAOS_PYTHON:-}" ]]; then
    cands+=("$ADAOS_PYTHON")
  fi
  cands+=(python3.11 python3 python)

  for c in "${cands[@]}"; do
    have "$c" || continue
    local p resolved_p
    p="$(command -v "$c")"
    resolved_p="$(normalize_python_candidate "$p")"
    if py_is_supported_python "$resolved_p" "$MIN_PYTHON" "$MAX_PYTHON_EXCLUSIVE"; then
      PY_BIN="$resolved_p"
      PY_VER="$("$resolved_p" -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")' 2>/dev/null || echo "3.11")"
      if [[ "$p" != "$resolved_p" ]]; then
        log "Resolved Python shim ${p} -> ${resolved_p}"
      fi
      log "Using Python ${PY_VER} -> ${PY_BIN}"
      return 0
    fi
  done

  return 1
}

smart_npm_install() {
  if have pnpm; then
    pnpm install
    USED_PKG_CMD="pnpm install"
    return
  fi
  if [[ -f package-lock.json ]]; then
    if npm ci; then
      USED_PKG_CMD="npm ci"
    else
      warn "npm ci failed; falling back to npm install..."
      npm install
      USED_PKG_CMD="npm install"
    fi
  else
    npm install
    USED_PKG_CMD="npm install"
  fi
}

open_subshell_help() {
  [[ "${BOOTSTRAP_OPEN_SUBSHELL:-0}" != "1" ]] && return 0
  local help_text
  read -r -d '' help_text <<'EOF' || true
READY.

Next steps:
  1) API:
     python -m adaos api serve --host 127.0.0.1 --port 8777 --reload
  2) Backend (optional):
     cd src/adaos/integrations/adaos-backend
     npm run start:api-dev
  3) Frontend (optional):
     cd src/adaos/integrations/adaos-client
     npm i
     npm run start
EOF

  if [[ -n "${SHELL:-}" && -x "$SHELL" ]]; then
    "$SHELL" --rcfile <(printf 'source %s\nprintf "%s\n"\n' "${VENV_ACTIVATE:-.venv/bin/activate}" "$help_text") -i
  else
    bash --rcfile <(printf 'source %s\nprintf "%s\n"\n' "${VENV_ACTIVATE:-.venv/bin/activate}" "$help_text") -i
  fi
}

cd "$(dirname "$0")/.." || fail "cannot cd to repo root"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --join-code) JOIN_CODE="${2:-}"; shift 2 ;;
    --role) ROLE="${2:-}"; shift 2 ;;
    --install-service) INSTALL_SERVICE="${2:-}"; shift 2 ;;
    --serve-host) SERVE_HOST="${2:-}"; shift 2 ;;
    --serve-port) SERVE_PORT="${2:-}"; shift 2 ;;
    --control-port) CONTROL_PORT="${2:-}"; shift 2 ;;
    --root-url) ROOT_URL="${2:-}"; shift 2 ;;
    --rev) REV="${2:-}"; shift 2 ;;
    --zone|--zone-id) ZONE_ID="${2:-}"; shift 2 ;;
    --language|--lang) LANGUAGE="${2:-}"; shift 2 ;;
    --python) PYTHON_ARG="${2:-}"; shift 2 ;;
    --node-name) NODE_NAME="${2:-}"; shift 2 ;;
    --workspace-registry-repo) WORKSPACE_REGISTRY_REPO="${2:-}"; shift 2 ;;
    --runtime-engine) SUPERVISOR_IMPLEMENTATION="${2:-}"; shift 2 ;;
    --supervisor-implementation) SUPERVISOR_IMPLEMENTATION="${2:-}"; shift 2 ;;
    --sidecar-implementation) SIDECAR_IMPLEMENTATION="${2:-}"; shift 2 ;;
    --core-update-implementation) UPDATE_IMPLEMENTATION="${2:-}"; shift 2 ;;
    --adaosd-binary) ADAOSD_BINARY="${2:-}"; shift 2 ;;
    --adaosd-service-config) ADAOSD_SERVICE_CONFIG="${2:-}"; shift 2 ;;
    --adaosd-approval-scope) ADAOSD_APPROVAL_SCOPE="${2:-}"; shift 2 ;;
    --adaosd-approval-id) ADAOSD_APPROVAL_ID="${2:-}"; shift 2 ;;
    --adaosd-approval-stand) ADAOSD_APPROVAL_STAND="${2:-}"; shift 2 ;;
    --no-core-update|--no_core_update) NO_CORE_UPDATE="1"; shift ;;
    --no_voice|--no-voice) NO_VOICE="1"; shift ;;
    --dev) DEV_MODE="1"; shift ;;
    --build-vendored-y-py) BUILD_VENDORED_Y_PY="1"; shift ;;
    -h|--help)
      cat <<EOF
Usage: tools/bootstrap.sh [options]
  --join-code CODE
  --role hub|member
  --install-service auto|always|never
  --serve-host HOST
  --serve-port PORT
  --control-port PORT
  --root-url URL
  --rev REV
  --zone ZONE_ID
  --language en|ru    Initial owner UI language (default: en)
  --python /path/to/python3.11
  --node-name NAME
  --workspace-registry-repo URL
  --supervisor-implementation python|go
  --sidecar-implementation python|go
  --core-update-implementation python|go
  --runtime-engine python|go  Deprecated alias for --supervisor-implementation
  --adaosd-binary /absolute/path/to/adaosd
  --adaosd-service-config /absolute/path/to/service.json  Optional custom Go service contract
  --adaosd-approval-scope m4c_canary|production
  --adaosd-approval-id ID
  --adaosd-approval-stand STAND
  --no-core-update      Disable hub/member core updates from CI/CD signals for this node
  --dev
  --build-vendored-y-py Build repository vendor/y-py (requires Rust/Cargo >=1.72)
  --no_voice            Disable optional Rasa NLU service/training
EOF
      exit 0
      ;;
    *) fail "Unknown arg: $1 (try --help)" ;;
  esac
done

if [[ -n "${PYTHON_ARG:-}" ]]; then
  ADAOS_PYTHON="$PYTHON_ARG"
  export ADAOS_PYTHON
fi

if [[ -z "${SUPERVISOR_IMPLEMENTATION:-}" ]]; then
  SUPERVISOR_IMPLEMENTATION="$(read_env_value_from_file ".env" "ADAOS_SUPERVISOR_IMPLEMENTATION" || true)"
fi
if [[ -z "${SUPERVISOR_IMPLEMENTATION:-}" ]]; then
  SUPERVISOR_IMPLEMENTATION="$(read_env_value_from_file ".env" "ADAOS_RUNTIME_ENGINE" || true)"
fi
if [[ -z "${SIDECAR_IMPLEMENTATION:-}" ]]; then
  SIDECAR_IMPLEMENTATION="$(read_env_value_from_file ".env" "ADAOS_SIDECAR_IMPLEMENTATION" || true)"
fi
if [[ -z "${SIDECAR_IMPLEMENTATION:-}" ]]; then
  SIDECAR_IMPLEMENTATION="$(read_env_value_from_file ".env" "ADAOS_REALTIME_IMPLEMENTATION" || true)"
fi
if [[ -z "${UPDATE_IMPLEMENTATION:-}" ]]; then
  UPDATE_IMPLEMENTATION="$(read_env_value_from_file ".env" "ADAOS_CORE_UPDATE_IMPLEMENTATION" || true)"
fi
if [[ -z "${ADAOSD_BINARY:-}" ]]; then
  ADAOSD_BINARY="$(read_env_value_from_file ".env" "ADAOSD_BINARY" || true)"
fi
if [[ -z "${ADAOSD_BINARY:-}" ]]; then
  ADAOSD_BINARY="$(read_env_value_from_file ".env" "ADAOS_REALTIME_GO_BINARY" || true)"
fi
SUPERVISOR_IMPLEMENTATION="$(printf '%s' "${SUPERVISOR_IMPLEMENTATION:-python}" | tr '[:upper:]' '[:lower:]')"
SIDECAR_IMPLEMENTATION="$(printf '%s' "${SIDECAR_IMPLEMENTATION:-python}" | tr '[:upper:]' '[:lower:]')"
UPDATE_IMPLEMENTATION="$(printf '%s' "${UPDATE_IMPLEMENTATION:-python}" | tr '[:upper:]' '[:lower:]')"
case "$UPDATE_IMPLEMENTATION" in
  python|go) ;;
  *) fail "core update implementation must be python or go" ;;
esac
if [[ "$UPDATE_IMPLEMENTATION" == "go" && "$SUPERVISOR_IMPLEMENTATION" != "go" ]]; then
  fail "Go core update orchestration requires --supervisor-implementation go"
fi
case "$ADAOSD_APPROVAL_SCOPE" in
  m4c_canary|production) ;;
  *) fail "adaosd approval scope must be m4c_canary or production" ;;
esac
systemctl_available=0
if have systemctl; then
  systemctl_available=1
fi
if ! adapter_error="$(
  adaosd_service_adapter_validate \
    "$SUPERVISOR_IMPLEMENTATION" \
    "$SIDECAR_IMPLEMENTATION" \
    "$ADAOSD_BINARY" \
    "$ADAOSD_SERVICE_CONFIG" \
    "$INSTALL_SERVICE" \
    "$(uname -s)" \
    "${EUID:-$(id -u)}" \
    "$systemctl_available" 2>&1
)"; then
  fail "$adapter_error"
fi

if [[ -n "${ZONE_ID:-}" ]]; then
  ZONE_ID="$(printf '%s' "$ZONE_ID" | tr '[:upper:]' '[:lower:]')"
  if [[ ! "$ZONE_ID" =~ ^[a-z]{2}$ ]]; then
    fail "ZONE_ID must be a two-letter lowercase country/region code (example: ru)"
  fi
fi
LANGUAGE="$(printf '%s' "${LANGUAGE:-en}" | tr '[:upper:]' '[:lower:]')"
if [[ "$LANGUAGE" != "en" && "$LANGUAGE" != "ru" ]]; then
  die "language must be en or ru"
fi

if [[ -n "${JOIN_CODE:-}" ]]; then
  if [[ "${SERVE_PORT:-}" == "8777" ]]; then
    SERVE_PORT="8778"
  fi
  if [[ "${CONTROL_PORT:-}" == "8777" ]]; then
    CONTROL_PORT="$SERVE_PORT"
  fi
fi

if [[ -z "${ROLE:-}" ]]; then
  if [[ -n "${JOIN_CODE:-}" ]]; then
    ROLE="member"
  else
    ROLE="hub"
  fi
fi

ensure_required_submodules

log "Choosing Python >=${MIN_PYTHON},<${MAX_PYTHON_EXCLUSIVE}..."
if ! choose_python_311; then
  fallback_to_uv "Python >=${MIN_PYTHON},<${MAX_PYTHON_EXCLUSIVE} not found (or not on PATH)."
fi

log "Checking Python venv support..."
if ! "$PY_BIN" -c "import venv, ensurepip" >/dev/null 2>&1; then
  warn "System Python cannot create venv with pip (missing venv/ensurepip)."
  warn "If you are on Debian/Ubuntu, try: sudo apt-get install -y python3.11-venv"
  fallback_to_uv "System Python venv support is missing."
fi

log "Creating venv (.venv)..."
if [[ -d "${VENV_DIR}" ]]; then
  if ! venv_is_usable; then
    warn "Existing ${VENV_DIR} looks incomplete (missing activate script); removing..."
    rm -rf "${VENV_DIR}"
  else
    VENV_VER="$(. "$VENV_ACTIVATE" >/dev/null 2>&1 && python -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")' || true)"
    if [[ -n "${VENV_VER:-}" && "$VENV_VER" != "$PY_VER" ]]; then
      warn "Existing ${VENV_DIR} is $VENV_VER; recreating for $PY_VER..."
      rm -rf "${VENV_DIR}"
    fi
  fi
fi
if [[ ! -d "${VENV_DIR}" ]]; then
  set +e
  venv_out="$("$PY_BIN" -m venv "${VENV_DIR}" 2>&1)"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    printf '%s\n' "$venv_out" >&2
    fallback_to_uv "venv creation failed."
  fi
fi

log "Installing Python deps (editable)..."
if ! venv_is_usable; then
  warn "${VENV_DIR} was created but activate script is missing. Trying to recreate venv once..."
  rm -rf "${VENV_DIR}"
  set +e
  venv_out="$("$PY_BIN" -m venv "${VENV_DIR}" 2>&1)"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    printf '%s\n' "$venv_out" >&2
    warn "If you are on Debian/Ubuntu, try: sudo apt-get install -y python3.11-venv"
    fallback_to_uv "venv recreation failed."
  fi
  if ! venv_is_usable; then
    warn "If you are on Debian/Ubuntu, try: sudo apt-get install -y python3.11-venv"
    fallback_to_uv "Broken venv layout."
  fi
fi
. "$VENV_ACTIVATE"
python -m pip install -U pip >/dev/null
USER_INSTALL_SPEC="."
if [[ "${DEV_MODE:-0}" == "1" ]]; then
  USER_INSTALL_SPEC=".[dev]"
fi
if [[ "$BUILD_VENDORED_Y_PY" == "1" ]]; then
  command -v cargo >/dev/null 2>&1 || fail "Rust/Cargo >=1.72 is required with --build-vendored-y-py. Install Rust with rustup."
  export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$PWD/.adaos/build/y-py-target}"
  python -m pip install -e .[dev] || fail "pip install -e .[dev] failed"
  python -m pip install --force-reinstall --no-deps ./vendor/y-py || fail "vendored y-py install failed"
else
  Y_PY_WHEELHOUSE="$PWD/.adaos/bootstrap/wheels"
  python tools/install_patched_y_py.py --installer pip --wheelhouse "$Y_PY_WHEELHOUSE" \
    || fail "published patched y-py wheel installation failed"
  python -m pip install --find-links "$Y_PY_WHEELHOUSE" --only-binary :all: -e "$USER_INSTALL_SPEC" || fail "pip install -e $USER_INSTALL_SPEC failed"
fi
python -c 'import importlib.metadata as m; assert m.version("y-py") == "0.6.2+adaos.1", m.version("y-py")' \
  || fail "AdaOS requires patched y-py==0.6.2+adaos.1"

log "Bootstrapping .env..."
if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    cp .env.example .env
    ok ".env created from .env.example"
  elif [[ -f .env.prod.sample ]]; then
    cp .env.prod.sample .env
    ok ".env created from .env.prod.sample"
  else
    warn "No .env found and no .env.example/.env.prod.sample present"
  fi
fi
if [[ -n "${ZONE_ID:-}" ]]; then
  write_env_var "ADAOS_ZONE_ID" "$(printf '%s' "$ZONE_ID" | tr '[:upper:]' '[:lower:]')" ".env"
fi
write_env_var "ADAOS_LANG" "$LANGUAGE" ".env"
export ADAOS_LANG="$LANGUAGE"
write_env_var "ADAOS_SUPERVISOR_IMPLEMENTATION" "$SUPERVISOR_IMPLEMENTATION" ".env"
write_env_var "ADAOS_SIDECAR_IMPLEMENTATION" "$SIDECAR_IMPLEMENTATION" ".env"
write_env_var "ADAOS_REALTIME_IMPLEMENTATION" "$SIDECAR_IMPLEMENTATION" ".env"
write_env_var "ADAOS_CORE_UPDATE_IMPLEMENTATION" "$UPDATE_IMPLEMENTATION" ".env"
remove_env_var "ADAOS_RUNTIME_ENGINE" ".env"
export ADAOS_SUPERVISOR_IMPLEMENTATION="$SUPERVISOR_IMPLEMENTATION"
export ADAOS_SIDECAR_IMPLEMENTATION="$SIDECAR_IMPLEMENTATION"
export ADAOS_REALTIME_IMPLEMENTATION="$SIDECAR_IMPLEMENTATION"
export ADAOS_CORE_UPDATE_IMPLEMENTATION="$UPDATE_IMPLEMENTATION"
if [[ "$SUPERVISOR_IMPLEMENTATION" == "go" || "$SIDECAR_IMPLEMENTATION" == "go" ]]; then
  write_env_var "ADAOSD_BINARY" "$ADAOSD_BINARY" ".env"
  export ADAOSD_BINARY
else
  remove_env_var "ADAOSD_BINARY" ".env"
fi
if [[ "$SIDECAR_IMPLEMENTATION" == "go" ]]; then
  write_env_var "ADAOS_REALTIME_GO_BINARY" "$ADAOSD_BINARY" ".env"
  export ADAOS_REALTIME_GO_BINARY="$ADAOSD_BINARY"
else
  remove_env_var "ADAOS_REALTIME_GO_BINARY" ".env"
  unset ADAOS_REALTIME_GO_BINARY || true
fi
if [[ "${DEV_MODE:-0}" == "1" ]]; then
  write_env_var "ENV_TYPE" "dev" ".env"
  write_env_var "ADAOS_SUPERVISOR_ENABLED" "0" ".env"
fi
if [[ -n "${ADAOS_CORE_UPDATE_REPO_URL:-}" ]]; then
  write_env_var "ADAOS_CORE_UPDATE_REPO_URL" "${ADAOS_CORE_UPDATE_REPO_URL}" ".env"
fi
if [[ -n "${WORKSPACE_REGISTRY_REPO:-}" ]]; then
  write_env_var "ADAOS_WORKSPACE_REGISTRY_REPO" "${WORKSPACE_REGISTRY_REPO}" ".env"
  export ADAOS_WORKSPACE_REGISTRY_REPO="${WORKSPACE_REGISTRY_REPO}"
fi

if [[ -z "${ENV_TYPE:-}" ]]; then
  ENV_TYPE="$(read_env_type_from_file ".env" || true)"
fi
if [[ "${DEV_MODE:-0}" == "1" ]]; then
  ENV_TYPE="dev"
fi
export ENV_TYPE="${ENV_TYPE:-prod}"

ADAOS_BASE_DIR="$(resolve_adaos_base_dir)"
mkdir -p "$ADAOS_BASE_DIR"
export ADAOS_BASE_DIR

print_bootstrap_config

log "Detecting git availability (adaos git autodetect)..."
python -m adaos git autodetect >/dev/null 2>&1 || true

export ADAOS_REV="$REV"
EFFECTIVE_ROOT_URL="$(effective_root_url "$ROOT_URL" "${ZONE_ID:-}")"
export ADAOS_API_BASE="$EFFECTIVE_ROOT_URL"
if [[ -n "${ZONE_ID:-}" ]]; then
  export ADAOS_ZONE_ID="$(printf '%s' "$ZONE_ID" | tr '[:upper:]' '[:lower:]')"
fi

# Native Application installation resolves immutable packages through the
# authenticated Root repository.  Establish node identity and certificates
# before running `adaos install`.
if [[ -n "${JOIN_CODE:-}" ]]; then
  log "Joining subnet via join-code..."
  if ! python -m adaos node join --code "$JOIN_CODE" --root "$EFFECTIVE_ROOT_URL"; then
    die "adaos node join failed (check output above)"
  fi
fi

if [[ -n "${ROLE:-}" ]]; then
  log "Setting node role: $ROLE"
  if ! python -m adaos node role set --role "$ROLE"; then
    die "adaos node role set failed (check output above)"
  fi
fi

if [[ "${ROLE:-}" == "hub" ]]; then
  log "Initializing Root subnet (adaos dev root init)..."
  if ! python -m adaos dev root init; then
    die "adaos dev root init failed (check output above)"
  fi
fi

log "Installing default webspace content (adaos install)..."
install_args=(install --language "$LANGUAGE")
if [[ "${NO_VOICE:-0}" == "1" ]]; then
  install_args+=(--no-rasa-nlu --no-train-nlu)
fi
configure_rasa_nlu
if ! python -m adaos "${install_args[@]}"; then
  fail "Required default Applications installation failed (check output above)"
fi

if [[ "${NO_CORE_UPDATE:-0}" == "1" ]]; then
  if ! set_core_update_enabled "python" "false"; then
    warn "core update flag setup failed (check output above)"
  fi
fi

if [[ -n "${NODE_NAME:-}" ]]; then
  if ! set_node_name "python" "$NODE_NAME"; then
    warn "node name setup failed (check output above)"
  fi
fi

control_base="http://${SERVE_HOST}:${CONTROL_PORT}"
token="$(
  python - "${ADAOS_BASE_DIR}" <<'PY' 2>/dev/null || echo "dev-local-token"
import json
import pathlib
import sys

try:
    import yaml  # type: ignore
except Exception:
    yaml = None

base = pathlib.Path(sys.argv[1])
runtime_path = base / "state" / "node_runtime.json"
node_path = base / "node.yaml"
token = ""
try:
    payload = json.loads(runtime_path.read_text(encoding="utf-8")) if runtime_path.exists() else {}
    if isinstance(payload, dict):
        token = str(payload.get("token") or "").strip()
except Exception:
    token = ""
if not token and yaml is not None:
    try:
        raw = yaml.safe_load(node_path.read_text(encoding="utf-8")) if node_path.exists() else {}
        if isinstance(raw, dict):
            token = str(raw.get("token") or "").strip()
    except Exception:
        token = ""
print(token or "dev-local-token")
PY
)"
expected_node_id="$(
  python -c 'import sys,yaml,pathlib; p=pathlib.Path(sys.argv[1]); d=yaml.safe_load(p.read_text(encoding="utf-8")) or {}; print(d.get("node_id") or "")' \
    "${ADAOS_BASE_DIR}/node.yaml" 2>/dev/null || echo ""
)"
log "Runtime state targets: ${ADAOS_BASE_DIR}/node.yaml + ${ADAOS_BASE_DIR}/state/node_runtime.json"

if [[ "$SUPERVISOR_IMPLEMENTATION" == "go" ]]; then
  if [[ -z "${ADAOSD_SERVICE_CONFIG:-}" ]]; then
    ADAOSD_SERVICE_CONFIG="${ADAOS_BASE_DIR}/config/adaosd/service.json"
    log "Generating adaosd supervisor and service contracts ..."
    adaosd_config_args=(
      -m adaos.apps.adaosd_installation
      --base-dir "$ADAOS_BASE_DIR"
      --repo-root "$PWD"
      --python "$(command -v python)"
      --binary "$ADAOSD_BINARY"
      --service-config "$ADAOSD_SERVICE_CONFIG"
      --dotenv "$PWD/.env"
      --serve-host "$SERVE_HOST"
      --serve-port "$SERVE_PORT"
      --control-port "$CONTROL_PORT"
      --sidecar-implementation "$SIDECAR_IMPLEMENTATION"
      --update-implementation "$UPDATE_IMPLEMENTATION"
      --approval-scope "$ADAOSD_APPROVAL_SCOPE"
    )
    [[ -z "${ADAOSD_APPROVAL_ID:-}" ]] || adaosd_config_args+=(--approval-id "$ADAOSD_APPROVAL_ID")
    [[ -z "${ADAOSD_APPROVAL_STAND:-}" ]] || adaosd_config_args+=(--approval-stand "$ADAOSD_APPROVAL_STAND")
    python "${adaosd_config_args[@]}" || fail "adaosd service contract generation failed"
  fi
  if ! adapter_error="$(adaosd_service_adapter_require_service_config "$ADAOSD_SERVICE_CONFIG" 2>&1)"; then
    fail "$adapter_error"
  fi
fi

log "Starting AdaOS API (${SERVE_HOST}:${SERVE_PORT}) ..."
service_installed=0
go_service_managed=0
if [[ "$SUPERVISOR_IMPLEMENTATION" == "go" ]]; then
  set +e
  go_service_status="$(adaosd_service_adapter_status "$ADAOSD_BINARY" "$ADAOSD_SERVICE_CONFIG" 2>/dev/null)"
  go_service_status_rc=$?
  set -e
  if [[ $go_service_status_rc -ne 0 ]]; then
    fail "Cannot inspect existing adaosd service state"
  fi
  if ! go_service_state="$(adaosd_service_adapter_classify_status python <<<"$go_service_status" 2>/dev/null)"; then
    fail "adaosd returned an invalid or unsupported service status"
  fi
  case "$go_service_state" in
    managed) go_service_managed=1 ;;
    unmanaged) ;;
    drift) fail "Managed adaosd service drift must be resolved before bootstrap" ;;
    *) fail "adaosd returned an unknown service status classification" ;;
  esac
fi

if [[ "$go_service_managed" == "1" ]]; then
  log "Refreshing existing Go-managed AdaOS service ..."
  set +e
  adaosd_install_output="$(adaosd_service_adapter_install "$ADAOSD_BINARY" "$ADAOSD_SERVICE_CONFIG" 2>&1)"
  adaosd_install_rc=$?
  set -e
  if [[ $adaosd_install_rc -ne 0 ]]; then
    printf '%s\n' "$adaosd_install_output"
    fail "Go-managed service refresh failed; preserved fallback state was not replaced"
  fi
  service_installed=1
  ok "Go-managed AdaOS service refreshed"
elif [[ "$INSTALL_SERVICE" != "never" ]]; then
  set +e
  autostart_enable_output="$(python -m adaos autostart enable --host "$SERVE_HOST" --port "$SERVE_PORT" 2>&1)"
  autostart_enable_rc=$?
  set -e
  if [[ $autostart_enable_rc -eq 0 ]]; then
    service_installed=1
    ok "Autostart installed (adaos autostart enable)"
    # Best-effort start:
    # - Windows (Git-Bash/MSYS): scheduled task is installed but not started automatically.
    # - Linux without systemctl (containers/WSL without systemd): enable writes unit but cannot start it.
    if have schtasks; then
      schtasks /Run /TN "AdaOS" >/dev/null 2>&1 || true
    fi
    if ! wait_for_autostart_activation 45; then
      warn "Autostart did not become active within startup grace period"
      print_autostart_diagnostics
      if [[ "$SUPERVISOR_IMPLEMENTATION" == "go" ]]; then
        fail "Python fallback service must be healthy before Go service handoff"
      fi
      warn "Falling back to background run"
      service_installed=0
    fi
  else
    warn "autostart enable failed"
    if [[ -n "${autostart_enable_output:-}" ]]; then
      printf '%s\n' "$autostart_enable_output"
    fi
    print_autostart_diagnostics
    if [[ "$SUPERVISOR_IMPLEMENTATION" == "go" ]]; then
      fail "Python fallback service installation is required before Go service handoff"
    fi
    warn "Falling back to background run"
  fi
  if [[ "$service_installed" == "1" && "$SUPERVISOR_IMPLEMENTATION" == "go" ]]; then
    log "Handing systemd ownership to adaosd ..."
    set +e
    adaosd_install_output="$(adaosd_service_adapter_install "$ADAOSD_BINARY" "$ADAOSD_SERVICE_CONFIG" 2>&1)"
    adaosd_install_rc=$?
    set -e
    if [[ $adaosd_install_rc -ne 0 ]]; then
      printf '%s\n' "$adaosd_install_output"
      print_autostart_diagnostics
      fail "Go service handoff failed; the installer restored the Python fallback"
    fi
    ok "Go-managed AdaOS service installed"
  fi
fi

if [[ "$SUPERVISOR_IMPLEMENTATION" == "go" && "$service_installed" != "1" ]]; then
  fail "Go supervisor requires an active managed service"
fi
if [[ "$service_installed" != "1" || "$INSTALL_SERVICE" == "never" ]]; then
  nohup python -m adaos api serve --host "$SERVE_HOST" --port "$SERVE_PORT" >/dev/null 2>&1 & disown || true
fi

log "Waiting for ready=true ..."
deadline=$(( $(date +%s) + 120 ))
ready_json=""
connected_to_hub=""
while [[ $(date +%s) -lt $deadline ]]; do
  if ready_json="$(http_get "${control_base}/api/node/status" "X-AdaOS-Token: ${token}" 2>/dev/null)"; then
    if python -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); exp=sys.argv[1]; ok=bool(d.get("ready")); nid=str(d.get("node_id") or ""); raise SystemExit(0 if (ok and (not exp or nid==exp)) else 1)' "$expected_node_id" <<<"$ready_json" >/dev/null 2>&1; then
      ok "READY: ${ready_json}"
      connected_to_hub="$(python -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); v=d.get("connected_to_hub"); print("" if v is None else str(bool(v)).lower())' <<<"$ready_json" 2>/dev/null || true)"
      break
    fi
  fi
  sleep 2
done

deep_link=""
tg_pair_code=""
log "Generating Telegram pairing link..."
set +e
tg_out="$(python -m adaos dev telegram 2>&1)"
tg_rc=$?
set -e
if [[ $tg_rc -eq 0 ]]; then
  tg_pair_code="$(printf '%s\n' "$tg_out" | sed -n 's/^[[:space:]]*pair_code:[[:space:]]*//p' | head -n 1 | tr -d '\r' || true)"
  deep_link="$(printf '%s\n' "$tg_out" | sed -n 's/^[[:space:]]*deep_link:[[:space:]]*//p' | head -n 1 | tr -d '\r' || true)"
fi
if [[ -z "${deep_link:-}" ]]; then
  warn "Telegram pairing link not generated automatically. Run: python -m adaos dev telegram"
fi

owner_url=""
owner_code=""
log "Generating Owner browser pairing code..."
set +e
owner_json="$(python -m adaos dev root login --print-only --json 2>/dev/null)"
owner_rc=$?
set -e
if [[ $owner_rc -eq 0 && -n "${owner_json:-}" ]]; then
  owner_url="$(python -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); print((d.get("verification_uri_complete") or d.get("verification_uri") or "").strip())' <<<"$owner_json" 2>/dev/null || true)"
  owner_code="$(python -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); print((d.get("user_code") or "").strip())' <<<"$owner_json" 2>/dev/null || true)"
fi

print_next_steps "$SERVE_HOST" "$SERVE_PORT" "$ROLE" "$deep_link" "$connected_to_hub" "$tg_pair_code" "$owner_url" "$owner_code"
show_optional_modules_note
printf "\nTo activate venv:\n  source %s\n\n" "${VENV_ACTIVATE:-.venv/bin/activate}"
open_subshell_help
