#!/usr/bin/env bash

# This file is sourced by bootstrap_uv.sh. Keep it side-effect free so the
# enterprise service boundary can be tested without running the full bootstrap.

adaosd_service_adapter_validate() {
  local supervisor="${1:-}"
  local sidecar="${2:-}"
  local binary="${3:-}"
  local config="${4:-}"
  local install_service="${5:-}"
  local host_os="${6:-}"
  local effective_uid="${7:-}"
  local systemctl_available="${8:-0}"

  case "$supervisor" in
    python|go) ;;
    *) printf 'supervisor implementation must be python or go\n' >&2; return 2 ;;
  esac
  case "$sidecar" in
    python|go) ;;
    *) printf 'sidecar implementation must be python or go\n' >&2; return 2 ;;
  esac
  if [[ "$supervisor" == "go" || "$sidecar" == "go" ]]; then
    if [[ "$binary" != /* || ! -f "$binary" || ! -x "$binary" ]]; then
      printf 'ADAOSD_BINARY must be an absolute executable file\n' >&2
      return 2
    fi
  fi
  if [[ -n "$config" && ( "$config" != /* || ! -f "$config" ) ]]; then
    printf 'ADAOSD_SERVICE_CONFIG must be an absolute regular file when supplied\n' >&2
    return 2
  fi
  [[ "$supervisor" == "go" ]] || return 0
  if [[ "$host_os" != "Linux" ]]; then
    printf 'Go supervisor bootstrap is qualified only on Linux\n' >&2
    return 2
  fi
  if [[ "$effective_uid" != "0" ]]; then
    printf 'Go supervisor bootstrap requires root systemd scope\n' >&2
    return 2
  fi
  if [[ "$systemctl_available" != "1" ]]; then
    printf 'Go supervisor bootstrap requires systemctl\n' >&2
    return 2
  fi
  if [[ "$install_service" == "never" ]]; then
    printf 'Go supervisor cannot be combined with --install-service never\n' >&2
    return 2
  fi
}

adaosd_service_adapter_require_service_config() {
  local config="${1:-}"
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
