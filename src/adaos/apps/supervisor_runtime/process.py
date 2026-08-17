from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ProcessSupervisorOperations:
    active_slot: Any
    active_slot_manifest: Any
    adopted_process_type: Any
    core_slot_status: Any
    current_base_dir: Any
    format_slot_value: Any
    listener_owner_pid: Any
    logger: Any
    new_runtime_instance_id: Any
    proc_details: Any
    read_json: Any
    read_memory_session_summary: Any
    read_slot_manifest: Any
    requests_module: Any
    runtime_api_ready: Any
    supervisor_runtime_state_path: Any


class ProcessSupervisor:
    """Own OS process and listener primitives used by the supervisor."""

    def __init__(self, psutil_module: Any | None) -> None:
        self.psutil = psutil_module
        self.active: Any | None = None
        self.candidate: Any | None = None
        self.sidecar: Any | None = None
        self.desired_running = True
        self.stopping = False
        self.lock = asyncio.Lock()
        self.monitor_task: asyncio.Task[Any] | None = None

    def track_active(self, process: Any | None) -> Any | None:
        self.active = process
        return process

    def track_candidate(self, process: Any | None) -> Any | None:
        self.candidate = process
        return process

    def track_sidecar(self, process: Any | None) -> Any | None:
        self.sidecar = process
        return process

    def request_running(self) -> None:
        self.desired_running = True
        self.stopping = False

    def request_stop(self) -> None:
        self.desired_running = False
        self.stopping = True

    def start_monitor(self, monitor: Any, *, name: str = "adaos-supervisor-monitor") -> asyncio.Task[Any]:
        existing = self.monitor_task
        if existing is not None and not existing.done():
            return existing
        task = asyncio.create_task(monitor(), name=name)
        self.monitor_task = task
        return task

    async def stop_monitor(self) -> None:
        task = self.monitor_task
        self.monitor_task = None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    async def _wait_for_exit(process: Any, timeout_sec: float, *, interval_sec: float) -> bool:
        deadline = time.time() + max(0.0, float(timeout_sec))
        checks = max(1, int(max(0.0, float(timeout_sec)) / interval_sec) + 2)
        while time.time() < deadline and checks > 0:
            checks -= 1
            if process.poll() is not None:
                return True
            await asyncio.sleep(interval_sec)
        return process.poll() is not None

    async def terminate_process(
        self,
        process: Any,
        *,
        graceful_wait_sec: float,
        terminate_wait_sec: float,
        before_signal: Callable[[str], Any] | None = None,
        signal_process: Callable[[Any, int], Any] | None = None,
    ) -> None:
        """Own the bounded graceful/TERM/KILL process termination ladder."""
        if process is None or process.poll() is not None:
            return
        if await self._wait_for_exit(process, graceful_wait_sec, interval_sec=0.2):
            return
        if before_signal is not None:
            before_signal("forced_terminate")
        signaler = signal_process or self.signal_family
        signaler(process, signal.SIGTERM)
        if await self._wait_for_exit(process, terminate_wait_sec, interval_sec=0.1):
            return
        if before_signal is not None:
            before_signal("forced_kill")
        signaler(process, getattr(signal, "SIGKILL", 9))
        if await self._wait_for_exit(process, terminate_wait_sec, interval_sec=0.1):
            return
        raise RuntimeError("process did not exit after forced kill")

    @staticmethod
    def listener_running(host: str, port: int, *, timeout: float = 0.35) -> bool:
        try:
            with socket.create_connection(
                (str(host or "127.0.0.1"), int(port)),
                timeout=max(0.05, float(timeout)),
            ):
                return True
        except Exception:
            return False

    @staticmethod
    def signal_family(proc: subprocess.Popen[Any], sig: int) -> None:
        if os.name != "nt":
            pid = getattr(proc, "pid", None)
            if pid:
                try:
                    os.killpg(int(pid), int(sig))
                    return
                except ProcessLookupError:
                    pass
                except Exception:
                    pass
        if sig == getattr(signal, "SIGKILL", 9):
            proc.kill()
        else:
            proc.terminate()

    @staticmethod
    def describe(proc: subprocess.Popen[Any] | None, *, cwd_hint: str | None = None) -> dict[str, Any]:
        if proc is None:
            return {
                "managed_pid": None,
                "managed_alive": False,
                "managed_cmdline": [],
                "managed_executable": None,
                "managed_cwd": None,
            }
        try:
            managed_pid = int(proc.pid or 0) or None
            managed_alive = proc.poll() is None
            raw_args = proc.args if isinstance(proc.args, (list, tuple)) else [str(proc.args or "")]
            managed_cmdline = [str(item) for item in raw_args if str(item or "").strip()]
            managed_executable = managed_cmdline[0] if managed_cmdline else None
            managed_cwd = str(cwd_hint or getattr(proc, "cwd", None) or "").strip() or None
        except Exception:
            managed_pid = None
            managed_alive = False
            managed_cmdline = []
            managed_executable = None
            managed_cwd = None
        return {
            "managed_pid": managed_pid,
            "managed_alive": managed_alive,
            "managed_cmdline": managed_cmdline,
            "managed_executable": managed_executable,
            "managed_cwd": managed_cwd,
        }

    def listener_owner_pid(self, host: str, port: int) -> int | None:
        if self.psutil is None:
            return None
        expected_port = int(port)
        expected_host = str(host or "127.0.0.1").strip()
        try:
            connections = self.psutil.net_connections(kind="tcp")
        except Exception:
            return None
        for connection in connections:
            if str(getattr(connection, "status", "")).upper() != "LISTEN":
                continue
            address = getattr(connection, "laddr", None)
            if not address or int(getattr(address, "port", 0) or 0) != expected_port:
                continue
            bound_host = str(getattr(address, "ip", "") or "")
            if expected_host not in {"0.0.0.0", "::", ""} and bound_host not in {
                expected_host,
                "0.0.0.0",
                "::",
                "::1" if expected_host == "127.0.0.1" else expected_host,
            }:
                continue
            try:
                pid = int(getattr(connection, "pid", 0) or 0)
            except Exception:
                pid = 0
            if pid > 0:
                return pid
        return None


    def runtime_launch_spec(
        self,
        manager: Any,
        operations: ProcessSupervisorOperations,
        *,
        slot: str | None = None,
        transition_role: str = "active",
        runtime_instance_id: str | None = None,
        profile_mode: str | None = None,
        profile_session_id: str | None = None,
        profile_trigger: str | None = None,
        skip_pending_update: bool = False,
    ) -> tuple[list[str] | None, str | None, dict[str, str], str | None, str, str]:
        resolved_slot = str(slot or operations.active_slot() or "").strip().upper() or None
        manifest = operations.read_slot_manifest(resolved_slot) if slot else operations.active_slot_manifest()
        slot_port = manager.slot_runtime_port(resolved_slot)
        slot_dir = str(operations.core_slot_status().get("slots", {}).get(resolved_slot or "", {}).get("path") or "")
        resolved_runtime_instance_id = str(
            runtime_instance_id or operations.new_runtime_instance_id(slot=resolved_slot, transition_role=transition_role)
        )
        requested_session_id = profile_session_id
        requested_mode = str(profile_mode or "").strip().lower() or ""
        resolved_profile_trigger = str(profile_trigger or "").strip() or None
        if not requested_mode:
            requested_session_id = str(manager._memory_active_session_id or "").strip() or None
            requested_mode = manager._desired_memory_profile_mode()
        if requested_session_id and not resolved_profile_trigger:
            session = operations.read_memory_session_summary(requested_session_id) or {}
            trigger_source = str(session.get("trigger_source") or "").strip() or "operator"
            trigger_reason = str(session.get("trigger_reason") or "").strip() or "supervisor.memory.request"
            resolved_profile_trigger = f"{trigger_source}:{trigger_reason}"
        env = manager._runtime_env(
            slot=resolved_slot,
            slot_dir=slot_dir,
            slot_port=slot_port,
            transition_role=transition_role,
            runtime_instance_id=resolved_runtime_instance_id,
            profile_mode=requested_mode,
            profile_session_id=requested_session_id,
            profile_trigger=resolved_profile_trigger,
            skip_pending_update=skip_pending_update,
        )
        if isinstance(manifest, dict):
            manifest_env = manifest.get("env")
            if isinstance(manifest_env, dict):
                for key, value in manifest_env.items():
                    env[str(key)] = str(value)
            if resolved_slot:
                env["ADAOS_ACTIVE_CORE_SLOT"] = resolved_slot
                env["ADAOS_ACTIVE_CORE_SLOT_DIR"] = slot_dir
            values = {
                "host": manager.runtime_host,
                "port": str(slot_port),
                "token": str(manager.token or ""),
                "slot": str(resolved_slot or ""),
                "slot_dir": slot_dir,
                "base_dir": str(operations.current_base_dir()),
                "python": os.sys.executable,
                "runtime_instance_id": resolved_runtime_instance_id,
                "transition_role": str(transition_role or "active"),
            }
            argv_raw = manifest.get("argv")
            if isinstance(argv_raw, list):
                argv = [operations.format_slot_value(str(item), values) for item in argv_raw if str(item).strip()]
                if argv:
                    cwd = str(manifest.get("cwd") or "").strip() or None
                    return argv, None, env, cwd, resolved_runtime_instance_id, str(transition_role or "active")
            command = str(manifest.get("command") or "").strip()
            if command:
                cwd = str(manifest.get("cwd") or "").strip() or None
                return None, operations.format_slot_value(command, values), env, cwd, resolved_runtime_instance_id, str(
                    transition_role or "active"
                )
        return (
            [
                sys.executable,
                "-m",
                "adaos.apps.autostart_runner",
                "--host",
                manager.runtime_host,
                "--port",
                str(slot_port),
            ],
            None,
            env,
            None,
            resolved_runtime_instance_id,
            str(transition_role or "active"),
        )


    def adopt_active_runtime_listener(
        self,
        manager: Any,
        operations: ProcessSupervisorOperations,
        *,
        reason: str,
    ) -> bool:
        current_slot = str(operations.active_slot() or "").strip().upper() or None
        runtime_port = manager.slot_runtime_port(current_slot)
        runtime_url = manager.slot_runtime_base_url(current_slot)
        listener_pid = operations.listener_owner_pid(manager.runtime_host, runtime_port)
        if not listener_pid:
            return False
        adopted = operations.adopted_process_type(listener_pid)
        identity: dict[str, Any] = {}
        api_ready = operations.runtime_api_ready(runtime_url, token=manager.token, timeout=1.5)
        if api_ready:
            headers = {"Accept": "application/json"}
            if manager.token:
                headers["X-AdaOS-Token"] = manager.token
            for identity_path in ("/api/ping", "/api/status"):
                try:
                    with operations.requests_module.get(
                        runtime_url + identity_path,
                        headers=headers,
                        timeout=2.0,
                    ) as response:
                        response.raise_for_status()
                        payload = response.json()
                    if isinstance(payload, dict) and isinstance(payload.get("runtime"), dict):
                        identity = dict(payload["runtime"])
                    if identity:
                        break
                except Exception:
                    continue
        else:
            managed = operations.proc_details(adopted, cwd_hint=str(adopted.cwd or "").strip() or None)
            expected_executable, expected_cwd, matches_active_slot = manager._managed_runtime_slot_expectations(
                manifest=operations.active_slot_manifest(),
                managed_executable=managed.get("managed_executable"),
                managed_cwd=managed.get("managed_cwd"),
            )
            if matches_active_slot is not True:
                operations.logger.warning(
                    "supervisor refused pre-ready runtime adoption slot=%s url=%s pid=%s expected_executable=%s "
                    "actual_executable=%s expected_cwd=%s actual_cwd=%s",
                    current_slot,
                    runtime_url,
                    listener_pid,
                    expected_executable,
                    managed.get("managed_executable"),
                    expected_cwd,
                    managed.get("managed_cwd"),
                )
                return False
            persisted = operations.read_json(operations.supervisor_runtime_state_path())
            try:
                persisted_pid = int(persisted.get("managed_pid") or 0)
            except Exception:
                persisted_pid = 0
            if persisted_pid == listener_pid:
                identity = {
                    "runtime_instance_id": persisted.get("runtime_instance_id"),
                    "transition_role": persisted.get("transition_role"),
                    "slot": persisted.get("managed_slot"),
                }
            operations.logger.info(
                "supervisor adopting slot-matched runtime listener before API readiness slot=%s url=%s pid=%s",
                current_slot,
                runtime_url,
                listener_pid,
            )
        reported_slot = str(identity.get("slot") or "").strip().upper()
        reported_role = str(identity.get("transition_role") or "active").strip().lower()
        if reported_slot and current_slot and reported_slot != current_slot:
            raise RuntimeError(
                f"runtime listener on {runtime_url} reports slot {reported_slot}, expected {current_slot}"
            )
        if reported_role != "active":
            raise RuntimeError(
                f"runtime listener on {runtime_url} reports transition role {reported_role or 'unknown'}"
            )
        manager._process_supervisor.track_active(adopted)
        manager._managed_runtime_instance_id = str(identity.get("runtime_instance_id") or "").strip() or None
        manager._managed_transition_role = "active"
        manager._managed_slot = current_slot
        manager._managed_runtime_port = runtime_port
        manager._managed_runtime_base_url = runtime_url
        manager._managed_runtime_cwd = str(adopted.cwd or "").strip() or None
        manager._managed_start_reason = str(reason or "supervisor.adopt.active_listener")
        manager._managed_runtime_api_identity_verified = bool(
            api_ready
            and reported_slot
            and current_slot
            and reported_slot == current_slot
            and reported_role == "active"
            and str(identity.get("runtime_instance_id") or "").strip()
        )
        manager._last_start_at = float(getattr(adopted, "_created_at", time.time()))
        manager._last_error = None
        manager._runtime_unhealthy_since = None
        manager._runtime_unhealthy_kind = None
        manager._reset_memory_baseline_scope(managed_pid=listener_pid)
        operations.logger.info(
            "supervisor adopted active runtime listener slot=%s url=%s pid=%s instance=%s",
            current_slot,
            runtime_url,
            listener_pid,
            manager._managed_runtime_instance_id,
        )
        return True



class AdoptedProcess:
    """Popen-compatible handle for a listener inherited across supervisor restart."""

    def __init__(self, pid: int, *, psutil_module: Any) -> None:
        if psutil_module is None:
            raise RuntimeError("psutil is required to adopt an existing process")
        self._psutil = psutil_module
        process = psutil_module.Process(int(pid))
        self.pid = int(pid)
        self._created_at = float(process.create_time())
        try:
            self.args = list(process.cmdline())
        except Exception:
            self.args = []
        try:
            self.cwd = str(process.cwd())
        except Exception:
            self.cwd = None

    def _process(self) -> Any | None:
        try:
            process = self._psutil.Process(self.pid)
            if abs(float(process.create_time()) - self._created_at) > 0.001:
                return None
            return process
        except Exception:
            return None

    def poll(self) -> int | None:
        process = self._process()
        return None if process is not None and process.is_running() else 0

    def terminate(self) -> None:
        process = self._process()
        if process is not None:
            process.terminate()

    def kill(self) -> None:
        process = self._process()
        if process is not None:
            process.kill()
