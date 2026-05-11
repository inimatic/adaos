#!/usr/bin/env bash
# tools/bootstrap.sh — bootstrap via venv + pip (Linux/macOS)
set -euo pipefail

CLIENT_SUBMODULE_PATH="src/adaos/integrations/adaos-client"
BACKEND_SUBMODULE_PATH="src/adaos/integrations/adaos-backend"
INFRA_SUBMODULE_PATH="src/adaos/integrations/infra-inimatic"

VENV_DIR=".venv"
VENV_ACTIVATE=".venv/bin/activate"
MIN_PYTHON="3.11.9"

JOIN_CODE=""
ROLE=""
INSTALL_SERVICE="auto" # auto|always|never
SERVE_HOST="127.0.0.1"
SERVE_PORT="8777"
CONTROL_PORT="8777"
ROOT_URL="https://api.inimatic.com"
REV="rev2026"
ZONE_ID=""
NO_VOICE="0"
DEV_MODE="0"
PYTHON_ARG=""
NODE_NAME=""
NO_CORE_UPDATE="0"
WORKSPACE_REGISTRY_REPO="${ADAOS_WORKSPACE_REGISTRY_REPO:-}"

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

read_env_type_from_file() {
  local path="$1"
  [[ -f "$path" ]] || return 0
  sed -n 's/^[[:space:]]*ENV_TYPE[[:space:]]*=[[:space:]]*//p' "$path" \
    | head -n 1 \
    | tr -d '\r' \
    | tr -d '"' \
    | tr -d "'" \
    | xargs \
    || true
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
    echo "     Then open https://app.inimatic.com/?mode=registration and enter the code."
  fi
  echo "  3) Start/stop/restart AdaOS API:"
  echo "     Start (foreground): python -m adaos api serve --host ${serve_host} --port ${serve_port}"
  echo "     Stop:              python -m adaos api stop"
  echo "     Restart:           python -m adaos api restart"
  echo "  4) Web UI:"
  echo "     Open https://myinimatic.web.app/ and connect to your local node (ports 8777/8778)."
  if [[ "${role:-}" == "member" ]]; then
    echo "  5) Member → hub connectivity:"
    echo "     connected_to_hub=${connected_to_hub:-unknown}"
    echo "     Details: python -m adaos node status"
  fi
  echo
  echo "Docs:"
  echo "  https://stipot-com.github.io/adaos/"
  if ! have qrencode; then
    echo
    echo "Tip: install 'qrencode' to show QR codes in terminal."
  fi
}

configure_rasa_nlu() {
  if [[ "${NO_VOICE:-0}" == "1" ]]; then
    export ADAOS_NLU_RASA=0
    log "Rasa NLU service disabled by --no_voice"
    return 0
  fi
  log "Rasa NLU will be prepared as an optional AdaOS service-skill"
}

py_is_311() {
  local bin="$1"
  "$bin" -c 'import sys; raise SystemExit(0 if (sys.version_info[0], sys.version_info[1]) == (3, 11) else 1)' \
    >/dev/null 2>&1
}

py_meets_min() {
  local bin="$1"
  local min_ver="$2"
  "$bin" - "$min_ver" <<'PY' >/dev/null 2>&1
import sys
min_ver = tuple(int(x) for x in sys.argv[1].split("."))
cur = sys.version_info[:3]
raise SystemExit(0 if cur >= min_ver else 1)
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
    if py_is_311 "$resolved_p" && py_meets_min "$resolved_p" "$MIN_PYTHON"; then
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
    --python) PYTHON_ARG="${2:-}"; shift 2 ;;
    --node-name) NODE_NAME="${2:-}"; shift 2 ;;
    --workspace-registry-repo) WORKSPACE_REGISTRY_REPO="${2:-}"; shift 2 ;;
    --no-core-update|--no_core_update) NO_CORE_UPDATE="1"; shift ;;
    --no_voice|--no-voice) NO_VOICE="1"; shift ;;
    --dev) DEV_MODE="1"; shift ;;
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
  --python /path/to/python3.11
  --node-name NAME
  --workspace-registry-repo URL
  --no-core-update      Disable hub/member core updates from CI/CD signals for this node
  --dev
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

if [[ -n "${ZONE_ID:-}" ]]; then
  ZONE_ID="$(printf '%s' "$ZONE_ID" | tr '[:upper:]' '[:lower:]')"
  if [[ ! "$ZONE_ID" =~ ^[a-z]{2}$ ]]; then
    fail "ZONE_ID must be a two-letter lowercase country/region code (example: ru)"
  fi
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

log "Choosing Python 3.11.9+..."
if ! choose_python_311; then
  fallback_to_uv "Python 3.11.9+ not found (or not on PATH)."
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
python -m pip install -e .[dev] || fail "pip install -e .[dev] failed"

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

log "Installing default webspace content (adaos install)..."
install_args=(install)
if [[ "${NO_VOICE:-0}" == "1" ]]; then
  install_args+=(--no-rasa-nlu --no-train-nlu)
fi
configure_rasa_nlu
if ! python -m adaos "${install_args[@]}"; then
  warn "adaos install failed (check output above)"
fi

export ADAOS_REV="$REV"
EFFECTIVE_ROOT_URL="$(effective_root_url "$ROOT_URL" "${ZONE_ID:-}")"
export ADAOS_API_BASE="$EFFECTIVE_ROOT_URL"
if [[ -n "${ZONE_ID:-}" ]]; then
  export ADAOS_ZONE_ID="$(printf '%s' "$ZONE_ID" | tr '[:upper:]' '[:lower:]')"
fi

if [[ -n "${JOIN_CODE:-}" ]]; then
  log "Joining subnet via join-code..."
  if ! python -m adaos node join --code "$JOIN_CODE" --root "$EFFECTIVE_ROOT_URL"; then
    die "adaos node join failed (check output above)"
  fi
fi

if [[ -n "${ROLE:-}" ]]; then
  log "Setting node role: $ROLE"
  if ! python -m adaos node role set --role "$ROLE"; then
    warn "adaos node role set failed (check output above)"
  fi
fi

if [[ "${ROLE:-}" == "hub" ]]; then
  log "Initializing Root subnet (adaos dev root init)..."
  if ! python -m adaos dev root init; then
    warn "adaos dev root init failed (check output above)"
  fi
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

log "Starting AdaOS API (${SERVE_HOST}:${SERVE_PORT}) ..."
service_installed=0
if [[ "$INSTALL_SERVICE" != "never" ]]; then
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
      warn "Autostart did not become active within startup grace period; falling back to background run"
      print_autostart_diagnostics
      service_installed=0
    fi
  else
    warn "autostart enable failed; will fallback to background run"
    if [[ -n "${autostart_enable_output:-}" ]]; then
      printf '%s\n' "$autostart_enable_output"
    fi
    print_autostart_diagnostics
  fi
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
