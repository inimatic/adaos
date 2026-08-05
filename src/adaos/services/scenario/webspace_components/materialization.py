from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from collections.abc import Callable, Mapping
from typing import Any


class MaterializationExecutorOwner:
    """Own bounded CPU and subprocess execution for materialization."""

    def __init__(self) -> None:
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()

    def get(self, *, max_workers: int) -> ThreadPoolExecutor:
        executor = self._executor
        if executor is not None:
            return executor
        with self._lock:
            executor = self._executor
            if executor is None:
                executor = ThreadPoolExecutor(
                    max_workers=max(1, int(max_workers)),
                    thread_name_prefix="adaos-materialize",
                )
                self._executor = executor
        return executor

    def shutdown(self) -> None:
        with self._lock:
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    async def run_cpu(
        self,
        function: Callable[..., Any],
        /,
        *args: Any,
        max_workers: int,
        oneshot: bool,
        **kwargs: Any,
    ) -> Any:
        if oneshot:
            return function(*args, **kwargs)
        loop = asyncio.get_running_loop()
        call = partial(function, *args, **kwargs)
        return await loop.run_in_executor(self.get(max_workers=max_workers), call)

    @staticmethod
    def _elapsed_ms(started_at: float) -> float:
        return round((time.perf_counter() - started_at) * 1000.0, 3)

    async def run_worker(
        self,
        request: Mapping[str, Any],
        *,
        timeout_s: float,
        max_rss_bytes: int,
        max_result_bytes: int,
        result_adapter: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> dict[str, Any]:
        """Run one isolated materialization request with bounded resources."""
        started = time.perf_counter()
        peak_rss = 0
        with tempfile.TemporaryDirectory(prefix="adaos-materialize-") as temp_dir:
            root = Path(temp_dir)
            request_path = root / "request.json"
            result_path = root / "result.json"
            stdout_path = root / "stdout.log"
            stderr_path = root / "stderr.log"
            request_path.write_text(
                json.dumps(dict(request), ensure_ascii=True, separators=(",", ":")),
                encoding="utf-8",
            )
            cmd = [
                sys.executable,
                "-m",
                "adaos.services.scenario.materialization_worker",
                str(request_path),
                str(result_path),
            ]
            env = os.environ.copy()
            env["ADAOS_MATERIALIZATION_WORKER"] = "0"
            creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0

            with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
                "w",
                encoding="utf-8",
            ) as stderr_file:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    env=env,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    creationflags=creationflags,
                )
                try:
                    import psutil

                    process = psutil.Process(proc.pid)
                except Exception:
                    process = None

                def _process_tree() -> list[Any]:
                    if process is None:
                        return []
                    try:
                        return [process, *process.children(recursive=True)]
                    except Exception:
                        return [process]

                def _process_tree_rss() -> int:
                    total = 0
                    for item in _process_tree():
                        try:
                            total += int(item.memory_info().rss)
                        except Exception:
                            continue
                    return total

                async def _stop_process_tree() -> None:
                    descendants = _process_tree()[1:]
                    for child in reversed(descendants):
                        try:
                            child.terminate()
                        except Exception:
                            continue
                    if proc.returncode is None:
                        try:
                            proc.terminate()
                        except ProcessLookupError:
                            pass
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            try:
                                proc.kill()
                            except ProcessLookupError:
                                pass
                            await proc.wait()
                    deadline = time.monotonic() + 5.0
                    alive = list(descendants)
                    while alive and time.monotonic() < deadline:
                        remaining = []
                        for child in alive:
                            try:
                                if child.is_running():
                                    remaining.append(child)
                            except Exception:
                                continue
                        alive = remaining
                        if alive:
                            await asyncio.sleep(0.05)
                    for child in alive:
                        try:
                            child.kill()
                        except Exception:
                            continue

                failure: str | None = None
                wait_task = asyncio.create_task(proc.wait())
                try:
                    while not wait_task.done():
                        elapsed_s = time.perf_counter() - started
                        if elapsed_s > timeout_s:
                            failure = "materialization_worker_timeout"
                            break
                        if process is not None:
                            try:
                                current_rss = _process_tree_rss()
                                peak_rss = max(peak_rss, current_rss)
                                if current_rss > max_rss_bytes:
                                    failure = "materialization_worker_rss_limit"
                                    break
                            except Exception:
                                process = None
                        try:
                            await asyncio.wait_for(asyncio.shield(wait_task), timeout=0.05)
                        except asyncio.TimeoutError:
                            continue
                    if failure:
                        await _stop_process_tree()
                        await wait_task
                        raise RuntimeError(
                            f"{failure}: elapsed_ms={self._elapsed_ms(started)} "
                            f"peak_rss_bytes={peak_rss}"
                        )
                    returncode = int(await wait_task)
                except BaseException:
                    await _stop_process_tree()
                    if not wait_task.done():
                        await asyncio.shield(wait_task)
                    raise

            try:
                stderr_tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            except Exception:
                stderr_tail = ""
            if not result_path.exists():
                raise RuntimeError(
                    f"materialization_worker_no_result: returncode={returncode} stderr={stderr_tail}"
                )
            result_size = int(result_path.stat().st_size)
            if result_size > max_result_bytes:
                raise RuntimeError(f"materialization_worker_result_limit: bytes={result_size}")
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if not isinstance(result, dict) or returncode != 0 or not bool(result.get("ok")):
                detail = str(result.get("detail") if isinstance(result, dict) else "")
                raise RuntimeError(
                    "materialization_worker_failed: "
                    f"returncode={returncode} detail={detail} stderr={stderr_tail}"
                )
            child_final_rss = int(result.get("worker_rss_bytes") or 0)
            worker_peak_rss = max(peak_rss, child_final_rss)
            if worker_peak_rss > max_rss_bytes:
                raise RuntimeError(
                    f"materialization_worker_rss_limit: peak_rss_bytes={worker_peak_rss}"
                )
            result["worker_peak_rss_bytes"] = worker_peak_rss
            result["worker_result_bytes"] = result_size
            result["worker_parent_elapsed_ms"] = self._elapsed_ms(started)
            snapshot_b64 = result.pop("snapshot_update_b64", None)
            state_vector_b64 = result.pop("state_vector_b64", None)
            if isinstance(snapshot_b64, str):
                result["snapshot_update"] = base64.b64decode(snapshot_b64.encode("ascii"))
            if isinstance(state_vector_b64, str):
                result["state_vector"] = base64.b64decode(state_vector_b64.encode("ascii"))
            payload = result.get("materialized_payload")
            if isinstance(payload, Mapping) and result_adapter is not None:
                result["entry"] = result_adapter(payload)
            return result
