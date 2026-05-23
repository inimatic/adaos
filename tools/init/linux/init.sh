#!/usr/bin/env bash
# Minimal "download & bootstrap" entrypoint (Linux).
# Served from GitHub raw:
#   https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh
# Zone arguments are passed through to tools/bootstrap.sh, for example: --zone ru
set -euo pipefail

REPO_OWNER="${ADAOS_INIT_REPO_OWNER:-inimatic}"
REPO_NAME="${ADAOS_INIT_REPO_NAME:-adaos}"
REPO_URL_DEFAULT="${ADAOS_INIT_REPO_URL:-}"
REV_DEFAULT="${ADAOS_INIT_REV:-rev2026}"

log()  { printf '\033[36m[*] %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m[+] %s\033[0m\n' "$*"; }
warn() { printf '\033[33m[!] %s\033[0m\n' "$*"; }
die()  { printf '\033[31m[x] %s\033[0m\n' "$*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

in_codespaces() {
  [[ "${CODESPACES:-}" == "true" || -n "${GITHUB_WORKSPACE:-}" ]]
}

current_dir_resolved() {
  pwd -P
}

codespaces_workspace_root() {
  local root="${GITHUB_WORKSPACE:-$PWD}"
  if [[ -d "$root" ]]; then
    (
      cd "$root" >/dev/null 2>&1 || exit 1
      pwd -P
    )
  else
    printf '%s\n' "$root"
  fi
}

repo_origin_url() {
  local repo_dir="$1"
  have git || return 0
  git -C "$repo_dir" remote get-url origin 2>/dev/null || true
}

looks_like_adaos_checkout() {
  local repo_dir="$1"
  [[ -f "$repo_dir/tools/bootstrap.sh" && -f "$repo_dir/pyproject.toml" ]]
}

default_dest() {
  if [[ -n "${ADAOS_INIT_DEST:-}" ]]; then
    printf '%s\n' "$ADAOS_INIT_DEST"
    return 0
  fi
  if in_codespaces; then
    printf '%s\n' "$(current_dir_resolved)"
    return 0
  fi
  printf '%s/%s\n' "$HOME" "$REPO_NAME"
}

DEST_DEFAULT="$(default_dest)"

fetch_to_file() {
  local url="$1"
  local out="$2"
  if have curl; then
    curl -fsSL "$url" -o "$out"
    return $?
  fi
  if have wget; then
    wget -qO "$out" "$url"
    return $?
  fi
  die "Neither curl nor wget is available. Install one of them and retry."
}

usage() {
  cat <<EOF
Usage: init.sh [--dest DIR] [--rev REV] [--use-git] [--archive|--no-git] [--force] [--use-git-from URL] [--workspace-registry-repo URL] [--codespaces] [--] [bootstrap args...]

Defaults:
  --rev  ${REV_DEFAULT}
  --dest ${DEST_DEFAULT}

Examples:
  curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --join-code ABCD --zone ru
  curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --join-code ABCD --node-name "Codespace Member" --zone ru
  curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --codespaces --node-name "Codespace Member" --no-core-update --zone ru
  curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --role hub --install-service auto --zone ru
  curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --use-git-from https://github.com/<you>/adaos.git --rev my-branch --zone ru
  curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --archive --zone ru
  curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --workspace-registry-repo https://github.com/<you>/adaos-registry.git --zone ru
EOF
}

try_install_git() {
  have git && return 0
  local sudo_cmd=""
  if [[ "${EUID:-$(id -u)}" == "0" ]]; then
    sudo_cmd=""
  elif have sudo && sudo -n true >/dev/null 2>&1; then
    sudo_cmd="sudo -n"
  else
    return 1
  fi

  if have apt-get; then
    $sudo_cmd apt-get update -y >/dev/null 2>&1 || true
    $sudo_cmd apt-get install -y git >/dev/null 2>&1 && return 0
  fi
  if have dnf; then
    $sudo_cmd dnf install -y git >/dev/null 2>&1 && return 0
  fi
  if have yum; then
    $sudo_cmd yum install -y git >/dev/null 2>&1 && return 0
  fi
  if have apk; then
    $sudo_cmd apk add --no-cache git >/dev/null 2>&1 && return 0
  fi
  if have pacman; then
    $sudo_cmd pacman -Sy --noconfirm git >/dev/null 2>&1 && return 0
  fi
  if have zypper; then
    $sudo_cmd zypper --non-interactive install git >/dev/null 2>&1 && return 0
  fi
  return 1
}

DEST="$DEST_DEFAULT"
REV="$REV_DEFAULT"
USE_GIT="${ADAOS_INIT_USE_GIT:-auto}"
ARCHIVE_MODE="${ADAOS_INIT_ARCHIVE:-0}"
FORCE_REPLACE="${ADAOS_INIT_FORCE:-0}"
REPO_URL="$REPO_URL_DEFAULT"
CODESPACES_MODE="${ADAOS_INIT_CODESPACES:-0}"
WORKSPACE_REGISTRY_URL="${ADAOS_WORKSPACE_REGISTRY_REPO:-}"
BOOTSTRAP_ARGS=()

if [[ -n "${REPO_URL:-}" ]]; then
  USE_GIT="1"
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --dest) DEST="${2:-}"; shift 2 ;;
    --rev) REV="${2:-}"; shift 2 ;;
    --use-git) USE_GIT="1"; shift ;;
    --archive|--no-git) ARCHIVE_MODE="1"; USE_GIT="0"; shift ;;
    --force) FORCE_REPLACE="1"; shift ;;
    --use-git-from) REPO_URL="${2:-}"; USE_GIT="1"; shift 2 ;;
    --workspace-registry-repo|--use-workspace-registry-from) WORKSPACE_REGISTRY_URL="${2:-}"; shift 2 ;;
    --codespaces) CODESPACES_MODE="1"; shift ;;
    --) shift; BOOTSTRAP_ARGS+=("$@"); break ;;
    *) BOOTSTRAP_ARGS+=("$1"); shift ;;
  esac
