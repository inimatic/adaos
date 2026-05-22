from __future__ import annotations

import fnmatch
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from adaos.services.runtime_paths import current_base_dir, current_logs_dir


MANAGED_HEADER = "# Managed by AdaOS self_hygiene. Local edits may be replaced.\n"
GiB = 1024 * 1024 * 1024
MiB = 1024 * 1024

DEFAULT_POLICY: dict[str, Any] = {
    "version": 1,
    "min_free_bytes": 2 * GiB,
    "pressure_free_bytes": int(1.5 * GiB),
    "warn_used_percent": 85.0,
    "pressure_used_percent": 92.0,
    "journald_system_max_use": "512M",
    "journald_system_keep_free": "2G",
    "journald_max_retention": "7day",
    "adaos_tmp_max_age": "3d",
    "system_tmp_max_age": "3d",
    "logs_rotate_size": "100M",
    "logs_rotate_keep": 7,
    "managed_backup_keep_days": 7,
    "managed_backup_keep_latest": 3,
}

_TRUTHY = {"1", "true", "yes", "on"}
_GLOBAL_TMP_PATTERNS = (
    "pip-unpack-*",
    "pip-install-*",
    "pip-metadata-*",
    "pip-ephem-wheel-cache-*",
    "pip-build-tracker-*",
)
_GLOBAL_TMP_LARGE_FILE_PATTERNS = ("tmp*",)


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def _policy(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = dict(DEFAULT_POLICY)
    if overrides:
        for key, value in overrides.items():
            if value is not None:
                policy[key] = value
    return policy


def _resolve_base_dir(base_dir: str | os.PathLike[str] | None = None) -> Path:
    if base_dir is not None and str(base_dir).strip():
        return Path(base_dir).expanduser().resolve()
    return current_base_dir()


def _resolve_logs_dir(base_dir: Path, logs_dir: str | os.PathLike[str] | None = None) -> Path:
    if logs_dir is not None and str(logs_dir).strip():
        return Path(logs_dir).expanduser().resolve()
    try:
        return current_logs_dir()
    except Exception:
        return (base_dir / "logs").resolve()


def _state_dir(base_dir: Path) -> Path:
    return (base_dir / "state" / "self_hygiene").resolve()


def _testing_mode() -> bool:
    return _truthy(os.getenv("ADAOS_TESTING"))


def _is_linux() -> bool:
    return platform.system().lower() == "linux"


def _is_windows() -> bool:
    return platform.system().lower() == "windows"


def _is_root() -> bool:
    if os.name == "nt":
        return False
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return False
    try:
        return int(geteuid()) == 0
    except Exception:
        return False


def _bytes_to_human(value: int) -> str:
    amount = float(max(0, int(value)))
    for suffix in ("B", "K", "M", "G", "T"):
        if amount < 1024.0 or suffix == "T":
            if suffix == "B":
                return f"{int(amount)}B"
            return f"{amount:.1f}{suffix}"
        amount /= 1024.0
    return f"{amount:.1f}T"


def disk_health(
    *,
    base_dir: str | os.PathLike[str] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = _resolve_base_dir(base_dir)
    effective = _policy(policy)
    try:
        usage = shutil.disk_usage(base if base.exists() else base.parent)
    except Exception:
        usage = shutil.disk_usage(Path.cwd())
    used_percent = 0.0
    if usage.total:
        used_percent = (float(usage.used) / float(usage.total)) * 100.0
    pressure = bool(
        usage.free < int(effective["pressure_free_bytes"])
        or used_percent >= float(effective["pressure_used_percent"])
    )
    warning = bool(
        pressure
        or usage.free < int(effective["min_free_bytes"])
        or used_percent >= float(effective["warn_used_percent"])
    )
    return {
        "ok": not pressure,
        "base_dir": str(base),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "free_human": _bytes_to_human(usage.free),
        "used_percent": round(used_percent, 2),
        "warning": warning,
        "pressure": pressure,
        "thresholds": {
            "min_free_bytes": int(effective["min_free_bytes"]),
            "pressure_free_bytes": int(effective["pressure_free_bytes"]),
            "warn_used_percent": float(effective["warn_used_percent"]),
            "pressure_used_percent": float(effective["pressure_used_percent"]),
        },
    }


def _write_text(path: Path, text: str, *, dry_run: bool) -> dict[str, Any]:
    existed = path.exists()
    old = None
    if existed:
        try:
            old = path.read_text(encoding="utf-8")
        except Exception:
            old = None
    changed = old != text
    if not dry_run and changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return {
        "path": str(path),
        "existed": existed,
        "changed": changed,
        "dry_run": dry_run,
    }


def _path_for_unit(value: Path) -> str:
    text = str(value)
    if " " in text or "\t" in text:
        return f'"{text}"'
    return text


def _journald_config(policy: dict[str, Any]) -> str:
    return (
        MANAGED_HEADER
        + "[Journal]\n"
        + f"SystemMaxUse={policy['journald_system_max_use']}\n"
        + f"SystemKeepFree={policy['journald_system_keep_free']}\n"
        + f"MaxRetentionSec={policy['journald_max_retention']}\n"
    )


def _tmpfiles_config(base_dir: Path, policy: dict[str, Any]) -> str:
    base_tmp = (base_dir / "tmp").resolve()
    lines = [
        MANAGED_HEADER.rstrip("\n"),
        f"d {_path_for_unit(base_tmp)} 0755 root root {policy['adaos_tmp_max_age']}",
    ]
    for pattern in _GLOBAL_TMP_PATTERNS:
        lines.append(f"R /tmp/{pattern} - - - {policy['system_tmp_max_age']}")
    return "\n".join(lines) + "\n"


def _logrotate_config(logs_dir: Path, policy: dict[str, Any]) -> str:
    rotate_keep = int(policy["logs_rotate_keep"])
    size = str(policy["logs_rotate_size"])
    log_globs = " ".join(
        [
            _path_for_unit((logs_dir / "*.log").resolve()),
            _path_for_unit((logs_dir / "*.jsonl").resolve()),
        ]
    )
    return (
        MANAGED_HEADER
        + f"{log_globs} {{\n"
        + f"    size {size}\n"
        + f"    rotate {rotate_keep}\n"
        + "    compress\n"
        + "    missingok\n"
        + "    notifempty\n"
        + "    copytruncate\n"
        + "}\n"
    )


def _systemd_service(base_dir: Path) -> str:
    python = _path_for_unit(Path(sys.executable).resolve())
    return (
        MANAGED_HEADER
        + "[Unit]\n"
        + "Description=AdaOS self hygiene\n\n"
        + "[Service]\n"
        + "Type=oneshot\n"
        + f"Environment=ADAOS_BASE_DIR={base_dir}\n"
        + f"ExecStart={python} -m adaos maintenance run --pressure-only --json\n"
    )


def _systemd_timer() -> str:
    return (
        MANAGED_HEADER
        + "[Unit]\n"
        + "Description=Run AdaOS self hygiene daily\n\n"
        + "[Timer]\n"
        + "OnBootSec=15min\n"
        + "OnUnitActiveSec=1d\n"
        + "Persistent=true\n\n"
        + "[Install]\n"
        + "WantedBy=timers.target\n"
    )


def _run_command(cmd: list[str], *, timeout: float = 10.0, dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        return {"cmd": cmd, "dry_run": True, "skipped": True}
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return {"cmd": cmd, "ok": False, "error": str(exc)}
    return {
        "cmd": cmd,
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "")[-2000:],
        "stderr": (completed.stderr or "")[-2000:],
    }


def apply_retention_policy(
    *,
    base_dir: str | os.PathLike[str] | None = None,
    logs_dir: str | os.PathLike[str] | None = None,
    dry_run: bool = False,
    enable_timer: bool = True,
    system_etc_dir: str | os.PathLike[str] | None = None,
    systemd_dir: str | os.PathLike[str] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = _resolve_base_dir(base_dir)
    logs = _resolve_logs_dir(base, logs_dir)
    effective = _policy(policy)
    actions: list[dict[str, Any]] = []
    warnings: list[str] = []
    os_policy: dict[str, Any]

    state_payload = {
        "policy": effective,
        "base_dir": str(base),
        "logs_dir": str(logs),
        "platform": platform.system(),
        "updated_at": time.time(),
        "enable_timer": bool(enable_timer),
    }

    if not _is_linux():
        os_policy = {
            "ok": True,
            "supported": False,
            "skipped": True,
            "reason": "windows_local_state_only" if _is_windows() else "unsupported_platform",
        }
    elif _testing_mode() and system_etc_dir is None:
        os_policy = {
            "ok": True,
            "supported": True,
            "skipped": True,
            "reason": "testing_mode_without_system_etc_dir",
        }
    elif not _is_root() and system_etc_dir is None:
        os_policy = {
            "ok": True,
            "supported": True,
            "skipped": True,
            "reason": "root_required_for_system_policy",
        }
    else:
        etc = Path(system_etc_dir or "/etc").expanduser().resolve()
        systemd_root = Path(systemd_dir or "/etc/systemd/system").expanduser().resolve()
        targets = [
            (etc / "systemd" / "journald.conf.d" / "adaos-retention.conf", _journald_config(effective)),
            (etc / "tmpfiles.d" / "adaos.conf", _tmpfiles_config(base, effective)),
            (etc / "logrotate.d" / "adaos", _logrotate_config(logs, effective)),
        ]
        if enable_timer:
            targets.extend(
                [
                    (systemd_root / "adaos-hygiene.service", _systemd_service(base)),
                    (systemd_root / "adaos-hygiene.timer", _systemd_timer()),
                ]
            )
        failed: list[dict[str, Any]] = []
        for path, text in targets:
            try:
                action = _write_text(path, text, dry_run=dry_run)
                actions.append(action)
            except Exception as exc:
                item = {"path": str(path), "ok": False, "error": str(exc)}
                failed.append(item)
                actions.append(item)
        commands: list[dict[str, Any]] = []
        if enable_timer and not failed and shutil.which("systemctl") and system_etc_dir is None and systemd_dir is None:
            commands.append(_run_command(["systemctl", "daemon-reload"], dry_run=dry_run))
            commands.append(_run_command(["systemctl", "enable", "--now", "adaos-hygiene.timer"], dry_run=dry_run))
        os_policy = {
            "ok": not failed and all(bool(cmd.get("ok", True)) for cmd in commands),
            "supported": True,
            "skipped": False,
            "actions": actions,
            "commands": commands,
        }
        if failed:
            warnings.append("failed to write one or more OS retention files")

    state_payload["os_policy"] = os_policy
    state_write = _write_text(
        _state_dir(base) / "retention-policy.json",
        json.dumps(state_payload, ensure_ascii=False, indent=2) + "\n",
        dry_run=dry_run,
    )

    return {
        "ok": bool(os_policy.get("ok", True)) and not warnings,
        "dry_run": dry_run,
        "base_dir": str(base),
        "logs_dir": str(logs),
        "policy": effective,
        "os_policy": os_policy,
        "state": state_write,
        "warnings": warnings,
    }


def retention_policy_status(
    *,
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    base = _resolve_base_dir(base_dir)
    path = _state_dir(base) / "retention-policy.json"
    if not path.exists():
        return {"configured": False, "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception as exc:
        return {"configured": False, "path": str(path), "error": str(exc)}
    return {"configured": True, "path": str(path), "payload": payload}


def _safe_remove_path(path: Path, *, dry_run: bool) -> dict[str, Any]:
    try:
        size = _path_size(path)
    except Exception:
        size = 0
    item = {"path": str(path), "bytes": int(size), "dry_run": dry_run}
    if dry_run:
        item["removed"] = False
        return item
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        item["removed"] = True
    except Exception as exc:
        item["removed"] = False
        item["error"] = str(exc)
    return item


def _path_size(path: Path) -> int:
    if path.is_file() or path.is_symlink():
        return int(path.stat().st_size)
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() or child.is_symlink():
                total += int(child.stat().st_size)
        except Exception:
            continue
    return total


def _is_old_enough(path: Path, *, now: float, min_age_seconds: float) -> bool:
    try:
        return (now - float(path.stat().st_mtime)) >= min_age_seconds
    except Exception:
        return False


def _clean_children(
    root: Path,
    *,
    now: float,
    min_age_seconds: float,
    dry_run: bool,
    patterns: Iterable[str] | None = None,
    large_tmp_files_only: bool = False,
    max_paths: int = 128,
) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    removed: list[dict[str, Any]] = []
    skipped: list[str] = []
    if not root.exists() or not root.is_dir():
        return {
            "ok": True,
            "root": str(root),
            "removed_total": 0,
            "removed": removed,
            "skipped_total": 0,
            "skipped": skipped,
        }

    pattern_list = tuple(patterns or ("*",))
    for child in root.iterdir():
        if len(removed) >= max_paths:
            skipped.append(str(child))
            continue
        name = child.name
        if not any(fnmatch.fnmatch(name, pattern) for pattern in pattern_list):
            continue
        if not _is_old_enough(child, now=now, min_age_seconds=min_age_seconds):
            skipped.append(str(child))
            continue
        if large_tmp_files_only:
            try:
                if not child.is_file() or int(child.stat().st_size) < 100 * MiB:
                    skipped.append(str(child))
                    continue
            except Exception:
                skipped.append(str(child))
                continue
        removed.append(_safe_remove_path(child, dry_run=dry_run))
    ok = all(bool(item.get("removed")) or bool(item.get("dry_run")) for item in removed)
    return {
        "ok": ok,
        "root": str(root),
        "removed_total": len(removed),
        "removed_bytes": sum(int(item.get("bytes") or 0) for item in removed),
        "removed": removed,
        "skipped_total": len(skipped),
        "skipped": skipped[:20],
    }


def _clean_pip_cache(*, dry_run: bool, timeout: float = 20.0) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    commands.append(_run_command([sys.executable, "-m", "pip", "cache", "purge"], timeout=timeout, dry_run=dry_run))
    uv = shutil.which("uv")
    if uv:
        commands.append(_run_command([uv, "cache", "clean"], timeout=timeout, dry_run=dry_run))
    return {
        "ok": all(bool(cmd.get("ok", True)) for cmd in commands),
        "commands": commands,
    }


def _managed_backup_marker(root: Path) -> Path | None:
    marker = root / ".adaos-managed-backup"
    if marker.exists():
        return marker
    policy_marker = root / ".adaos-retention.json"
    if policy_marker.exists():
        return policy_marker
    return None


def _backup_roots_from_env() -> list[Path]:
    raw = str(os.getenv("ADAOS_HYGIENE_BACKUP_ROOTS") or "").strip()
    if not raw:
        return []
    return [Path(part).expanduser().resolve() for part in raw.split(os.pathsep) if part.strip()]


def _clean_managed_backup_root(
    root: Path,
    *,
    now: float,
    dry_run: bool,
    keep_days: int,
    keep_latest: int,
) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    marker = _managed_backup_marker(root)
    if marker is None:
        return {
            "ok": True,
            "root": str(root),
            "managed": False,
            "skipped": True,
            "reason": "missing_adaos_managed_backup_marker",
        }
    if not root.exists() or not root.is_dir():
        return {"ok": True, "root": str(root), "managed": True, "skipped": True, "reason": "missing_root"}
    entries = []
    for child in root.iterdir():
        if child.name.startswith(".adaos-"):
            continue
        if not child.is_dir() or child.is_symlink():
            continue
        try:
            entries.append((float(child.stat().st_mtime), child))
        except Exception:
            continue
    entries.sort(reverse=True)
    keep_paths = {child for _, child in entries[: max(0, keep_latest)]}
    cutoff = now - max(0, keep_days) * 86400.0
    removed = []
    for mtime, child in entries:
        if child in keep_paths or mtime >= cutoff:
            continue
        removed.append(_safe_remove_path(child, dry_run=dry_run))
    return {
        "ok": all(bool(item.get("removed")) or bool(item.get("dry_run")) for item in removed),
        "root": str(root),
        "managed": True,
        "marker": str(marker),
        "removed_total": len(removed),
        "removed_bytes": sum(int(item.get("bytes") or 0) for item in removed),
        "removed": removed,
        "kept_latest": len(keep_paths),
    }


def run_hygiene(
    *,
    base_dir: str | os.PathLike[str] | None = None,
    trigger: str = "manual",
    dry_run: bool = False,
    pressure_only: bool = False,
    include_pip_cache: bool = True,
    include_global_tmp: bool = True,
    backup_roots: Iterable[str | os.PathLike[str]] | None = None,
    global_tmp_roots: Iterable[str | os.PathLike[str]] | None = None,
    tmp_min_age_seconds: float | None = None,
    max_paths: int = 128,
    policy: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    base = _resolve_base_dir(base_dir)
    effective = _policy(policy)
    current_time = time.time() if now is None else float(now)
    health = disk_health(base_dir=base, policy=effective)
    pressure = bool(health.get("pressure"))
    actions: dict[str, Any] = {}
    if pressure_only and not pressure:
        return {
            "ok": True,
            "skipped": True,
            "reason": "disk_pressure_not_detected",
            "trigger": trigger,
            "dry_run": dry_run,
            "health": health,
            "actions": actions,
        }

    min_age = float(tmp_min_age_seconds if tmp_min_age_seconds is not None else (6 * 3600 if pressure else 24 * 3600))
    base_tmp = (base / "tmp").resolve()
    actions["adaos_tmp"] = _clean_children(
        base_tmp,
        now=current_time,
        min_age_seconds=min_age,
        dry_run=dry_run,
        max_paths=max_paths,
    )

    if include_global_tmp and _is_linux():
        roots = [Path(path).expanduser().resolve() for path in (global_tmp_roots or ["/tmp"])]
        tmp_actions = []
        for root in roots:
            tmp_actions.append(
                _clean_children(
                    root,
                    now=current_time,
                    min_age_seconds=min_age,
                    dry_run=dry_run,
                    patterns=_GLOBAL_TMP_PATTERNS,
                    max_paths=max_paths,
                )
            )
            tmp_actions.append(
                _clean_children(
                    root,
                    now=current_time,
                    min_age_seconds=min_age,
                    dry_run=dry_run,
                    patterns=_GLOBAL_TMP_LARGE_FILE_PATTERNS,
                    large_tmp_files_only=True,
                    max_paths=max_paths,
                )
            )
        actions["system_tmp"] = tmp_actions

    if include_pip_cache and (pressure or not pressure_only):
        actions["pip_cache"] = _clean_pip_cache(dry_run=dry_run, timeout=10.0 if pressure else 20.0)

    roots = list(backup_roots or _backup_roots_from_env())
    if roots:
        actions["managed_backups"] = [
            _clean_managed_backup_root(
                Path(root),
                now=current_time,
                dry_run=dry_run,
                keep_days=int(effective["managed_backup_keep_days"]),
                keep_latest=int(effective["managed_backup_keep_latest"]),
            )
            for root in roots
        ]

    state_payload = {
        "trigger": trigger,
        "dry_run": dry_run,
        "started_at": current_time,
        "base_dir": str(base),
        "health": health,
        "actions": actions,
    }
    state_path = _state_dir(base) / "last-run.json"
    try:
        state_payload["state"] = _write_text(
            state_path,
            json.dumps(state_payload, ensure_ascii=False, indent=2) + "\n",
            dry_run=dry_run,
        )
    except Exception as exc:
        state_payload["state"] = {"path": str(state_path), "ok": False, "error": str(exc)}

    ok = True
    for value in actions.values():
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict) and item.get("ok") is False:
                ok = False
    state_payload["ok"] = ok
    return state_payload


def status(*, base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    base = _resolve_base_dir(base_dir)
    return {
        "ok": True,
        "base_dir": str(base),
        "platform": platform.system(),
        "disk": disk_health(base_dir=base),
        "retention_policy": retention_policy_status(base_dir=base),
    }


__all__ = [
    "DEFAULT_POLICY",
    "apply_retention_policy",
    "disk_health",
    "retention_policy_status",
    "run_hygiene",
    "status",
]
