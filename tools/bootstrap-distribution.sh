#!/usr/bin/env bash
# Standalone Linux entrypoint: Root metadata, regional bytes, managed Python.
set -euo pipefail
umask 077
export PYTHONNOUSERSITE=1
unset PYTHONHOME PYTHONPATH
DEST="${ADAOS_INIT_DEST:-$HOME/adaos}"
ROOT_URL="${ADAOS_BOOTSTRAP_ROOT_URL:-}"
ZONE="${ADAOS_ZONE_ID:-global}"
CHANNEL="${ADAOS_BOOTSTRAP_CHANNEL:-stable}"
RELEASE="${ADAOS_BOOTSTRAP_RELEASE:-current}"
SOURCE="${ADAOS_BOOTSTRAP_SOURCE_DIR:-}"
REV="${ADAOS_INIT_REV:-main}"
WITHOUT_UV=0
RUNTIME_ONLY=0
UV_BINARY=""
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dest) DEST="$2"; shift 2 ;;
    --distribution-root) ROOT_URL="$2"; shift 2 ;;
    --channel) CHANNEL="$2"; shift 2 ;;
    --release) RELEASE="$2"; shift 2 ;;
    --source-dir) SOURCE="$2"; shift 2 ;;
    --without-uv) WITHOUT_UV=1; shift ;;
    --uv-binary) UV_BINARY="$2"; shift 2 ;;
    --runtime-only) RUNTIME_ONLY=1; shift ;;
    --zone|--zone-id) ZONE="$2"; ARGS+=("$1" "$2"); shift 2 ;;
    --root-url) ROOT_URL="$2"; ARGS+=("$1" "$2"); shift 2 ;;
    --rev) REV="$2"; ARGS+=("$1" "$2"); shift 2 ;;
    --help|-h)
      printf '%s\n' 'Usage: bootstrap-distribution.sh [--dest DIR] [--zone ru|global] [--distribution-root URL] [--release ID] [--without-uv] [--runtime-only] [bootstrap arguments]'
      exit 0 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
ZONE="$(printf '%s' "$ZONE" | tr '[:upper:]' '[:lower:]')"
if [[ "$RELEASE" == current && "$REV" != main ]]; then RELEASE="$REV"; fi
[[ "${ADAOS_BOOTSTRAP_WITHOUT_UV:-0}" == 1 ]] && WITHOUT_UV=1
token() { [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$ ]]; }
for value in "$ZONE" "$CHANNEL" "$RELEASE"; do token "$value" || { echo 'Invalid distribution argument' >&2; exit 2; }; done
if [[ -z "$ROOT_URL" ]]; then
  if [[ "$ZONE" == ru ]]; then ROOT_URL="https://ru.api.inimatic.com"; else ROOT_URL="https://api.inimatic.com"; fi
