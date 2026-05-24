from __future__ import annotations

import argparse
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
from adaos.services.core_slots import write_slot_manifest


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
    return parser.parse_args()


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    completed = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed rc={completed.returncode}: {' '.join(cmd)}\n"
            f"stdout:\n{completed.stdout[-4000:]}\n"
            f"stderr:\n{completed.stderr[-4000:]}"
        )


def _run_json(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> dict[str, object]:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed rc={completed.returncode}: {' '.join(cmd)}\n"
            f"stdout:\n{completed.stdout[-4000:]}\n"
            f"stderr:\n{completed.stderr[-4000:]}"
        )
    try:
        payload = json.loads(completed.stdout or "{}")
    except Exception as exc:
        raise RuntimeError(f"command returned invalid JSON: {' '.join(cmd)}\nstdout:\n{completed.stdout[-4000:]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"command returned non-object JSON: {' '.join(cmd)}")
    return payload


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
    path.write_text(text.replace(old, new), encoding="utf-8", newline="")
    return True


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
    path.write_text(updated, encoding="utf-8", newline="")
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
        for child in site_packages.rglob("*"):
            if not child.is_file():
                continue
            suffix = child.suffix.lower()
            if suffix in {".pyc", ".pyo", ".so", ".pyd", ".dll", ".dylib", ".a", ".lib"}:
                continue
            try:
                if child.stat().st_size > 2 * 1024 * 1024:
                    continue
            except Exception:
                continue
            paths.append(child)
    return list(dict.fromkeys(paths))


def _repair_moved_venv(
    venv_dir: Path,
    *,
    original_venv_dir: Path,
    original_repo_dir: Path | None = None,
    final_repo_dir: Path | None = None,
) -> dict[str, object]:
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
    }


def _repair_copied_venv(venv_dir: Path, *, source_venv_dir: Path) -> dict[str, object]:
    return _repair_moved_venv(venv_dir, original_venv_dir=source_venv_dir)


