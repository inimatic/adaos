from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import hashlib
import json
import logging
import os
import site
import subprocess
import sys
import sysconfig
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.request import Request, urlopen

import yaml

from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit
from adaos.services.runtime_identity import runtime_instance_id, runtime_transition_role
from adaos.services.distributed_runtime.membership_supervisor import (
    DistributedServiceMembershipSupervisor,
    ServiceMembershipSpec,
)
from adaos.services.skill.dependency_disk_guard import ensure_dependency_disk_budget
from adaos.services.skill.dependency_requirements import resolve_skill_dependency_args
from adaos.services.skill.runtime_env import SkillRuntimeEnvironment
from adaos.services.skill.service_event_bridge import service_event_bridge_environment
from adaos.domain.blob_storage import BlobStorageRequirements
from adaos.domain.relational_storage import RelationalStorageRequirements
from adaos.services.storage.blob import get_blob_storage_broker
from adaos.services.storage.relational import get_relational_storage_broker

_log = logging.getLogger("adaos.skill.service")


def _bounded_env_seconds(name: str, *, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.getenv(name) or default).strip())
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def _path_discovery_signature(path: Path) -> tuple[str, int, int, int]:
    try:
        stat = path.stat()
        return (str(path), int(stat.st_mtime_ns), int(stat.st_ctime_ns), int(stat.st_size))
    except OSError:
        return (str(path), -1, -1, -1)


def _ensure_failure_cooloff_s(failures: int) -> float:
    try:
        base = float(str(os.getenv("ADAOS_SERVICE_ENSURE_FAILURE_COOLOFF_S") or "15").strip())
    except Exception:
        base = 15.0
    base = max(1.0, min(base, 300.0))
    try:
        multiplier = 2 ** max(0, min(int(failures or 1) - 1, 5))
    except Exception:
        multiplier = 1
    return min(300.0, base * multiplier)


@dataclass(slots=True)
class ServiceSpec:
    skill: str
    skill_root: Path
    host: str
    port: int
    command: list[str]
    workdir: Path
    env_mode: str
    python_selector: str | None
    venv_dir: Path | None
    dependencies: list[str]
    requirements_file: Path | None
    health_path: str
    health_timeout_ms: int

    self_managed_enabled: bool
    crash_max_in_window: int
    crash_window_s: int
    crash_cooloff_s: int
    health_interval_s: int
    health_failures_before_issue: int
    hook_on_issue: str | None
    hook_on_self_heal: str | None
    hook_timeout_s: float

    doctor_enabled: bool
    doctor_cooldown_s: int
    doctor_issue_types: list[str]
    doctor_include_log_tail_lines: int

    ui_enabled: bool = False
    ui_path: str = "/"
    ui_access: str = "authenticated"
    ui_origin_policy: str = "same-origin"
    ui_embedding: str = "external"
    ui_content_security_policy: str = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'"
    )
    ui_max_request_bytes: int = 1024 * 1024
    storage_relational: Mapping[str, Any] | None = None
    storage_blob: Mapping[str, Any] | None = None
    resource_budget: Mapping[str, Any] | None = None
    publish_topics: tuple[str, ...] = ()
    distributed_membership: ServiceMembershipSpec | None = None
    startup_ready_timeout_s: float = 10.0

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


def _read_marker(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    return value or None


def _latest_runtime_version(runtime_root: Path) -> str | None:
    current = _read_marker(runtime_root / "current_version")
    if current:
        return current
    try:
        versions = sorted(child.name for child in runtime_root.iterdir() if child.is_dir() and child.name != "data")
    except Exception:
        return None
    return versions[-1] if versions else None


def _active_runtime_skill_root(skills_root: Path, skill_name: str) -> Path | None:
    env = SkillRuntimeEnvironment(skills_root=skills_root, skill_name=skill_name)
    version = env.resolve_active_version()
    if not version:
        return None
    slot = env.read_active_slot(version)
    root = env.build_slot_paths(version, slot).src_dir / "skills" / skill_name
    return root if (root / "skill.yaml").exists() else None


def _runtime_deactivation(skills_root: Path, skill_name: str) -> dict[str, Any]:
    try:
        payload = SkillRuntimeEnvironment(skills_root=skills_root, skill_name=skill_name).read_deactivation()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _runtime_is_deactivated(skills_root: Path, skill_name: str) -> bool:
    return bool(_runtime_deactivation(skills_root, skill_name).get("deactivated"))


def _infer_runtime_slot_root(skill_root: Path, skill_name: str) -> Path | None:
    try:
        root = skill_root.expanduser().resolve()
    except Exception:
        root = skill_root
    if root.name != skill_name:
        return None
    if root.parent.name != "skills":
        return None
    src_dir = root.parent.parent
    if src_dir.name != "src":
        return None
    slot_root = src_dir.parent
    if slot_root.parent.name != "slots":
        return None
    return slot_root


def _infer_runtime_bucket_root(skill_root: Path, skill_name: str) -> Path | None:
    slot_root = _infer_runtime_slot_root(skill_root, skill_name)
    if slot_root is None:
        return None
    return slot_root.parent.parent


def _read_skill_manifest(skill_root: Path) -> dict:
    skill_yaml = skill_root / "skill.yaml"
    if not skill_yaml.exists():
        return {}
    try:
        return yaml.safe_load(skill_yaml.read_text(encoding="utf-8")) or {}
    except Exception:
        _log.debug("failed to read skill.yaml at %s", skill_yaml, exc_info=True)
        return {}


def _resolve_service_spec(skill_name: str, skill_root: Path, manifest: Mapping[str, Any]) -> ServiceSpec | None:
    runtime = manifest.get("runtime") or {}
    if not isinstance(runtime, Mapping):
        runtime = {}
    kind = runtime.get("kind") or "module"
    if kind != "service":
        return None

    service = manifest.get("service") or {}
    if not isinstance(service, Mapping):
        service = {}

    host = str(service.get("host") or "127.0.0.1")
    port = int(service.get("port") or 0)
    if port <= 0:
        return None

    cmd_raw = service.get("command") or []
    if not isinstance(cmd_raw, list) or not all(isinstance(x, str) and x.strip() for x in cmd_raw):
        return None
    command = [str(x) for x in cmd_raw]

    workdir_raw = service.get("workdir")
    workdir = (skill_root / workdir_raw).resolve() if isinstance(workdir_raw, str) and workdir_raw else skill_root

    deps: list[str] = []
    service_deps = service.get("dependencies")
    dep_list = service_deps if isinstance(service_deps, list) else (manifest.get("dependencies") or [])
    if isinstance(dep_list, list):
        deps = [str(d) for d in dep_list if isinstance(d, str) and d.strip()]

    requirements_file = None
    req_in = skill_root / "requirements.in"
    if req_in.exists():
        requirements_file = req_in

    env_cfg = runtime.get("env") or {}
    if not isinstance(env_cfg, Mapping):
        env_cfg = {}
    explicit_env_mode = env_cfg.get("mode")
    env_mode = str(explicit_env_mode or ("venv" if deps or requirements_file else "global"))
    python_selector = env_cfg.get("python") if isinstance(env_cfg.get("python"), str) else None
    venv_dir_raw = env_cfg.get("venv_dir") if isinstance(env_cfg.get("venv_dir"), str) else None
    if venv_dir_raw:
        raw_venv_dir = Path(venv_dir_raw).expanduser()
        venv_dir = (skill_root / raw_venv_dir).resolve() if not raw_venv_dir.is_absolute() else raw_venv_dir.resolve()
    else:
        bucket_root = _infer_runtime_bucket_root(skill_root, skill_name)
        venv_dir = (bucket_root / "venv").resolve() if env_mode == "venv" and bucket_root is not None else None

    health = service.get("healthcheck") or {}
    if not isinstance(health, Mapping):
        health = {}
    health_path = str(health.get("path") or "/health")
    health_timeout_ms = int(health.get("timeout_ms") or 3000)
    startup_timeout_ms = health.get("startup_timeout_ms")
    if startup_timeout_ms is None:
        startup_ready_timeout_s = _bounded_env_seconds(
            "ADAOS_SERVICE_STARTUP_READY_TIMEOUT_SECONDS",
            default=300.0,
            minimum=5.0,
            maximum=900.0,
        )
    else:
        try:
            startup_ready_timeout_s = float(startup_timeout_ms) / 1000.0
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "service.healthcheck.startup_timeout_ms must be numeric"
            ) from exc
        startup_ready_timeout_s = max(5.0, min(startup_ready_timeout_s, 900.0))
    distributed_membership = ServiceMembershipSpec.from_mapping(
        skill_name,
        service.get("membership"),
    )

    self_managed = service.get("self_managed") or {}
    if not isinstance(self_managed, Mapping):
        self_managed = {}
    self_managed_enabled = bool(self_managed.get("enabled") is True)

    crash_cfg = self_managed.get("crash") or {}
    if not isinstance(crash_cfg, Mapping):
        crash_cfg = {}
    crash_max_in_window = int(crash_cfg.get("max_in_window") or 3)
    crash_window_s = int(crash_cfg.get("window_s") or 60)
    crash_cooloff_s = int(crash_cfg.get("cooloff_s") or 30)

    health_cfg = self_managed.get("health") or {}
    if not isinstance(health_cfg, Mapping):
        health_cfg = {}
    health_interval_s = int(health_cfg.get("interval_s") or 10)
    health_failures_before_issue = int(health_cfg.get("failures_before_issue") or 3)

    hooks_cfg = self_managed.get("hooks") or {}
    if not isinstance(hooks_cfg, Mapping):
        hooks_cfg = {}
    hook_on_issue = hooks_cfg.get("on_issue") if isinstance(hooks_cfg.get("on_issue"), str) else None
    hook_on_self_heal = hooks_cfg.get("on_self_heal") if isinstance(hooks_cfg.get("on_self_heal"), str) else None
    hook_timeout_s = float(hooks_cfg.get("timeout_s") or 10.0)

    doctor_cfg = self_managed.get("doctor") or {}
    if not isinstance(doctor_cfg, Mapping):
        doctor_cfg = {}
    doctor_enabled = bool(doctor_cfg.get("enabled") is True)
    doctor_cooldown_s = int(doctor_cfg.get("cooldown_s") or 60)
    doctor_issue_types_raw = doctor_cfg.get("issue_types") or []
    doctor_issue_types: list[str] = []
    if isinstance(doctor_issue_types_raw, list):
        doctor_issue_types = [str(x).strip() for x in doctor_issue_types_raw if isinstance(x, str) and x.strip()]
    doctor_include_log_tail_lines = int(doctor_cfg.get("include_log_tail_lines") or 50)

    ui_cfg = service.get("ui") or {}
    if not isinstance(ui_cfg, Mapping):
        ui_cfg = {}
    ui_enabled = bool(ui_cfg.get("enabled") is True)
    ui_path = str(ui_cfg.get("path") or "/").strip()
    if not ui_path.startswith("/") or "?" in ui_path or "#" in ui_path or ".." in ui_path.split("/"):
        raise ValueError("service.ui.path must be an absolute path without traversal, query, or fragment")
    ui_access = str(ui_cfg.get("access") or "authenticated").strip()
    ui_origin_policy = str(ui_cfg.get("origin_policy") or "same-origin").strip()
    ui_embedding = str(ui_cfg.get("embedding") or "external").strip()
    if ui_access != "authenticated":
        raise ValueError("service UI access must be authenticated")
    if ui_origin_policy != "same-origin":
        raise ValueError("service UI origin policy must be same-origin")
    if ui_embedding not in {"external", "same-origin"}:
        raise ValueError("service UI embedding policy is invalid")
    ui_content_security_policy = str(
        ui_cfg.get("content_security_policy")
        or (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'"
        )
    ).strip()
    if not ui_content_security_policy or len(ui_content_security_policy) > 2048:
        raise ValueError("service UI content security policy is invalid")
    ui_max_request_bytes = int(ui_cfg.get("max_request_bytes") or 1024 * 1024)
    if not 0 <= ui_max_request_bytes <= 16 * 1024 * 1024:
        raise ValueError("service UI request limit is invalid")

    capabilities = {
        str(item).strip()
        for item in (manifest.get("capabilities") or [])
        if str(item).strip()
    }
    storage_cfg = service.get("storage") or {}
    if not isinstance(storage_cfg, Mapping):
        storage_cfg = {}

    def storage_binding(kind: str, capability: str) -> dict[str, Any] | None:
        value = storage_cfg.get(kind)
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError(f"service.storage.{kind} must be an object")
        if capability not in capabilities:
            raise ValueError(f"service.storage.{kind} requires capability {capability}")
        logical_name = str(value.get("logical_name") or "").strip().lower()
        environment = str(value.get("environment") or "").strip()
        if not logical_name or not logical_name.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"service.storage.{kind}.logical_name is invalid")
        if not environment.startswith("ADAOS_") or not environment.replace("_", "").isalnum() or not environment.upper() == environment:
            raise ValueError(f"service.storage.{kind}.environment is invalid")
        return {
            "logical_name": logical_name,
            "environment": environment,
            "prefer_provisioned": bool(value.get("prefer_provisioned") is True),
            "requirements": dict(value.get("requirements") or {}),
        }

    storage_relational = storage_binding("relational", "storage.relational")
    storage_blob = storage_binding("blob", "storage.blob")
    memory_budget = manifest.get("memory_budget") or {}
    if not isinstance(memory_budget, Mapping):
        memory_budget = {}
    process_budget = memory_budget.get("process") or {}
    if not isinstance(process_budget, Mapping):
        process_budget = {}
    resource_budget = dict(process_budget)
    if memory_budget.get("expected_rss_mb") is not None:
        resource_budget["expected_rss_mb"] = memory_budget.get("expected_rss_mb")
    events = manifest.get("events") or {}
    if not isinstance(events, Mapping):
        events = {}
    publish_topics = tuple(
        sorted(
            {
                str(item).strip()
                for item in (events.get("publish") or [])
                if isinstance(item, str) and str(item).strip()
            }
        )
    )

    return ServiceSpec(
        skill=skill_name,
        skill_root=skill_root,
        host=host,
        port=port,
        command=command,
        workdir=workdir,
        env_mode=env_mode,
        python_selector=python_selector,
        venv_dir=venv_dir,
        dependencies=deps,
        requirements_file=requirements_file,
        health_path=health_path,
        health_timeout_ms=health_timeout_ms,
        self_managed_enabled=self_managed_enabled,
        crash_max_in_window=max(1, crash_max_in_window),
        crash_window_s=max(1, crash_window_s),
        crash_cooloff_s=max(0, crash_cooloff_s),
        health_interval_s=max(1, health_interval_s),
        health_failures_before_issue=max(1, health_failures_before_issue),
        hook_on_issue=hook_on_issue.strip() if hook_on_issue and hook_on_issue.strip() else None,
        hook_on_self_heal=hook_on_self_heal.strip() if hook_on_self_heal and hook_on_self_heal.strip() else None,
        hook_timeout_s=max(0.1, hook_timeout_s),
        doctor_enabled=doctor_enabled,
        doctor_cooldown_s=max(0, doctor_cooldown_s),
        doctor_issue_types=doctor_issue_types,
        doctor_include_log_tail_lines=max(0, doctor_include_log_tail_lines),
        ui_enabled=ui_enabled,
        ui_path=ui_path,
        ui_access=ui_access,
        ui_origin_policy=ui_origin_policy,
        ui_embedding=ui_embedding,
        ui_content_security_policy=ui_content_security_policy,
        ui_max_request_bytes=ui_max_request_bytes,
        storage_relational=storage_relational,
        storage_blob=storage_blob,
        resource_budget=resource_budget,
        publish_topics=publish_topics,
        distributed_membership=distributed_membership,
        startup_ready_timeout_s=startup_ready_timeout_s,
    )