fi
ROOT_URL="${ROOT_URL%/}"
if [[ "$ROOT_URL" != https://* || "$ROOT_URL" == *'?'* || "$ROOT_URL" == *'@'* || "$ROOT_URL" == *'#'* ]]; then
  [[ "${ADAOS_BOOTSTRAP_ALLOW_HTTP_TEST:-0}" == 1 && "$ROOT_URL" == http://* ]] || { echo 'Bootstrap Root requires HTTPS' >&2; exit 2; }
fi
have() { command -v "$1" >/dev/null 2>&1; }
have curl || have wget || { echo 'curl or wget is required' >&2; exit 2; }
have sha256sum || { echo 'sha256sum is required' >&2; exit 2; }
STATE="${ADAOS_BOOTSTRAP_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/adaos/bootstrap}"
export ADAOS_BOOTSTRAP_STATE_DIR="$STATE"
mkdir -p "$STATE"
[[ ! -L "$STATE" ]] || { echo 'Refusing symlink bootstrap state directory' >&2; exit 2; }
if [[ "$(find "$STATE" -maxdepth 1 -type d -name 'attempt-*' | wc -l)" -ge 128 ]]; then
  echo 'Bootstrap outbox reached 128 attempts; preserve pending records and archive acknowledged attempts before retrying.' >&2
  exit 1
fi
ID="$(cat /proc/sys/kernel/random/uuid)"
ATTEMPT="$STATE/attempt-$ID"
mkdir "$ATTEMPT"
printf '%s\n' "$ROOT_URL" > "$ATTEMPT/root-url"
STAGE=resolve
ROUTE=standard
sync_file() { sync -f "$1" 2>/dev/null || sync; }
post() {
  local endpoint="$1" file="$2" headers="$3" body="$4"
  if have curl; then
    curl -fsS --connect-timeout 3 --max-time 6 -D "$headers" -o "$body" -H 'Content-Type: application/json' --data-binary "@$file" "$ROOT_URL/v1/bootstrap/$endpoint"
  else
    wget -q --server-response --timeout=6 --tries=1 --header='Content-Type: application/json' --post-file="$file" -O "$body" "$ROOT_URL/v1/bootstrap/$endpoint" 2> "$headers"
  fi
}
event() {
  local kind="$1" reason="$2" status="${3:-0}" event_id
  event_id="$(cat /proc/sys/kernel/random/uuid)"
  printf '{"attempt_id":"%s","event_id":"%s","type":"%s","stage":"%s","reason":"%s","route":"%s","status":"%s"}\n' \
    "$ID" "$event_id" "$kind" "$STAGE" "$reason" "$ROUTE" "$status" > "$ATTEMPT/event-$event_id.json"
  sync_file "$ATTEMPT/event-$event_id.json"
}
flush() {
  [[ -f "$ATTEMPT/request.json.ack" || -f "$ATTEMPT/request.ack" ]] || return 0
  local file
  for file in "$ATTEMPT"/event-*.json; do
    [[ -f "$file" ]] || continue
    if post events "$file" "$ATTEMPT/event.headers" "$ATTEMPT/event.receipt" 2>/dev/null && grep -Eq '"durable"[[:space:]]*:[[:space:]]*true' "$ATTEMPT/event.receipt"; then
      printf '{"durable":true}\n' > "${file%.json}.ack"
      sync_file "${file%.json}.ack"
    else break; fi
  done
}
failed() {
  local code="$?"
  trap - ERR
  event installation_failed stage_execution_failed "$code"
  flush
  printf 'Bootstrap failed at %s. Diagnostic outbox: %s\n' "$STAGE" "$ATTEMPT" >&2
  exit "$code"
}
trap failed ERR
cleanup_payloads() {
  local result="$?"
  if [[ "$result" == 0 ]]; then
    # Only directories created inside this unique attempt; retain facts/receipts.
    rm -rf -- "$ATTEMPT/kit" "$ATTEMPT/runtime" "$ATTEMPT/source"
    rm -f -- "$ATTEMPT/bundle.tar.gz" "$ATTEMPT/python.tar.gz" "$ATTEMPT/uv.tar.gz" "$ATTEMPT/source.tar.gz"
  fi
}
trap cleanup_payloads EXIT
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"
DIST=unknown
DIST_VERSION=unknown
if [[ -f /etc/os-release ]]; then
  DIST="$(sed -n 's/^ID=//p' /etc/os-release | tr -d '\"')"
  DIST_VERSION="$(sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '\"')"
fi
token "$DIST" || DIST=unknown
token "$DIST_VERSION" || DIST_VERSION=unknown
LIBC=unknown
LIBC_VERSION=0.0
if getconf GNU_LIBC_VERSION >/dev/null 2>&1; then
  LIBC=glibc
  LIBC_VERSION="$(getconf GNU_LIBC_VERSION | awk '{print $2}')"
elif ldd --version 2>&1 | grep -q musl; then LIBC=musl; fi
POLICY=auto
[[ "$WITHOUT_UV" == 1 ]] && POLICY=disabled
printf '{"attempt_id":"%s","installer_version":"1","zone_id":"%s","channel":"%s","requested_release":"%s","uv_policy":"%s","profile":{"os":"%s","distribution":"%s","distribution_version":"%s","arch":"%s","libc":"%s","libc_version":"%s","python":"3.11","features":["core"]}}\n' \
  "$ID" "$ZONE" "$CHANNEL" "$RELEASE" "$POLICY" "$OS" "$DIST" "$DIST_VERSION" "$ARCH" "$LIBC" "$LIBC_VERSION" > "$ATTEMPT/request.json"
sync_file "$ATTEMPT/request.json"
STATUS=unavailable
if post resolve "$ATTEMPT/request.json" "$ATTEMPT/resolve.headers" "$ATTEMPT/resolve.json"; then
  if grep -Eq '"durable"[[:space:]]*:[[:space:]]*true' "$ATTEMPT/resolve.json"; then
    printf '{"durable":true}\n' > "$ATTEMPT/request.ack"
    sync_file "$ATTEMPT/request.ack"
    STATUS="$(sed -n 's/^[[:space:]]*[Xx]-[Aa]da[Oo][Ss]-[Dd]istribution:[[:space:]]*//p' "$ATTEMPT/resolve.headers" | tr -d '\r' | tail -n 1)"
  fi
fi
fetch() {
  local url="$1" output="$2" expected="$3"
  { [[ "$url" == https://* ]] || [[ "${ADAOS_BOOTSTRAP_ALLOW_HTTP_TEST:-0}" == 1 && "$url" == http://* ]]; } || return 2
  if have curl; then
    curl -fSL --connect-timeout 8 --max-time 600 --max-filesize 2147483648 "$url" -o "$output.partial" || return $?
  else
    wget --timeout=20 --tries=2 -qO "$output.partial" "$url" || return $?
  fi
  STAGE=verify
  printf '%s  %s\n' "$expected" "$output.partial" | sha256sum -c - || return $?
  mv "$output.partial" "$output"
}
EXTRA=()
[[ "$WITHOUT_UV" == 1 ]] && EXTRA+=(--without-uv)
[[ "$RUNTIME_ONLY" == 1 ]] && EXTRA+=(--runtime-only)
[[ -n "$UV_BINARY" ]] && EXTRA+=(--uv "$UV_BINARY")
if [[ -e "$DEST/.git" ]]; then
  STAGE=configure
  event installation_failed git_checkout_destination 2
  flush
  echo 'Prepared releases cannot replace a Git checkout; use --dev or choose a fresh --dest.' >&2
  exit 2
fi
if [[ "$STATUS" == ready ]]; then
  ROUTE=bundle
  event distribution_selected compatible_bundle
  URL="$(sed -n 's/^[[:space:]]*[Xx]-[Aa]da[Oo][Ss]-[Bb]undle-[Uu][Rr][Ll]:[[:space:]]*//p' "$ATTEMPT/resolve.headers" | tr -d '\r' | tail -n 1)"
  SHA="$(sed -n 's/^[[:space:]]*[Xx]-[Aa]da[Oo][Ss]-[Bb]undle-[Ss][Hh][Aa]256:[[:space:]]*//p' "$ATTEMPT/resolve.headers" | tr -d '\r' | tail -n 1)"
  [[ "$SHA" =~ ^[0-9a-f]{64}$ ]]
  STAGE=download
  fetch "$URL" "$ATTEMPT/bundle.tar.gz" "$SHA"
  STAGE=extract
  mkdir "$ATTEMPT/kit"
  tar -xzf "$ATTEMPT/bundle.tar.gz" --no-same-owner --no-same-permissions -C "$ATTEMPT/kit"
  STAGE=python
  "$ATTEMPT/kit/runtime/python/bin/python3" "$ATTEMPT/kit/source/tools/bootstrap_distribution.py" \
    --kit "$ATTEMPT/kit" --source "$ATTEMPT/kit/source" --attempt "$ATTEMPT" --dest "$DEST" "${EXTRA[@]}" -- "${ARGS[@]}"
  exit $?
fi
if [[ "$STATUS" == distribution_missing ]]; then event distribution_missing compatible_bundle_missing; fi
event fallback_started "$([[ "$STATUS" == distribution_missing ]] && echo configuration_unavailable || echo root_unavailable)"
printf '%s\n' 'Prepared distribution unavailable; using standard downloads with a managed Python (system Python is ignored).'
STAGE=python
[[ "$OS" == linux && "$ARCH" == x86_64 && "$LIBC" == glibc ]] || {
  event installation_failed standard_runtime_not_supported 1
  flush
  echo 'Standard managed runtime currently supports Linux x86_64 glibc; configuration request retained.' >&2
  exit 1
}
# Keep these pins identical to tools/bootstrap-runtime.lock.json. No remote shell execution.
PYTHON_URL='https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.11.18%2B20261110-x86_64-unknown-linux-gnu-install_only.tar.gz'
PYTHON_SHA='ac0b758b6ae4ba1cb87f7091eb6bb17c9a383a9bcdb5c18493cdd4f34e35ae8c'
UV_URL='https://github.com/astral-sh/uv/releases/download/0.13.7/uv-x86_64-unknown-linux-gnu.tar.gz'
UV_SHA='c5221ff41b7892bfc223543883600125f6a47fe845093e0f5bdff4f359196d83'
mkdir "$ATTEMPT/runtime"
if [[ "$WITHOUT_UV" != 1 ]]; then
  if fetch "$UV_URL" "$ATTEMPT/uv.tar.gz" "$UV_SHA"; then
    tar -xzf "$ATTEMPT/uv.tar.gz" --no-same-owner -C "$ATTEMPT/runtime"
    [[ -n "$UV_BINARY" ]] || EXTRA+=(--uv "$ATTEMPT/runtime/uv-x86_64-unknown-linux-gnu/uv")
  else event stage_failed uv_download_failed "$?"; fi
fi
fetch "$PYTHON_URL" "$ATTEMPT/python.tar.gz" "$PYTHON_SHA"
tar -xzf "$ATTEMPT/python.tar.gz" --no-same-owner -C "$ATTEMPT/runtime"
if [[ -z "$SOURCE" ]]; then
  STAGE=download
  [[ "$REV" =~ ^[A-Za-z0-9._/-]+$ && "$REV" != *..* ]]
  [[ "$RELEASE" == current || "$RELEASE" == "$REV" ]] || { echo 'Exact missing release requires --rev pointing to its source.' >&2; false; }
  URL="https://codeload.github.com/inimatic/adaos/tar.gz/$REV"
  if have curl; then curl -fSL --connect-timeout 8 --max-time 180 "$URL" -o "$ATTEMPT/source.tar.gz"; else wget --timeout=20 --tries=2 -qO "$ATTEMPT/source.tar.gz" "$URL"; fi
  mkdir "$ATTEMPT/source"
  tar -xzf "$ATTEMPT/source.tar.gz" --strip-components=1 --no-same-owner -C "$ATTEMPT/source"
  SOURCE="$ATTEMPT/source"
fi
STAGE=python
"$ATTEMPT/runtime/python/bin/python3" "$SOURCE/tools/bootstrap_distribution.py" \
  --python-home "$ATTEMPT/runtime/python" --source "$SOURCE" --attempt "$ATTEMPT" --dest "$DEST" "${EXTRA[@]}" -- "${ARGS[@]}"
