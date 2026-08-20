from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Sequence

from adaos.services.bootstrap_update import BOOTSTRAP_CRITICAL_PATHS
from adaos.services.env_policy import env_float


_LOCAL_SOURCE_COPY_IGNORES = (
    ".git",
    ".adaos",
    ".venv",
    ".codex-tmp",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "node_modules",
)


def _is_probably_git_sha(value: str) -> bool:
    token = str(value or "").strip()
    if len(token) < 7 or len(token) > 40:
        return False
    for ch in token:
        if ch not in "0123456789abcdefABCDEF":
            return False
    return True


def _checkout_target_version(repo_dir: Path, *, target_rev: str, target_version: str) -> None:
    """
    Ensure the checkout is at the requested git commit-ish when target_version looks like a SHA.

    This prevents "partial update" situations where a branch tip moves (or is different
    from what the update coordinator expects) while the update runner still prepares a slot.
    """
    target_version = str(target_version or "").strip()
    if not _is_probably_git_sha(target_version):
        return
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git is required for core updates but is not installed")
    try:
        _run([git, "checkout", target_version], cwd=repo_dir)
        return
    except Exception:
        # Shallow clones may not contain the commit object even if the branch was specified.
        # Fetch more history for the target branch and retry.
        if target_rev:
            _run([git, "fetch", "--depth", "50", "origin", target_rev], cwd=repo_dir)
        else:
            _run([git, "fetch", "--depth", "50", "origin"], cwd=repo_dir)
        _run([git, "checkout", target_version], cwd=repo_dir)


def _resolve_branch_head(repo_url: str, target_rev: str) -> str:
    repo_url = str(repo_url or "").strip()
    target_rev = str(target_rev or "").strip()
    if not repo_url or not target_rev:
        return ""
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git is required for core update branch head resolution but is not installed")
    completed = subprocess.run(
        [git, "ls-remote", "--heads", repo_url, target_rev],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"failed to resolve remote branch head for {target_rev}: git ls-remote rc={completed.returncode}\n"
            f"stdout:\n{completed.stdout[-4000:]}\n"
            f"stderr:\n{completed.stderr[-4000:]}"
        )
    for line in (completed.stdout or "").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == f"refs/heads/{target_rev}" and _is_probably_git_sha(parts[0]):
            return parts[0]
    raise RuntimeError(f"remote branch {target_rev!r} was not found in {repo_url}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare inactive AdaOS core slot")
    parser.add_argument("--target-rev", default="")
    parser.add_argument("--target-version", default="")
    parser.add_argument("--slot", required=True)
    parser.add_argument("--slot-dir", required=True)
    parser.add_argument("--base-dir", default="")
    parser.add_argument("--repo-root", default="")
    parser.add_argument("--source-repo-root", default="")
    parser.add_argument("--shared-dotenv-path", default="")
    parser.add_argument("--repo-url", default=os.getenv("ADAOS_CORE_UPDATE_REPO_URL", "https://github.com/inimatic/adaos.git"))
    parser.add_argument("--prepare-lease-path", default="")
    parser.add_argument("--prepare-lease-token", default="")
    return parser.parse_args()


def _low_priority_io_command(cmd: Sequence[str]) -> list[str]:
    command = [str(item) for item in cmd]
    mode = str(os.getenv("ADAOS_CORE_UPDATE_IO_PRIORITY", "idle") or "idle").strip().lower()
    if not sys.platform.startswith("linux") or mode in {"", "0", "off", "none", "disabled"}:
        return command
    ionice = shutil.which("ionice")
    if not ionice:
        return command
    if mode in {"idle", "3"}:
        return [ionice, "-c", "3", "--", *command]
    # Explicit BE/7 mode keeps updates progressing on hosts where idle I/O can
    # starve; the default idle class protects channel-critical persistence.
    return [ionice, "-c", "2", "-n", "7", "--", *command]


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    run_cmd = _low_priority_io_command(cmd)
    completed = subprocess.run(run_cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed rc={completed.returncode}: {' '.join(run_cmd)}\n"
            f"stdout:\n{completed.stdout[-4000:]}\n"
            f"stderr:\n{completed.stderr[-4000:]}"
        )