done

if [[ "$CODESPACES_MODE" == "1" ]]; then
  DEST="$(current_dir_resolved)"
fi

[[ -n "${DEST:-}" ]] || die "--dest is empty"
[[ -n "${REV:-}" ]] || die "--rev is empty"
if [[ -n "${REPO_URL:-}" ]]; then
  REPO_URL="$(printf '%s' "$REPO_URL" | xargs)"
  [[ -n "${REPO_URL:-}" ]] || die "--use-git-from requires a non-empty URL"
fi
if [[ "$ARCHIVE_MODE" == "1" && -n "${REPO_URL:-}" ]]; then
  die "--archive/--no-git cannot be combined with --use-git-from"
fi
if [[ -n "${WORKSPACE_REGISTRY_URL:-}" ]]; then
  WORKSPACE_REGISTRY_URL="$(printf '%s' "$WORKSPACE_REGISTRY_URL" | xargs)"
  [[ -n "${WORKSPACE_REGISTRY_URL:-}" ]] || die "--workspace-registry-repo requires a non-empty URL"
fi

REPO_DIR="$DEST"
REUSE_EXISTING_REPO=0
CORE_UPDATE_REPO_URL="${ADAOS_CORE_UPDATE_REPO_URL:-}"

log "Preparing repo at: ${REPO_DIR}"
mkdir -p "$REPO_DIR"

if ! have git; then
  log "git not found; trying to install (best-effort)..."
  if try_install_git; then
    ok "git installed"
  else
    warn "git is not available; AdaOS will run in archive (no-git) mode for skills/scenarios until you enable git"
  fi
fi

if [[ "$USE_GIT" == "auto" ]]; then
  if have git; then
    USE_GIT="1"
  else
    USE_GIT="0"
  fi
fi
if [[ "$USE_GIT" == "1" ]] && ! have git; then
  die "git is not installed (required for git mode). Either install git, or run with --archive."
