"""Credential-free Codex model discovery, using the executor's local identity."""

from __future__ import annotations

import json
from pathlib import Path
from queue import Empty, Queue
import subprocess
from threading import Lock, Thread
import time
from typing import Any


_LOCK = Lock()
_CACHE: dict[tuple, tuple[float, dict[str, Any]]] = {}


def _discover(executable: str, environment: dict[str, str], *, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    process = subprocess.Popen([executable, "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", cwd=Path.home(), env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    incoming: Queue = Queue(maxsize=64)

    def read():
        try:
            for line in process.stdout:
                incoming.put(json.loads(line), timeout=timeout)
        except Exception:
            pass
        finally:
            try:
                incoming.put(None, timeout=timeout)
            except Exception:
                pass

    reader = Thread(target=read, name="codex-model-discovery", daemon=True)
    reader.start()

    def send(method: str, params: dict, identifier: int | None = None):
        message = {"method": method, "params": params}
        if identifier is not None:
            message["id"] = identifier
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def result(identifier: int):
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("Codex model discovery timed out")
            try:
                message = incoming.get(timeout=remaining)
            except Empty as exc:
                raise TimeoutError("Codex model discovery timed out") from exc
            if message is None:
                raise RuntimeError("Codex model discovery connection closed")
            if message.get("id") == identifier:
                if "error" in message:
                    raise RuntimeError(f"Codex model discovery rejected request {identifier}")
                return message["result"]

    try:
        send("initialize", {"clientInfo": {"name": "adaos_model_catalog", "version": "1.0"}}, 1)
        result(1)
        send("initialized", {})
        models = []
        cursor = None
        for index in range(10):
            send("model/list", {"limit": 50, "includeHidden": False, **({"cursor": cursor} if cursor else {})}, index + 2)
            page = result(index + 2)
            for row in page.get("data") or []:
                if not row.get("model") or row.get("hidden"):
                    continue
                models.append({"id": row["model"], "label": row.get("displayName") or row["model"],
                    "default_reasoning_effort": row.get("defaultReasoningEffort"),
                    "supported_reasoning_efforts": [item["reasoningEffort"] for item in row.get("supportedReasoningEfforts", [])],
                    "default": row.get("isDefault") is True})
            cursor = page.get("nextCursor")
            if not cursor:
                return {"status": "ready", "source": "codex:model/list", "models": models,
                        "elapsed_ms": round((time.monotonic() - started) * 1000)}
        raise RuntimeError("Codex model discovery pagination exceeded its bound")
    finally:
        process.stdin.close()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        reader.join(timeout=1)
        process.stdout.close()


def local_codex_models(*, refresh: bool = False, timeout: float = 10) -> dict[str, Any]:
    from adaos.services.skill_factory_worker import SubprocessCodexExecutor

    try:
        executor = SubprocessCodexExecutor()
        executable = executor._resolve_executable()
        environment = executor._bounded_environment()
        home = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")
        auth = home / "auth.json"
        key = (executable, str(home), auth.stat().st_mtime_ns if auth.is_file() else None)
        with _LOCK:
            cached = _CACHE.get(key)
            if not refresh and cached and time.monotonic() - cached[0] < 60:
                return {**cached[1], "cached": True}
            value = _discover(executable, environment, timeout=timeout)
            _CACHE.clear()
            _CACHE[key] = (time.monotonic(), value)
            return {**value, "cached": False}
    except Exception as exc:
        return {"status": "unavailable", "source": "codex:model/list", "models": [],
                "reason": f"codex_discovery_{type(exc).__name__}"}