def _copy_seed_venv(source_venv_dir: Path, target_venv_dir: Path) -> dict[str, object]:
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
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    try:
        shutil.copytree(source, target, symlinks=True)
    except Exception:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(source, target)
    repair = _repair_copied_venv(target, source_venv_dir=source)
    return {
        "ok": True,
        "seeded": True,
        "source_venv_dir": str(source),
        "target_venv_dir": str(target),
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


def _prepare_seed_venv(
    *,
    venv_dir: Path,
    slot_dir: Path,
    repo_root_dir: Path | None,
) -> dict[str, object]:
    if str(os.getenv("ADAOS_CORE_UPDATE_SEED_VENV", "1") or "1").strip().lower() in {"0", "false", "no", "off"}:
        return {
            "ok": True,
            "seeded": False,
            "source": "disabled",
            "reason": "disabled_by_env",
            "target_venv_dir": str(venv_dir),
        }
    for source_name, source_path in (
        ("active_slot", _active_slot_seed_venv(slot_dir)),
        ("root_venv", _root_seed_venv(repo_root_dir)),
    ):
        if source_path is None:
            continue
        try:
            result = _copy_seed_venv(source_path, venv_dir)
            result["source"] = source_name
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
        cmd = [uv, "sync", "--no-dev", "--python", sys.executable]
        if _uv_locked_enabled():
            cmd.insert(2, "--locked")
        completed = subprocess.run(
            cmd,
            cwd=str(checkout_dir),
            env=env,
            capture_output=True,
            text=True,
        )
        attempts.append(
            {
                "installer": "uv",
                "command": cmd,
                "returncode": int(completed.returncode),
                "stdout_tail": (completed.stdout or "")[-4000:],
                "stderr_tail": (completed.stderr or "")[-4000:],
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

    if not _venv_is_usable(venv_dir):
        _run([sys.executable, "-m", "venv", str(venv_dir)])
    py = _venv_python(venv_dir)
    try:
        _run([str(py), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
        _run([str(py), "-m", "pip", "install", str(checkout_dir)])
    except Exception as first_exc:
        attempts.append(
            {
                "installer": "pip",
                "returncode": 1,
                "error": str(first_exc),
                "error_type": type(first_exc).__name__,
                "after_seed": bool(seed.get("seeded")),
            }
        )
        if bool(seed.get("seeded")):
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
        "seed": seed,
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

    shutil.rmtree(target, ignore_errors=False, onerror=_retry_with_writeable)


def _replace_slot_dir(prepared_slot: Path, slot_dir: Path) -> None:
    if slot_dir.exists():
        try:
            _force_remove_tree(slot_dir)
        except Exception:
            pass
    if slot_dir.exists():
        raise RuntimeError(
            f"slot directory cleanup failed; refusing nested move into existing path: {slot_dir}"
        )
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


def _clone_local_repo(source_repo_root: Path, target_rev: str, target_version: str, checkout_dir: Path) -> None:
    git = shutil.which("git")
    git_dir = source_repo_root / ".git"
    pinned_target = bool(str(target_rev or "").strip()) or _is_probably_git_sha(str(target_version or "").strip())
    if git and git_dir.exists() and (pinned_target or not _git_worktree_has_changes(source_repo_root)):
        try:
            _run([git, "clone", str(source_repo_root), str(checkout_dir)])
            if target_rev:
                _run([git, "checkout", target_rev], cwd=checkout_dir)
            _checkout_target_version(checkout_dir, target_rev=target_rev, target_version=target_version)
            return
        except Exception:
            pass
    shutil.copytree(
        source_repo_root,
        checkout_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            ".git",
            ".adaos",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".coverage",
            "node_modules",
        ),
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
) -> str:
    git_available = bool(shutil.which("git"))
    source_exists = source_repo_dir is not None and source_repo_dir.exists()
    source_is_git = _is_git_repo(source_repo_dir)
    local_error: Exception | None = None

    if source_exists and source_is_git and source_repo_dir is not None:
        try:
            _clone_local_repo(source_repo_dir, target_rev, target_version, checkout_dir)
            _validate_checkout_target_version(
                checkout_dir,
                target_version=target_version,
                source_label="local source repo",
            )
            return "local_source_tree"
        except Exception as exc:
            local_error = exc
            shutil.rmtree(checkout_dir, ignore_errors=True)

    if git_available and repo_url:
        try:
            _clone_repo(repo_url, target_rev, target_version, checkout_dir)
            _validate_checkout_target_version(
                checkout_dir,
                target_version=target_version,
                source_label="remote repo clone",
            )
            return "remote_git_clone"
        except Exception as exc:
            if local_error is not None:
                raise RuntimeError(
                    f"failed to prepare requested target_version {target_version or '<unspecified>'}: "
                    f"local source repo failed ({local_error}); remote repo clone failed ({exc})"
                ) from exc
            raise

    if source_exists and source_repo_dir is not None:
        _clone_local_repo(source_repo_dir, target_rev, target_version, checkout_dir)
        _validate_checkout_target_version(
            checkout_dir,
            target_version=target_version,
            source_label="copied local source tree",
        )
        return "local_source_tree"

    _clone_repo(repo_url, target_rev, target_version, checkout_dir)
    _validate_checkout_target_version(
        checkout_dir,
        target_version=target_version,
        source_label="remote repo clone",
    )
    return "remote_git_clone"


def _strip_repo_vcs_metadata(repo_dir: Path) -> None:
    git_dir = repo_dir / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir, ignore_errors=True)


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
        return left.read_bytes() != right.read_bytes()
    except Exception:
        return True


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
    explicit = str(os.getenv("ADAOS_BASE_VERSION") or "").strip()
    if explicit:
        return explicit
    pyproject_path = repo_dir / "pyproject.toml"
    try:
        payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except Exception:
        return "0.1.0"
    project = payload.get("project") if isinstance(payload, dict) else None
    if not isinstance(project, dict):
        return "0.1.0"
    version = str(project.get("version") or "").strip()
    return version or "0.1.0"


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
        timeout=20,
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
    source_repo_dir = Path(str(source_repo_root or "")).expanduser().resolve() if str(source_repo_root or "").strip() else None
    shared_dotenv = str(shared_dotenv_path or "").strip()
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"adaos-core-{slot_name.lower()}-", dir=str(slot_dir.parent)))
    prepared_slot = tmp_dir / slot_name
    prepared_slot.mkdir(parents=True, exist_ok=True)
    try:
        checkout_tmp = prepared_slot / "repo"
        source_kind = _prepare_checkout_repo(
            checkout_dir=checkout_tmp,
            source_repo_dir=source_repo_dir,
            repo_url=repo_url,
            target_rev=target_rev,
            target_version=target_version,
        )
        venv_tmp = prepared_slot / "venv"
        venv_seed = _prepare_seed_venv(
            venv_dir=venv_tmp,
            slot_dir=slot_dir,
            repo_root_dir=repo_root_dir,
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
        manifest = {
            "slot": slot_name,
            "created_at": time.time(),
            "target_rev": target_rev,
            "target_version": str(target_version or "").strip(),
            "root_repo_root": str(repo_root_dir) if repo_root_dir is not None else "",
            "source_kind": source_kind,
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
        write_slot_manifest(slot_name, manifest)
        return manifest
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
    )


def main() -> None:
    args = _parse_args()
    manifest = _prepare_slot(args)
    print(json.dumps({"ok": True, "slot": args.slot, "manifest": manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