fi

ensure_origin() {
  local repo_dir="$1"
  local url="$2"
  local current_origin
  current_origin="$(git -C "$repo_dir" remote get-url origin 2>/dev/null || true)"
  if [[ -z "${current_origin:-}" ]]; then
    git -C "$repo_dir" remote add origin "$url"
  elif [[ "$current_origin" != "$url" ]]; then
    log "Updating origin URL: ${url}"
    git -C "$repo_dir" remote set-url origin "$url"
  fi
}

ensure_required_submodules() {
  local repo_dir="$1"
  local rasa_path="src/adaos/integrations/rasa-port"
  have git || return 0
  [[ -d "$repo_dir/.git" || -f "$repo_dir/.git" ]] || return 0
  log "Ensuring required submodules..."
  git -C "$repo_dir" submodule sync -- "$rasa_path"
  git -C "$repo_dir" submodule update --init --recursive "$rasa_path"
  if [[ -f "$repo_dir/$rasa_path/.git" && ! -f "$repo_dir/$rasa_path/pyproject.toml" ]]; then
    warn "rasa-port submodule worktree is incomplete; restoring from HEAD..."
    git -C "$repo_dir/$rasa_path" restore --source=HEAD --worktree .
    git -C "$repo_dir/$rasa_path" restore --source=HEAD --staged .
  fi
}

if in_codespaces && looks_like_adaos_checkout "$REPO_DIR"; then
  REUSE_EXISTING_REPO=1
  ok "Using existing AdaOS checkout in-place: ${REPO_DIR}"
  if [[ -n "${REPO_URL:-}" ]]; then
    log "Codespaces mode reuses the current checkout; --use-git-from will be used for core updates without recloning."
  fi
fi

if [[ "$REUSE_EXISTING_REPO" == "1" ]]; then
  ensure_required_submodules "$REPO_DIR"
elif [[ "$USE_GIT" == "1" ]]; then
  clone_url="${REPO_URL:-https://github.com/${REPO_OWNER}/${REPO_NAME}.git}"
  if [[ -d "$REPO_DIR/.git" ]]; then
    log "Existing git repo detected; updating..."
    ensure_origin "$REPO_DIR" "$clone_url"
    git -C "$REPO_DIR" fetch --all --prune
    git -C "$REPO_DIR" checkout "$REV"
    git -C "$REPO_DIR" pull --ff-only
    git -C "$REPO_DIR" branch --set-upstream-to="origin/${REV}" "$REV" >/dev/null 2>&1 || true
  elif looks_like_adaos_checkout "$REPO_DIR"; then
    log "Adopting existing AdaOS source tree into git checkout..."
    git -C "$REPO_DIR" init
    ensure_origin "$REPO_DIR" "$clone_url"
    git -C "$REPO_DIR" fetch origin "$REV"
    git -C "$REPO_DIR" symbolic-ref HEAD "refs/heads/${REV}"
    git -C "$REPO_DIR" reset --hard "origin/${REV}"
    git -C "$REPO_DIR" branch --set-upstream-to="origin/${REV}" "$REV" >/dev/null 2>&1 || true
  elif find "$REPO_DIR" -mindepth 1 -maxdepth 1 | read -r _; then
    if [[ "$FORCE_REPLACE" != "1" ]]; then
      die "Destination is non-empty and is not an AdaOS git checkout: ${REPO_DIR}. Use --force to replace it, or choose another --dest."
    fi
    warn "Removing non-empty destination because --force was supplied: ${REPO_DIR}"
    rm -rf "$REPO_DIR"
    log "Cloning ${clone_url} (${REV})..."
    git clone -b "$REV" "$clone_url" "$REPO_DIR"
    git -C "$REPO_DIR" branch --set-upstream-to="origin/${REV}" "$REV" >/dev/null 2>&1 || true
  else
    log "Cloning ${clone_url} (${REV})..."
    git clone -b "$REV" "$clone_url" "$REPO_DIR"
    git -C "$REPO_DIR" branch --set-upstream-to="origin/${REV}" "$REV" >/dev/null 2>&1 || true
  fi
  ensure_required_submodules "$REPO_DIR"
