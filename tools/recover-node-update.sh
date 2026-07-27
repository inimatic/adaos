#!/usr/bin/env bash
set -euo pipefail

# One-shot break-glass entry point for a node whose root checkout/CLI is broken.
# The normal A/B updater still performs the state change; this script only finds
# a verified control runtime and dispatches one pinned request to it.

usage() {
  cat <<'EOF'
Usage: recover-node-update.sh --target-rev <branch> --target-version <40-hex-sha> [--dry-run|--observe]

Environment overrides:
  ADAOS_BASE_DIR              State root (default: /root/.adaos)
  ADAOS_ROOT_REPO_ROOT        Root checkout (default: /root/adaos)
  ADAOS_SHARED_DOTENV_PATH    Shared dotenv (default: <root>/.env)
  ADAOS_RECOVERY_REPO_URL     Expected git remote (default: active manifest repo_url)
  ADAOS_RECOVERY_CONTROL_REPO Explicit verified control checkout (advanced/testing)
  ADAOS_RECOVERY_CONTROL_PYTHON Explicit verified control Python (advanced/testing)
EOF
}

target_rev=""
target_version=""
mode="dispatch"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --target-rev)
      target_rev="${2:-}"
      shift 2
      ;;
    --target-version)
      target_version="${2:-}"
      shift 2
      ;;
    --dry-run)
      mode="dry-run"
      shift
      ;;
    --observe)
      mode="observe"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "[fatal] unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! [[ "${target_rev}" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]]; then
  echo "[fatal] --target-rev must be a safe branch name" >&2
  exit 2