def _http_get(url: str, *, timeout_ms: int) -> tuple[int, str]:
    req = Request(url, method="GET")
    with urlopen(req, timeout=timeout_ms / 1000.0) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
        return int(resp.status), body


def _service_health_ok(spec: ServiceSpec) -> bool:
    try:
        status_code, _ = _http_get(spec.base_url + spec.health_path, timeout_ms=spec.health_timeout_ms)
    except Exception:
        return False
    return 200 <= status_code < 300


def _spawn_service_process(
    cmd: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    log_path: Path,
) -> subprocess.Popen:
    """Perform filesystem and process-launch I/O outside the runtime event loop."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as logf:
        return subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=dict(env),
            stdout=logf,
            stderr=logf,
        )


def _listener_host_matches(expected: str, actual: str) -> bool:
    expected_token = str(expected or "").strip().lower()
    actual_token = str(actual or "").strip().lower()
    if expected_token in {"", "0.0.0.0", "::"} or actual_token in {"0.0.0.0", "::"}:
        return True
    aliases = {
        "localhost": {"localhost", "127.0.0.1", "::1"},
        "127.0.0.1": {"localhost", "127.0.0.1", "::1"},
        "::1": {"localhost", "127.0.0.1", "::1"},
    }
    return actual_token in aliases.get(expected_token, {expected_token})


def _service_listener_snapshot(spec: ServiceSpec) -> dict[str, Any]:
    try:
        import psutil  # type: ignore
    except Exception:
        return {"pid": None, "error": "psutil_unavailable"}

    for conn in psutil.net_connections(kind="inet"):
        try:
            if getattr(conn, "status", "") != psutil.CONN_LISTEN:
                continue
            laddr = getattr(conn, "laddr", None)
            host = str(getattr(laddr, "ip", "") or (laddr[0] if laddr else "")).strip()
            port = int(getattr(laddr, "port", 0) or (laddr[1] if laddr else 0))
        except Exception:
            continue
        if port != int(spec.port) or not _listener_host_matches(spec.host, host):
            continue
        pid = getattr(conn, "pid", None)
        snapshot: dict[str, Any] = {"pid": int(pid) if pid else None, "host": host, "port": port}
        if not pid:
            return snapshot
        try:
            proc = psutil.Process(int(pid))
            cwd = proc.cwd()
            snapshot["cwd"] = cwd
            snapshot["cmdline"] = proc.cmdline()
            snapshot["workdir_matches"] = Path(cwd).expanduser().resolve() == spec.workdir.expanduser().resolve()
            snapshot["ppid"] = int(proc.ppid())
            snapshot["create_time"] = float(proc.create_time())

            process_env: Mapping[str, str] = {}
            try:
                process_env = proc.environ()
            except Exception as exc:
                snapshot["environment_error"] = f"{type(exc).__name__}: {exc}"
            owner_instance_id = str(process_env.get("ADAOS_RUNTIME_INSTANCE_ID") or "").strip() or None
            owner_service_skill = str(process_env.get("ADAOS_SERVICE_SKILL") or "").strip() or None
            expected_instance_id = runtime_instance_id()
            snapshot["owner_runtime_instance_id"] = owner_instance_id
            snapshot["expected_runtime_instance_id"] = expected_instance_id
            snapshot["owner_service_skill"] = owner_service_skill
            snapshot["runtime_instance_matches"] = bool(
                owner_instance_id and owner_instance_id == expected_instance_id
            )
            snapshot["service_skill_matches"] = bool(owner_service_skill and owner_service_skill == spec.skill)

            runtime_descendant = int(pid) == os.getpid()
            ancestor_pids: list[int] = []
            parent = proc
            for _ in range(64):
                parent_pid = int(parent.ppid())
                if parent_pid <= 0 or parent_pid == int(parent.pid):
                    break
                ancestor_pids.append(parent_pid)
                if parent_pid == os.getpid():
                    runtime_descendant = True
                    break
                try:
                    parent = psutil.Process(parent_pid)
                except Exception:
                    break
            snapshot["ancestor_pids"] = ancestor_pids
            snapshot["runtime_descendant"] = runtime_descendant
            snapshot["orphaned"] = int(snapshot["ppid"]) in {0, 1}
            snapshot["ownership_verified"] = bool(
                snapshot["runtime_instance_matches"] or runtime_descendant
            )
            snapshot["ownership_basis"] = (
                "runtime_instance"
                if snapshot["runtime_instance_matches"]
                else "runtime_process_tree"
                if runtime_descendant
                else "runtime_instance_mismatch"
                if owner_instance_id
                else "unverified"
            )
        except Exception as exc:
            snapshot["error"] = f"{type(exc).__name__}: {exc}"
            snapshot["workdir_matches"] = False
        return snapshot
    return {"pid": None, "error": "listener_not_found"}


def _listener_owned_by_current_runtime(listener: Mapping[str, Any]) -> bool:
    return bool(listener.get("ownership_verified"))


def _listener_is_managed_service(listener: Mapping[str, Any]) -> bool:
    return bool(
        listener.get("service_skill_matches")
        or listener.get("owner_runtime_instance_id")
        or listener.get("workdir_matches")
    )


def _poll_service_processes(
    processes: list[tuple[str, subprocess.Popen]],
) -> dict[str, tuple[int, int | None]]:
    """Poll tracked child processes outside the runtime event-loop thread."""
    return {name: (id(proc), proc.poll()) for name, proc in processes}


def _terminate_process_tree(pid: int, *, timeout_s: float = 3.0) -> bool:
    if int(pid or 0) <= 0 or int(pid) == os.getpid():
        return False
    try:
        import psutil  # type: ignore
        proc = psutil.Process(int(pid))
    except Exception:
        return False

    targets = []
    try:
        targets.extend(proc.children(recursive=True))
    except Exception:
        pass
    targets.append(proc)
    for target in targets:
        try:
            target.terminate()
        except Exception:
            pass
    try:
        _, alive = psutil.wait_procs(targets, timeout=max(0.1, timeout_s))
    except Exception:
        alive = []
    for target in alive:
        try:
            target.kill()
        except Exception:
            pass
    return True


def _process_tree_pids(pid: int | None) -> set[int]:
    if int(pid or 0) <= 0:
        return set()
    try:
        import psutil  # type: ignore

        proc = psutil.Process(int(pid))
    except Exception:
        return set()
    pids = {int(proc.pid)}
    try:
        pids.update(int(child.pid) for child in proc.children(recursive=True))
    except Exception:
        pass
    return pids


def _process_tree_resource_counters(pid: int | None) -> dict[str, Any]:
    observed_at = time.time()
    if int(pid or 0) <= 0:
        return {"available": False, "reason": "pid_unavailable", "observed_at": observed_at}
    try:
        import psutil  # type: ignore

        owner = psutil.Process(int(pid))
    except Exception as exc:
        return {
            "available": False,
            "reason": f"process_unavailable:{type(exc).__name__}",
            "observed_at": observed_at,
            "owner_pid": int(pid or 0) or None,
        }

    processes = [owner]
    try:
        processes.extend(owner.children(recursive=True))
    except Exception:
        pass
    rss_bytes = 0
    read_bytes = 0
    write_bytes = 0
    thread_total = 0
    open_handle_total = 0
    handle_kind = "unavailable"
    identities: list[str] = []
    pids: list[int] = []
    for proc in processes:
        try:
            proc_pid = int(proc.pid)
            created_at = float(proc.create_time())
        except Exception:
            continue
        identities.append(f"{proc_pid}:{created_at:.6f}")
        pids.append(proc_pid)
        try:
            rss_bytes += int(proc.memory_info().rss or 0)
        except Exception:
            pass
        try:
            counters = proc.io_counters()
            read_bytes += int(getattr(counters, "read_bytes", 0) or 0)
            write_bytes += int(getattr(counters, "write_bytes", 0) or 0)
        except Exception:
            pass
        try:
            thread_total += int(proc.num_threads() or 0)
        except Exception:
            pass
        try:
            if hasattr(proc, "num_fds"):
                open_handle_total += int(proc.num_fds() or 0)
                handle_kind = "file_descriptors"
            elif hasattr(proc, "num_handles"):
                open_handle_total += int(proc.num_handles() or 0)
                handle_kind = "windows_handles"
        except Exception:
            pass
    return {
        "schema": "adaos.skill_service_resource_counters.v1",
        "available": bool(identities),
        "reason": "sampled" if identities else "process_tree_unavailable",
        "observed_at": observed_at,
        "owner_pid": int(pid),
        "generation": hashlib.sha256("|".join(sorted(identities)).encode("utf-8")).hexdigest()[:16],
        "process_total": len(identities),
        "pids": sorted(pids)[:32],
        "rss_bytes": rss_bytes,
        "read_bytes": read_bytes,
        "write_bytes": write_bytes,
        "thread_total": thread_total,
        "open_handle_total": open_handle_total,
        "open_handle_kind": handle_kind,
    }


def _path_value(value: Any) -> Path:
    resolved = value() if callable(value) else value
    return Path(resolved).expanduser().resolve()


def _optional_path_value(owner: Any, *names: str) -> Path | None:
    for name in names:
        try:
            raw = getattr(owner, name)
        except Exception:
            continue
        try:
            value = raw() if callable(raw) else raw
        except Exception:
            continue
        if value is None:
            continue
        try:
            return Path(value).expanduser().resolve()
        except Exception:
            continue
    return None


def _service_pythonpath(owner: Any, skill_root: Path, current: str = "") -> str:
    entries: list[str] = [str(skill_root)]
    package_path = _optional_path_value(owner, "package_path", "package_dir")
    if package_path is not None:
        package_root = package_path.parent if package_path.name == "adaos" else package_path
        entries.append(str(package_root))
    repo_root = _optional_path_value(owner, "repo_root")
    if repo_root is not None:
        repo_src = repo_root / "src"
        entries.append(str(repo_src if repo_src.exists() else repo_root))
    if current:
        entries.extend(current.split(os.pathsep))
    return os.pathsep.join(dict.fromkeys(entry for entry in entries if entry)).strip(os.pathsep)


def _current_interpreter_site_packages() -> list[Path]:
    candidates: list[Path] = []
    for key in ("purelib", "platlib"):
        try:
            raw = sysconfig.get_paths().get(key)
        except Exception:
            raw = None
        if raw:
            candidates.append(Path(raw).expanduser())
    try:
        candidates.extend(Path(raw).expanduser() for raw in site.getsitepackages())
    except Exception:
        pass

    paths: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            path = candidate.resolve()
        except Exception:
            continue
        if path.name not in {"site-packages", "dist-packages"} or not path.exists():
            continue
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            paths.append(path)
    return paths


def _venv_site_packages(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Lib" / "site-packages"
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return venv_dir / "lib" / version / "site-packages"


def _write_host_site_overlay(venv_dir: Path) -> None:
    target_site = _venv_site_packages(venv_dir).resolve()
    host_sites = [
        path
        for path in _current_interpreter_site_packages()
        if str(path.resolve()).casefold() != str(target_site).casefold()
    ]
    overlay = target_site / "_adaos_host_site.pth"
    if not host_sites:
        try:
            overlay.unlink(missing_ok=True)
        except Exception:
            _log.debug("failed to remove empty host site overlay path=%s", overlay, exc_info=True)
        return

    target_site.mkdir(parents=True, exist_ok=True)
    content = "".join(f"{path}\n" for path in host_sites)
    try:
        if overlay.exists() and overlay.read_text(encoding="utf-8") == content:
            return
        overlay.write_text(content, encoding="utf-8")
    except Exception:
        _log.warning("failed to write service host site overlay path=%s", overlay, exc_info=True)


class ServiceSkillSupervisor:
    def __init__(self) -> None:
        self._ctx = get_ctx()
        self._procs: dict[str, subprocess.Popen] = {}
        self._proc_specs: dict[str, tuple[Any, ...]] = {}
        self._process_states: dict[str, dict[str, Any]] = {}
        self._health_states: dict[str, dict[str, Any]] = {}
        self._specs: dict[str, ServiceSpec] = {}
        self._task: asyncio.Task | None = None
        self._health_task: asyncio.Task | None = None
        self._membership = DistributedServiceMembershipSupervisor(self._ctx)

        self._issues_cache: dict[str, list[dict[str, Any]]] = {}
        self._crash_history: dict[str, deque[float]] = {}
        self._cooloff_until: dict[str, float] = {}
        self._health_failures: dict[str, int] = {}
        self._next_health_check_at: dict[str, float] = {}
        self._next_resource_sample_at: dict[str, float] = {}
        self._resource_counters: dict[str, dict[str, Any]] = {}
        self._resource_activity: dict[str, dict[str, Any]] = {}
        self._resource_violation_counts: dict[str, dict[str, int]] = {}
        self._resource_issue_last_at: dict[str, float] = {}
        self._doctor_cooldown_until: dict[str, float] = {}
        self._doctor_requests_cache: dict[str, list[dict[str, Any]]] = {}
        self._external_ready_specs: dict[str, tuple[Any, ...]] = {}
        self._external_ready_at: dict[str, float] = {}
        self._ensure_failure_counts: dict[str, int] = {}
        self._operation_locks: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = {}
        self._discover_lock = threading.Lock()
        self._discover_async_lock: asyncio.Lock | None = None
        self._discover_async_lock_loop: asyncio.AbstractEventLoop | None = None
        self._discover_executor: ThreadPoolExecutor | None = None
        self._discover_last_at = 0.0
        self._discover_last_full_at = 0.0
        self._discover_source_state: tuple[tuple[str, int, int, int], ...] | None = None
        self._discover_probe_interval_s = _bounded_env_seconds(
            "ADAOS_SERVICE_DISCOVERY_PROBE_INTERVAL_S",
            default=5.0,
            minimum=1.0,
            maximum=300.0,
        )
        self._discover_full_interval_s = _bounded_env_seconds(
            "ADAOS_SERVICE_DISCOVERY_FULL_INTERVAL_S",
            default=300.0,
            minimum=30.0,
            maximum=3600.0,
        )
        self._manifest_state: dict[str, tuple[Any, ...]] = {}
        self._shutdown_requested = False

    # ------------------------------------------------------------------ public
    def _discovery_source_signature(self, skills_root: Path) -> tuple[tuple[str, int, int, int], ...]:
        workspace_root_raw = self._ctx.paths.workspace_dir()
        workspace_root = Path(workspace_root_raw() if callable(workspace_root_raw) else workspace_root_raw)
        return tuple(
            _path_discovery_signature(path)
            for path in (
                skills_root,
                skills_root / ".runtime",
                workspace_root / "registry.json",
            )
        )

    def ensure_discovered(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._discover_last_at) < self._discover_probe_interval_s:
            return
        skills_root_raw = self._ctx.paths.skills_dir()
        skills_root = Path(skills_root_raw() if callable(skills_root_raw) else skills_root_raw)
        if not skills_root.exists():
            self._discover_last_at = now
            return

        with self._discover_lock:
            now = time.monotonic()
            if not force and (now - self._discover_last_at) < self._discover_probe_interval_s:
                return
            source_state = self._discovery_source_signature(skills_root)
            full_scan_fresh = (now - self._discover_last_full_at) < self._discover_full_interval_s
            if not force and full_scan_fresh and source_state == self._discover_source_state:
                self._discover_last_at = now
                return
            next_specs: dict[str, ServiceSpec] = {}
            next_state: dict[str, tuple[Any, ...]] = {}

            for workspace_skill_dir in skills_root.iterdir():
                skill_dir = workspace_skill_dir
                if not skill_dir.is_dir() or skill_dir.name.startswith((".", "_")):
                    continue
                if _runtime_is_deactivated(skills_root, skill_dir.name):
                    _log.info("skipping deactivated service skill=%s during discovery", skill_dir.name)
                    continue
                runtime_skill_dir = _active_runtime_skill_root(skills_root, skill_dir.name)
                if runtime_skill_dir is not None:
                    skill_dir = runtime_skill_dir
                skill_yaml = skill_dir / "skill.yaml"
                if skill_yaml.exists():
                    try:
                        st = skill_yaml.stat()
                        state = (str(skill_dir.resolve()), int(st.st_mtime_ns), int(st.st_size), float(st.st_ctime_ns))
                    except Exception:
                        state = (str(skill_dir), -1, -1, -1.0)
                else:
                    state = (str(skill_dir), 0, 0, 0.0)

                manifest_was_discovered = workspace_skill_dir.name in self._manifest_state
                prev_state = self._manifest_state.get(workspace_skill_dir.name)
                prev_spec = self._specs.get(workspace_skill_dir.name)
                if not force and manifest_was_discovered and prev_state == state:
                    if prev_spec is not None:
                        next_specs[workspace_skill_dir.name] = prev_spec
                    next_state[workspace_skill_dir.name] = state
                    continue

                manifest = _read_skill_manifest(skill_dir)
                spec = _resolve_service_spec(workspace_skill_dir.name, skill_dir, manifest)
                if spec:
                    next_specs[workspace_skill_dir.name] = spec
                next_state[workspace_skill_dir.name] = state

            self._specs = next_specs
            self._manifest_state = next_state
            self._discover_last_at = now
            self._discover_last_full_at = now
            self._discover_source_state = self._discovery_source_signature(skills_root)

    async def refresh_discovered(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._discover_last_at) < self._discover_probe_interval_s:
            return
        loop = asyncio.get_running_loop()
        if self._discover_async_lock is None or self._discover_async_lock_loop is not loop:
            self._discover_async_lock = asyncio.Lock()
            self._discover_async_lock_loop = loop
        async with self._discover_async_lock:
            now = time.monotonic()
            if not force and (now - self._discover_last_at) < self._discover_probe_interval_s:
                return
            if self._discover_executor is None:
                self._discover_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="adaos-skill-discovery")
            await loop.run_in_executor(self._discover_executor, partial(self.ensure_discovered, force=force))

    def resolve_base_url(self, skill_name: str) -> str | None:
        spec = self._specs.get(skill_name)
        return spec.base_url if spec else None

    def ui_surface(self, skill_name: str, *, check_health: bool = False) -> dict[str, Any] | None:
        """Return a redacted, same-origin UI surface; never expose the upstream URL."""

        spec = self._specs.get(skill_name)
        if spec is None or not spec.ui_enabled:
            return None
        status = self.status(skill_name, check_health=check_health) or {}
        return {
            "schema": "adaos.service.ui_surface.v1",
            "service": skill_name,
            "access": spec.ui_access,
            "origin_policy": spec.ui_origin_policy,
            "embedding": spec.ui_embedding,
            "proxy_path": f"/api/services/{skill_name}/ui/",
            "bootstrap_path": f"/api/services/{skill_name}/ui-bootstrap",
            "health": {
                "running": bool(status.get("running") or status.get("external_ready")),
                "ok": status.get("health_ok") if check_health else None,
            },
        }

    def _service_storage_environment(self, spec: ServiceSpec, bucket_root: Path) -> dict[str, str]:
        """Resolve opaque bindings to process-only locations for their owning service."""

        values: dict[str, str] = {}
        owner_ref = f"skill:{spec.skill}"
        if spec.storage_relational:
            config = dict(spec.storage_relational)
            raw_requirements = dict(config.get("requirements") or {})
            raw_requirements["migration_owner"] = owner_ref
            if config.get("prefer_provisioned"):
                profiles = get_relational_storage_broker(self._ctx).provider_profiles()
                raw_requirements["preferred_providers"] = tuple(
                    [item.provider_id for item in profiles if item.provider_id != "sqlite"]
                    + [item.provider_id for item in profiles if item.provider_id == "sqlite"]
                )
            requirements = RelationalStorageRequirements(**raw_requirements)
            broker = get_relational_storage_broker(self._ctx)
            binding = broker.bind(
                owner_ref=owner_ref,
                logical_name=str(config["logical_name"]),
                requirements=requirements,
                scope_root=bucket_root / "data" / "db",
            )
            values[str(config["environment"])] = broker.service_uri(binding, owner_ref=owner_ref)
            values["ADAOS_SERVICE_RELATIONAL_BINDING"] = json.dumps(
                binding.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        if spec.storage_blob:
            config = dict(spec.storage_blob)
            requirements = BlobStorageRequirements(**dict(config.get("requirements") or {}))
            broker = get_blob_storage_broker(self._ctx)
            binding = broker.bind(
                owner_ref=owner_ref,
                logical_name=str(config["logical_name"]),
                requirements=requirements,
                scope_root=bucket_root / "data" / "files",
                prefer_provisioned=bool(config.get("prefer_provisioned")),
            )
            values[str(config["environment"])] = broker.service_uri(binding, owner_ref=owner_ref)
            values["ADAOS_SERVICE_BLOB_BINDING"] = json.dumps(
                binding.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        return values

    def _service_launch_plan(
        self,
        name: str,
        spec: ServiceSpec,
        python: Path,
    ) -> tuple[list[str], dict[str, str], Path]:
        """Prepare filesystem and storage-backed process inputs off the owner loop."""

        env = os.environ.copy()
        env["ADAOS_SERVICE_SKILL"] = name
        env["ADAOS_SERVICE_HOST"] = spec.host
        env["ADAOS_SERVICE_PORT"] = str(spec.port)
        env["ADAOS_SERVICE_ROOT"] = str(spec.skill_root)
        env["ADAOS_SERVICE_WORKDIR"] = str(spec.workdir)
        env["ADAOS_SERVICE_OWNER_PID"] = str(os.getpid())
        env.update(
            service_event_bridge_environment(
                name, publish_topics=spec.publish_topics
            )
        )
        env["ADAOS_RUNTIME_INSTANCE_ID"] = runtime_instance_id()
        env["ADAOS_RUNTIME_TRANSITION_ROLE"] = runtime_transition_role()
        config = getattr(self._ctx, "config", None)
        node_id = str(
            getattr(config, "node_id_value", "")
            or getattr(config, "node_id", "")
            or ""
        ).strip()
        if node_id:
            env["ADAOS_NODE_ID"] = node_id
        bucket_root = _infer_runtime_bucket_root(spec.skill_root, name)
        if bucket_root is not None:
            skill_env_path = bucket_root / "data" / "db" / "skill_env.json"
            internal_data = bucket_root / "data" / "internal"
            # Owner identity and storage paths are capabilities of this service,
            # never ambient values inherited from the parent runtime.
            env["ADAOS_SKILL_ENV_PATH"] = str(skill_env_path)
            env["ADAOS_SKILL_MEMORY_PATH"] = str(skill_env_path)
            env["ADAOS_SKILL_INTERNAL_DATA_ROOT"] = str(internal_data)
            env["ADAOS_SKILL_INTERNAL_ACTIVE_PATH"] = str(internal_data)
            env["ADAOS_SKILL_INTERNAL_TARGET_PATH"] = str(internal_data)
            env.update(self._service_storage_environment(spec, bucket_root))
        env["ADAOS_SKILL_NAME"] = name
        env["ADAOS_SKILL_PACKAGE"] = f"skills.{name}"
        env["ADAOS_SKILL_ROOT"] = str(spec.skill_root)
        env["ADAOS_SKILL_MODE"] = "runtime"
        env["ADAOS_BASE_DIR"] = str(_path_value(self._ctx.paths.base_dir()))
        for env_name, path_value in (
            ("ADAOS_PACKAGE_DIR", _optional_path_value(self._ctx.paths, "package_path", "package_dir")),
            ("ADAOS_REPO_ROOT", _optional_path_value(self._ctx.paths, "repo_root")),
            ("ADAOS_MODELS_DIR", _optional_path_value(self._ctx.paths, "models_dir")),
            ("ADAOS_STATE_DIR", _optional_path_value(self._ctx.paths, "state_dir")),
            ("ADAOS_LOGS_DIR", _optional_path_value(self._ctx.paths, "logs_dir")),
        ):
            if path_value is not None:
                env[env_name] = str(path_value)
        env["PYTHONPATH"] = _service_pythonpath(self._ctx.paths, spec.skill_root, env.get("PYTHONPATH", ""))

        cmd = self._build_command(python, spec.command)
        logs_dir = self._ctx.paths.logs_dir()
        logs_dir = Path(logs_dir() if callable(logs_dir) else logs_dir)
        log_path_fn = getattr(self._ctx.paths, "skill_service_log_path", None)
        log_path = Path(log_path_fn(name)) if callable(log_path_fn) else logs_dir / f"service.{name}.log"
        return cmd, env, log_path

    def list(self) -> list[str]:
        return sorted(self._specs.keys())

    def status(self, name: str, *, check_health: bool = False) -> dict[str, Any] | None:
        spec = self._specs.get(name)
        if not spec:
            return None

        proc = self._procs.get(name)
        process_state = dict(self._process_states.get(name) or {})
        state_matches = bool(proc and process_state.get("process_identity") == id(proc))
        if state_matches:
            running = bool(process_state.get("running"))
            code = process_state.get("exit_code")
        else:
            # A newly spawned process is running until the off-loop watchdog
            # observes an exit. Popen.returncode is a cached value and does not
            # call waitpid(), so status remains a current-state read.
            code = getattr(proc, "returncode", None) if proc else None
            running = bool(proc and code is None)
        pid = int(proc.pid) if proc and proc.pid else None
        now = time.time()
        process_observed_at = process_state.get("observed_at") if state_matches else None
        process_age_s = max(0.0, now - float(process_observed_at)) if process_observed_at else None
        spec_key = self._spec_key(spec)
        process_spec_matches = bool(
            running and self._proc_specs.get(name) == spec_key
        )
        external_ready = self._external_ready_specs.get(name) == spec_key and not running
        resource_activity = dict(
            self._resource_activity.get(name)
            or {
                "schema": "adaos.skill_service_resource_activity.v1",
                "available": False,
                "reason": "awaiting_sample" if running else "service_not_running",
                "budget": dict(spec.resource_budget or {}),
            }
        )
        if not running and not external_ready:
            resource_activity["available"] = False
            resource_activity["reason"] = "service_not_running"

        payload: dict[str, Any] = {
            "name": name,
            "kind": "service",
            "running": running,
            "pid": pid,
            "exit_code": code,
            "process_observed_at": process_observed_at,
            "process_observation_age_s": round(process_age_s, 3) if process_age_s is not None else None,
            "process_observation_source": process_state.get("source") if state_matches else "spawn_pending_poll",
            "process_spec_matches": process_spec_matches,
            "base_url": spec.base_url,
            "host": spec.host,
            "port": spec.port,
            "skill_root": str(spec.skill_root),
            "workdir": str(spec.workdir),
            "command": spec.command,
            "env_mode": spec.env_mode,
            "python_selector": spec.python_selector,
            "venv_dir": str(spec.venv_dir) if spec.venv_dir else None,
            "health_path": spec.health_path,
            "self_managed": {
                "enabled": spec.self_managed_enabled,
                "crash": {
                    "max_in_window": spec.crash_max_in_window,
                    "window_s": spec.crash_window_s,
                    "cooloff_s": spec.crash_cooloff_s,
                },
                "health": {
                    "interval_s": spec.health_interval_s,
                    "failures_before_issue": spec.health_failures_before_issue,
                },
                "hooks": {
                    "on_issue": spec.hook_on_issue,
                    "on_self_heal": spec.hook_on_self_heal,
                    "timeout_s": spec.hook_timeout_s,
                },
            },
            "cooloff_until": self._cooloff_until.get(name),
            "external_ready": external_ready,
            "external_ready_at": self._external_ready_at.get(name) if external_ready else None,
            "runtime_owner": {
                "runtime_instance_id": runtime_instance_id(),
                "runtime_pid": os.getpid(),
                "transition_role": runtime_transition_role(),
                "basis": "tracked_process" if running else "verified_listener" if external_ready else None,
            },
            "resource_activity": resource_activity,
            "distributed_membership": self._membership.status(name),
        }

        if check_health:
            health_state = dict(self._health_states.get(name) or {})
            health_observed_at = health_state.get("observed_at")
            health_age_s = max(0.0, now - float(health_observed_at)) if health_observed_at else None
            health_stale_after_s = max(5.0, float(spec.health_interval_s) * 2.0)
            health_stale = health_age_s is None or health_age_s > health_stale_after_s
            payload["health_ok"] = None if health_stale else health_state.get("ok")
            payload["last_health_ok"] = health_state.get("ok")
            payload["health_observed_at"] = health_observed_at
            payload["health_observation_age_s"] = round(health_age_s, 3) if health_age_s is not None else None
            payload["health_observation_stale"] = health_stale
            payload["health_observation_source"] = health_state.get("source") or "awaiting_background_probe"

        return payload

    async def start(self, name: str) -> None:
        if self._shutdown_requested:
            raise RuntimeError("service supervisor is shutting down")
        await self.refresh_discovered()
        spec = self._specs.get(name)
        if not spec:
            raise KeyError(name)
        await self.ensure_started(name, spec, force=True)
        self._ensure_background_tasks()

    async def stop(self, name: str, *, timeout_s: float = 3.0) -> None:
        async with self._operation_lock(name):
            await self._stop_owned(name, timeout_s=timeout_s)

    async def _stop_owned(self, name: str, *, timeout_s: float = 3.0) -> None:
        proc = self._procs.get(name)
        if not proc:
            spec = self._specs.get(name)
            external_ready = bool(spec and self._external_ready_specs.get(name) == self._spec_key(spec))
            if not external_ready or spec is None:
                self._external_ready_specs.pop(name, None)
                self._external_ready_at.pop(name, None)
                return
            listener = await asyncio.to_thread(_service_listener_snapshot, spec)
            listener_pid = int(listener.get("pid") or 0)
            if (
                listener_pid <= 0
                or not _listener_owned_by_current_runtime(listener)
                or not _listener_is_managed_service(listener)
            ):
                await self._record_issue(
                    name,
                    issue_type="service_stop_owner_unverified",
                    message="refusing to stop an external service listener whose current runtime ownership is not verified",
                    severity="error",
                    details={"listener": listener, "timeout_s": timeout_s},
                )
                return
            terminated = await asyncio.to_thread(
                _terminate_process_tree,
                listener_pid,
                timeout_s=timeout_s,
            )
            if not terminated:
                await self._record_issue(
                    name,
                    issue_type="service_stop_failed",
                    message="failed to terminate the verified external service listener",
                    severity="error",
                    details={"listener": listener, "timeout_s": timeout_s},
                )
                return
            self._external_ready_specs.pop(name, None)
            self._external_ready_at.pop(name, None)
            emit(
                self._ctx.bus,
                "skill.service.stopped",
                {"skill": name, "pid": listener_pid, "external": True},
                source="skill.service",
            )
            return

        self._external_ready_specs.pop(name, None)
        self._external_ready_at.pop(name, None)

        code = await asyncio.to_thread(proc.poll)
        if code is None:
            # Service entrypoints may supervise their own child process (for
            # example an MLflow/Uvicorn server).  Terminating only the tracked
            # launcher can leave a healthy orphan on the configured port; the
            # next start then misclassifies that endpoint as external.  Stop
            # the complete owned tree while we still know its root PID.
            terminated_tree = await asyncio.to_thread(
                _terminate_process_tree,
                int(proc.pid),
                timeout_s=timeout_s,
            )
            if not terminated_tree:
                try:
                    proc.terminate()
                except Exception:
                    pass

            deadline = time.time() + timeout_s
            while time.time() < deadline:
                code = await asyncio.to_thread(proc.poll)
                if code is not None:
                    break
                await asyncio.sleep(0.05)

            if code is None:
                try:
                    proc.kill()
                except Exception:
                    pass

        self._procs.pop(name, None)
        self._proc_specs.pop(name, None)
        self._process_states[name] = {
            "process_identity": id(proc),
            "pid": int(proc.pid),
            "running": False,
            "exit_code": getattr(proc, "returncode", None),
            "observed_at": time.time(),
            "source": "stop",
        }
        self._health_states[name] = {
            "ok": False,
            "observed_at": time.time(),
            "source": "stop",
        }
        emit(self._ctx.bus, "skill.service.stopped", {"skill": name, "pid": proc.pid}, source="skill.service")

    async def restart(self, name: str) -> None:
        if self._shutdown_requested:
            return
        await self.refresh_discovered()
        spec = self._specs.get(name)
        if not spec:
            raise KeyError(name)
        async with self._operation_lock(name):
            await self._stop_owned(name)
            await self._ensure_started_owned(name, spec, force=True)
        self._ensure_background_tasks()

    async def quarantine_resource_pressure(
        self,
        name: str,
        *,
        reason: str,
        pressure: dict[str, Any] | None = None,
        cooloff_s: float = 120.0,
    ) -> dict[str, Any]:
        await self.refresh_discovered()
        spec = self._specs.get(name)
        if not spec:
            raise KeyError(name)
        bounded_cooloff = max(30.0, min(float(cooloff_s or 120.0), 3600.0))
        async with self._operation_lock(name):
            before = self.status(name, check_health=False) or {"name": name}
            observed_pids = {
                int(pid)
                for pid in (pressure or {}).get("observed_pids", [])
                if str(pid or "").strip().isdigit() and int(pid) > 0
            }
            proc = self._procs.get(name)
            proc_code = await asyncio.to_thread(proc.poll) if proc else None
            owner_pid = int(proc.pid) if proc and proc_code is None else None
            owner_basis = "tracked_process" if owner_pid else None
            listener: dict[str, Any] = {}
            if owner_pid is None and self._external_ready_specs.get(name) == self._spec_key(spec):
                listener = await asyncio.to_thread(_service_listener_snapshot, spec)
                if _listener_owned_by_current_runtime(listener) and _listener_is_managed_service(listener):
                    owner_pid = int(listener.get("pid") or 0) or None
                    owner_basis = "verified_listener"
            owner_pids = await asyncio.to_thread(_process_tree_pids, owner_pid)
            matched_pids = sorted(observed_pids & owner_pids)
            if not observed_pids or not matched_pids:
                issue = await self._record_issue(
                    name,
                    issue_type="memory_resource_pressure_owner_mismatch",
                    message="refusing skill memory quarantine because observed processes are not in the current owned tree",
                    severity="error",
                    details={
                        "reason": str(reason or "supervisor.memory.skill_pressure"),
                        "pressure": dict(pressure or {}),
                        "observed_pids": sorted(observed_pids),
                        "owner_pid": owner_pid,
                        "owner_pids": sorted(owner_pids),
                        "owner_basis": owner_basis,
                        "listener": listener,
                    },
                )
                return {
                    "ok": False,
                    "skill": name,
                    "action": "quarantine_resource_pressure",
                    "stopped": False,
                    "error": "observed_process_owner_mismatch",
                    "issue_id": issue.get("id"),
                    "observed_pids": sorted(observed_pids),
                    "owner_pid": owner_pid,
                    "owner_pids": sorted(owner_pids),
                }
            issue = await self._record_issue(
                name,
                issue_type="memory_resource_pressure",
                message="service skill quarantined after attributed critical host memory pressure",
                severity="error",
                details={
                    "reason": str(reason or "supervisor.memory.skill_pressure"),
                    "pressure": dict(pressure or {}),
                    "cooloff_s": bounded_cooloff,
                    "owner_basis": owner_basis,
                    "matched_pids": matched_pids,
                    "service": before,
                },
            )
            self._cooloff_until[name] = time.time() + bounded_cooloff
            await self._stop_owned(name, timeout_s=min(10.0, max(3.0, bounded_cooloff / 10.0)))
            after = self.status(name, check_health=False) or {"name": name}
            stopped = not bool(after.get("running")) and not bool(after.get("external_ready"))
            result = {
                "ok": stopped,
                "skill": name,
                "action": "quarantine_resource_pressure",
                "stopped": stopped,
                "cooloff_until": self._cooloff_until.get(name),
                "issue_id": issue.get("id"),
                "owner_basis": owner_basis,
                "matched_pids": matched_pids,
                "before": before,
                "after": after,
            }
            emit(self._ctx.bus, "skill.service.resource_pressure", result, source="skill.service")
            return result

    async def start_all(self) -> None:
        if self._shutdown_requested:
            return
        started_at = time.perf_counter()
        await self.refresh_discovered(force=True)
        attempted = 0
        failed: list[str] = []
        specs = list(self._specs.items())
        specs.sort(
            key=lambda item: 0
            if getattr(item[1], "distributed_membership", None) is not None
            else 1
        )
        distributed_remaining = sum(
            1
            for _, spec in specs
            if getattr(spec, "distributed_membership", None) is not None
        )
        for name, spec in specs:
            if self._shutdown_requested:
                return
            attempted += 1
            service_started_at = time.perf_counter()
            service_status = "ready"
            try:
                await self.ensure_started(name, spec, force=False)
            except asyncio.CancelledError:
                service_status = "cancelled"
                raise
            except Exception as exc:
                service_status = "failed"
                failed.append(name)
                await self._record_ensure_failure(name, spec, exc)
                _log.warning("failed to start service skill=%s", name, exc_info=True)
            finally:
                _log.info(
                    "service skill startup result skill=%s status=%s duration_s=%.3f",
                    name,
                    service_status,
                    time.perf_counter() - service_started_at,
                )
                if getattr(spec, "distributed_membership", None) is not None:
                    distributed_remaining -= 1
                    if distributed_remaining == 0:
                        self._ensure_health_task()

        self._ensure_background_tasks()
        _log.log(
            logging.WARNING if failed else logging.INFO,
            "service skill startup summary attempted=%s failed=%s duration_s=%.3f failed_skills=%s",
            attempted,
            len(failed),
            time.perf_counter() - started_at,
            failed,
        )

    def issues(self, name: str) -> list[dict[str, Any]]:
        self.ensure_discovered()
        if name not in self._specs:
            raise KeyError(name)
        return list(self._load_issues(name))

    async def inject_issue(self, name: str, *, issue_type: str, message: str, details: dict[str, Any] | None = None) -> None:
        await self.refresh_discovered()
        spec = self._specs.get(name)
        if not spec:
            raise KeyError(name)
        await self._record_issue(name, issue_type=issue_type, message=message, severity="manual", details=details or {})

    def doctor_requests(self, name: str) -> list[dict[str, Any]]:
        self.ensure_discovered()
        if name not in self._specs:
            raise KeyError(name)
        return list(self._load_doctor_requests(name))

    async def request_doctor(self, name: str, *, reason: str, issue: dict[str, Any] | None = None) -> dict[str, Any] | None:
        await self.refresh_discovered()
        spec = self._specs.get(name)
        if not spec:
            raise KeyError(name)
        if not spec.self_managed_enabled or not spec.doctor_enabled:
            return None
        return await self._emit_doctor_request(spec, reason=reason, issue=issue)

    async def self_heal(self, name: str, *, reason: str, issue: dict[str, Any] | None = None) -> dict[str, Any] | None:
        await self.refresh_discovered()
        spec = self._specs.get(name)
        if not spec:
            raise KeyError(name)
        if not spec.self_managed_enabled or not spec.hook_on_self_heal:
            return None
        return await self._run_hook(spec, spec.hook_on_self_heal, payload={"reason": reason, "issue": issue})

    async def ensure_started(self, name: str, spec: ServiceSpec, *, force: bool) -> None:
        async with self._operation_lock(name):
            await self._ensure_started_owned(name, spec, force=force)

    async def _ensure_started_owned(self, name: str, spec: ServiceSpec, *, force: bool) -> None:
        if self._shutdown_requested:
            return
        proc = self._procs.get(name)
        spec_key = self._spec_key(spec)
        proc_code = await asyncio.to_thread(proc.poll) if proc else None
        if proc and proc_code is None:
            if self._proc_specs.get(name) == spec_key:
                self._ensure_failure_counts.pop(name, None)
                return
            await self._stop_owned(name, timeout_s=3.0)

        now = time.time()
        cooloff_until = float(self._cooloff_until.get(name) or 0.0)
        if not force and now < cooloff_until:
            return

        external_already_marked = self._external_ready_specs.get(name) == spec_key
        endpoint_healthy = await asyncio.to_thread(_service_health_ok, spec)
        if endpoint_healthy:
            listener = await asyncio.to_thread(_service_listener_snapshot, spec)
            if _listener_owned_by_current_runtime(listener):
                self._external_ready_specs[name] = spec_key
                if not external_already_marked:
                    self._external_ready_at[name] = time.time()
                else:
                    self._external_ready_at.setdefault(name, time.time())
                self._health_states[name] = {
                    "ok": True,
                    "observed_at": time.time(),
                    "source": "external_adoption",
                }
                if not external_already_marked:
                    emit(
                        self._ctx.bus,
                        "skill.service.ready",
                        {"skill": name, "pid": listener.get("pid"), "external": True},
                        source="skill.service",
                    )
                self._ensure_failure_counts.pop(name, None)
                return

            stale_pid = int(listener.get("pid") or 0)
            if stale_pid > 0 and _listener_is_managed_service(listener):
                await self._record_issue(
                    name,
                    issue_type="stale_service_endpoint",
                    message="service endpoint is healthy but is not owned by the current runtime; restarting it",
                    severity="warning",
                    details={
                        "pid": stale_pid,
                        "cwd": listener.get("cwd"),
                        "ppid": listener.get("ppid"),
                        "create_time": listener.get("create_time"),
                        "owner_runtime_instance_id": listener.get("owner_runtime_instance_id"),
                        "expected_runtime_instance_id": listener.get("expected_runtime_instance_id"),
                        "ownership_basis": listener.get("ownership_basis"),
                        "runtime_descendant": listener.get("runtime_descendant"),
                        "orphaned": listener.get("orphaned"),
                        "expected_workdir": str(spec.workdir),
                        "host": spec.host,
                        "port": spec.port,
                    },
                )
                await asyncio.to_thread(_terminate_process_tree, stale_pid, timeout_s=3.0)
                deadline = time.time() + 3.0
                while time.time() < deadline and await asyncio.to_thread(_service_health_ok, spec):
                    await asyncio.sleep(0.1)
                if await asyncio.to_thread(_service_health_ok, spec):
                    await self._record_issue(
                        name,
                        issue_type="stale_service_endpoint_still_alive",
                        message="service endpoint remained healthy after terminating the stale listener; refusing duplicate start",
                        severity="error",
                        details={"pid": stale_pid, "host": spec.host, "port": spec.port},
                    )
                    return
            else:
                await self._record_issue(
                    name,
                    issue_type="service_endpoint_identity_unknown",
                    message="service endpoint is healthy but its runtime location cannot be verified; refusing duplicate start",
                    severity="error",
                    details={"listener": listener, "expected_workdir": str(spec.workdir), "host": spec.host, "port": spec.port},
                )
                return
        else:
            listener = await asyncio.to_thread(_service_listener_snapshot, spec)
            listener_pid = int(listener.get("pid") or 0)
            if listener_pid > 0:
                failures = self._ensure_failure_counts.get(name, 0) + 1
                self._ensure_failure_counts[name] = failures
                cooloff_s = max(float(spec.crash_cooloff_s or 0), _ensure_failure_cooloff_s(failures))
                self._cooloff_until[name] = time.time() + cooloff_s
                await self._record_issue(
                    name,
                    issue_type="service_endpoint_unhealthy_listener_present",
                    message="service listener already owns the configured port but healthcheck failed; refusing duplicate start",
                    severity="warning",
                    details={
                        "pid": listener_pid,
                        "cwd": listener.get("cwd"),
                        "cmdline": listener.get("cmdline"),
                        "workdir_matches": listener.get("workdir_matches"),
                        "expected_workdir": str(spec.workdir),
                        "host": spec.host,
                        "port": spec.port,
                        "health_path": spec.health_path,
                        "cooloff_s": cooloff_s,
                        "failures": failures,
                    },
                )
                return
        self._external_ready_specs.pop(name, None)
        self._external_ready_at.pop(name, None)

        python = await asyncio.to_thread(self._select_python, spec)
        cmd, env, log_path = await asyncio.to_thread(
            self._service_launch_plan,
            name,
            spec,
            python,
        )

        if self._shutdown_requested:
            return
        _log.info("starting service skill=%s cmd=%s cwd=%s", name, cmd, spec.workdir)
        proc = await asyncio.to_thread(
            _spawn_service_process,
            cmd,
            cwd=spec.workdir,
            env=env,
            log_path=log_path,
        )
        self._procs[name] = proc
        self._proc_specs[name] = spec_key
        self._process_states[name] = {
            "process_identity": id(proc),
            "pid": int(proc.pid),
            "running": True,
            "exit_code": None,
            "observed_at": time.time(),
            "source": "spawn",
        }
        emit(self._ctx.bus, "skill.service.started", {"skill": name, "pid": proc.pid}, source="skill.service")

        try:
            await self._wait_ready(spec)
        except Exception:
            await self._stop_owned(name, timeout_s=3.0)
            raise
        proc_code = await asyncio.to_thread(proc.poll)
        if proc_code is not None and await asyncio.to_thread(_service_health_ok, spec):
            listener = await asyncio.to_thread(_service_listener_snapshot, spec)
            if _listener_owned_by_current_runtime(listener):
                self._procs.pop(name, None)
                self._proc_specs.pop(name, None)
                self._external_ready_specs[name] = spec_key
                self._external_ready_at.setdefault(name, time.time())
                self._health_states[name] = {
                    "ok": True,
                    "observed_at": time.time(),
                    "source": "launcher_exit_adoption",
                }
                emit(
                    self._ctx.bus,
                    "skill.service.ready",
                    {"skill": name, "pid": listener.get("pid"), "external": True},
                    source="skill.service",
                )
                self._ensure_failure_counts.pop(name, None)
                return
            await self._record_issue(
                name,
                issue_type="service_endpoint_owner_unverified_after_start",
                message="service launcher exited but the healthy endpoint is not owned by the current runtime",
                severity="error",
                details={"listener": listener, "expected_workdir": str(spec.workdir)},
            )
            return
        emit(self._ctx.bus, "skill.service.ready", {"skill": name, "pid": proc.pid}, source="skill.service")
        self._ensure_failure_counts.pop(name, None)

    async def _record_ensure_failure(self, name: str, spec: ServiceSpec, exc: BaseException) -> None:
        failures = self._ensure_failure_counts.get(name, 0) + 1
        self._ensure_failure_counts[name] = failures
        cooloff_s = max(float(spec.crash_cooloff_s or 0), _ensure_failure_cooloff_s(failures))
        self._cooloff_until[name] = time.time() + cooloff_s
        await self._record_issue(
            name,
            issue_type="service_ensure_failed",
            message="service ensure failed; applying backoff before retry",
            severity="warning",
            details={
                "error_type": type(exc).__name__,
                "error": str(exc),
                "cooloff_s": cooloff_s,
                "failures": failures,
                "host": spec.host,
                "port": spec.port,
            },
        )

    async def shutdown(self) -> None:
        self._shutdown_requested = True
        if self._task:
            try:
                self._task.cancel()
            except Exception:
                pass
            self._task = None
        if self._health_task:
            try:
                self._health_task.cancel()
            except Exception:
                pass
            self._health_task = None
        for name in list(self._procs):
            try:
                await self.stop(name)
            except Exception:
                _log.warning("failed to stop service during supervisor shutdown skill=%s", name, exc_info=True)
                proc = self._procs.pop(name, None)
                self._proc_specs.pop(name, None)
                self._external_ready_specs.pop(name, None)
                self._external_ready_at.pop(name, None)
                proc_code = await asyncio.to_thread(proc.poll) if proc else None
                if proc and proc_code is None:
                    with contextlib.suppress(Exception):
                        proc.kill()
        if self._discover_executor:
            self._discover_executor.shutdown(wait=False, cancel_futures=True)
            self._discover_executor = None
        self._discover_async_lock = None
        self._discover_async_lock_loop = None

    # ------------------------------------------------------------------ internals
    def _record_polled_process_states(
        self,
        states: Mapping[str, tuple[int, int | None]],
        *,
        source: str,
    ) -> None:
        observed_at = time.time()
        for name, observed in states.items():
            proc = self._procs.get(name)
            if not proc or observed[0] != id(proc):
                continue
            code = observed[1]
            self._process_states[name] = {
                "process_identity": observed[0],
                "pid": int(proc.pid),
                "running": code is None,
                "exit_code": code,
                "observed_at": observed_at,
                "source": source,
            }

    def _operation_lock(self, name: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        current = self._operation_locks.get(name)
        if current is None or current[0] is not loop:
            lock = asyncio.Lock()
            self._operation_locks[name] = (loop, lock)
            return lock
        return current[1]

    def _ensure_background_tasks(self) -> None:
        if self._shutdown_requested:
            return
        if self._task is None:
            self._task = asyncio.create_task(self._watchdog_loop(), name="adaos-skill-service-watchdog")
        self._ensure_health_task()

    def _ensure_health_task(self) -> None:
        if self._shutdown_requested:
            return
        if self._health_task is None:
            self._health_task = asyncio.create_task(self._health_loop(), name="adaos-skill-service-health")

    def _service_state_dir(self, skill: str) -> Path:
        state_raw = self._ctx.paths.state_dir()
        state_dir = Path(state_raw() if callable(state_raw) else state_raw)
        return state_dir / "services" / skill

    def _issues_path(self, skill: str) -> Path:
        return self._service_state_dir(skill) / "issues.json"

    def _doctor_requests_path(self, skill: str) -> Path:
        return self._service_state_dir(skill) / "doctor_requests.json"

    def _load_issues(self, skill: str) -> list[dict[str, Any]]:
        cached = self._issues_cache.get(skill)
        if cached is not None:
            return cached
        path = self._issues_path(skill)
        if not path.exists():
            self._issues_cache[skill] = []
            return self._issues_cache[skill]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._issues_cache[skill] = [x for x in data if isinstance(x, dict)]
            else:
                self._issues_cache[skill] = []
        except Exception:
            self._issues_cache[skill] = []
        return self._issues_cache[skill]

    def _persist_issues(self, skill: str) -> None:
        issues = self._issues_cache.get(skill)
        if issues is None:
            return
        path = self._issues_path(skill)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(issues, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_doctor_requests(self, skill: str) -> list[dict[str, Any]]:
        cached = self._doctor_requests_cache.get(skill)
        if cached is not None:
            return cached
        path = self._doctor_requests_path(skill)
        if not path.exists():
            self._doctor_requests_cache[skill] = []
            return self._doctor_requests_cache[skill]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._doctor_requests_cache[skill] = [x for x in data if isinstance(x, dict)]
            else:
                self._doctor_requests_cache[skill] = []
        except Exception:
            self._doctor_requests_cache[skill] = []
        return self._doctor_requests_cache[skill]

    def _persist_doctor_requests(self, skill: str) -> None:
        items = self._doctor_requests_cache.get(skill)
        if items is None:
            return
        path = self._doctor_requests_path(skill)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _record_issue(
        self,
        skill: str,
        *,
        issue_type: str,
        message: str,
        severity: str,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "id": f"iss.{int(time.time()*1000)}",
            "ts": time.time(),
            "type": issue_type,
            "severity": severity,
            "message": message,
            "details": details,
        }
        issues = self._load_issues(skill)
        issues.append(entry)
        if len(issues) > 200:
            del issues[: len(issues) - 200]
        self._persist_issues(skill)

        emit(
            self._ctx.bus,
            "skill.service.issue",
            {"skill": skill, "issue": entry},
            source="skill.service",
        )

        spec = self._specs.get(skill)
        if spec and spec.self_managed_enabled and spec.doctor_enabled:
            await self._maybe_request_doctor(spec, issue=entry)
        return entry

    def _read_service_log_tail(self, skill: str, *, max_lines: int) -> list[str]:
        if max_lines <= 0:
            return []
        logs_dir = self._ctx.paths.logs_dir()
        logs_dir = Path(logs_dir() if callable(logs_dir) else logs_dir)
        log_path_fn = getattr(self._ctx.paths, "skill_service_log_path", None)
        path = Path(log_path_fn(skill)) if callable(log_path_fn) else logs_dir / f"service.{skill}.log"
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            return raw[-max_lines:]
        except Exception:
            return []

    async def _maybe_request_doctor(self, spec: ServiceSpec, *, issue: dict[str, Any]) -> None:
        issue_type = issue.get("type") if isinstance(issue.get("type"), str) else None
        if spec.doctor_issue_types and issue_type not in spec.doctor_issue_types:
            return

        now = time.time()
        cooloff_until = float(self._doctor_cooldown_until.get(spec.skill) or 0.0)
        if now < cooloff_until:
            return
        self._doctor_cooldown_until[spec.skill] = now + float(spec.doctor_cooldown_s)
        await self._emit_doctor_request(spec, reason="issue", issue=issue)

    async def _emit_doctor_request(self, spec: ServiceSpec, *, reason: str, issue: dict[str, Any] | None) -> dict[str, Any]:
        status = self.status(spec.skill, check_health=True) or {"name": spec.skill}
        payload: dict[str, Any] = {
            "id": f"doc.{int(time.time()*1000)}",
            "ts": time.time(),
            "skill": spec.skill,
            "reason": reason,
            "issue": issue,
            "service": status,
            "log_tail": self._read_service_log_tail(spec.skill, max_lines=spec.doctor_include_log_tail_lines),
        }

        items = self._load_doctor_requests(spec.skill)
        items.append(payload)
        if len(items) > 100:
            del items[: len(items) - 100]
        self._persist_doctor_requests(spec.skill)

        emit(self._ctx.bus, "skill.service.doctor.request", payload, source="skill.service")
        return payload

    async def _run_hook(self, spec: ServiceSpec, entrypoint: str, *, payload: dict[str, Any]) -> dict[str, Any] | None:
        python = await asyncio.to_thread(self._select_python, spec)
        helper = r"""