def _run_json(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> dict[str, object]:
    run_cmd = _low_priority_io_command(cmd)
    completed = subprocess.run(
        run_cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed rc={completed.returncode}: {' '.join(run_cmd)}\n"
            f"stdout:\n{completed.stdout[-4000:]}\n"
            f"stderr:\n{completed.stderr[-4000:]}"
        )
    try:
        payload = json.loads(completed.stdout or "{}")
    except Exception as exc:
        raise RuntimeError(
            f"command returned invalid JSON: {' '.join(run_cmd)}\nstdout:\n{completed.stdout[-4000:]}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"command returned non-object JSON: {' '.join(run_cmd)}")
    return payload


def _verify_prepare_lease(path: str | os.PathLike[str] = "", token: str = "") -> None:
    lease_path_raw = str(path or "").strip()
    lease_token = str(token or "").strip()
    if not lease_path_raw and not lease_token:
        return
    if not lease_path_raw or not lease_token:
        raise RuntimeError("core update prepare lease is incomplete")
    lease_path = Path(lease_path_raw).expanduser().resolve()
    try:
        payload = json.loads(lease_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"core update prepare lease is not active: {lease_path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("core update prepare lease payload is invalid")
    if str(payload.get("token") or "").strip() != lease_token:
        raise RuntimeError("core update prepare lease token mismatch")
    if str(payload.get("state") or "").strip().lower() != "active":
        reason = str(payload.get("reason") or payload.get("revoked_reason") or "revoked").strip()
        raise RuntimeError(f"core update prepare lease revoked: {reason}")


def _git_worktree_has_changes(repo_dir: Path) -> bool:
    git = shutil.which("git")
    if not git or not repo_dir.exists():
        return False
    completed = subprocess.run(
        [git, "status", "--porcelain", "--untracked-files=all"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return False
    return bool(str(completed.stdout or "").strip())


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _venv_is_usable(venv_dir: Path) -> bool:
    python_bin = _venv_python(venv_dir)
    return venv_dir.exists() and python_bin.exists()


def _rewrite_text_file(path: Path, *, old: str, new: str) -> bool:
    try:
        raw = path.read_bytes()
    except Exception:
        return False
    if b"\x00" in raw:
        return False
    try:
        text = raw.decode("utf-8")
    except Exception:
        return False
    if old not in text:
        return False
    if not _write_text_file_private(path, text.replace(old, new)):
        return False
    return True


def _write_text_file_private(path: Path, text: str) -> bool:
    try:
        file_stat = path.stat()
    except Exception:
        return False
    hardlinked = bool(getattr(file_stat, "st_nlink", 1) > 1)
    if not hardlinked:
        path.write_text(text, encoding="utf-8", newline="")
        return True

    tmp = path.with_name(f".{path.name}.adaos-private-{os.getpid()}-{time.time_ns()}")
    try:
        tmp.write_text(text, encoding="utf-8", newline="")
        with contextlib.suppress(Exception):
            os.chmod(tmp, stat.S_IMODE(file_stat.st_mode))
        os.replace(tmp, path)
        return True
    except Exception:
        with contextlib.suppress(Exception):
            tmp.unlink(missing_ok=True)
        return False


def _rewrite_text_file_many(path: Path, replacements: Sequence[tuple[str, str]]) -> bool:
    try:
        raw = path.read_bytes()
    except Exception:
        return False
    if b"\x00" in raw:
        return False
    try:
        text = raw.decode("utf-8")
    except Exception:
        return False
    updated = text
    for old, new in replacements:
        if old and old != new:
            updated = updated.replace(old, new)
    if updated == text:
        return False
    if not _write_text_file_private(path, updated):
        return False
    return True


def _venv_text_repair_paths(venv_dir: Path) -> list[Path]:
    paths: list[Path] = []
    if os.name == "nt":
        scripts_dir = venv_dir / "Scripts"
    else:
        scripts_dir = venv_dir / "bin"
    if scripts_dir.exists():
        paths.extend(child for child in scripts_dir.iterdir() if child.is_file())
    pyvenv_cfg = venv_dir / "pyvenv.cfg"
    if pyvenv_cfg.exists():
        paths.append(pyvenv_cfg)
    site_package_roots = list(venv_dir.glob("lib/python*/site-packages"))
    site_package_roots.extend(venv_dir.glob("Lib/site-packages"))
    for site_packages in site_package_roots:
        if not site_packages.is_dir():
            continue
        # Absolute environment/source paths are generated only in launchers,
        # environment metadata, editable-install finders, and direct-url
        # metadata. Walking and reading every package source file made slot
        # preparation saturate disks with large scientific environments.
        patterns = (
            "*.pth",
            "*.egg-link",
            "__editable__*.py",
            "*.dist-info/direct_url.json",
            "*.data/scripts/*",
        )
        for pattern in patterns:
            paths.extend(child for child in site_packages.glob(pattern) if child.is_file())
    return list(dict.fromkeys(paths))


def _repair_moved_venv(
    venv_dir: Path,
    *,
    original_venv_dir: Path,
    original_repo_dir: Path | None = None,
    final_repo_dir: Path | None = None,
) -> dict[str, object]:
    started_at = time.time()
    repaired: list[str] = []
    replacements: list[tuple[str, str]] = [(str(original_venv_dir), str(venv_dir))]
    if original_repo_dir is not None and final_repo_dir is not None:
        replacements.append((str(original_repo_dir), str(final_repo_dir)))
    for child in _venv_text_repair_paths(venv_dir):
        if _rewrite_text_file_many(child, replacements):
            repaired.append(str(child))
    return {
        "ok": True,
        "venv_dir": str(venv_dir),
        "original_venv_dir": str(original_venv_dir),
        "original_repo_dir": str(original_repo_dir) if original_repo_dir is not None else "",
        "final_repo_dir": str(final_repo_dir) if final_repo_dir is not None else "",
        "repaired_files": repaired,
        "repaired_files_total": len(repaired),
        "elapsed_s": round(time.time() - started_at, 3),
    }


def _repair_copied_venv(venv_dir: Path, *, source_venv_dir: Path) -> dict[str, object]:
    return _repair_moved_venv(venv_dir, original_venv_dir=source_venv_dir)


def _run_seed_copy_command(
    cmd: Sequence[str],
    *,
    source: Path,
    target: Path,
    method: str,
) -> dict[str, object]:
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    run_cmd = _low_priority_io_command(cmd)
    completed = subprocess.run(
        run_cmd,
        cwd=str(source.parent),
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return {
            "ok": True,
            "method": method,
            "command": run_cmd,
            "workload_command": list(cmd),
        }
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    return {
        "ok": False,
        "method": method,
        "command": run_cmd,
        "workload_command": list(cmd),
        "returncode": int(completed.returncode),
        "stdout_tail": (completed.stdout or "")[-2000:],
        "stderr_tail": (completed.stderr or "")[-2000:],
    }


def _seed_copy_attempts_snapshot(attempts: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    return [dict(attempt) for attempt in attempts]


def _env_flag(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on"}


def _linux_hardlink_seed_allowed(*, source: Path, target: Path, checkout_dir: Path | None, mode: str) -> bool:
    if not sys.platform.startswith("linux"):
        return False
    if mode == "hardlink":
        return True
    if mode not in {"auto", "auto-hardlink", "hardlink-auto"}:
        return False
    # A failed installer must be able to fall back to the seeded environment.
    # Hardlink copies can no longer be trusted after an installer has touched
    # them, so keep the optimization opt-in instead of sacrificing recovery.
    if not _env_flag("ADAOS_CORE_UPDATE_LINUX_SEED_HARDLINK_AUTO", "0"):
        return False
    if checkout_dir is None or not (checkout_dir / "uv.lock").exists() or not _uv_install_enabled():
        return False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        return source.stat().st_dev == target.parent.stat().st_dev
    except Exception:
        return False


def _copy_seed_venv_tree(source: Path, target: Path, *, checkout_dir: Path | None = None) -> dict[str, object]:
    mode = str(os.getenv("ADAOS_CORE_UPDATE_LINUX_SEED_COPY_MODE", "auto") or "auto").strip().lower()
    attempts: list[dict[str, object]] = []
    if sys.platform.startswith("linux") and mode not in {"copy", "python", "shutil"}:
        cp = shutil.which("cp")
        if cp:
            # Keep the trailing ``/.`` literal. ``Path(source) / "."`` is
            # normalized back to ``source``; with our pre-created target,
            # GNU cp would then create ``target/<source.name>`` and leave the
            # expected virtualenv executable one directory too deep.
            source_contents = f"{source}/."
            hardlink_enabled = _linux_hardlink_seed_allowed(
                source=source,
                target=target,
                checkout_dir=checkout_dir,
                mode=mode,
            ) or _env_flag("ADAOS_CORE_UPDATE_LINUX_SEED_HARDLINK", "0")
            if mode == "auto" and not hardlink_enabled:
                attempt = _run_seed_copy_command(
                    [cp, "-a", "--reflink=auto", source_contents, str(target)],
                    source=source,
                    target=target,
                    method="cp_reflink_auto",
                )
                attempts.append(attempt)
                if bool(attempt.get("ok")):
                    return {**attempt, "attempts": _seed_copy_attempts_snapshot(attempts)}
            elif mode in {"auto", "reflink", "cow", "copy-on-write"}:
                attempt = _run_seed_copy_command(
                    [cp, "-a", "--reflink=always", source_contents, str(target)],
                    source=source,
                    target=target,
                    method="cp_reflink",
                )
                attempts.append(attempt)
                if bool(attempt.get("ok")):
                    return {**attempt, "attempts": _seed_copy_attempts_snapshot(attempts)}
            if hardlink_enabled and mode not in {"reflink", "cow", "copy-on-write"}:
                attempt = _run_seed_copy_command(
                    [cp, "-al", source_contents, str(target)],
                    source=source,
                    target=target,
                    method="cp_hardlink",
                )
                attempts.append(attempt)
                if bool(attempt.get("ok")):
                    return {**attempt, "attempts": _seed_copy_attempts_snapshot(attempts)}
            if mode in {"auto", "archive", "cp", "auto-hardlink", "hardlink-auto"}:
                attempt = _run_seed_copy_command(
                    [cp, "-a", source_contents, str(target)],
                    source=source,
                    target=target,
                    method="cp_archive",
                )
                attempts.append(attempt)
                if bool(attempt.get("ok")):
                    return {**attempt, "attempts": _seed_copy_attempts_snapshot(attempts)}

    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    try:
        shutil.copytree(source, target, symlinks=True)
        return {"ok": True, "method": "shutil_copytree_symlinks", "attempts": attempts}
    except Exception as first_exc:
        attempts.append(
            {
                "ok": False,
                "method": "shutil_copytree_symlinks",
                "error": str(first_exc),
                "error_type": type(first_exc).__name__,
            }
        )
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(source, target)
        return {"ok": True, "method": "shutil_copytree", "attempts": attempts}


def _copy_seed_venv(
    source_venv_dir: Path,
    target_venv_dir: Path,
    *,
    checkout_dir: Path | None = None,
) -> dict[str, object]:
    started_at = time.time()
    source = Path(source_venv_dir).expanduser().resolve()
    target = Path(target_venv_dir).expanduser().resolve()
    if not _venv_is_usable(source):
        return {
            "ok": False,
            "seeded": False,
            "source_venv_dir": str(source),
            "target_venv_dir": str(target),
            "reason": "source_venv_unusable",
        }
    copy_started_at = time.time()
    copy_result = _copy_seed_venv_tree(source, target, checkout_dir=checkout_dir)
    copy_finished_at = time.time()
    repair = _repair_copied_venv(target, source_venv_dir=source)
    return {
        "ok": True,
        "seeded": True,
        "source_venv_dir": str(source),
        "target_venv_dir": str(target),
        "copy_method": str(copy_result.get("method") or ""),
        "copy_elapsed_s": round(copy_finished_at - copy_started_at, 3),
        "elapsed_s": round(time.time() - started_at, 3),
        "copy_attempts": copy_result.get("attempts") if isinstance(copy_result.get("attempts"), list) else [],
        "repair": repair,
    }


def _active_slot_seed_venv(slot_dir: Path) -> Path | None:
    slots_parent = slot_dir.parent
    active_marker = slots_parent.parent / "active"
    try:
        active = active_marker.read_text(encoding="utf-8").strip().upper()
    except Exception:
        active = ""
    if active not in {"A", "B"}:
        return None
    candidate = slots_parent / active / "venv"
    return candidate.resolve() if _venv_is_usable(candidate) else None


def _root_seed_venv(repo_root_dir: Path | None) -> Path | None:
    if repo_root_dir is None:
        return None
    candidates = [
        repo_root_dir / ".venv",
    ]
    for candidate in candidates:
        if _venv_is_usable(candidate):
            return candidate.resolve()
    return None


def _venv_build_toolchain_snapshot(venv_dir: Path) -> dict[str, object]:
    """Report whether a copied venv can build the local project without PyPI.

    Core updates normally seed the inactive slot from an already working
    environment. Requiring an unconditional online upgrade of pip/setuptools/
    wheel defeats that fallback and made member updates fail during transient
    PyPI outages.
    """

    venv = Path(venv_dir).expanduser().resolve()
    if not _venv_is_usable(venv):
        return {"ready": False, "reason": "venv_unusable", "packages": {}}
    code = (
        "import importlib.metadata as m,json; "
        "names=('pip','setuptools','wheel'); "
        "installed={str(d.metadata.get('Name') or '').lower():d.version for d in m.distributions()}; "
        "versions={name:installed.get(name,'') for name in names}; "
        "print(json.dumps(versions,sort_keys=True))"
    )
    try:
        completed = subprocess.run(
            [str(_venv_python(venv)), "-c", code],
            capture_output=True,
            text=True,
            timeout=15.0,
        )
        packages = json.loads(completed.stdout or "{}") if completed.returncode == 0 else {}
    except Exception as exc:
        return {
            "ready": False,
            "reason": "probe_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "packages": {},
        }
    if not isinstance(packages, dict):
        packages = {}
    normalized = {name: str(packages.get(name) or "").strip() for name in ("pip", "setuptools", "wheel")}
    missing = [name for name, version in normalized.items() if not version]
    return {
        "ready": not missing,
        "reason": "ready" if not missing else "packages_missing",
        "packages": normalized,
        "missing": missing,
    }


def _prepare_seed_venv(
    *,
    venv_dir: Path,
    slot_dir: Path,
    repo_root_dir: Path | None,
    checkout_dir: Path | None = None,
) -> dict[str, object]:
    if str(os.getenv("ADAOS_CORE_UPDATE_SEED_VENV", "1") or "1").strip().lower() in {"0", "false", "no", "off"}:
        return {
            "ok": True,
            "seeded": False,
            "source": "disabled",
            "reason": "disabled_by_env",
            "target_venv_dir": str(venv_dir),
        }
    candidates: list[tuple[str, Path, dict[str, object]]] = []
    for source_name, source_path in (
        ("active_slot", _active_slot_seed_venv(slot_dir)),
        ("root_venv", _root_seed_venv(repo_root_dir)),
    ):
        if source_path is None:
            continue
        candidates.append((source_name, source_path, _venv_build_toolchain_snapshot(source_path)))
    # Prefer an environment that already contains the local build toolchain.
    # Stable sorting preserves active_slot preference when both are complete.
    candidates.sort(key=lambda item: not bool(item[2].get("ready")))
    fresh_uv_enabled = _env_flag("ADAOS_CORE_UPDATE_FRESH_UV_ENVIRONMENT", "1")
    locked_checkout = checkout_dir is not None and (checkout_dir / "uv.lock").is_file()
    uv = shutil.which("uv") if fresh_uv_enabled and locked_checkout and _uv_install_enabled() else None
    if uv:
        payload: dict[str, object] = {
            "ok": True,
            "seeded": False,
            "source": "locked_uv_fresh_environment",
            "reason": "avoid_active_slot_venv_copy",
            "target_venv_dir": str(venv_dir),
            "installer": str(uv),
        }
        if candidates:
            fallback_name, fallback_path, fallback_toolchain = candidates[0]
            payload.update(
                {
                    "fallback_source": fallback_name,
                    "fallback_source_venv_dir": str(fallback_path),
                    "fallback_source_build_toolchain": fallback_toolchain,
                }
            )
        return payload
    for source_name, source_path, toolchain in candidates:
        try:
            result = _copy_seed_venv(source_path, venv_dir, checkout_dir=checkout_dir)
            result["source"] = source_name
            result["source_build_toolchain"] = toolchain
            if bool(result.get("ok")):
                return result
        except Exception as exc:
            last_error = {
                "source": source_name,
                "source_venv_dir": str(source_path),
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
            continue
    payload: dict[str, object] = {
        "ok": True,
        "seeded": False,
        "source": "",
        "reason": "no_usable_seed_venv",
        "target_venv_dir": str(venv_dir),
    }
    if "last_error" in locals():
        payload["last_error"] = last_error
    return payload


def _uv_install_enabled() -> bool:
    return str(os.getenv("ADAOS_CORE_UPDATE_UV", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}


def _uv_locked_enabled() -> bool:
    return str(os.getenv("ADAOS_CORE_UPDATE_UV_LOCKED", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}


def _uv_link_mode_snapshot(*, uv: str, venv_dir: Path) -> dict[str, object]:
    requested = str(os.getenv("UV_LINK_MODE") or "").strip().lower()
    if requested:
        return {"mode": requested, "reason": "explicit_uv_link_mode"}
    if not _env_flag("ADAOS_CORE_UPDATE_UV_HARDLINK_CACHE", "1"):
        return {"mode": "", "reason": "cache_hardlink_disabled"}
    try:
        completed = subprocess.run(
            [uv, "cache", "dir"],
            capture_output=True,
            text=True,
            timeout=15.0,
        )
        cache_dir = Path(str(completed.stdout or "").strip()).expanduser().resolve()
        venv_parent = Path(venv_dir).expanduser().resolve().parent
        venv_parent.mkdir(parents=True, exist_ok=True)
        if completed.returncode != 0 or not cache_dir.is_dir():
            return {"mode": "", "reason": "cache_dir_unavailable"}
        if cache_dir.stat().st_dev != venv_parent.stat().st_dev:
            return {
                "mode": "",
                "reason": "cache_target_filesystem_mismatch",
                "cache_dir": str(cache_dir),
            }
        return {
            "mode": "hardlink",
            "reason": "cache_target_same_filesystem",
            "cache_dir": str(cache_dir),
        }
    except Exception as exc:
        return {
            "mode": "",
            "reason": "cache_link_mode_probe_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _install_slot_project(
    *,
    checkout_dir: Path,
    venv_dir: Path,
    seed: dict[str, object],
) -> dict[str, object]:
    started_at = time.time()
    uv = shutil.which("uv") if _uv_install_enabled() else None
    attempts: list[dict[str, object]] = []
    if uv and (checkout_dir / "uv.lock").exists():
        env = dict(os.environ)
        env["UV_PROJECT_ENVIRONMENT"] = str(venv_dir)
        link_mode = _uv_link_mode_snapshot(uv=uv, venv_dir=venv_dir)
        if str(link_mode.get("mode") or ""):
            env["UV_LINK_MODE"] = str(link_mode["mode"])
        cmd = [uv, "sync", "--no-dev", "--python", sys.executable]
        if _uv_locked_enabled():
            cmd.insert(2, "--locked")
        run_cmd = _low_priority_io_command(cmd)
        completed = subprocess.run(
            run_cmd,
            cwd=str(checkout_dir),
            env=env,
            capture_output=True,
            text=True,
        )
        attempts.append(
            {
                "installer": "uv",
                "command": run_cmd,
                "workload_command": cmd,
                "returncode": int(completed.returncode),
                "stdout_tail": (completed.stdout or "")[-4000:],
                "stderr_tail": (completed.stderr or "")[-4000:],
                "link_mode": link_mode,
            }
        )
        if completed.returncode == 0:
            return {
                "ok": True,
                "installer": "uv",
                "started_at": started_at,
                "finished_at": time.time(),
                "elapsed_s": round(time.time() - started_at, 3),
                "seed": seed,
                "attempts": attempts,
            }

    effective_seed = seed
    if str(seed.get("source") or "").strip() == "locked_uv_fresh_environment":
        fallback_source_raw = str(seed.get("fallback_source_venv_dir") or "").strip()
        fallback_source = Path(fallback_source_raw).expanduser() if fallback_source_raw else None
        if fallback_source is not None and _venv_is_usable(fallback_source):
            shutil.rmtree(venv_dir, ignore_errors=True)
            try:
                fallback_seed = _copy_seed_venv(fallback_source, venv_dir, checkout_dir=None)
            except Exception as exc:
                fallback_seed = {
                    "ok": False,
                    "seeded": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            fallback_seed["source"] = str(seed.get("fallback_source") or "")
            fallback_seed["source_build_toolchain"] = seed.get("fallback_source_build_toolchain")
            attempts.append(
                {
                    "installer": "deferred_seed",
                    "returncode": 0 if bool(fallback_seed.get("ok")) else 1,
                    "reason": "locked_uv_sync_failed",
                    "copy_method": fallback_seed.get("copy_method"),
                    "elapsed_s": fallback_seed.get("elapsed_s"),
                    "error": fallback_seed.get("error"),
                }
            )
            if bool(fallback_seed.get("ok")):
                effective_seed = fallback_seed

    if str(effective_seed.get("copy_method") or "").strip() == "cp_hardlink":
        attempts.append(
            {
                "installer": "pip",
                "returncode": None,
                "seed_discarded": True,
                "seed_discard_reason": "hardlink_seed_replaced_before_pip_fallback",
            }
        )
        shutil.rmtree(venv_dir, ignore_errors=True)
        source_venv = Path(str(effective_seed.get("source_venv_dir") or "")).expanduser()
        if str(effective_seed.get("source_venv_dir") or "").strip() and _venv_is_usable(source_venv):
            reseed = _copy_seed_venv(source_venv, venv_dir, checkout_dir=None)
            attempts.append(
                {
                    "installer": "pip_seed",
                    "returncode": 0 if bool(reseed.get("ok")) else 1,
                    "reason": "safe_copy_after_uv_hardlink_attempt",
                    "copy_method": reseed.get("copy_method"),
                }
            )

    if not _venv_is_usable(venv_dir):
        _run([sys.executable, "-m", "venv", str(venv_dir)])
    py = _venv_python(venv_dir)
    toolchain = _venv_build_toolchain_snapshot(venv_dir)
    try:
        if bool(toolchain.get("ready")):
            attempts.append(
                {
                    "installer": "pip_toolchain",
                    "returncode": 0,
                    "bootstrap_skipped": True,
                    "reason": "seeded_build_toolchain_ready",
                    "packages": toolchain.get("packages"),
                }
            )
            _run([str(py), "-m", "pip", "install", "--no-build-isolation", str(checkout_dir)])
        else:
            _run([str(py), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
            _run([str(py), "-m", "pip", "install", str(checkout_dir)])
    except Exception as first_exc:
        attempts.append(
            {
                "installer": "pip",
                "returncode": 1,
                "error": str(first_exc),
                "error_type": type(first_exc).__name__,
                "after_seed": bool(effective_seed.get("seeded")),
            }
        )
        if bool(effective_seed.get("seeded")):
            shutil.rmtree(venv_dir, ignore_errors=True)
            _run([sys.executable, "-m", "venv", str(venv_dir)])
            py = _venv_python(venv_dir)
            _run([str(py), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
            _run([str(py), "-m", "pip", "install", str(checkout_dir)])
        else:
            raise
    attempts.append({"installer": "pip", "returncode": 0})
    return {
        "ok": True,
        "installer": "pip",
        "started_at": started_at,
        "finished_at": time.time(),
        "elapsed_s": round(time.time() - started_at, 3),
        "seed": effective_seed,
        "attempts": attempts,
    }


def _force_remove_tree(path: Path) -> None:
    target = Path(path).expanduser().resolve()

    def _retry_with_writeable(func, value, _exc_info) -> None:
        try:
            os.chmod(value, stat.S_IWRITE)
        except Exception:
            pass
        func(value)

    # Git pack indexes are briefly held open by Windows Search and antivirus
    # immediately after a clone.  The onerror callback handles read-only
    # attributes, while the outer retry handles those transient file handles.
    # Keep the retry bounded and fail closed if metadata still survives.
    delays = (0.0, 0.05, 0.1, 0.2, 0.4, 0.8, 1.0, 1.0)
    last_error: Exception | None = None
    for attempt, delay in enumerate(delays):
        if not target.exists():
            return
        if attempt and delay > 0.0:
            time.sleep(delay)
        try:
            shutil.rmtree(target, ignore_errors=False, onerror=_retry_with_writeable)
        except FileNotFoundError:
            if not target.exists():
                return
            last_error = FileNotFoundError(str(target))
        except (PermissionError, OSError) as exc:
            last_error = exc
        else:
            return
    if last_error is not None:
        raise last_error
    if target.exists():
        raise RuntimeError(f"failed to remove directory tree: {target}")


def _replace_slot_dir(prepared_slot: Path, slot_dir: Path) -> None:
    cleanup_errors: list[str] = []
    if slot_dir.exists():
        for attempt in range(2):
            try:
                _force_remove_tree(slot_dir)
            except FileNotFoundError as exc:
                cleanup_errors.append(f"attempt={attempt + 1}: {type(exc).__name__}: {exc}")
                if not slot_dir.exists():
                    break
                time.sleep(0.05)
                continue
            except Exception as exc:
                cleanup_errors.append(f"attempt={attempt + 1}: {type(exc).__name__}: {exc}")
                if not slot_dir.exists():
                    break
                time.sleep(0.05)
                continue
            if not slot_dir.exists():
                break
    if slot_dir.exists():
        quarantine = slot_dir.with_name(
            f"adaos-core-stale-{slot_dir.name.lower()}-{int(time.time() * 1000)}-{os.getpid()}"
        )
        try:
            slot_dir.rename(quarantine)
        except Exception as exc:
            details = "; ".join(cleanup_errors) if cleanup_errors else "cleanup left destination present"
            raise RuntimeError(
                "slot directory cleanup failed; refusing nested move into existing path: "
                f"{slot_dir}; cleanup_errors={details}; quarantine_error={type(exc).__name__}: {exc}"
            ) from exc
    shutil.move(str(prepared_slot), str(slot_dir))


def _cleanup_stale_temp_slot_dirs(
    slots_root: Path,
    *,
    min_age_seconds: float = 300.0,
    now: float | None = None,
) -> dict[str, object]:
    root = Path(slots_root).expanduser().resolve()
    current_time = time.time() if now is None else float(now)
    min_age = max(0.0, float(min_age_seconds or 0.0))
    removed_paths: list[str] = []
    skipped_recent_paths: list[str] = []
    failed_paths: list[str] = []

    if not root.exists():
        return {
            "ok": True,
            "root": str(root),
            "removed_total": 0,
            "removed_paths": removed_paths,
            "skipped_recent_total": 0,
            "skipped_recent_paths": skipped_recent_paths,
            "failed_total": 0,
            "failed_paths": failed_paths,
        }

    for child in root.iterdir():
        if child.is_symlink() or not child.is_dir():
            continue
        if child.parent != root:
            continue
        if not child.name.startswith("adaos-core-"):
            continue
        try:
            age_seconds = max(0.0, current_time - float(child.stat().st_mtime))
        except Exception:
            failed_paths.append(str(child))
            continue
        if age_seconds < min_age:
            skipped_recent_paths.append(str(child))
            continue
        try:
            shutil.rmtree(child, ignore_errors=False)
            removed_paths.append(str(child))
        except Exception:
            failed_paths.append(str(child))

    return {
        "ok": not failed_paths,
        "root": str(root),
        "removed_total": len(removed_paths),
        "removed_paths": removed_paths,
        "skipped_recent_total": len(skipped_recent_paths),
        "skipped_recent_paths": skipped_recent_paths,
        "failed_total": len(failed_paths),
        "failed_paths": failed_paths,
    }


def _core_update_hygiene(
    *,
    base_dir: str | os.PathLike[str] = "",
    trigger: str,
    pressure_only: bool,
    tmp_min_age_seconds: float,
) -> dict[str, object]:
    if str(os.getenv("ADAOS_CORE_UPDATE_HYGIENE", "1") or "1").strip().lower() in {"0", "false", "no", "off"}:
        return {"ok": True, "skipped": True, "reason": "disabled_by_env", "trigger": trigger}
    if str(os.getenv("ADAOS_TESTING", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}:
        return {"ok": True, "skipped": True, "reason": "testing_mode", "trigger": trigger}
    try:
        from adaos.services.self_hygiene import run_hygiene

        return run_hygiene(
            base_dir=str(base_dir or ""),
            trigger=trigger,
            pressure_only=pressure_only,
            include_pip_cache=False,
            include_global_tmp=True,
            tmp_min_age_seconds=tmp_min_age_seconds,
            max_paths=48,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "trigger": trigger}


def _migrate_installed_skill_runtimes(
    python_executable: Path,
    *,
    repo_root: str | os.PathLike[str] = "",
    base_dir: str | os.PathLike[str] = "",
    shared_dotenv_path: str | os.PathLike[str] = "",
    run_tests: bool = True,
) -> dict[str, object]:
    env = dict(os.environ)
    repo_root_path = Path(str(repo_root or "")).expanduser().resolve() if str(repo_root or "").strip() else None
    if str(base_dir or "").strip():
        env["ADAOS_BASE_DIR"] = str(base_dir)
    if str(shared_dotenv_path or "").strip():
        env["ADAOS_SHARED_DOTENV_PATH"] = str(shared_dotenv_path)
    if repo_root_path is not None:
        env["ADAOS_SLOT_REPO_ROOT"] = str(repo_root_path)
        python_entries = [str(repo_root_path / "src")]
        existing_pythonpath = str(env.get("PYTHONPATH") or "").strip()
        if existing_pythonpath:
            python_entries.append(existing_pythonpath)
        env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(entry for entry in python_entries if str(entry).strip()))
    migrate_script = repo_root_path / "src" / "adaos" / "apps" / "skill_runtime_migrate.py" if repo_root_path is not None else None
    if migrate_script is not None:
        if not migrate_script.exists():
            apps_dir = migrate_script.parent
            visible = []
            if apps_dir.exists():
                try:
                    visible = sorted(child.name for child in apps_dir.iterdir() if child.is_file())[:20]
                except Exception:
                    visible = []
            return {
                "ok": True,
                "skipped": True,
                "unsupported": True,
                "reason": "missing_skill_runtime_migration_entrypoint",
                "message": (
                    "prepared slot repo does not contain skill runtime migration entrypoint; "
                    "continuing without runtime migration"
                ),
                "repo_root": str(repo_root_path),
                "script_path": str(migrate_script),
                "apps_dir_exists": apps_dir.exists(),
                "visible_files": visible,
                "run_tests": bool(run_tests),
                "failed_total": 0,
                "rollback_total": 0,
                "deactivated_total": 0,
                "deferred": False,
                "skills": [],
            }
        cmd = [str(python_executable), str(migrate_script), "--json"]
    else:
        cmd = [str(python_executable), "-m", "adaos.apps.skill_runtime_migrate", "--json"]
    if not run_tests:
        cmd.append("--skip-tests")
    return _run_json(
        cmd,
        cwd=repo_root_path,
        env=env,
    )


def _clone_repo(repo_url: str, target_rev: str, target_version: str, checkout_dir: Path) -> None:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git is required for core updates but is not installed")
    cmd = [git, "clone", "--depth", "1"]
    if target_rev:
        cmd.extend(["--branch", target_rev])
    cmd.extend([repo_url, str(checkout_dir)])
    _run(cmd)
    _checkout_target_version(checkout_dir, target_rev=target_rev, target_version=target_version)


def _bounded_exception_summary(exc: Exception, *, max_chars: int = 1200) -> str:
    if isinstance(exc, shutil.Error):
        failures = exc.args[0] if exc.args and isinstance(exc.args[0], list) else []
        preview: list[str] = []
        for item in failures[:3]:
            if isinstance(item, tuple) and len(item) >= 3:
                source, target, error = item[:3]
                preview.append(f"{source!s} -> {target!s}: {error!s}")
            else:
                preview.append(str(item))
        text = f"shutil.Error: {len(failures)} copy failure(s)"
        if preview:
            text += "; first failures: " + "; ".join(preview)
    else:
        text = f"{type(exc).__name__}: {exc}"
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max(0, max_chars - 3)] + "..."


def _local_repo_contains_target(source_repo_root: Path, target_version: str) -> bool:
    target = str(target_version or "").strip()
    if not _is_probably_git_sha(target):
        return True
    git = shutil.which("git")
    if not git or not _is_git_repo(source_repo_root):
        return False
    try:
        completed = subprocess.run(
            [git, "-C", str(source_repo_root), "cat-file", "-e", f"{target}^{{commit}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return False
    return completed.returncode == 0


def _clear_failed_checkout(checkout_dir: Path, *, stage: str) -> None:
    if not checkout_dir.exists():
        return
    try:
        _force_remove_tree(checkout_dir)
    except Exception as exc:
        raise RuntimeError(
            f"cannot recover checkout after {stage}: cleanup failed for {checkout_dir}: "
            f"{_bounded_exception_summary(exc)}"
        ) from exc
    if checkout_dir.exists():
        raise RuntimeError(f"cannot recover checkout after {stage}: cleanup left destination present: {checkout_dir}")


def _write_prepared_slot_manifest(slot: str, slot_dir_path: Path, payload: dict[str, object]) -> dict[str, object]:
    manifest = dict(payload)
    manifest["slot"] = str(slot).strip().upper()
    path = Path(slot_dir_path).expanduser().resolve() / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        staged.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)
    return manifest


def _clone_local_repo(source_repo_root: Path, target_rev: str, target_version: str, checkout_dir: Path) -> None:
    git = shutil.which("git")
    git_dir = source_repo_root / ".git"
    immutable_target = _is_probably_git_sha(str(target_version or "").strip())
    checkout_requested = bool(str(target_rev or "").strip()) or immutable_target
    if git and git_dir.exists() and (checkout_requested or not _git_worktree_has_changes(source_repo_root)):
        try:
            _run([git, "clone", str(source_repo_root), str(checkout_dir)])
            if target_rev:
                _run([git, "checkout", target_rev], cwd=checkout_dir)
            _checkout_target_version(checkout_dir, target_rev=target_rev, target_version=target_version)
            return
        except Exception as exc:
            _clear_failed_checkout(checkout_dir, stage="local git clone")
            if immutable_target:
                raise RuntimeError(
                    f"local git clone failed for immutable target {target_version}: "
                    f"{_bounded_exception_summary(exc)}"
                ) from exc
    elif immutable_target:
        raise RuntimeError(
            f"local source cannot provide immutable target {target_version}: git checkout is unavailable"
        )
    shutil.copytree(
        source_repo_root,
        checkout_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*_LOCAL_SOURCE_COPY_IGNORES),
    )


def _validate_checkout_target_version(repo_dir: Path, *, target_version: str, source_label: str) -> None:
    target_version = str(target_version or "").strip()
    if not _is_probably_git_sha(target_version):
        return
    actual = _git_text(repo_dir, "rev-parse", "HEAD")
    if not actual:
        raise RuntimeError(
            f"{source_label} did not produce a verifiable git checkout for requested target_version {target_version}"
        )
    actual_norm = actual.lower()
    target_norm = target_version.lower()
    matches = actual_norm == target_norm or (
        len(target_norm) < 40 and actual_norm.startswith(target_norm)
    )
    if not matches:
        raise RuntimeError(
            f"{source_label} resolved to git commit {actual} instead of requested target_version {target_version}"
        )


def _prepare_checkout_repo(
    *,
    checkout_dir: Path,
    source_repo_dir: Path | None,
    repo_url: str,
    target_rev: str,
    target_version: str,
) -> tuple[str, dict[str, object]]:
    git_available = bool(shutil.which("git"))
    source_exists = source_repo_dir is not None and source_repo_dir.exists()
    source_is_git = _is_git_repo(source_repo_dir)
    local_error: Exception | None = None
    attempts: list[dict[str, object]] = []

    if source_exists and source_is_git and source_repo_dir is not None:
        if not _local_repo_contains_target(source_repo_dir, target_version):
            local_error = RuntimeError(
                f"local source repo does not contain requested target_version {target_version}"
            )
            attempts.append(
                {
                    "source": "local_source_tree",
                    "state": "skipped",
                    "reason": "target_commit_missing",
                    "target_version": target_version,
                }
            )
        else:
            try:
                _clone_local_repo(source_repo_dir, target_rev, target_version, checkout_dir)
                _validate_checkout_target_version(
                    checkout_dir,
                    target_version=target_version,
                    source_label="local source repo",
                )
                attempts.append({"source": "local_source_tree", "state": "succeeded"})
                return "local_source_tree", {"kind": "local_source_tree", "attempts": attempts}
            except Exception as exc:
                local_error = exc
                attempts.append(
                    {
                        "source": "local_source_tree",
                        "state": "failed",
                        "error": _bounded_exception_summary(exc),
                    }
                )
                _clear_failed_checkout(checkout_dir, stage="local source preparation")

    if git_available and repo_url:
        try:
            _clone_repo(repo_url, target_rev, target_version, checkout_dir)
            _validate_checkout_target_version(
                checkout_dir,
                target_version=target_version,
                source_label="remote repo clone",
            )
            attempts.append({"source": "remote_git_clone", "state": "succeeded"})
            return "remote_git_clone", {"kind": "remote_git_clone", "attempts": attempts}
        except Exception as exc:
            attempts.append(
                {
                    "source": "remote_git_clone",
                    "state": "failed",
                    "error": _bounded_exception_summary(exc),
                }
            )
            if local_error is not None:
                raise RuntimeError(
                    f"failed to prepare requested target_version {target_version or '<unspecified>'}: "
                    f"local source repo failed ({_bounded_exception_summary(local_error)}); "
                    f"remote repo clone failed ({_bounded_exception_summary(exc)})"
                ) from exc
            raise

    if source_exists and source_repo_dir is not None:
        if _is_probably_git_sha(str(target_version or "").strip()):
            reason = _bounded_exception_summary(local_error) if local_error is not None else "git checkout is unavailable"
            raise RuntimeError(
                f"cannot prepare immutable target_version {target_version} from a copied source tree: {reason}"
            )
        _clone_local_repo(source_repo_dir, target_rev, target_version, checkout_dir)
        _validate_checkout_target_version(
            checkout_dir,
            target_version=target_version,
            source_label="copied local source tree",
        )
        attempts.append({"source": "local_source_tree", "state": "succeeded", "mode": "copy"})
        return "local_source_tree", {"kind": "local_source_tree", "attempts": attempts}

    _clone_repo(repo_url, target_rev, target_version, checkout_dir)
    _validate_checkout_target_version(
        checkout_dir,
        target_version=target_version,
        source_label="remote repo clone",
    )
    attempts.append({"source": "remote_git_clone", "state": "succeeded"})
    return "remote_git_clone", {"kind": "remote_git_clone", "attempts": attempts}


def _strip_repo_vcs_metadata(repo_dir: Path) -> None:
    git_dir = repo_dir / ".git"
    if git_dir.exists():
        # Git object files are commonly read-only on Windows.  Silent cleanup
        # leaves a partial ``.git/objects`` tree inside the immutable slot and
        # makes its provenance ambiguous, so use the same writeable retry as
        # slot replacement and fail preparation if metadata survives.
        _force_remove_tree(git_dir)
    if git_dir.exists():
        raise RuntimeError(f"prepared slot retains VCS metadata: {git_dir}")


def _path_content_differs(left: Path, right: Path) -> bool:
    left_exists = left.exists()
    right_exists = right.exists()
    if left_exists != right_exists:
        return True
    if not left_exists:
        return False
    if left.is_dir() or right.is_dir():
        return left.is_dir() != right.is_dir()
    try:
        left_bytes = left.read_bytes()
        right_bytes = right.read_bytes()
    except Exception:
        return True
    if left_bytes == right_bytes:
        return False
    try:
        left_text = left_bytes.decode("utf-8")
        right_text = right_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return True
    # A Windows working checkout can use CRLF while the detached candidate
    # clone uses LF.  That is not a bootstrap change and must not force a root
    # promotion which only rewrites line endings in the user's checkout.
    return left_text.replace("\r\n", "\n") != right_text.replace("\r\n", "\n")


def _detect_bootstrap_promotion_requirement(candidate_repo_dir: Path, repo_root: Path | None) -> dict[str, object]:
    checked_paths = list(BOOTSTRAP_CRITICAL_PATHS)
    if repo_root is None or not repo_root.exists():
        return {
            "required": False,
            "basis": "repo_root_unavailable",
            "checked_paths": checked_paths,
            "changed_paths": [],
        }
    changed_paths: list[str] = []
    for rel_path in checked_paths:
        if _path_content_differs(candidate_repo_dir / rel_path, repo_root / rel_path):
            changed_paths.append(rel_path)
    return {
        "required": bool(changed_paths),
        "basis": "path_compare",
        "checked_paths": checked_paths,
        "changed_paths": changed_paths,
    }


def _is_git_repo(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return (path / ".git").exists()
    except Exception:
        return False


def _git_text(repo_dir: Path, *args: str) -> str:
    git = shutil.which("git")
    if not git or not _is_git_repo(repo_dir):
        return ""
    try:
        completed = subprocess.run(
            [git, "-C", str(repo_dir), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return (completed.stdout or "").strip()
    except Exception:
        return ""


def _checkout_base_version(repo_dir: Path) -> str:
    pyproject_path = repo_dir / "pyproject.toml"
    try:
        payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except Exception:
        payload = None
    project = payload.get("project") if isinstance(payload, dict) else None
    if isinstance(project, dict):
        version = str(project.get("version") or "").strip()
        if version:
            return version
    explicit = str(os.getenv("ADAOS_BASE_VERSION") or "").strip()
    if explicit:
        return explicit
    return "0.1.0"


def _checkout_build_version(repo_dir: Path) -> str:
    explicit = str(os.getenv("ADAOS_BUILD_VERSION") or "").strip()
    if explicit:
        return explicit
    base = _checkout_base_version(repo_dir)
    rev_count = _git_text(repo_dir, "rev-list", "--count", "HEAD")
    if not rev_count:
        return base
    short_sha = _git_text(repo_dir, "rev-parse", "--short", "HEAD")
    suffix = f"+{rev_count}"
    if short_sha:
        suffix += f".{short_sha}"
    return f"{base}{suffix}"


def _checkout_build_date(repo_dir: Path) -> str:
    return _git_text(repo_dir, "show", "-s", "--format=%cI", "HEAD")


_PREPARED_SLOT_IMPORT_MODULES: tuple[str, ...] = (
    "adaos.apps.supervisor",
    "adaos.services.core_update_policy",
    "adaos.services.realtime_sidecar",
    "adaos.services.nats_config",
    "adaos.services.nats_ws_transport",
    "adaos.services.runtime_dotenv",
    "adaos.services.runtime_paths",
    "adaos.services.runtime_refresh",
    "adaos.services.node_display",
    "adaos.services.node_runtime_state",
    "adaos.services.scenario.webspace_runtime",
    "adaos.services.subnet.link_client",
    "adaos.services.subnet.link_manager",
    "adaos.apps.cli.commands.setup",
    "adaos.apps.cli.commands.skill",
)
_PREPARED_SLOT_IMPORT_TIMEOUT_ENV = "ADAOS_CORE_UPDATE_IMPORT_VALIDATE_TIMEOUT_SEC"
_PREPARED_SLOT_IMPORT_TIMEOUT_DEFAULT_SEC = 300.0


def _prepared_slot_import_timeout_sec() -> float:
    return env_float(
        _PREPARED_SLOT_IMPORT_TIMEOUT_ENV,
        _PREPARED_SLOT_IMPORT_TIMEOUT_DEFAULT_SEC,
        minimum=10.0,
        maximum=900.0,
    )


def _validate_prepared_slot_imports(python_bin: Path) -> dict[str, object]:
    modules = list(_PREPARED_SLOT_IMPORT_MODULES)
    script = (
        "import importlib, json\n"
        f"modules = {json.dumps(modules)}\n"
        "loaded = []\n"
        "for name in modules:\n"
        "    importlib.import_module(name)\n"
        "    loaded.append(name)\n"
        "print(json.dumps({'ok': True, 'modules': loaded}))\n"
    )
    env = dict(os.environ)
    # Validate the installed package, not the slot repo PYTHONPATH overlay.
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [str(python_bin), "-c", script],
        capture_output=True,
        text=True,
        timeout=_prepared_slot_import_timeout_sec(),
        env=env,
    )
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"prepared slot import validation failed: {details}")
    try:
        payload = json.loads((completed.stdout or "").strip() or "{}")
    except Exception:
        payload = {"ok": True, "modules": modules, "raw": completed.stdout}
    return {
        "ok": True,
        "modules": list(payload.get("modules") or modules) if isinstance(payload, dict) else modules,
    }


def prepare_slot(
    *,
    slot: str,
    slot_dir_path: str | os.PathLike[str],
    base_dir: str | os.PathLike[str] = "",
    repo_root: str | os.PathLike[str] = "",
    source_repo_root: str | os.PathLike[str] = "",
    shared_dotenv_path: str | os.PathLike[str] = "",
    target_rev: str = "",
    target_version: str = "",
    repo_url: str | None = None,
    migrate_skill_runtimes: bool = True,
    prepare_lease_path: str | os.PathLike[str] = "",
    prepare_lease_token: str = "",
) -> dict[str, object]:
    slot_name = str(slot).strip().upper()
    slot_dir = Path(slot_dir_path).expanduser().resolve()
    slot_dir.mkdir(parents=True, exist_ok=True)
    try:
        cleanup_min_age_seconds = float(
            str(os.getenv("ADAOS_CORE_SLOT_TMP_CLEANUP_MIN_AGE_S", "300") or "300").strip() or "300"
        )
    except Exception:
        cleanup_min_age_seconds = 300.0
    _cleanup_stale_temp_slot_dirs(
        slot_dir.parent,
        min_age_seconds=cleanup_min_age_seconds,
    )
    preflight_hygiene = _core_update_hygiene(
        base_dir=str(base_dir or ""),
        trigger="core_update.preflight",
        pressure_only=True,
        tmp_min_age_seconds=6 * 3600.0,
    )
    repo_root_dir = Path(str(repo_root or "")).expanduser().resolve() if str(repo_root or "").strip() else None
    target_rev = str(target_rev or "").strip()
    target_version = str(target_version or "").strip()
    if repo_url is None:
        repo_url = str(os.getenv("ADAOS_CORE_UPDATE_REPO_URL", "https://github.com/inimatic/adaos.git")).strip()
    else:
        repo_url = str(repo_url).strip()
    requested_target_version = target_version
    resolved_target_version = ""
    target_resolution = "request"
    if _is_probably_git_sha(target_version):
        # A SHA is an immutable rollout pin. Never replace it with the current
        # branch head: doing so can install a different build than requested.
        resolved_target_version = target_version
        target_resolution = "pinned_commit"
    elif target_rev and repo_url:
        resolved_target_version = _resolve_branch_head(repo_url, target_rev)
        if resolved_target_version:
            target_version = resolved_target_version
            target_resolution = "remote_branch_head"
    source_repo_dir = Path(str(source_repo_root or "")).expanduser().resolve() if str(source_repo_root or "").strip() else None
    shared_dotenv = str(shared_dotenv_path or "").strip()
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"adaos-core-{slot_name.lower()}-", dir=str(slot_dir.parent)))
    prepared_slot = tmp_dir / slot_name
    prepared_slot.mkdir(parents=True, exist_ok=True)
    try:
        checkout_tmp = prepared_slot / "repo"
        checkout_result = _prepare_checkout_repo(
            checkout_dir=checkout_tmp,
            source_repo_dir=source_repo_dir,
            repo_url=repo_url,
            target_rev=target_rev,
            target_version=target_version,
        )
        if isinstance(checkout_result, tuple):
            source_kind, source_checkout = checkout_result
        else:
            source_kind = str(checkout_result)
            source_checkout = {"kind": source_kind, "attempts": []}
        venv_tmp = prepared_slot / "venv"
        venv_seed = _prepare_seed_venv(
            venv_dir=venv_tmp,
            slot_dir=slot_dir,
            repo_root_dir=repo_root_dir,
            checkout_dir=checkout_tmp,
        )
        install_result = _install_slot_project(
            checkout_dir=checkout_tmp,
            venv_dir=venv_tmp,
            seed=venv_seed,
        )

        final_repo_dir = slot_dir / "repo"
        final_venv_dir = slot_dir / "venv"
        original_venv_dir = venv_tmp.resolve()
        final_py = _venv_python(final_venv_dir)
        git_commit = _git_text(checkout_tmp, "rev-parse", "HEAD")
        git_short_commit = _git_text(checkout_tmp, "rev-parse", "--short", "HEAD")
        git_branch = _git_text(checkout_tmp, "rev-parse", "--abbrev-ref", "HEAD")
        git_subject = _git_text(checkout_tmp, "show", "-s", "--format=%s", "HEAD")
        build_version = _checkout_build_version(checkout_tmp)
        base_version = _checkout_base_version(checkout_tmp)
        build_date = _checkout_build_date(checkout_tmp)
        bootstrap_update = _detect_bootstrap_promotion_requirement(checkout_tmp, repo_root_dir)
        _strip_repo_vcs_metadata(checkout_tmp)
        _verify_prepare_lease(prepare_lease_path, prepare_lease_token)
        manifest = {
            "slot": slot_name,
            "created_at": time.time(),
            "target_rev": target_rev,
            "target_version": str(target_version or "").strip(),
            "requested_target_version": str(requested_target_version or "").strip(),
            "resolved_target_version": str(resolved_target_version or "").strip(),
            "target_resolution": target_resolution,
            "root_repo_root": str(repo_root_dir) if repo_root_dir is not None else "",
            "source_kind": source_kind,
            "source_checkout": source_checkout,
            "source_repo_root": str(source_repo_dir) if source_repo_dir is not None else "",
            "repo_url": repo_url,
            "repo_dir": str(final_repo_dir),
            "venv_dir": str(final_venv_dir),
            "base_version": base_version,
            "build_version": build_version,
            "build_date": build_date,
            "git_commit": git_commit,
            "git_short_commit": git_short_commit,
            "git_branch": git_branch,
            "git_subject": git_subject,
            "bootstrap_update": bootstrap_update,
            "venv_seed": venv_seed,
            "install": install_result,
            "cwd": str(final_repo_dir),
            "argv": [
                str(final_py),
                "-m",
                "adaos.apps.autostart_runner",
                "--host",
                "{host}",
                "--port",
                "{port}",
            ],
            "env": {
                "ADAOS_BASE_DIR": str(base_dir or ""),
                "ADAOS_SLOT_REPO_ROOT": str(final_repo_dir),
                "ADAOS_SHARED_DOTENV_PATH": shared_dotenv,
                "PYTHONPATH": str(final_repo_dir / "src"),
                "PYTHONUNBUFFERED": "1",
            },
            "self_hygiene": {
                "preflight": preflight_hygiene,
            },
        }
        _replace_slot_dir(prepared_slot, slot_dir)
        repair = _repair_moved_venv(
            final_venv_dir,
            original_venv_dir=original_venv_dir,
            original_repo_dir=checkout_tmp.resolve(),
            final_repo_dir=final_repo_dir.resolve(),
        )
        manifest["venv_repair"] = repair
        manifest["import_validation"] = _validate_prepared_slot_imports(final_py)
        if migrate_skill_runtimes:
            skill_runtime_migration = _migrate_installed_skill_runtimes(
                final_py,
                repo_root=str(final_repo_dir),
                base_dir=str(base_dir or ""),
                shared_dotenv_path=shared_dotenv,
                run_tests=True,
            )
            if not bool(skill_runtime_migration.get("ok")) and not bool(skill_runtime_migration.get("safe_for_core_update")):
                failed = []
                for item in skill_runtime_migration.get("skills") or []:
                    if not isinstance(item, dict) or bool(item.get("ok")):
                        continue
                    failed.append(
                        f"{item.get('skill') or 'skill'}:{item.get('failed_stage') or 'failed'}"
                    )
                suffix = ", ".join(failed[:5])
                if len(failed) > 5:
                    suffix += f" (+{len(failed) - 5} more)"
                if suffix:
                    raise RuntimeError(f"installed skill runtime migration failed: {suffix}")
                raise RuntimeError(
                    f"installed skill runtime migration failed: {json.dumps(skill_runtime_migration, ensure_ascii=False)}"
                )
        else:
            skill_runtime_migration = {
                "ok": True,
                "total": 0,
                "failed_total": 0,
                "rollback_total": 0,
                "deactivated_total": 0,
                "run_tests": False,
                "deferred": True,
                "skills": [],
            }
        manifest["skill_runtime_migration"] = skill_runtime_migration
        manifest["self_hygiene"]["post_prepare"] = _core_update_hygiene(
            base_dir=str(base_dir or ""),
            trigger="core_update.post_prepare",
            pressure_only=False,
            tmp_min_age_seconds=3600.0,
        )
        return _write_prepared_slot_manifest(slot_name, slot_dir, manifest)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _prepare_slot(args: argparse.Namespace) -> dict[str, object]:
    return prepare_slot(
        slot=args.slot,
        slot_dir_path=args.slot_dir,
        base_dir=args.base_dir,
        repo_root=args.repo_root,
        source_repo_root=args.source_repo_root,
        shared_dotenv_path=args.shared_dotenv_path,
        target_rev=args.target_rev,
        target_version=args.target_version,
        repo_url=args.repo_url,
        prepare_lease_path=args.prepare_lease_path,
        prepare_lease_token=args.prepare_lease_token,
    )


def main() -> None:
    args = _parse_args()
    manifest = _prepare_slot(args)
    print(json.dumps({"ok": True, "slot": args.slot, "manifest": manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