fi
if ! [[ "${target_version}" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo "[fatal] --target-version must be an exact 40-character commit SHA" >&2
  exit 2
fi
target_version="${target_version,,}"

base_dir="${ADAOS_BASE_DIR:-/root/.adaos}"
root_repo="${ADAOS_ROOT_REPO_ROOT:-/root/adaos}"
shared_dotenv="${ADAOS_SHARED_DOTENV_PATH:-${root_repo}/.env}"
active_slot="$(tr -d '[:space:]' < "${base_dir}/state/core_slots/active" 2>/dev/null || true)"
previous_slot="$(tr -d '[:space:]' < "${base_dir}/state/core_slots/previous" 2>/dev/null || true)"

if [ -f "${shared_dotenv}" ]; then
  set -a
  # shellcheck disable=SC1090
  . "${shared_dotenv}"
  set +a
fi

selected_python=""
selected_repo=""
selected_source=""
import_smoke='import adaos.apps.cli.app,adaos.apps.supervisor,adaos.apps.autostart_runner'

select_runtime() {
  local source="$1"
  local repo="$2"
  local python="$3"
  if [ ! -x "${python}" ] || [ ! -d "${repo}/src/adaos" ]; then
    return 1
  fi
  if ! env PYTHONPATH="${repo}/src" "${python}" -c "${import_smoke}" >/dev/null 2>&1; then
    return 1
  fi
  selected_python="${python}"
  selected_repo="${repo}"
  selected_source="${source}"
  return 0
}

if [ -n "${ADAOS_RECOVERY_CONTROL_REPO:-}" ] || [ -n "${ADAOS_RECOVERY_CONTROL_PYTHON:-}" ]; then
  if [ -z "${ADAOS_RECOVERY_CONTROL_REPO:-}" ] || [ -z "${ADAOS_RECOVERY_CONTROL_PYTHON:-}" ]; then
    echo "[fatal] both ADAOS_RECOVERY_CONTROL_REPO and ADAOS_RECOVERY_CONTROL_PYTHON are required" >&2
    exit 2
  fi
  select_runtime "explicit" "${ADAOS_RECOVERY_CONTROL_REPO}" "${ADAOS_RECOVERY_CONTROL_PYTHON}" || true
fi
if [ -z "${selected_python}" ]; then
  select_runtime "root" "${root_repo}" "${root_repo}/.venv/bin/python" || true
fi
if [ -z "${selected_python}" ]; then
  for slot in "${active_slot}" "${previous_slot}" A B; do
    if [ "${slot}" != "A" ] && [ "${slot}" != "B" ]; then
      continue
    fi
    slot_repo="${base_dir}/state/core_slots/slots/${slot}/repo"
    slot_python="${base_dir}/state/core_slots/slots/${slot}/venv/bin/python"
    if select_runtime "slot:${slot}" "${slot_repo}" "${slot_python}"; then
      break
    fi
  done
fi
if [ -z "${selected_python}" ]; then
  echo "[fatal] neither the root checkout nor an A/B slot passed the control import preflight" >&2
  exit 4
fi

status_path="${base_dir}/state/core_update/status.json"
manifest_path="${base_dir}/state/core_slots/slots/${active_slot}/manifest.json"
read_field() {
  local path="$1"
  local field="$2"
  "${selected_python}" - "${path}" "${field}" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
field = sys.argv[2]
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    payload = {}
value = payload.get(field) if isinstance(payload, dict) else None
print("" if value is None else str(value))
PY
}

update_state="$(read_field "${status_path}" state)"
update_phase="$(read_field "${status_path}" phase)"
active_commit="$(read_field "${manifest_path}" git_commit | tr '[:upper:]' '[:lower:]')"
active_build="$(read_field "${manifest_path}" build_version)"
repo_url="${ADAOS_RECOVERY_REPO_URL:-$(read_field "${manifest_path}" repo_url)}"
repo_url="${repo_url:-https://github.com/inimatic/adaos.git}"

echo "[recovery] control=${selected_source} python=${selected_python}"
echo "[recovery] active_slot=${active_slot:--} build=${active_build:--} commit=${active_commit:--}"
echo "[recovery] update_state=${update_state:-idle} phase=${update_phase:--}"
echo "[recovery] target=${target_rev}@${target_version}"

if [ "${active_commit}" = "${target_version}" ]; then
  echo "[ok] active slot already matches the requested commit"
  exit 0
fi

case "${update_state,,}" in
  preparing|planned|countdown|draining|stopping|restarting|applying|validated)
    echo "[blocked] an update transition is already active; observe it instead of dispatching another command" >&2
    exit 5
    ;;
esac

operation_dir="${base_dir}/state/node_recovery/${target_version}"
intent_path="${operation_dir}/intent.env"
if [ -f "${intent_path}" ]; then
  echo "[observe] a recovery intent for this exact target already exists; it will not be dispatched again"
  sed -n '1,80p' "${intent_path}"
  exit 0
fi
if [ "${mode}" = "observe" ]; then
  echo "[observe] no recovery intent exists for this target"
  exit 0
fi

remote_head="$(git ls-remote "${repo_url}" "refs/heads/${target_rev}" 2>/dev/null | awk 'NR == 1 {print tolower($1)}')"
if [ -z "${remote_head}" ]; then
  echo "[fatal] unable to resolve ${repo_url} refs/heads/${target_rev}" >&2
  exit 6
fi
if [ "${remote_head}" != "${target_version}" ]; then
  echo "[fatal] branch head ${remote_head} does not match pinned target ${target_version}" >&2
  exit 6
fi

if [ "${mode}" = "dry-run" ]; then
  echo "[ok] dry-run preflight passed; no files or runtime state were changed"
  exit 0
fi

mkdir -p "${operation_dir}"
intent_tmp="${intent_path}.$$"
{
  printf 'schema=adaos.node-recovery-intent.v1\n'
  printf 'state=dispatching\n'
  printf 'target_rev=%s\n' "${target_rev}"
  printf 'target_version=%s\n' "${target_version}"
  printf 'control_source=%s\n' "${selected_source}"
  printf 'control_python=%s\n' "${selected_python}"
  printf 'started_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "${intent_tmp}"
mv -f "${intent_tmp}" "${intent_path}"

output_path="${operation_dir}/dispatch.json"
set +e
(
  cd "${selected_repo}"
  env \
    ADAOS_BASE_DIR="${base_dir}" \
    ADAOS_ROOT_REPO_ROOT="${root_repo}" \
    ADAOS_SHARED_DOTENV_PATH="${shared_dotenv}" \
    ADAOS_ACTIVE_CORE_SLOT="${active_slot}" \
    ADAOS_SLOT_REPO_ROOT="${selected_repo}" \
    ADAOS_CLI_SLOT_BOUND=1 \
    ADAOS_DISABLE_ACTIVE_SLOT_PYTHON_REEXEC=1 \
    PYTHONPATH="${selected_repo}/src" \
    "${selected_python}" -m adaos autostart update-start \
      --target-rev "${target_rev}" \
      --target-version "${target_version}" \
      --countdown-sec 0 \
      --reason "operator.node_recovery:${target_version:0:12}" \
      --json
) | tee "${output_path}"
dispatch_rc=${PIPESTATUS[0]}
set -e

intent_tmp="${intent_path}.$$"
{
  sed '/^state=/d;/^finished_at=/d;/^dispatch_rc=/d' "${intent_path}"
  if [ "${dispatch_rc}" -eq 0 ]; then
    printf 'state=dispatched\n'
  else
    printf 'state=ambiguous\n'
  fi
  printf 'dispatch_rc=%s\n' "${dispatch_rc}"
  printf 'finished_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "${intent_tmp}"
mv -f "${intent_tmp}" "${intent_path}"

if [ "${dispatch_rc}" -ne 0 ]; then
  echo "[fatal] recovery dispatch failed or its acknowledgement was lost; inspect status, do not repeat automatically" >&2
  exit "${dispatch_rc}"
fi
echo "[ok] exactly one pinned transactional update was dispatched"