import asyncio
import importlib
import json
import sys

def _resolve(ep: str):
    if ":" not in ep:
        raise SystemExit("entrypoint must be module:function")
    mod, fn = ep.split(":", 1)
    m = importlib.import_module(mod)
    f = getattr(m, fn)
    return f

async def _run_async(ep: str, payload: dict):
    f = _resolve(ep)
    res = f(payload)
    if asyncio.iscoroutine(res):
        res = await res
    return res

if len(sys.argv) < 3:
    raise SystemExit("Usage: hook.py <entrypoint> <payload_json>")

ep = sys.argv[1]
payload = json.loads(sys.argv[2])
result = asyncio.run(_run_async(ep, payload))
print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = _service_pythonpath(self._ctx.paths, spec.skill_root, env.get("PYTHONPATH", ""))

        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                [str(python), "-c", helper, entrypoint, json.dumps(payload, ensure_ascii=False)],
                cwd=str(spec.skill_root),
                env=env,
                capture_output=True,
                timeout=spec.hook_timeout_s,
            )
        except subprocess.TimeoutExpired:
            await self._record_issue(
                spec.skill,
                issue_type="hook_timeout",
                message=f"hook timed out: {entrypoint}",
                severity="warning",
                details={"entrypoint": entrypoint, "timeout_s": spec.hook_timeout_s},
            )
            return None

        stdout = (proc.stdout or b"").decode("utf-8", errors="ignore").strip()
        if proc.returncode != 0:
            stderr = (proc.stderr or b"").decode("utf-8", errors="ignore").strip()
            await self._record_issue(
                spec.skill,
                issue_type="hook_failed",
                message=f"hook failed: {entrypoint}",
                severity="warning",
                details={"entrypoint": entrypoint, "returncode": proc.returncode, "stderr": stderr[-2000:]},
            )
            return None

        # If the hook printed logs, try to parse the last JSON object line.
        lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
        if not lines:
            return {"ok": True, "result": None}
        for ln in reversed(lines):
            if ln.startswith("{") and ln.endswith("}"):
                try:
                    data = json.loads(ln)
                    if isinstance(data, dict):
                        return data
                except Exception:
                    continue
        return {"ok": True, "result": stdout}

    def _select_python(self, spec: ServiceSpec) -> Path:
        if spec.env_mode != "venv":
            return Path(sys.executable)

        venv_dir = spec.venv_dir or (self._service_state_dir(spec.skill) / "venv")
        python = self._venv_python(venv_dir)
        if python.exists():
            _write_host_site_overlay(venv_dir)
            self._install_deps_if_needed(python, spec, venv_dir)
            return python

        selector = spec.python_selector or "3.11"
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            cmd = ["py", f"-{selector}", "-m", "venv", str(venv_dir)]
        else:
            cmd = [sys.executable, "-m", "venv", str(venv_dir)]
        subprocess.run(cmd, check=True)

        python = self._venv_python(venv_dir)
        _write_host_site_overlay(venv_dir)
        self._install_deps_if_needed(python, spec, venv_dir)
        return python

    @staticmethod
    def _venv_python(venv_dir: Path) -> Path:
        return venv_dir / "Scripts" / "python.exe" if os.name == "nt" else venv_dir / "bin" / "python"

    @staticmethod
    def _spec_key(spec: ServiceSpec) -> tuple[Any, ...]:
        return (
            str(spec.skill_root.resolve()),
            str(spec.workdir.resolve()),
            str(spec.venv_dir.resolve()) if spec.venv_dir else "",
            spec.host,
            spec.port,
            tuple(spec.command),
            str(spec.requirements_file.resolve()) if spec.requirements_file else "",
            tuple(spec.dependencies),
            spec.ui_enabled,
            spec.ui_path,
            spec.ui_access,
            spec.ui_origin_policy,
            spec.ui_embedding,
            spec.ui_content_security_policy,
            spec.ui_max_request_bytes,
            json.dumps(dict(spec.storage_relational or {}), sort_keys=True, default=str),
            json.dumps(dict(spec.storage_blob or {}), sort_keys=True, default=str),
        )

    def _install_deps(self, python: Path, spec: ServiceSpec) -> None:
        base = [str(python), "-m", "pip", "install", "--upgrade", "--disable-pip-version-check"]
        subprocess.run([*base, "pip"], check=False)
        if spec.requirements_file:
            subprocess.run([*base, "-r", str(spec.requirements_file)], check=True)
        dependency_args = resolve_skill_dependency_args(
            spec.dependencies,
            skill_dir=spec.skill_root,
            repo_root=_optional_path_value(self._ctx.paths, "repo_root"),
        )
        if dependency_args:
            subprocess.run([*base, *dependency_args], check=True)

    def _install_deps_if_needed(self, python: Path, spec: ServiceSpec, venv_dir: Path) -> None:
        marker_path = venv_dir / ".adaos-service-deps.json"
        marker = self._dependency_marker(spec)
        try:
            current = marker_path.read_text(encoding="utf-8")
        except Exception:
            current = ""
        if current == marker:
            return
        ensure_dependency_disk_budget(
            venv_dir,
            spec.dependencies,
            has_requirements_file=bool(spec.requirements_file),
            skill_name=spec.skill,
        )
        self._install_deps(python, spec)
        try:
            marker_path.write_text(marker, encoding="utf-8")
        except Exception:
            _log.warning("failed to write service dependency marker skill=%s path=%s", spec.skill, marker_path, exc_info=True)

    def _dependency_marker(self, spec: ServiceSpec) -> str:
        requirement: dict[str, Any] | None = None
        if spec.requirements_file:
            try:
                raw = spec.requirements_file.read_bytes()
                requirement = {
                    "path": str(spec.requirements_file.resolve()),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            except Exception:
                requirement = {"path": str(spec.requirements_file)}
        payload = {
            "skill_root": str(spec.skill_root.resolve()),
            "dependencies": list(spec.dependencies),
            "requirements_file": requirement,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _build_command(python: Path, argv: list[str]) -> list[str]:
        if not argv:
            return [str(python)]
        first = argv[0].lower()
        if first == "python":
            return [str(python), *argv[1:]]
        if first.startswith("-"):
            return [str(python), *argv]
        return [str(python), *argv]

    async def _wait_ready(self, spec: ServiceSpec) -> None:
        timeout_s = max(0.01, float(spec.startup_ready_timeout_s))
        deadline = time.time() + timeout_s
        url = spec.base_url + spec.health_path
        while time.time() < deadline:
            try:
                code, body = await asyncio.to_thread(_http_get, url, timeout_ms=spec.health_timeout_ms)
                if 200 <= code < 300:
                    # Best-effort sanity: ensure it's JSON-ish.
                    try:
                        json.loads(body)
                    except Exception:
                        pass
                    self._health_states[spec.skill] = {
                        "ok": True,
                        "observed_at": time.time(),
                        "source": "startup_readiness",
                    }
                    return
            except Exception:
                await asyncio.sleep(0.25)
        _log.warning(
            "service skill=%s did not become ready within %.1fs (%s)",
            spec.skill,
            timeout_s,
            url,
        )
        self._health_states[spec.skill] = {
            "ok": False,
            "observed_at": time.time(),
            "source": "startup_readiness_timeout",
        }
        raise TimeoutError(
            f"service '{spec.skill}' did not become ready within {timeout_s:.1f} seconds"
        )

    def _record_resource_sample(
        self,
        name: str,
        spec: ServiceSpec,
        sample: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        current = dict(sample)
        previous = self._resource_counters.get(name) or {}
        budget = dict(spec.resource_budget or {})
        elapsed_s = max(0.0, float(current.get("observed_at") or 0.0) - float(previous.get("observed_at") or 0.0))
        same_generation = bool(
            current.get("generation")
            and current.get("generation") == previous.get("generation")
            and elapsed_s > 0.0
        )

        def rate(field: str) -> float | None:
            if not same_generation:
                return None
            delta = max(0, int(current.get(field) or 0) - int(previous.get(field) or 0))
            return round(delta / elapsed_s, 3)

        write_rate = rate("write_bytes")
        read_rate = rate("read_bytes")
        handle_budget_key = (
            "max_file_descriptors"
            if current.get("open_handle_kind") == "file_descriptors"
            else "max_windows_handles"
            if current.get("open_handle_kind") == "windows_handles"
            else "max_open_handles"
        )
        checks: tuple[tuple[str, float | int | None, str, float], ...] = (
            ("write_bytes_per_second", write_rate, "max_write_bytes_per_second", 1.0),
            ("read_bytes_per_second", read_rate, "max_read_bytes_per_second", 1.0),
            ("open_handles", int(current.get("open_handle_total") or 0), handle_budget_key, 1.0),
            ("threads", int(current.get("thread_total") or 0), "max_threads", 1.0),
            ("rss_bytes", int(current.get("rss_bytes") or 0), "max_rss_mb", 1024.0 * 1024.0),
        )
        violations: list[dict[str, Any]] = []
        counts = self._resource_violation_counts.setdefault(name, {})
        active_metrics: set[str] = set()
        sustained_samples = max(1, min(int(budget.get("sustained_samples") or 3), 30))
        for metric, observed, budget_key, multiplier in checks:
            try:
                limit = float(budget.get(budget_key) or 0) * multiplier
            except Exception:
                limit = 0.0
            if observed is None or limit <= 0 or float(observed) <= limit:
                counts[metric] = 0
                continue
            active_metrics.add(metric)
            counts[metric] = int(counts.get(metric) or 0) + 1
            violations.append(
                {
                    "metric": metric,
                    "observed": observed,
                    "limit": int(limit),
                    "samples": counts[metric],
                    "sustained": counts[metric] >= sustained_samples,
                }
            )
        for metric in list(counts):
            if metric not in active_metrics:
                counts[metric] = 0
        sustained = [item for item in violations if item.get("sustained")]
        activity = {
            "schema": "adaos.skill_service_resource_activity.v1",
            "available": bool(current.get("available")),
            "reason": current.get("reason"),
            "observed_at": current.get("observed_at"),
            "sample_interval_seconds": round(elapsed_s, 3) if same_generation else None,
            "owner_pid": current.get("owner_pid"),
            "generation": current.get("generation"),
            "process_total": int(current.get("process_total") or 0),
            "pids": list(current.get("pids") or []),
            "rss_bytes": int(current.get("rss_bytes") or 0),
            "read_bytes": int(current.get("read_bytes") or 0),
            "write_bytes": int(current.get("write_bytes") or 0),
            "read_bytes_per_second": read_rate,
            "write_bytes_per_second": write_rate,
            "thread_total": int(current.get("thread_total") or 0),
            "open_handle_total": int(current.get("open_handle_total") or 0),
            "open_handle_kind": current.get("open_handle_kind"),
            "budget": budget,
            "violations": violations,
            "pressure": "sustained" if sustained else "observed" if violations else "none",
        }
        self._resource_counters[name] = current
        self._resource_activity[name] = activity
        return sustained

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            if self._shutdown_requested:
                return
            now = time.time()

            # Ensure all discovered services are up (unless in crash cooloff).
            await self.refresh_discovered()
            process_states = await asyncio.to_thread(
                _poll_service_processes,
                list(self._procs.items()),
            )
            self._record_polled_process_states(process_states, source="watchdog_ensure")
            for name, spec in list(self._specs.items()):
                if self._shutdown_requested:
                    return
                proc = self._procs.get(name)
                observed = process_states.get(name)
                if proc and observed and observed[0] == id(proc) and observed[1] is None:
                    continue
                cooloff_until = float(self._cooloff_until.get(name) or 0.0)
                if now < cooloff_until:
                    continue
                try:
                    await self.ensure_started(name, spec, force=False)
                except Exception as exc:
                    await self._record_ensure_failure(name, spec, exc)
                    _log.warning("failed to ensure service running skill=%s", name, exc_info=True)

            tracked_processes = list(self._procs.items())
            process_states = await asyncio.to_thread(_poll_service_processes, tracked_processes)
            self._record_polled_process_states(process_states, source="watchdog_crash_scan")
            for name, proc in tracked_processes:
                if self._shutdown_requested:
                    return
                observed = process_states.get(name)
                if not observed or observed[0] != id(proc):
                    continue
                code = observed[1]
                if code is None:
                    continue
                emit(self._ctx.bus, "skill.service.crashed", {"skill": name, "code": code}, source="skill.service")
                self._procs.pop(name, None)
                spec = self._specs.get(name)
                if not spec:
                    continue

                # Crash loop detection (self-managed).
                history = self._crash_history.get(name)
                if history is None:
                    history = deque(maxlen=50)
                    self._crash_history[name] = history
                history.append(now)
                while history and (now - history[0]) > float(spec.crash_window_s):
                    history.popleft()

                if spec.self_managed_enabled and len(history) >= int(spec.crash_max_in_window):
                    self._cooloff_until[name] = now + float(spec.crash_cooloff_s)
                    issue = await self._record_issue(
                        name,
                        issue_type="crash_loop",
                        message=f"service crashed {len(history)} times in {spec.crash_window_s}s; cooloff {spec.crash_cooloff_s}s",
                        severity="error",
                        details={"exit_code": code, "crashes": len(history), "window_s": spec.crash_window_s, "cooloff_s": spec.crash_cooloff_s},
                    )
                    if spec.hook_on_issue:
                        await self._run_hook(spec, spec.hook_on_issue, payload={"issue": issue})
                    if spec.hook_on_self_heal:
                        await self._run_hook(spec, spec.hook_on_self_heal, payload={"issue": issue, "reason": "crash_loop"})
                    continue

                try:
                    await self.ensure_started(name, spec, force=False)
                except Exception as exc:
                    await self._record_ensure_failure(name, spec, exc)
                    _log.warning("failed to restart service skill=%s", name, exc_info=True)

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self._shutdown_requested:
                return
            now = time.time()
            await self.refresh_discovered()
            await asyncio.to_thread(self._membership.expire_stale)
            process_states = await asyncio.to_thread(
                _poll_service_processes,
                list(self._procs.items()),
            )
            self._record_polled_process_states(process_states, source="health_loop")

            for name, spec in list(self._specs.items()):
                if self._shutdown_requested:
                    return
                budget = dict(spec.resource_budget or {})
                try:
                    resource_interval_s = float(budget.get("sample_interval_seconds") or 10.0)
                except Exception:
                    resource_interval_s = 10.0
                resource_interval_s = max(2.0, min(resource_interval_s, 300.0))
                resource_next_at = float(self._next_resource_sample_at.get(name) or 0.0)
                if now >= resource_next_at:
                    self._next_resource_sample_at[name] = now + resource_interval_s
                    proc = self._procs.get(name)
                    observed = process_states.get(name)
                    owner_pid = (
                        int(proc.pid)
                        if proc and observed and observed[0] == id(proc) and observed[1] is None
                        else None
                    )
                    if owner_pid is None and self._external_ready_specs.get(name) == self._spec_key(spec):
                        listener = await asyncio.to_thread(_service_listener_snapshot, spec)
                        if _listener_owned_by_current_runtime(listener):
                            owner_pid = int(listener.get("pid") or 0) or None
                    sample = await asyncio.to_thread(_process_tree_resource_counters, owner_pid)
                    sustained = self._record_resource_sample(name, spec, sample)
                    issue_cooldown_s = max(60.0, resource_interval_s * 6.0)
                    if sustained and now - float(self._resource_issue_last_at.get(name) or 0.0) >= issue_cooldown_s:
                        self._resource_issue_last_at[name] = now
                        activity = dict(self._resource_activity.get(name) or {})
                        issue = await self._record_issue(
                            name,
                            issue_type="service_resource_budget_exceeded",
                            message="service process exceeded its declared sustained resource budget",
                            severity="warning",
                            details={"activity": activity, "violations": sustained},
                        )
                        emit(
                            self._ctx.bus,
                            "skill.service.resource_budget_exceeded",
                            {"skill": name, "issue_id": issue.get("id"), "activity": activity},
                            source="skill.service",
                        )
                next_at = float(self._next_health_check_at.get(name) or 0.0)
                if now < next_at:
                    continue
                self._next_health_check_at[name] = now + float(spec.health_interval_s)

                proc = self._procs.get(name)
                observed = process_states.get(name)
                tracked_running = bool(
                    proc and observed and observed[0] == id(proc) and observed[1] is None
                )
                external_running = self._external_ready_specs.get(name) == self._spec_key(spec)
                if not tracked_running and not external_running:
                    self._health_states[name] = {
                        "ok": False,
                        "observed_at": time.time(),
                        "source": "process_not_running",
                    }
                    if spec.distributed_membership is not None:
                        await asyncio.to_thread(
                            self._membership.reconcile,
                            name,
                            spec.distributed_membership,
                            readiness=False,
                            health={"status": "process_not_running", "ready": False},
                            pressure={"state": "unavailable"},
                        )
                    continue

                ok = False
                status_code = 0
                body = ""
                try:
                    status_code, body = await asyncio.to_thread(
                        _http_get, spec.base_url + spec.health_path, timeout_ms=spec.health_timeout_ms
                    )
                    ok = 200 <= status_code < 300
                except Exception:
                    ok = False
                self._health_states[name] = {
                    "ok": ok,
                    "observed_at": time.time(),
                    "source": "background_probe",
                }
                if spec.distributed_membership is not None:
                    try:
                        service_health = json.loads(body) if body else {}
                    except (TypeError, ValueError):
                        service_health = {}
                    distributed = (
                        service_health.get("distributed")
                        if isinstance(service_health, Mapping)
                        else None
                    )
                    declared_health = (
                        distributed.get("health")
                        if isinstance(distributed, Mapping)
                        else None
                    )
                    declared_pressure = (
                        distributed.get("pressure")
                        if isinstance(distributed, Mapping)
                        else None
                    )
                    membership_health = (
                        dict(declared_health)
                        if isinstance(declared_health, Mapping)
                        else {"status": "passing" if ok else "failing", "ready": ok}
                    )
                    membership_health.update(
                        {"ready": ok, "http_status": int(status_code or 0)}
                    )
                    membership_pressure = (
                        dict(declared_pressure)
                        if isinstance(declared_pressure, Mapping)
                        else {"state": "normal" if ok else "unavailable"}
                    )
                    await asyncio.to_thread(
                        self._membership.reconcile,
                        name,
                        spec.distributed_membership,
                        readiness=ok,
                        health=membership_health,
                        pressure=membership_pressure,
                    )

                if ok:
                    self._health_failures[name] = 0
                    continue

                if not spec.self_managed_enabled:
                    continue

                failures = int(self._health_failures.get(name) or 0) + 1
                self._health_failures[name] = failures
                if failures < int(spec.health_failures_before_issue):
                    continue

                self._health_failures[name] = 0
                issue = await self._record_issue(
                    name,
                    issue_type="healthcheck_failed",
                    message=f"healthcheck failed {spec.health_failures_before_issue} times",
                    severity="warning",
                    details={"url": spec.base_url + spec.health_path, "timeout_ms": spec.health_timeout_ms},
                )
                if spec.hook_on_issue:
                    await self._run_hook(spec, spec.hook_on_issue, payload={"issue": issue})
                if spec.hook_on_self_heal:
                    await self._run_hook(spec, spec.hook_on_self_heal, payload={"issue": issue, "reason": "healthcheck_failed"})


def _task_runtime_state(task: asyncio.Task | None) -> dict[str, Any]:
    if task is None:
        return {"state": "not_started"}
    if task.cancelled():
        return {"state": "cancelled"}
    if not task.done():
        return {"state": "running"}
    try:
        error = task.exception()
    except (asyncio.CancelledError, RuntimeError):
        return {"state": "cancelled"}
    if error is None:
        return {"state": "completed"}
    return {
        "state": "failed",
        "error_type": type(error).__name__,
        "error": str(error)[:300],
    }


def service_supervisor_runtime_summary() -> dict[str, Any]:
    supervisor = _SUPERVISOR
    if supervisor is None:
        return {
            "schema": "adaos.skill_service_supervisor.runtime.v1",
            "state": "not_initialized",
            "initialized": False,
            "distributed": [],
        }

    distributed: list[dict[str, Any]] = []
    for name, spec in sorted(supervisor._specs.items())[:64]:
        if getattr(spec, "distributed_membership", None) is None:
            continue
        membership = supervisor._membership.status(name)
        health = dict(supervisor._health_states.get(name) or {})
        process = dict(supervisor._process_states.get(name) or {})
        distributed.append(
            {
                "skill": name,
                "process_running": bool(process.get("running")),
                "health_ok": health.get("ok"),
                "health_source": health.get("source"),
                "membership": {
                    key: membership[key]
                    for key in (
                        "enabled",
                        "ok",
                        "state",
                        "group_id",
                        "authority",
                        "action",
                        "instance_id",
                        "error",
                        "observed_at",
                    )
                    if key in membership
                },
            }
        )
        if len(distributed) >= 32:
            break

    health_task = _task_runtime_state(supervisor._health_task)
    watchdog_task = _task_runtime_state(supervisor._task)
    shutdown_requested = bool(supervisor._shutdown_requested)
    failed = health_task.get("state") == "failed" or watchdog_task.get("state") == "failed"
    return {
        "schema": "adaos.skill_service_supervisor.runtime.v1",
        "state": "shutdown" if shutdown_requested else "failed" if failed else "running",
        "initialized": True,
        "shutdown_requested": shutdown_requested,
        "discovered_count": len(supervisor._specs),
        "tasks": {
            "health": health_task,
            "watchdog": watchdog_task,
        },
        "distributed": distributed,
    }


_SUPERVISOR: ServiceSkillSupervisor | None = None


def get_service_supervisor() -> ServiceSkillSupervisor:
    global _SUPERVISOR
    if _SUPERVISOR is None or getattr(_SUPERVISOR, "_shutdown_requested", False):
        _SUPERVISOR = ServiceSkillSupervisor()
    return _SUPERVISOR
