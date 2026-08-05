from __future__ import annotations

import os
import signal
import socket
import subprocess
from typing import Any


class ProcessSupervisor:
    """Own OS process and listener primitives used by the supervisor."""

    def __init__(self, psutil_module: Any | None) -> None:
        self.psutil = psutil_module

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