else
  # No-git path: download GitHub archive.
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp" >/dev/null 2>&1 || true' EXIT
  archive="$tmp/adaos.tar.gz"
  url="https://codeload.github.com/${REPO_OWNER}/${REPO_NAME}/tar.gz/refs/heads/${REV}"
  log "Downloading source archive: ${url}"
  fetch_to_file "$url" "$archive"

  log "Extracting..."
  if [[ -d "$REPO_DIR" ]]; then
    resolved_repo_dir="$(
      cd "$REPO_DIR" >/dev/null 2>&1 || exit 1
      pwd -P
    )"
    if find "$REPO_DIR" -mindepth 1 -maxdepth 1 | read -r _; then
      if [[ "$FORCE_REPLACE" != "1" ]]; then
        die "Refusing to overwrite non-empty destination in archive mode: ${REPO_DIR}. Use --force to replace it, or install with git."
      fi
      if [[ "$resolved_repo_dir" == "$(current_dir_resolved)" ]]; then
        die "Refusing to remove the current working directory. Choose another --dest."
      fi
      rm -rf "$REPO_DIR"
      mkdir -p "$REPO_DIR"
    elif [[ ! -d "$REPO_DIR" ]]; then
      mkdir -p "$REPO_DIR"
    fi
  else
    mkdir -p "$REPO_DIR"
  fi
  tar -xzf "$archive" -C "$tmp"
  top_dir="$(find "$tmp" -maxdepth 1 -type d -name "${REPO_NAME}-*" | head -n 1 || true)"
  [[ -n "${top_dir:-}" ]] || die "Failed to locate extracted directory"
  (cd "$top_dir" && tar -cf - .) | (cd "$REPO_DIR" && tar -xf -)
  ok "Source extracted to: ${REPO_DIR}"
fi

cd "$REPO_DIR"

if [[ -z "${CORE_UPDATE_REPO_URL:-}" && -n "${REPO_URL:-}" ]]; then
  CORE_UPDATE_REPO_URL="$REPO_URL"
fi
if [[ -z "${CORE_UPDATE_REPO_URL:-}" && -d "$REPO_DIR/.git" ]]; then
  CORE_UPDATE_REPO_URL="$(repo_origin_url "$REPO_DIR")"
fi
if [[ -n "${CORE_UPDATE_REPO_URL:-}" ]]; then
  log "Core update repo URL: ${CORE_UPDATE_REPO_URL}"
  export ADAOS_CORE_UPDATE_REPO_URL="$CORE_UPDATE_REPO_URL"
fi
if [[ -n "${WORKSPACE_REGISTRY_URL:-}" ]]; then
  log "Workspace registry repo URL: ${WORKSPACE_REGISTRY_URL}"
  export ADAOS_WORKSPACE_REGISTRY_REPO="$WORKSPACE_REGISTRY_URL"
fi

# Ensure bootstrap gets a --rev unless caller already passed one.
have_rev=0
have_install_service=0
for ((i=0; i<${#BOOTSTRAP_ARGS[@]}; i++)); do
  if [[ "${BOOTSTRAP_ARGS[$i]}" == "--rev" ]]; then
    have_rev=1
  fi
  if [[ "${BOOTSTRAP_ARGS[$i]}" == "--install-service" ]]; then
    have_install_service=1
  fi
done
if [[ "$have_rev" != "1" ]]; then
  BOOTSTRAP_ARGS+=("--rev" "$REV")
fi
if in_codespaces && [[ "$have_install_service" != "1" ]]; then
  warn "Codespaces detected; defaulting to --install-service never because systemd user services are usually unavailable."
  BOOTSTRAP_ARGS+=("--install-service" "never")
fi

log "Running bootstrap..."
bash tools/bootstrap.sh "${BOOTSTRAP_ARGS[@]}"
