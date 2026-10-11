#!/usr/bin/env bash

# This file is sourced by bootstrap_uv.sh. Keep it side-effect free so the
# enterprise service boundary can be tested without running the full bootstrap.

adaosd_service_adapter_validate() {
  local engine="${1:-}"
  local binary="${2:-}"
  local config="${3:-}"
  local install_service="${4:-}"
  local host_os="${5:-}"
  local effective_uid="${6:-}"
  local systemctl_available="${7:-0}"

  case "$engine" in
    python) return 0 ;;
    go) ;;
    *) printf 'runtime engine must be python or go\n' >&2; return 2 ;;
  esac
  if [[ "$host_os" != "Linux" ]]; then
    printf 'Go service bootstrap is qualified only on Linux\n' >&2
    return 2
  fi
  if [[ "$effective_uid" != "0" ]]; then
    printf 'Go service bootstrap requires root systemd scope\n' >&2
    return 2
  fi
  if [[ "$systemctl_available" != "1" ]]; then
    printf 'Go service bootstrap requires systemctl\n' >&2
    return 2
  fi
  if [[ "$install_service" == "never" ]]; then
    printf 'Go runtime engine cannot be combined with --install-service never\n' >&2
    return 2
  fi
  if [[ "$binary" != /* || ! -f "$binary" || ! -x "$binary" ]]; then
    printf 'ADAOSD_BINARY must be an absolute executable file\n' >&2
    return 2
  fi
  if [[ "$config" != /* || ! -f "$config" ]]; then
    printf 'ADAOSD_SERVICE_CONFIG must be an absolute regular file\n' >&2
    return 2
  fi
}

adaosd_service_adapter_install() {
  local binary="$1"
  local config="$2"
  "$binary" service install --config "$config" --replace --json
}

adaosd_service_adapter_status() {
  local binary="$1"
  local config="$2"
  "$binary" service status --config "$config" --json
}

adaosd_service_adapter_classify_status() {
  local python="$1"
  "$python" -c '
import json
import sys

status = json.load(sys.stdin)
if status.get("schema") != "adaosd.service_status.v1":
    raise SystemExit("unsupported adaosd service status schema")
managed = status.get("managed") is True
matches = status.get("unit_matches") is True
print("managed" if managed and matches else "drift" if managed else "unmanaged")
'
}
